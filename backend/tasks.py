"""任务处理器：把各类操作接进队列。

每个处理器签名统一为 (task, ctx) -> (ok, result, error)，
并且都在 worker 线程里跑，不阻塞 WebUI。
"""
from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path

from backend import audio, config, formats, runner, store
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
    return p


def _out_path(src: Path, suffix: str, subdir: str = "") -> Path:
    """输出路径【永远由系统生成】，落在 outputs/ 下，不覆盖已有文件。"""
    base = config.OUTPUTS / subdir if subdir else config.OUTPUTS
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
    store.set_file_info(task.file_id, info.as_dict())        # type: ignore[arg-type]
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

    out = _out_path(src, f".{target}")
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
    store.set_file_info(task.file_id, audio.probe(p).as_dict())   # type: ignore[arg-type]
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
    store.set_file_info(task.file_id, audio.probe(p).as_dict())   # type: ignore[arg-type]
    # 文件变了，旧封面缓存必须作废，否则 /cover 还在回上一张图
    audio.drop_cover_cache(task.file_id or "")
    return True, {"cover": img.name, "command": r.args}, ""


def h_cover_extract(task: store.TaskRow, ctx: Context) -> tuple[bool, dict, str]:
    p = _file_of(task)
    out = _out_path(p, ".jpg", subdir="covers")
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
    store.set_file_info(task.file_id, audio.probe(p).as_dict())   # type: ignore[arg-type]
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

    out = _out_path(src, f".norm{src.suffix}")
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
    return True, {
        **_artifact(out),
        "targetLufs": target,
        "measured": measured,
    }, ""


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
            v = tags.get(key, "") or ""
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

def h_loudness(task: store.TaskRow, ctx: Context) -> tuple[bool, dict, str]:
    """响度分析：ebur128 单趟拿时间线 + 8 项指标，按文件缓存。

    **只读**：不改 `uploads/` 里的文件（contract.py 里 mode=read）。
    **三个 op 共用这一个 handler**，靠任务里带的 `_op` 分流
    （路由塞进去的，见 `routers/ops.py`）：

      · `loudness`         —— 只算 + 缓存，前端聚焦时画 Canvas
      · `loudness-image`   —— 额外渲染一张 **PNG** 落 `outputs/loudness/`
      · `loudness-report`  —— 额外写一份 **Markdown 报告** 落 `outputs/loudness/`

    **PNG 是纯 PIL 画的**（`audio.render_loudness_png`），不是"画成 rawvideo
    再喂 ffmpeg"那条路 —— 后者要么自己写字体位图、要么依赖 `drawtext`
    找系统中文字体，两者都比"PIL + DejaVu"脆得多。纵轴走 `AXIS_Y`
    分段控制点（非线性，有效响度区被放大），规格见
    `响度总览图（LoudnessAnalysis）实现构想.md` §3。

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
        dest = _out_path(p, ".loudness.md", subdir="loudness")
        dest.write_text(md, encoding="utf-8")
        ctx.progress(100)
        out.update({
            **_artifact(dest),
            "kind": "markdown",
            "lines": md.count("\n") + 1,
        })
        return True, out, ""

    if task.params.get("_op") == "loudness-image":
        # 把时间线画成 PNG（路线 B）。规格见 响度总览图实现构想.md §3。
        try:
            png = audio.render_loudness_png(
                data,
                title=p.stem,
                ref_lufs=float(task.params.get("refLufs", -23.0)),
                high_lufs=(float(task.params["highLufs"])
                           if task.params.get("highLufs") not in (None, "")
                           else None),
                width=int(task.params.get("width") or audio.PNG_W),
            )
        except Exception as e:                       # noqa: BLE001
            return False, {}, f"渲染响度图失败：{type(e).__name__}: {e}"
        dest = _out_path(p, ".loudness.png", subdir="loudness")
        dest.write_bytes(png)
        ctx.progress(100)
        out.update({
            **_artifact(dest),
            "kind": "image",
            "width": int(task.params.get("width") or audio.PNG_W),
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

    out = _out_path(src, ".png", subdir="waveforms")
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
    """打包一批文件为 ZIP（需求 §4.6）。file_ids 放在 params 里。

    **装什么**（方案 §3.2.3）：对每个文件，沿链往前找"最后一个产出过文件的那一步"：
      · 那一步成功且产出可打包的东西 → 装**它的产物**
      · 那一步失败/取消/跳过 → **这个文件不进 ZIP**（§9.7：绝不回落，更不回落到源文件）
      · 前面没有任何产出（`打包` 自己是第一项，或前面只有 probe/校验这类）→ 装源文件

    所以 `转 FLAC → 标准化 → 打包` 拿到的是成品，而 `打包` 单独一张仍然打包源文件。

    `result` 里回两个东西（**ZIP 里装了什么必须能查**）：
      · `sources` —— 每个成员来自哪一步（`step2:convert` / `source`）
      · `skipped` —— 哪些文件**没进**以及为什么
    """
    ids = task.params.get("fileIds") or []
    if not ids:
        return False, {}, "没有要打包的文件"

    # 链上解算：拿到"要装的路径 + 来源说明"，或"这个文件不装 + 原因"
    plans, skipped = _zip_plan(task, ids)
    if not plans:
        return False, {}, ("没有任何文件可以打包："
                           + "；".join(f"{n}（{w}）" for n, w in skipped) if skipped
                           else "没有可打包的文件")

    out = _out_path(Path("batch"), ".zip", subdir="zips")
    sources: list[dict] = []
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for i, (fid, path, from_, display) in enumerate(plans):
            if not path.exists():
                skipped.append((display, "产物已不在磁盘上"))
                continue
            # arcname 用**可读的文件名**：源文件保持它在 uploads 里的相对路径，
            # 产物用它的文件名。直接用 rel_path 的话产物会变成
            # "song.norm.flac" 这种没有目录上下文的名字，而源文件带子目录 ——
            # 混在一起解压出来分不清谁是谁。
            arc = display
            z.write(path, arcname=arc)
            sources.append({"fileId": fid, "arcname": arc, "from": from_,
                            "bytes": path.stat().st_size})
            n += 1
            ctx.progress(10 + 85 * (i + 1) / len(plans))
    ctx.progress(100)
    return True, {
        **_artifact(out),
        "files": n,
        "sources": sources,
        # 没进 ZIP 的文件**必须列出来**，否则用户拿到一个"看起来成功"的包，
        # 永远不知道里面少了一个
        "skipped": [{"name": nm, "reason": rs} for nm, rs in skipped],
    }, ""


def _zip_plan(task: store.TaskRow, ids: list[str]) -> tuple[list, list]:
    """为 ZIP 解算每个文件要装的路径。→ `(plans, skipped)`。

    这是 §9.7 那条"不回落"的落点，也是最容易写错的一处：
    **一旦链上断过，就不能再往前找、也不能回落到源文件** ——
    那等于静默交出一个用户没要的文件。
    """
    plans: list[tuple[str, Path, str, str]] = []
    skipped: list[tuple[str, str]] = []
    chain_id = task.chain_id
    step_idx = task.step_idx if task.step_idx is not None else 0

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
        if not chain_id:
            # 不在链上（右键菜单/单卡执行）→ 旧行为：打包源文件
            plans.append((fid, f.path, "source", f.rel_path))
            continue

        # ⚠ **在真正开打之前重新解算一次**，不要用传进来的 `task` 快照。
        #
        # 为什么：汇总任务被 worker 取走时（`_run_one` 开头 `store.get_task`），
        # 上游可能**刚刚**成功、它的产物回填还在飞；屏障那边的等待是有时限的
        # （`ATTACH_GRACE`），过了时限就放行。于是 `task.params` 里没有产物信息，
        # ZIP 就装走了源文件 —— 表现是**偶发**。
        fresh = store.get_task(task.id) or task
        step_idx = fresh.step_idx if fresh.step_idx is not None else step_idx

        # 先看"链上有没有派生产物" —— 这是一次确定性的 JOIN（`files.derived_from`
        # → 任务 → chain_id/step_idx），不依赖任何"谁先跑完"的时序。
        # 它比沿任务行推导可靠得多（那条路曾是"偶发装源文件"的根源）。
        art = store.chain_derived_artifact(chain_id, fid, step_idx)
        if art:
            src = config.OUTPUTS / str(art["relPath"]).replace("\\", "/")
            if src.exists():
                plans.append((fid, src, f"step{art['step']}:{art['type']}", src.name))
                continue

        anchor = store.chain_started_at(chain_id)
        got = store.chain_final_artifact(chain_id, fid, step_idx, before_ts=anchor)
        if got is None:
            # 链上断过 → 这个文件没有可交付的产物。**不回落**（§9.7），
            # 但要说清是哪一步断的 —— 用户才知道该去修哪里。
            rows = store.tasks_before(chain_id, step_idx, fid, before_ts=anchor)
            last = rows[0] if rows else None
            if last is not None:
                skipped.append((f.name,
                                f"第 {int(last.step_idx or 0) + 1} 步"
                                f"（{last.type}）{_state_cn(last.state)}"))
            else:
                skipped.append((f.name, "链上没有可用的产物"))
            continue
        path, from_ = got
        # arcname：源文件用它在 uploads 里的相对路径（保留子目录），
        # 产物用文件名 —— 混在一起解压出来才分得清
        arc = f.rel_path if from_ == "source" else path.name
        plans.append((fid, path, from_, arc))
    return plans, skipped


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
