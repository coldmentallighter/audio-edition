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

from backend import audio, config, runner, store
from backend.queue import Context
from backend.toolchain import toolchain

# ---------------------------------------------------------------- 工具

# 目标格式 → ffmpeg 编码参数白名单（需求 §4.8：参数必须来自白名单）
FORMAT_ARGS: dict[str, list[str]] = {
    "flac": ["-c:a", "flac"],
    "wav":  ["-c:a", "pcm_s24le"],
    "mp3":  ["-c:a", "libmp3lame"],
    "aac":  ["-c:a", "aac"],
    "m4a":  ["-c:a", "aac"],
    "ogg":  ["-c:a", "libvorbis"],
    "opus": ["-c:a", "libopus"],
    "aiff": ["-c:a", "pcm_s16be"],
    "wma":  ["-c:a", "wmav2"],
}

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
# "Codec flac does not support bitrate" 或产出意外结果
LOSSLESS_FORMATS = {"flac", "wav", "aiff"}
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
        "output": str(out.relative_to(config.ROOT)).replace("\\", "/"),
        "name": out.name,
        "size": out.stat().st_size,
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
        "output": str(out.relative_to(config.ROOT)).replace("\\", "/"),
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
        "output": str(out.relative_to(config.ROOT)).replace("\\", "/"),
        "name": out.name,
        "size": out.stat().st_size,
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
    """打包一批文件为 ZIP（需求 §4.6）。file_ids 放在 params 里。"""
    ids = task.params.get("fileIds") or []
    if not ids:
        return False, {}, "没有要打包的文件"
    out = _out_path(Path("batch"), ".zip", subdir="zips")
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for i, fid in enumerate(ids):
            f = store.get_file(fid)
            if not f or not f.path.exists():
                continue
            z.write(f.path, arcname=f.rel_path)
            n += 1
            ctx.progress(10 + 85 * (i + 1) / len(ids))
    ctx.progress(100)
    return True, {
        "output": str(out.relative_to(config.ROOT)).replace("\\", "/"),
        "files": n,
        "size": out.stat().st_size,
    }, ""


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
