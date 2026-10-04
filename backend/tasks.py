"""任务处理器：把各类操作接进队列。

每个处理器签名统一为 (task, ctx) -> (ok, result, error)，
并且都在 worker 线程里跑，不阻塞 WebUI。
"""
from __future__ import annotations

import json
import re
import shutil
import time
import zipfile
from pathlib import Path

from backend import audio, config, formats, logs, runner, store
from backend.formats import is_lossy
from backend.queue import Context
from backend.toolchain import toolchain

# ---------------------------------------------------------------- 工具

# 目标格式 → ffmpeg 编码参数白名单（需求 §4.8：参数必须来自白名单）
#
# ⚠ 这份表的事实源在 `backend/formats.py`（四个消费方共用），这里只是**别名**。
# 别再往这里抄第二份 —— 抄了就会出现"formats.py 认得这个容器、tasks.py 不认得"
# （或反过来）的分叉，而两边的失败方式都是静默的。加格式请改 formats.py。
FORMAT_ARGS: dict[str, list[str]] = formats.FORMAT_ARGS

SAMPLE_RATES = {8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000}
CHANNELS = {1, 2, 6, 8}
FLAC_LEVELS = {0, 1, 2, 3, 4, 5, 6, 7, 8}
BITRATES = {"96k", "128k", "160k", "192k", "256k", "320k"}

# 位深覆盖：只对无损容器有意义。
# 取值全部在本机 ffmpeg 9.0.2 上实跑验证过：
#   wav  + pcm_s16le/pcm_s24le/pcm_s32le/pcm_f32le  ✓
#   aiff + pcm_s16be/pcm_s24be/pcm_s32be/pcm_f32be  ✓（AIFF 是**大端**，
#         不能像 WAV 那样用 le，写错过一次）
#   flac 走 -sample_fmt：s16→16bit、s32→24bit；**浮点不被 flac 编码器支持**
BIT_DEPTHS = ("16", "24", "32", "32f")
_PCM_CODECS: dict[str, dict[str, str]] = {
    "wav":  {"16": "pcm_s16le", "24": "pcm_s24le", "32": "pcm_s32le", "32f": "pcm_f32le"},
    "aiff": {"16": "pcm_s16be", "24": "pcm_s24be", "32": "pcm_s32be", "32f": "pcm_f32be"},
}
_FLAC_SAMPLE_FMT = {"16": "s16", "24": "s32", "32": "s32"}   # 32f 不支持

# 无损格式没有"码率"这个可调项，给了 -b:a 只会让 ffmpeg 报
# "Codec flac does not support bitrate" 或产出意外结果。
# 这份名单的事实源在 `backend/formats.py`（三个消费方共用），这里只是取个短别名。
# 注意是**不带点**的容器名（下面拿它跟 `target` 比），别顺手加 `.`。
LOSSLESS_FORMATS = {k for k in FORMAT_ARGS if not is_lossy(k)}
COVER_UNSUPPORTED = {"wav", "aac", "aiff"}
COVER_CAPABLE = {"m4a", "mp3", "mp4", "mov"}
# ---------------------------------------------------------------- 波形图

WAVE_MIN_W, WAVE_MAX_W = 200, 8000
# 上限 4096 是为了让 4K（3840×2160）和手机壁纸（1170×2532）这类
# 真实用途能落地 —— 原来卡在 2000，那两张卡会被静默夹到 2000
WAVE_MIN_H, WAVE_MAX_H = 60, 4096
WAVE_SCALES = {"lin", "log", "sqrt", "cbrt"}
WAVE_BACKGROUNDS = {"transparent": None, "black": "0x000000", "white": "0xFFFFFF"}

# ffmpeg 内部会走一轮 YUV 往返，纯白 0xFFFFFF 出来是 (254,254,254)。
# 把每个通道里"接近满值"的像素拉回 255，保证导出的白色是 #FFFFFF 而不是 #FEFEFE。
# 对非白颜色也能把被削掉的那 1~2 拉回来（其余通道不受影响）。
_WAVE_SNAP = ("lutrgb="
              "r='if(gt(val,250),255,val)':"
              "g='if(gt(val,250),255,val)':"
              "b='if(gt(val,250),255,val)'")


def _hex_color(raw: object, fallback: str = "0xFFFFFF") -> str:
    s = str(raw or "").strip().lower().lstrip("#")
    if s.startswith("0x"):
        s = s[2:]
    if not re.fullmatch(r"[0-9a-f]{6}", s):
        return fallback
    return "0x" + s.upper()



def _file_of(task: store.TaskRow) -> Path:
    """本任务的**输入文件**。

    执行链（方案 §3.1.3）下有两种解析路径，**都不是这里新加的机制**：

      · `in_place`（改标签/改封面/重命名）→ 就地改写，`file_id` 不变，
        所以下一步读同一个 `file_id` 自然读到改过的内容。**这条是零代码的**。
      · `derived`（转换/标准化）→ 上游成功时 `attach_src_output` 已经把本任务的
        `file_id` 换成**派生产物那一行**，所以下面这行同样拿到产物。

    换句话说：**"吃上一步的产物"是通过换 `file_id` 实现的，不是在这里做路径拼装。**
    这样租约、`aggregate_state`、任务历史三处都自动跟着走，不会各错一份。
    """
    f = store.get_file(task.file_id) if task.file_id else None
    if not f:
        raise RuntimeError("任务没有关联文件")
    p = f.path
    if not p.exists():
        raise RuntimeError(f"源文件不存在: {f.name}")
    _guard_input(task, p)
    return p


def _guard_input(task: store.TaskRow, p: Path) -> None:
    """拿到的东西**根本不是音频也不是图片**时，报一句人话（规则 R8）。

    什么时候会发生：`tasks.file_id` 指向一个旁路产物（`.md` 报告 / `.zip`）。
    正常路径下 `queue._publish_derived` 只交接 `derived` 音频产物，走不到这里；
    但**历史库里已经有被错误交接的任务行**（`src_output` 指向 `.md`），
    用户还可能从别的机器导入链快照 —— 那时 ffmpeg 只会抛
    `Invalid data found when processing input`，用户完全看不出因果。

    ⚠ **图片不算**：上传封面图会被自动探测、也能被 `zip` 收走，
    那是合法场景（`probe` 读图片是有意义的，ffprobe 会给出容器信息）。
    所以只有"既不是音频也不是图片"才拦。
    """
    if config.is_audio(p) or config.is_image(p):
        return
    from backend.cards.specs import op_of_task
    from backend.cards import contract as _contract

    op = op_of_task(task.type, task.params)
    needs = _contract.needs_of(op) if op in _contract.CONTRACT else ()
    if needs and "audio" not in needs:
        return
    raise RuntimeError(
        f"这一步需要音频，但拿到的是 {p.suffix or '未知格式'} 文件（{p.name}）—— "
        f"上一步交出的是**旁路产物**（波形图 / 响度图 / 响度报告 / 提取出的封面），"
        f"它不会被递给下一步。把这一步移出链，或把执行链切成**并行档**"
        f"（各步都直接作用于原文件）。")


def _out_path(src: Path, suffix: str, subdir: str = "",
              task: "store.TaskRow | None" = None) -> Path:
    """输出路径【永远由系统生成】，落在 outputs/ 下，不覆盖已有文件。

    **执行链的产物落进"本次执行的目录"**（`执行链打包与串行交接方案.md` §5）：

        outputs/upload-20261002-f99a87/loudness/song.loudness.md
        └────── 本次执行 ──────┘└─ 分类子目录 ─┘

    理由：产物按 `unique_path` 加 `-1`/`-2` 堆在同一个 `outputs/loudness/` 里，
    用户分不清"哪个是刚才那次跑出来的"（要求原文：**每一次执行链得到的产物
    不能和另一次合并到一起**）。目录名带 `chain_id` 尾号 → 天生唯一、不会撞。

    **非链任务（右键菜单 / 单卡执行）保持旧路径**：那些是一次性动作，
    没有"哪一次执行"的语义，改了只会让所有存量断言与用户习惯一起变。
    """
    base = config.OUTPUTS / subdir if subdir else config.OUTPUTS
    if task is not None and task.chain_id:
        started = store.chain_started_at(task.chain_id) or time.time()
        base = config.OUTPUTS / config.run_dir_name(task.chain_id, started)
        if subdir:
            base = base / subdir
    base.mkdir(parents=True, exist_ok=True)
    return config.unique_path(base / f"{src.stem}{suffix}")


def _artifact(out: Path) -> dict:
    """产物的对外字段。

    **两个都要，各有各的用处，不能合并**：
      · `output`  —— 相对**项目根**，前端"下载/显示产物"读的就是它（`app.js` 里
                     那张 `output` 字段）。改它等于连带改前端。
      · `relPath` —— 相对 **`outputs/`**，后端拿它建派生产物行、交给下游当输入
                     （`store.add_derived_file` 的 `rel_to_outputs` 就是这个口径）。

    写成同一个函数，是为了避免"某个 handler 加了 relPath、另一个漏了"——
    漏了的表现是**那一步的产物接不到下一步**，而且只在链里才暴露。
    """
    rel = str(out.relative_to(config.OUTPUTS)).replace("\\", "/")
    return {
        "output": str(out.relative_to(config.ROOT)).replace("\\", "/"),
        "relPath": rel,
        "name": out.name,
        "size": out.stat().st_size if out.exists() else 0,
    }


# ---------------------------------------------------------------- probe

def h_probe(task: store.TaskRow, ctx: Context) -> tuple[bool, dict, str]:
    p = _file_of(task)
    ctx.progress(20)
    info = audio.probe(p)
    if not info.ok:
        return False, {}, info.error
    ctx.progress(90)
    # **合并**而不是覆盖：probe 读不出响度（ffprobe 只读元数据、不解码），
    # 整体覆盖等于"每探测一次就把已经测出来的响度擦掉"。文件内容真的换了的话，
    # 旧测量值在 `store.add_file` 那一步就已经被丢掉了（`_forget_measurements`）。
    store.merge_file_info(task.file_id, info.as_dict())      # type: ignore[arg-type]
    return True, info.as_dict(), ""


# ---------------------------------------------------------------- peaks

def h_peaks(task: store.TaskRow, ctx: Context) -> tuple[bool, dict, str]:
    p = _file_of(task)
    ctx.progress(10)
    buckets = int(task.params.get("buckets", 1000))
    # force 之前只挂在 GET /api/files/{fid}/peaks 上，任务处理器从不透传，
    # 于是卡片里配「强制重算」不会生效
    force = bool(task.params.get("force", False))
    data = audio.peaks(p, buckets=buckets, force=force)
    ctx.progress(95)
    return True, {
        "buckets": data["buckets"],
        "channels": data["channels"],
        "duration": data["duration"],
        "key": data["key"],
        "cached": True,
    }, ""


# ---------------------------------------------------------------- convert

def h_convert(task: store.TaskRow, ctx: Context) -> tuple[bool, dict, str]:
    src = _file_of(task)
    ffmpeg = toolchain.require("ffmpeg")

    target = str(task.params.get("format", "")).lower().lstrip(".")
    if target not in FORMAT_ARGS:
        return False, {}, f"不支持的目标格式: {target}"

    # 所有数值参数都做白名单校验，非法值直接拒绝
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]

    # 位深覆盖：只有无损容器有这个概念。卡片规格里用 onlyIf 限制了，
    # 但 /api/ops 可以被直接调用，所以这里必须自己再挡一次。
    depth = str(task.params.get("bitDepth") or "").strip()
    if depth:
        if depth not in BIT_DEPTHS:
            return False, {}, f"位深只能是 {list(BIT_DEPTHS)}，收到 {depth}"
        if target in _PCM_CODECS:
            args += ["-c:a", _PCM_CODECS[target][depth]]
        elif target == "flac":
            sf = _FLAC_SAMPLE_FMT.get(depth)
            if not sf:
                return False, {}, "FLAC 不支持浮点位深（32f），请选 16 / 24 / 32"
            args += ["-c:a", "flac", "-sample_fmt", sf]
        else:
            return False, {}, f"{target} 是无损之外的容器，不支持指定位深"
    else:
        args += FORMAT_ARGS[target]

    if target == "flac":
        lvl = task.params.get("compressionLevel")
        if lvl is not None:
            lvl = int(lvl)
            if lvl not in FLAC_LEVELS:
                return False, {}, f"FLAC 压缩等级必须在 0-8，收到 {lvl}"
            args += ["-compression_level", str(lvl)]

    br = task.params.get("bitrate")
    # 无损目标忽略码率：libFLAC/pcm 不支持 -b:a，传了会报错或产出意外结果
    if br and target not in LOSSLESS_FORMATS:
        if br not in BITRATES:
            return False, {}, f"码率不在白名单: {br}"
        args += ["-b:a", br]

    sr = task.params.get("sampleRate")
    if sr:
        sr = int(sr)
        if sr not in SAMPLE_RATES:
            return False, {}, f"采样率不在白名单: {sr}"
        args += ["-ar", str(sr)]

    ch = task.params.get("channels")
    if ch:
        ch = int(ch)
        if ch not in CHANNELS:
            return False, {}, f"声道数不在白名单: {ch}"
        args += ["-ac", str(ch)]

    # 保留元数据（需求 §4.2 / P1）
    if task.params.get("keepTags", True):
        args += ["-map_metadata", "0"]

    want_cover = task.params.get("keepCover", True)
    if want_cover and target in COVER_CAPABLE:
        args += ["-map", "0", "-c:v", "mjpeg"]
        if target in ("m4a", "mp4", "mov"):
            args += ["-disposition:v", "attached_pic"]
        elif target == "mp3":
            args += ["-id3v2_version", "3", "-disposition:v", "attached_pic"]
    else:
        # wav / aiff / aac 以及用户主动取消封面时，只映射音频
        args += ["-map", "0:a"]

    out = _out_path(src, f".{target}", task=task)
    args.append(str(out))

    ctx.progress(30)
    r = runner.run(args, timeout=config.TASK_TIMEOUT)
    if not r.ok:
        out.unlink(missing_ok=True)
        return False, {}, r.stderr_summary

    ctx.progress(100)
    return True, {
        **_artifact(out),
        "command": args,
    }, ""


# ---------------------------------------------------------------- tags

def h_tag_edit(task: store.TaskRow, ctx: Context) -> tuple[bool, dict, str]:
    p = _file_of(task)
    tags = task.params.get("tags") or {}
    clear = bool(task.params.get("clearMissing", False))
    ctx.progress(30)
    r = audio.write_tags(p, tags, clear_missing=clear)
    if not r.ok:
        return False, {}, r.error
    ctx.progress(100)
    after = audio.read_tags(p)
    # 改标签会**重写**文件，但音频没动 —— 响度测量值仍然有效，所以合并而不是覆盖。
    store.merge_file_info(task.file_id, audio.probe(p).as_dict())   # type: ignore[arg-type]
    return True, {
        "method": r.method,
        "written": r.written,
        "removed": r.removed,
        "reencoded": False,
        "tags": after,
    }, ""


# ---------------------------------------------------------------- cover

def h_cover_embed(task: store.TaskRow, ctx: Context) -> tuple[bool, dict, str]:
    p = _file_of(task)
    raw = task.params.get("imagePath") or ""
    if not raw:
        return False, {}, "缺少封面路径"
    # 封面也必须在受管目录内（禁止引用任意本地路径）
    img = config.safe_join(config.UPLOADS, raw)
    if not img.exists():
        return False, {}, f"封面不存在: {raw}"
    if not config.is_image(img):
        return False, {}, f"不是受支持的图片格式: {img.suffix}"

    ctx.progress(30)
    r = audio.embed_cover(p, img, picture_type=str(task.params.get("pictureType", "Front Cover")))
    if not r.ok:
        return False, {}, r.stderr_summary
    ctx.progress(100)
    # 嵌封面只动元数据/图片流，音频没动 → 响度测量值保留（合并，别整体覆盖）
    store.merge_file_info(task.file_id, audio.probe(p).as_dict())   # type: ignore[arg-type]
    # 文件变了，旧封面缓存必须作废，否则 /cover 还在回上一张图
    audio.drop_cover_cache(task.file_id or "")
    return True, {"cover": img.name, "command": r.args}, ""


def h_cover_extract(task: store.TaskRow, ctx: Context) -> tuple[bool, dict, str]:
    p = _file_of(task)
    out = _out_path(p, ".jpg", subdir="covers", task=task)
    ctx.progress(30)
    got = audio.extract_cover(p, out)
    if not got:
        return False, {}, "该文件没有内嵌封面"
    ctx.progress(100)
    return True, {
        "output": str(got.relative_to(config.ROOT)).replace("\\", "/"),
        "size": got.stat().st_size,
    }, ""


def h_cover_remove(task: store.TaskRow, ctx: Context) -> tuple[bool, dict, str]:
    p = _file_of(task)
    ctx.progress(40)
    r = audio.remove_cover(p)
    if not r.ok:
        return False, {}, r.stderr_summary
    ctx.progress(100)
    # 同理：删封面不动音频，响度测量值必须留住
    store.merge_file_info(task.file_id, audio.probe(p).as_dict())   # type: ignore[arg-type]
    audio.drop_cover_cache(task.file_id or "")
    return True, {"removed": True}, ""


# ---------------------------------------------------------------- normalize

def h_normalize(task: store.TaskRow, ctx: Context) -> tuple[bool, dict, str]:
    """响度标准化：ffmpeg loudnorm 两遍法。"""
    src = _file_of(task)
    ffmpeg = toolchain.require("ffmpeg")

    target = float(task.params.get("targetLufs", -16.0))
    if not (-40.0 <= target <= 0.0):
        return False, {}, f"目标响度超范围: {target}"
    tp = float(task.params.get("truePeak", -1.5))
    lra = float(task.params.get("lra", 11.0))

    # 第一遍：测量
    ctx.progress(15)
    r1 = runner.run([
        ffmpeg, "-hide_banner", "-loglevel", "info", "-i", str(src),
        "-af", f"loudnorm=I={target}:TP={tp}:LRA={lra}:print_format=json",
        "-f", "null", "-",
    ], timeout=config.TASK_TIMEOUT)
    measured = _parse_loudnorm(r1.stderr or "")
    ctx.progress(55)

    # 第二遍：用实测值做线性归一
    af = f"loudnorm=I={target}:TP={tp}:LRA={lra}"
    if measured:
        af += (f":measured_I={measured.get('input_i')}"
               f":measured_TP={measured.get('input_tp')}"
               f":measured_LRA={measured.get('input_lra')}"
               f":measured_thresh={measured.get('input_thresh')}"
               f":offset={measured.get('target_offset')}")
    af += ":linear=true"

    out = _out_path(src, f".norm{src.suffix}", task=task)
    norm_args = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
        "-af", af, "-map", "0:a",                    # 归一化只处理音频
    ]
    if src.suffix.lower() in (".m4a", ".mp4", ".mov", ".mp3"):
        # 源里可能带封面，照旧带着走，但要显式给封面编码器
        norm_args += ["-map", "0:v?", "-c:v", "mjpeg", "-disposition:v", "attached_pic"]
    norm_args.append(str(out))
    r2 = runner.run(norm_args, timeout=config.TASK_TIMEOUT)
    if not r2.ok:
        out.unlink(missing_ok=True)
        return False, {}, r2.stderr_summary

    ctx.progress(100)
    # 第一遍已经测出来了，顺手落库 —— 卡片上那行「Loudness」的数据来源。
    # `input_i` 就是**整段素材的积分响度**（LUFS，带门限），正是 UI 要显示的值。
    _store_measurements(task.file_id, {
        "loudness": measured.get("input_i"),
        "truePeak": measured.get("input_tp"),
        "loudnessRange": measured.get("input_lra"),
    })
    return True, {
        **_artifact(out),
        "targetLufs": target,
        "measured": measured,
    }, ""


def _store_measurements(fid: str | None, values: dict) -> None:
    """把**内容测量值**（响度一组）合并进文件记录，卡片上那行「Loudness」用它。

    只认 `store.MEASUREMENT_KEYS` 里的键（单一事实源：哪些字段是"必须解码才拿得到"
    的，由 store 那边定义，这里不抄第二份），缺项/取不到就跳过 —— 别写 `null` 进去，
    那会让前端分不清"没测过"和"测出来是空"。

    ⚠ 上游给的多半是**字符串**（ffmpeg 的 loudnorm JSON 里 `"input_i": "-19.30"`），
    所以要在这里转数字：`info.loudness` 一旦是字符串，前端 `Number()` 能兜住，
    但任何拼接/比较都会变成字符串语义。
    """
    if not fid:
        return
    patch: dict = {}
    for key in store.MEASUREMENT_KEYS:
        v = (values or {}).get(key)
        if v is None or v == "":
            continue
        try:
            patch[key] = round(float(v), 2)
        except (TypeError, ValueError):
            continue
    if patch:
        store.merge_file_info(fid, patch)


def _parse_loudnorm(stderr: str) -> dict:
    """从 loudnorm 的 JSON 摘要里取实测值。"""
    i = stderr.rfind("{")
    j = stderr.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        return json.loads(stderr[i:j + 1])
    except Exception:
        return {}


# ---------------------------------------------------------------- rename

def h_rename(task: store.TaskRow, ctx: Context) -> tuple[bool, dict, str]:
    """按标签重命名（在 uploads 内改 rel_path，不移动出受管目录）。"""
    f = store.get_file(task.file_id) if task.file_id else None
    if not f:
        return False, {}, "任务没有关联文件"
    src = f.path
    if not src.exists():
        return False, {}, "源文件不存在"

    tags = audio.read_tags(src)
    pattern = str(task.params.get("pattern") or "{tracknumber} - {title}")
    name = _render_pattern(pattern, tags, src)
    if not name:
        return False, {}, "模板渲染结果为空"

    safe = config.sanitize_name(name + src.suffix)
    if safe == src.name:
        return True, {"renamed": False, "name": safe, "reason": "名称未变"}, ""

    # 目标名要同时避开**磁盘上的文件**和**数据库里的行**。
    # `unique_path` 只看磁盘，而 files.rel_path 上有 UNIQUE 约束，
    # 且软删除的行仍然占着那个 rel_path —— 于是"删掉一个文件、再把另一个
    # 重命名成同名"会直接抛 IntegrityError: UNIQUE constraint failed。
    dst = _unique_dst(src.with_name(safe))
    ctx.progress(60)
    src.rename(dst)
    st = dst.stat()
    new_rel = str(dst.relative_to(config.UPLOADS.resolve())).replace("\\", "/")
    # 更新文件记录（保持同一 id，任务历史不断）
    try:
        store.rename_file(f.id, new_rel, dst.name, st.st_size, st.st_mtime)
    except Exception as e:                       # noqa: BLE001
        # 兜底：万一还是撞了，把文件改回去，别留下"磁盘改了库里没改"的错位
        try:
            dst.rename(src)
        except OSError:
            pass
        return False, {}, f"改名失败：{type(e).__name__}: {e}"
    ctx.progress(100)
    return True, {"renamed": True, "from": f.name, "name": dst.name}, ""


def _unique_dst(dst: Path) -> Path:
    """找一个磁盘与数据库都不冲突的目标路径。"""
    if not dst.exists() and not store.rel_path_exists(
            str(dst.relative_to(config.UPLOADS.resolve())).replace("\\", "/")):
        return dst
    stem, suf = dst.stem, dst.suffix
    for i in range(1, 10000):
        cand = dst.with_name(f"{stem}-{i}{suf}")
        rel = str(cand.relative_to(config.UPLOADS.resolve())).replace("\\", "/")
        if not cand.exists() and not store.rel_path_exists(rel):
            return cand
    raise RuntimeError(f"无法为 {dst.name} 找到可用文件名")

#: tag 名的别名表。ffprobe 吐出来的键名没有统一大小写，取决于容器：
#:   FLAC（Vorbis comment）  TRACKNUMBER / TITLE / ARTIST / ALBUM（大写）
#:   MP3（ID3v2）            TRCK / TIT2 ...
#:   M4A / MP4               trkn / ©nam ...
#: 精确匹配只命中其中一种，表现就是"那一行永远是 —"（TrackNumber 实测踩过）
#: 或"占位符原样留在文件名里"（`{tracknumber:02}`）。
#: 只列**同一含义的多种拼法**，不做跨字段猜测。
_TAG_ALIASES: dict[str, tuple[str, ...]] = {
    "track":       ("track", "tracknumber", "trck", "track_number"),
    "tracknumber": ("tracknumber", "track", "trck", "track_number"),
    "disc":        ("disc", "discnumber", "tpos", "disc_number"),
    "discnumber":  ("discnumber", "disc", "tpos", "disc_number"),
    "date":        ("date", "year"),
}


def _tag(tags: dict, *names: str) -> str:
    """按一组候选名**大小写不敏感**地取标签值，取不到返回空串。

    ⚠ 不能直接 `tags.get("track")` —— 见上面 `_TAG_ALIASES` 的注释。
    先做一次小写索引再查，避免每个调用点各写一遍 `.lower()`。
    """
    low = {str(k).lower(): v for k, v in (tags or {}).items()}
    for n in names:
        v = low.get(n.lower())
        if v:
            return str(v).strip()
    return ""


def _tag_for(tags: dict, key: str) -> str:
    """按**归一化后的短名**取标签（`_TAG_ALIASES` 里有别名就一起试）。"""
    return _tag(tags, key, *_TAG_ALIASES.get(key, ()))

def _render_pattern(pattern: str, tags: dict[str, str], src: Path) -> str:
    """支持 {title} {artist} {album} {tracknumber} {date} 等占位符。

    也支持零填充：`{tracknumber:02}` —— 标签里的音轨号常是 "1" 而不是 "01"，
    直接拼出来排序会乱（1, 10, 2, …）。原来这里只认 `{名字}`，
    带 `:02` 的写法不会被替换，会**原样留在文件名里**。
    """
    import re as _re

    def sub(m):
        key = m.group(1).strip().lower()
        width = m.group(2)
        if key in ("filename", "name"):
            v = src.stem
        else:
            v = _tag_for(tags, key)
        if width and v:
            try:
                n = int(width)
            except ValueError:
                return v
            # 只有纯数字才补零：对 "Live" 这种值 zfill 没有意义
            if v.isdigit():
                return v.zfill(min(n, 8))
        return v

    out = _re.sub(r"\{([a-zA-Z_]+)(?::(\d+))?\}", sub, pattern)
    # 取不到值的占位符会留空，于是模板里的分隔符会剩下残渣：
    #   "{artist} - {album} - {title}" 缺 album → "陈婧霏 - - 夜航西飞"
    #   "[{genre}] {artist}"           缺 genre  → "[] 陈婧霏"
    # 这两类清理是**可预测**的（只动空括号和连续分隔符），不做更聪明的猜测。
    out = _re.sub(r"[\[\(【「]\s*[\]\)】」]", "", out)
    out = _re.sub(r"(?:\s*-\s*){2,}", " - ", out)
    out = _re.sub(r"\s{2,}", " ", out).strip(" -_.")
    return out


# ---------------------------------------------------------------- loudness

def _size_text(n: int) -> str:
    for unit, div in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def _algorithm_text() -> str:
    """元数据卡「测量算法」那一格的值。

    卡只有 200pt 宽：标签「测量算法」14pt 占 ~56pt，`ebur128 / FFmpeg <版本>`
    即使降到 10pt 也要 ~121pt —— 放不下（实测会被截成 `ebur128 / FFmpeg 9.0…`）。
    所以取短写法 `ebur128 <版本>`，版本号运行时从工具链读。
    """
    import re
    try:
        raw = toolchain.get("ffmpeg").version or ""
    except Exception:                                          # noqa: BLE001
        raw = ""
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", raw)
    return f"ebur128 {m.group(1)}" if m else "ebur128"


def _cover_data_uri(p: Path, task: store.TaskRow,
                    box: float = 100.0) -> str | None:
    """把内嵌封面读出来、缩到合适尺寸、编成 **data URI**（直接嵌进 SVG）。

    老板 2026-10："封面要嵌入。"

    三个要点：

    1. **必须缩小**。原图常见 1000–1500px，直接 base64 进 SVG 会让文件涨到几百 KB；
       元数据卡里那个框是 `layout.COVER.w`（100pt），按 2× 取 200px 足够清晰。
    2. 缩完统一转 **JPEG**（q=85）：PNG 封面转 JPEG 后体积通常小一个量级，而
       100pt 的显示尺寸看不出差别。
    3. **失败就当没有封面**（返回 `None`，渲染器画灰底占位）。提取要走 ffmpeg，
       一个没有封面的文件不该让整张图失败。
    """
    import base64                                               # noqa: PLC0415
    import io as _io                                            # noqa: PLC0415

    # ⚠ 扩展名必须是 ffmpeg 认识的图片格式：`extract_cover` 把路径直接交给 ffmpeg，
    # 由**扩展名**决定 muxer —— 写成 `.img` 会得到 "Unable to find a suitable output
    # format"，然后被下面的 `except` 吞掉，表现成"这个文件没有封面"。实测踩过。
    tmp = config.CACHE / f"loud-cover-{task.id}.jpg"
    try:
        if audio.extract_cover(p, tmp) is None:
            return None
        from PIL import Image                                    # noqa: PLC0415
        with Image.open(tmp) as im:
            im = im.convert("RGB")
            side = max(64, int(round(box * 2)))                  # 2× 显示尺寸
            im.thumbnail((side, side))
            buf = _io.BytesIO()
            im.save(buf, format="JPEG", quality=85, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:                                       # noqa: BLE001
        # 不静默：封面嵌入失败要说出来，否则会被当成"这个文件本来就没封面"
        logs.log(f"⚠ 封面嵌入失败（图上会画灰底占位）：{type(e).__name__}: {e}")
        return None
    finally:
        tmp.unlink(missing_ok=True)


def _render_loudness_page(task: store.TaskRow, p: Path, data: dict) -> str:
    """组装一份响度时间线的**整页 SVG**（元数据 + 章节标记 + 内嵌封面）。

    上色用的主题来自任务参数 `theme` / `themeMode` —— 那是**客户端在执行链那一刻**
    读的 `data-theme` / `data-mode`（见 `app.js` 的 `currentTheme()`）。
    没传就落到 t1 浅色，**确定性**：同样的输入永远出同样的图。
    """
    from backend import loudness_svg                            # noqa: PLC0415
    from backend import theme as theme_mod                      # noqa: PLC0415

    info = audio.probe(p)
    tags = info.tags or {}
    meta = {
        "title":  _tag_for(tags, "title"),
        "artist": _tag_for(tags, "artist"),
        "album":  _tag_for(tags, "album"),
        "track":  _tag_for(tags, "track"),
        "duration": info.duration,
        "channels": info.channels,
        "sampleRate": info.sample_rate,
        "bits": info.bits,
        "sizeText": _size_text(info.size),
        "algorithm": _algorithm_text(),
    }
    # 标记：音频自带的章节，有则加载、无则不出标记行（`chapters()` 自己吞异常）
    markers = audio.chapters(p)
    # DRP 的每一次出现（还没接线时 `summary` 里没有，就是空列表 -> 不出 PT 行）
    s = data.get("summary") or {}
    patterns = s.get("drpOccurrences") or []
    # 认不出的主题值**不报错**，落回默认 —— 一张图不值得让整条链失败
    th, md = theme_mod.normalize(task.params.get("theme"), task.params.get("themeMode"))
    return loudness_svg.render_loudness_svg(
        data,
        title=p.name,
        meta=meta,
        markers=markers,
        patterns=patterns,
        cover=_cover_data_uri(p, task) if info.has_cover else None,
        ref_lufs=float(task.params.get("refLufs", -23.0)),
        width=(float(task.params["width"]) if task.params.get("width") else None),
        theme=th,
        mode=md,
    )


def h_loudness(task: store.TaskRow, ctx: Context) -> tuple[bool, dict, str]:
    """响度分析：ebur128 单趟拿时间线 + 8 项指标，按文件缓存。

    **只读**：不改 `uploads/` 里的文件（contract.py 里 mode=read）。
    **三个 op 共用这一个 handler**，靠任务里带的 `_op` 分流
    （路由塞进去的，见 `routers/ops.py`）：

      · `loudness`         —— 只算 + 缓存，前端聚焦时画 Canvas
      · `loudness-image`   —— 额外渲染一张 **SVG** 落 `outputs/loudness/`
      · `loudness-report`  —— 额外写一份 **Markdown 报告** 落 `outputs/loudness/`

    **图是 SVG，不是 PNG**：版式唯一来源 `backend/chart_layout.py`（从 `大致布局.ai`
    实测），纵轴唯一来源 `backend/chart_axis.py`（F 轴：上界 +0.3 / 拐点 −30 /
    上段线性 70%，9 个刻度全出文字，红区 −3）。渲染器 `backend/loudness_svg.py`。

    三个 op 都**只读源文件**，产物全部落 `outputs/`。
    """
    p = _file_of(task)
    force = bool(task.params.get("force", False))
    ctx.progress(10)
    try:
        data = audio.loudness_timeline(p, force=force, tag=task.id)
    except RuntimeError as e:
        return False, {}, str(e)
    ctx.progress(75)

    # 顺手把测量值落库：卡片上那行「Loudness」以前只在**跑过标准化**之后才有
    # （而且 `h_normalize` 当时根本没在存），现在只要跑过任意一个响度 op 就有。
    # 三个 op 共用这一段，所以在分流**之前**写，一次就够。
    s = data.get("summary") or {}
    _store_measurements(task.file_id, {
        "loudness": s.get("integrated"),
        "truePeak": s.get("truePeakMax"),
        "loudnessRange": s.get("lra"),
        "samplePeak": s.get("samplePeakMax"),
        "dra": s.get("dra"),
        "drp": s.get("drp"),
    })

    out: dict = {
        "key": data.get("key"),
        "duration": data.get("duration"),
        "frames": data.get("frames", 0),
        "hz": data.get("hz", 10),
        "cached": data.get("cached", False),
        "summary": data.get("summary", {}),
    }

    if task.params.get("_op") == "loudness-report":
        try:
            md = audio.render_loudness_markdown(
                [{"name": p.name, "data": data}],
                detail=str(task.params.get("detail") or "summary"),
                time_points=int(task.params.get("timePoints", 5)),
                frame_table=bool(task.params.get("frameTable", False)),
                ref_lufs=float(task.params.get("refLufs", -23.0)),
            )
        except Exception as e:                       # noqa: BLE001
            return False, {}, f"渲染报告失败：{type(e).__name__}: {e}"
        dest = _out_path(p, ".loudness.md", subdir="loudness", task=task)
        dest.write_text(md, encoding="utf-8")
        ctx.progress(100)
        out.update({
            **_artifact(dest),
            "kind": "markdown",
            "lines": md.count("\n") + 1,
        })
        return True, out, ""

    if task.params.get("_op") == "loudness-image":
        # 把时间线画成 **SVG** 整页（版式见 backend/chart_layout.py，纵轴见
        # backend/chart_axis.py，渲染器见 backend/loudness_svg.py）。
        #
        # 为什么不是 PNG：① 中文不会退化成 `?`（PNG 那条路找不到系统 CJK 字体就用
        # `?` 顶替，而标记名实测可能是 `标记 0` 这种中文）；② 颜色按用户执行链那一刻的
        # 主题**算成实色烘进文件**（2026-10 改的，见 `_render_loudness_page`）；
        # ③ 矢量，任意缩放；④ 是文本，测试能直接断言坐标与颜色。
        #
        # ⚠ 每次都重写这个文件（没有"已存在就跳过"）—— 换主题再跑一次链必须出新的颜色。
        # 缓存的是**时间线**（与主题无关），不是这张图。
        svg = _render_loudness_page(task, p, data)
        dest = _out_path(p, ".loudness.svg", subdir="loudness", task=task)
        dest.write_text(svg, encoding="utf-8")
        ctx.progress(100)
        out.update({
            **_artifact(dest),
            "kind": "image",
            "format": "svg",
        })
        return True, out, ""

    ctx.progress(90)
    return True, out, ""


# ---------------------------------------------------------------- 波形图

def h_waveform(task: store.TaskRow, ctx: Context) -> tuple[bool, dict, str]:
    """把整首歌的波形渲染成 PNG。

    默认「白色波形 + 透明底」：showwavespic 本身就画在透明画布上，
    不指定背景时 alpha=0，直接就能当素材叠到任何底图上。
    """
    src = _file_of(task)
    ffmpeg = toolchain.require("ffmpeg")
    p = task.params

    try:
        width = int(p.get("width", 1920))
        height = int(p.get("height", 400))
    except (TypeError, ValueError):
        return False, {}, "宽高必须是整数"
    width = max(WAVE_MIN_W, min(WAVE_MAX_W, width))
    height = max(WAVE_MIN_H, min(WAVE_MAX_H, height))

    color = _hex_color(p.get("color"), "0xFFFFFF")
    scale = str(p.get("scale", "lin")).lower()
    if scale not in WAVE_SCALES:
        return False, {}, f"刻度不在白名单: {scale}"
    bg_key = str(p.get("background", "transparent")).lower()
    if bg_key not in WAVE_BACKGROUNDS:
        return False, {}, f"背景不在白名单: {bg_key}"
    split = bool(p.get("splitChannels", False))

    filt = f"showwavespic=s={width}x{height}:colors={color}"
    if split:
        filt += ":split_channels=1"
    if scale != "lin":
        filt += f":scale={scale}"
    filt += "," + _WAVE_SNAP

    out = _out_path(src, ".png", subdir="waveforms", task=task)
    bg = WAVE_BACKGROUNDS[bg_key]

    ctx.progress(25)
    if bg is None:
        # 透明底：直接出 rgba
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(src), "-filter_complex", filt,
                "-frames:v", "1", "-pix_fmt", "rgba", str(out)]
    else:
        # 实底：再生成一张纯色画布叠上去，波形压在下面会看不见，所以波形在上
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(src),
                "-f", "lavfi", "-i", f"color=c={bg}:s={width}x{height}",
                "-filter_complex", f"[0:a]{filt}[w];[1:v][w]overlay=format=auto:shortest=1",
                "-frames:v", "1", "-pix_fmt", "rgb24", str(out)]

    r = runner.run(args, timeout=config.TASK_TIMEOUT)
    if not r.ok:
        out.unlink(missing_ok=True)
        return False, {}, r.stderr_summary

    ctx.progress(100)
    if not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        return False, {}, "没有生成任何图像"

    return True, {
        **_artifact(out),
        "width": width,
        "height": height,
        "color": color.replace("0x", "#"),
        "background": bg_key,
        "splitChannels": split,
        "scale": scale,
        "command": args,
    }, ""


# ---------------------------------------------------------------- verify

def h_verify(task: store.TaskRow, ctx: Context) -> tuple[bool, dict, str]:
    p = _file_of(task)
    ctx.progress(30)
    if p.suffix.lower() != ".flac":
        return False, {}, "仅支持 FLAC 完整性校验"
    r = audio.verify_flac(p)
    ctx.progress(100)
    if not r.ok:
        return False, {"stderr": r.stderr_summary}, r.stderr_summary
    return True, {"verified": True, "duration": round(r.duration, 2)}, ""


# ---------------------------------------------------------------- zip

def h_zip(task: store.TaskRow, ctx: Context) -> tuple[bool, dict, str]:
    """打包一批文件为 ZIP（需求 §4.6 + `执行链打包与串行交接方案.md` §4）。

    **装什么 = 打包窗口里的终产物**。窗口是
    「上一个打包步骤（不含）」→「本次打包步骤（不含）」，两种档位规则不同：

      · **串行**：每个文件取窗口内**最后一个音频产物**（`转 FLAC → 标准化 → 打包`
        装的是标准化后的那份），外加窗口内**全部旁路产物**（波形/响度图/报告/封面）
      · **并行**：窗口内**全部**非 zip 产物（`转 FLAC → 转 MP3 → 打包` 两个都装）
      · 该文件在窗口内没有任何产物、也没有失败 → 装**源文件**
        （`探测 → 打包`、`打包` 单独一张都是这条）
      · 窗口内有失败 → 串行整份跳过（§9.7 **不回落**）；并行装成功的那部分并在
        `warnings` 里点名少装了什么（并行各步互不依赖，"装能装的 + 说清"更有用）

    产物落 `outputs/<本次执行的目录>/`，名字是
    `upload-<日期>-<来源卡片名…>.zip`（§4.5 / §5）。

    `result` 里回四样东西（**ZIP 里装了什么必须能查**）：
      · `sources`  —— 每个成员来自哪一步（`step2:convert` / `source`）
      · `skipped`  —— 哪些文件**没进**以及为什么
      · `warnings` —— 进了但**少装了东西**的（并行档的部分失败）
      · `window`   —— 这次收集的是哪一段（`{from, to}`，排查"为什么没装某个产物"）
    """
    ids = task.params.get("fileIds") or []
    if not ids:
        return False, {}, "没有要打包的文件"

    plans, skipped, warnings, step_names, window = _zip_plan(task, ids)
    if not plans:
        return False, {}, ("没有任何文件可以打包："
                           + "；".join(f"{n}（{w}）" for n, w in skipped) if skipped
                           else "没有可打包的文件")
    for w in warnings:
        logs.warn(f"⚠ 打包：{w}")

    out = _zip_out_path(task, step_names)
    sources: list[dict] = []
    names_taken: set[str] = set()
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for i, item in enumerate(plans):
            fid, path, from_, display, step = item
            if not path.exists():
                skipped.append((display, "产物已不在磁盘上"))
                continue
            # arcname 用**可读的文件名**：源文件保持它在 uploads 里的相对路径
            # （保留子目录），产物用文件名。直接用 rel_path 的话产物会变成
            # "song.norm.flac" 这种没有目录上下文的名字，而源文件带子目录 ——
            # 混在一起解压出来分不清谁是谁。
            #
            # ⚠ 同一个包里的成员名**必须唯一**（zip 允许重名，但解压时会互相覆盖）：
            # 并行档一个文件可能同时装了 `song.flac` 与另一步产出的同名文件。
            arc = _unique_arcname(z, display, names_taken)
            z.write(path, arcname=arc)
            sources.append({"fileId": fid, "arcname": arc, "from": from_,
                            "step": step, "bytes": path.stat().st_size})
            n += 1
            ctx.progress(10 + 85 * (i + 1) / len(plans))
    ctx.progress(100)
    return True, {
        **_artifact(out),
        "files": n,
        "sources": sources,
        "skipped": [{"name": nm, "reason": rs} for nm, rs in skipped],
        "warnings": warnings,
        "window": window,
    }, ""


def _unique_arcname(z: zipfile.ZipFile, name: str, taken: set[str]) -> str:
    """包内成员名去重（`song.flac` → `song-1.flac`）。只影响 zip 内部。"""
    if name not in taken and name not in z.namelist():
        taken.add(name)
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    for i in range(1, 10000):
        cand = f"{stem}-{i}.{ext}" if ext else f"{name}-{i}"
        if cand not in taken and cand not in z.namelist():
            taken.add(cand)
            return cand
    taken.add(name)
    return name


def _zip_out_path(task: store.TaskRow, step_names: list[str]) -> Path:
    """ZIP 的落点。

    链上 → **本次执行的产物目录**（`outputs/upload-<日期>-<chain 末6>/`，§5）；
    非链（右键菜单/单卡）→ 保持旧的 `outputs/zips/`（存量行为不变）。
    """
    if task.chain_id:
        started = store.chain_started_at(task.chain_id) or time.time()
        base = config.OUTPUTS / config.run_dir_name(task.chain_id, started)
        date = config.run_date(started)
    else:
        base = config.OUTPUTS / "zips"
        date = config.run_date(time.time())
    base.mkdir(parents=True, exist_ok=True)
    return config.unique_path(base / config.zip_filename(date, step_names))


def _zip_plan(task: store.TaskRow, ids: list[str]) -> tuple[list, list, list, list, dict]:
    """按**打包窗口**解算每个文件要装的东西。

    → `(plans, skipped, warnings, step_names, window)`
      · `plans` 每项 `(file_id, 路径, 来源说明, 包内名, 步骤号)`（源文件步骤号 = -1）
      · `step_names` 被装入产物的那些步骤的**卡片名**（给 ZIP 命名，§4.5）

    这是 §9.7 那条"不回落"的落点，也是最容易写错的一处：
    **一旦链上断过，就不能再往前找、也不能回落到源文件** ——
    那等于静默交出一个用户没要的文件。
    """
    plans: list[tuple[str, Path, str, str, int]] = []
    skipped: list[tuple[str, str]] = []
    warnings: list[str] = []
    chain_id = task.chain_id

    if not chain_id:
        # 不在链上（右键菜单/单卡执行）→ 旧行为：打包源文件
        for fid in ids:
            f = store.get_file(fid)
            if not f or f.state == "deleted":
                skipped.append((fid[:8], "文件不存在或已删除"))
                continue
            if not f.path.exists():
                skipped.append((f.name, "工作副本已不在磁盘上"))
                continue
            plans.append((fid, f.path, "source", f.rel_path, -1))
        return plans, skipped, warnings, ["源文件"], {"from": 0, "to": 0}

    # ⚠ **在真正开打之前重新解算一次**，不要用传进来的 `task` 快照：
    # 屏障那边的等待是有时限的（`ATTACH_GRACE`），过了时限就放行，
    # 而那一刻上游可能刚刚成功、产物回填还在飞。
    fresh = store.get_task(task.id) or task
    step_idx = int(fresh.step_idx if fresh.step_idx is not None else 0)
    win = store.chain_zip_window(chain_id, step_idx)
    serial = (fresh.chain_mode == "serial")
    states = {(t["file_id"], t["step_idx"]): t for t in win["tasks"]}
    used_steps: set[int] = set()

    for fid in ids:
        f = store.get_file(fid)
        if not f or f.state == "deleted":
            skipped.append((fid[:8], "文件不存在或已删除"))
            continue
        if not f.path.exists():
            # 记录还在、磁盘上的工作副本没了（被外部删掉 / 启动清空了 uploads）。
            # 必须在这里说清楚：否则会一路走到"产物解算不出来"，报一句
            # 看不出因果的"链上没有可用的产物"。
            skipped.append((f.name, "工作副本已不在磁盘上"))
            continue

        if serial:
            # 沿**派生血缘**往前走：源文件 → 产物行 → 它的产物 ……（一次遍历搞定，
            # 因为 `win["artifacts"]` 已按 step 排序）。边走边收集：
            # 最后一个音频产物 = 文件走到窗口末尾时的形态；沿途的旁路产物全收。
            lineage = {fid}
            picked: list[dict] = []
            last_audio: dict | None = None
            for a in win["artifacts"]:
                if (a["producing_file_id"] or "") not in lineage:
                    continue
                if a["produce"] == "derived":
                    last_audio = a          # 后出现的取代先出现的
                else:
                    lineage.add(a["row_id"])
                    picked.append(a)
            if last_audio is not None:
                lineage.add(last_audio["row_id"])
                picked.insert(0, last_audio)

            bad = [t for t in win["tasks"]
                   if t["file_id"] in lineage
                   and t["state"] in ("failed", "cancelled", "skipped")]
            if bad:
                # 串行：断过就整份跳过（不回落、也不装更早的产物）
                b = sorted(bad, key=lambda t: t["step_idx"])[0]
                skipped.append((f.name, f"第 {b['step_idx'] + 1} 步"
                                        f"（{b['type']}）{_state_cn(b['state'])}"))
                continue
            if not picked:
                plans.append((fid, f.path, "source", f.rel_path, -1))
                continue
            for a in picked:
                path = config.OUTPUTS / str(a["rel_path"]).replace("\\", "/")
                if not path.exists():
                    skipped.append((f.name, f"第 {a['step'] + 1} 步的产物已不在磁盘上"))
                    continue
                used_steps.add(a["step"])
                plans.append((fid, path, f"step{a['step'] + 1}:{a['type']}",
                              a["name"], a["step"]))
            continue

        # 并行：窗口内**全部**非 zip 产物（各步都作用于同一个源文件，没有血缘）
        mine = [a for a in win["artifacts"] if (a["producing_file_id"] or "") == fid]
        bad = [t for t in win["tasks"]
               if t["file_id"] == fid
               and t["state"] in ("failed", "cancelled", "skipped")]
        if not mine and bad:
            b = sorted(bad, key=lambda t: t["step_idx"])[0]
            skipped.append((f.name, f"第 {b['step_idx'] + 1} 步"
                                    f"（{b['type']}）{_state_cn(b['state'])}"))
            continue
        if not mine:
            plans.append((fid, f.path, "source", f.rel_path, -1))
            continue
        for a in mine:
            path = config.OUTPUTS / str(a["rel_path"]).replace("\\", "/")
            if not path.exists():
                skipped.append((f.name, f"第 {a['step'] + 1} 步的产物已不在磁盘上"))
                continue
            used_steps.add(a["step"])
            plans.append((fid, path, f"step{a['step'] + 1}:{a['type']}",
                          a["name"], a["step"]))
        if bad:
            # 并行：**装成功的 + 点名少装了什么**（与串行的"整份跳过"有意不同）
            b = sorted(bad, key=lambda t: t["step_idx"])[0]
            warnings.append(
                f"{f.name}：第 {b['step_idx'] + 1} 步（{b['type']}）"
                f"{_state_cn(b['state'])}，只装了这个文件成功的 {len(mine)} 个产物")

    # 命名：只用**真的装了东西**的那些步骤名（按链上顺序）
    step_names = [win["steps"][s]["name"] for s in sorted(used_steps)
                  if s in win["steps"]] or ["源文件"]
    return plans, skipped, warnings, step_names, {"from": win["from"], "to": win["to"]}


def _state_cn(state: str) -> str:
    return {"failed": "失败", "cancelled": "已取消", "skipped": "被跳过",
            "pending": "还没跑", "running": "还在跑"}.get(state, state)


# ---------------------------------------------------------------- 注册

def register_all(q) -> None:
    q.register("probe", h_probe)
    q.register("peaks", h_peaks)
    q.register("convert", h_convert)
    q.register("tag_edit", h_tag_edit)
    q.register("cover_embed", h_cover_embed)
    q.register("cover_extract", h_cover_extract)
    # h_cover_remove 之前漏注册，导致「删除封面」卡片和右键菜单都报
    # "没有 cover_remove 的处理器"。store.TASK_TYPES 里一直是有它的。
    q.register("cover_remove", h_cover_remove)
    q.register("normalize", h_normalize)
    q.register("rename", h_rename)
    q.register("waveform", h_waveform)
    q.register("verify", h_verify)
    q.register("zip", h_zip)
    # 响度：两个 op（`loudness` / `loudness-image`）共用同一个 handler，
    # 卡片的 op 名与任务类型是两套命名（路由用连字符、任务用下划线）
    q.register("loudness", h_loudness)
