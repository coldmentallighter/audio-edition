"""音频操作：探测信息、读写标签、封面增删、峰值数据。

原则（需求 §4.1）：
  · 改元数据【不重新编码】—— FLAC 走 metaflac，其他格式走 mutagen
  · 探测统一走 ffprobe -of json
  · 峰值走 ffmpeg 解码 + astats，按文件哈希缓存
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from backend import config, runner
from backend.toolchain import toolchain

# ---------------------------------------------------------------- 探测

PROBE_ENTRIES = (
    "format=format_name,duration,size,bit_rate,tags:"
    "stream=index,codec_name,codec_type,sample_rate,channels,channel_layout,"
    "bits_per_raw_sample,bit_rate,duration"
)


@dataclass
class ProbeInfo:
    path: Path
    format: str = ""
    codec: str = ""
    duration: float = 0.0
    size: int = 0
    bit_rate: int = 0
    sample_rate: int = 0
    channels: int = 0
    channel_layout: str = ""
    bits: int = 0
    tags: dict[str, str] = field(default_factory=dict)
    has_cover: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def as_dict(self) -> dict:
        return {
            "format": self.format.upper(),
            "codec": self.codec,
            "duration": round(self.duration, 3),
            "size": self.size,
            "bitRate": self.bit_rate,
            "sampleRate": self.sample_rate,
            "channels": self.channels,
            "channelLayout": self.channel_layout,
            "bits": self.bits,
            "tags": self.tags,
            "hasCover": self.has_cover,
            "error": self.error,
        }


def probe(path: Path) -> ProbeInfo:
    """用 ffprobe 读取音频信息。"""
    info = ProbeInfo(path=path)
    if not path.exists():
        info.error = "文件不存在"
        return info
    try:
        ffprobe = toolchain.require("ffprobe")
    except Exception as e:
        info.error = str(e)
        return info

    r = runner.run([
        ffprobe, "-v", "error",
        "-show_entries", PROBE_ENTRIES,
        "-of", "json",
        str(path),
    ], timeout=30)

    if not r.ok:
        info.error = r.stderr_summary
        return info

    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError as e:
        info.error = f"ffprobe 输出无法解析: {e}"
        return info

    fmt = data.get("format") or {}
    info.format = (fmt.get("format_name") or "").split(",")[0]
    info.size = int(fmt.get("size") or path.stat().st_size)
    try:
        info.duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        info.duration = 0.0
    try:
        info.bit_rate = int(float(fmt.get("bit_rate") or 0))
    except (TypeError, ValueError):
        info.bit_rate = 0

    streams = data.get("streams") or []
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio:
        info.codec = audio.get("codec_name") or ""
        info.sample_rate = _int(audio.get("sample_rate"))
        info.channels = _int(audio.get("channels"))
        info.channel_layout = audio.get("channel_layout") or ""
        info.bits = _int(audio.get("bits_per_raw_sample"))

    # 封面：ffprobe 会把 attached_pic 报成 video 流
    info.has_cover = any(
        s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") == 1
        for s in streams
    ) or any(s.get("codec_type") == "video" for s in streams)

    info.tags = read_tags(path)
    return info


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------- 标签

# WAV / AIFF 的标签是 ID3 帧。mutagen **没有** EasyWAVE / EasyAIFF，
# 所以 `File(path, easy=True)` 对这两种格式拿到的是裸 ID3，
# `f['title'] = [...]` 会抛 "not a Frame instance"。必须自己映射帧名。
ID3_FRAMES = {
    "title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2",
    "genre": "TCON", "date": "TDRC", "tracknumber": "TRCK", "discnumber": "TPOS",
    "composer": "TCOM",
}
ID3_FRAMES_REV = {v: k for k, v in ID3_FRAMES.items()}
# comment 单独构造（COMM 需要 lang/desc），不放进上面的表
ID3_EXT = {".wav", ".aiff", ".aif"}

# 统一键名（Vorbis / ID3 写法差异在这里抹平）
CANON_KEYS = {
    "title": "title", "artist": "artist", "album": "album",
    "genre": "genre", "date": "date", "year": "date",
    "tracknumber": "tracknumber", "track": "tracknumber",
    "comment": "comment", "description": "comment",
    "albumartist": "albumartist", "discnumber": "discnumber",
    "composer": "composer", "lyrics": "lyrics",
}
# 前端展示字段 → Vorbis 键
UI_FIELDS = ["title", "artist", "album", "albumartist", "genre", "date",
             "tracknumber", "discnumber", "composer", "comment"]


def read_tags(path: Path) -> dict[str, str]:
    """读标签。FLAC 优先 metaflac，其余走 mutagen，最后退 ffprobe。"""
    tags: dict[str, str] = {}
    if path.suffix.lower() == ".flac":
        tags = _read_tags_metaflac(path)
    if not tags:
        tags = _read_tags_mutagen(path)
    if not tags:
        tags = _read_tags_ffprobe(path)
    # 只保留认识的键，并做大小写归并
    out: dict[str, str] = {}
    for k, v in tags.items():
        ck = CANON_KEYS.get(k.strip().lower())
        if ck:
            out[ck] = v
    return out


def _read_tags_metaflac(path: Path) -> dict[str, str]:
    mf = toolchain.path_of("metaflac")
    if not mf:
        return {}
    r = runner.run([mf, "--export-tags-to=-", str(path)], timeout=30)
    if not r.ok:
        return {}
    out: dict[str, str] = {}
    for line in (r.stdout or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _read_tags_mutagen(path: Path) -> dict[str, str]:
    try:
        import mutagen
    except ImportError:
        return {}
    try:
        f = mutagen.File(str(path), easy=True)
    except Exception:
        return {}
    if not f:
        return {}
    out: dict[str, str] = {}
    for k, v in (f.tags or {}).items():
        if isinstance(v, list):
            v = v[0] if v else ""
        key = str(k)
        # WAV / AIFF 的标签是 ID3 帧（TIT2 这种），不是 easy 名字 ——
        # 不翻译的话下面 CANON_KEYS 一条都匹配不上，读出来永远是空。
        if key.upper() in ID3_FRAMES_REV:
            key = ID3_FRAMES_REV[key.upper()]
        out[key] = str(v)
    return out


def _read_tags_ffprobe(path: Path) -> dict[str, str]:
    ffprobe = toolchain.path_of("ffprobe")
    if not ffprobe:
        return {}
    r = runner.run([
        ffprobe, "-v", "error", "-show_entries", "format_tags",
        "-of", "json", str(path),
    ], timeout=30)
    if not r.ok:
        return {}
    try:
        d = json.loads(r.stdout or "{}")
        return {str(k): str(v) for k, v in (d.get("format", {}).get("tags") or {}).items()}
    except Exception:
        return {}


@dataclass
class TagWriteResult:
    written: int = 0
    removed: int = 0
    method: str = ""
    error: str = ""
    # 「这个容器压根不支持标签」要和「写入时出错了」分开：
    # 前者是**预期**情况（AAC/图片…），HTTP 上该是 4xx；后者才是 5xx。
    # 用标志位而不是去匹配 error 字符串 —— 字符串一改就悄悄失效。
    unsupported: bool = False

    @property
    def ok(self) -> bool:
        return not self.error


def write_tags(path: Path, tags: dict[str, str], *, clear_missing: bool = False) -> TagWriteResult:
    """写标签，不重新编码。

    clear_missing=True 时，UI_FIELDS 里没给值的字段会被删除。
    """
    res = TagWriteResult()
    if not path.exists():
        res.error = "文件不存在"
        return res

    clean = {k.lower(): str(v) for k, v in tags.items() if v is not None}
    if path.suffix.lower() == ".flac":
        return _write_tags_metaflac(path, clean, clear_missing)
    return _write_tags_mutagen(path, clean, clear_missing)


def _write_tags_metaflac(path: Path, tags: dict[str, str], clear_missing: bool) -> TagWriteResult:
    mf = toolchain.path_of("metaflac")
    res = TagWriteResult(method="metaflac")
    if not mf:
        res.error = "metaflac 不可用"
        return res

    args = [mf]
    for k in UI_FIELDS:
        if k in tags and tags[k] != "":
            # --set-tag 的键名用大写 Vorbis 写法
            args.append(f"--set-tag={k.upper()}={tags[k]}")
            res.written += 1
        elif clear_missing:
            args.append(f"--remove-tag={k.upper()}")
            res.removed += 1
    if len(args) == 1:
        return res                       # 没有任何改动
    args.append(str(path))
    r = runner.run(args, timeout=60)
    if not r.ok:
        res.error = r.stderr_summary
        res.written = 0
    return res


def _write_tags_mutagen(path: Path, tags: dict[str, str], clear_missing: bool) -> TagWriteResult:
    res = TagWriteResult(method="mutagen")
    try:
        import mutagen
    except ImportError:
        res.error = "mutagen 未安装"
        return res
    try:
        f = mutagen.File(str(path), easy=True)
        if f is None:
            res.error = f"mutagen 无法识别 {path.suffix}"
            res.unsupported = True
            return res
        if f.tags is None:
            try:
                f.add_tags()
            except Exception as e:
                # AAC 之类根本不支持标签：必须报出来，不能假装写完
                res.error = f"{path.suffix} 不支持写入标签（{e}）"
                res.unsupported = True
                return res

        # mutagen 把 f.tags 标成 Optional，且 add_tags() 不保证一定生效
        # （个别实现会静默失败）。取局部引用并在这里一次性挡掉 None，
        # 后面就不用每处 in / del / [] 都重复判断了。
        tags_obj = f.tags
        if tags_obj is None:
            res.error = f"{path.suffix} 不支持写入标签（add_tags 未生效）"
            res.unsupported = True
            return res

        id3_mode = path.suffix.lower() in ID3_EXT
        failed: list[str] = []
        for k, v in tags.items():
            if v == "":
                continue
            try:
                if id3_mode:
                    _set_id3_frame(f, k, v)
                else:
                    f[k] = [v]
                res.written += 1
            except Exception as e:
                # 原来这里是 `except Exception: pass` —— 写入失败被吞掉，
                # 调用方拿到 ok=True 但一个字节都没写。WAV/AIFF 就栽在这。
                failed.append(f"{k}: {e}")

        if clear_missing:
            if id3_mode:
                for fid, easy in ID3_FRAMES_REV.items():
                    if easy not in tags and fid in tags_obj:
                        del tags_obj[fid]
                        res.removed += 1
                if "comment" not in tags and "COMM" in tags_obj:
                    del tags_obj["COMM"]
                    res.removed += 1
            else:
                for k in list(tags_obj):
                    if CANON_KEYS.get(str(k).lower()) in UI_FIELDS and k not in tags:
                        try:
                            del tags_obj[k]
                            res.removed += 1
                        except Exception:
                            pass

        if failed and not res.written:
            res.error = "写入失败：" + "；".join(failed[:3])
            return res
        f.save()
        if failed:
            res.error = "部分字段写入失败：" + "；".join(failed[:3])
        elif res.written == 0 and not res.removed:
            res.error = "没有任何标签被写入（键名可能不被该格式支持）"
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
    return res


def _set_id3_frame(f, key: str, value: str) -> None:
    """按 easy 名字写一个 ID3 帧（WAV / AIFF 用）。"""
    from mutagen.id3 import Frames
    if key == "comment":
        f.tags.setall("COMM", [Frames["COMM"](encoding=3, lang="XXX", desc="", text=[value])])
        return
    fid = ID3_FRAMES.get(key)
    if not fid:
        raise KeyError(f"ID3 里没有 {key} 对应的帧")
    f.tags.setall(fid, [Frames[fid](encoding=3, text=[value])])


# ---------------------------------------------------------------- 封面

def extract_cover(path: Path, out: Path) -> Optional[Path]:
    """导出内嵌封面为图片文件；没有封面返回 None。"""
    if path.suffix.lower() == ".flac":
        mf = toolchain.path_of("metaflac")
        if mf:
            r = runner.run([mf, "--export-picture-to=-", str(path)], timeout=60)
            # 二进制经 utf-8 解码会损坏，所以 FLAC 走下面的 ffmpeg 路径更稳
    ffmpeg = toolchain.path_of("ffmpeg")
    if not ffmpeg:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    r = runner.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(path),
        "-map", "0:v:0", "-frames:v", "1",
        "-c:v", "mjpeg", "-q:v", "2",
        str(out),
    ], timeout=120)
    if r.ok and out.exists() and out.stat().st_size > 0:
        return out
    return None


def embed_cover(path: Path, image: Path, *, picture_type: str = "Front Cover") -> runner.Result:
    """嵌入封面。

    FLAC 用 metaflac --import-picture-from（不重编码）；
    其余格式用 ffmpeg -disposition:v attached_pic + copy 音频流。

    只有部分容器支持"音频流 + 附加图片流"。实测（ffmpeg 9.0.2）：

        flac / mp3 / m4a / wma   成功，且确实写进了图片流
        ogg / opus / wav / aac   容器拒绝视频流，直接报错
        **aiff                    退出码 0，但图片流被静默丢掉**

    aiff 那种最难查：不报错、返回成功、封面却没进去。所以这里用白名单
    提前挡住，而不是把 ffmpeg 的原始报错（"Error sending frames to
    consumers: Invalid argument"）丢给用户。
    """
    ext = path.suffix.lower()
    if ext == ".flac":
        mf = toolchain.path_of("metaflac")
        if mf:
            return runner.run([
                mf,
                f"--import-picture-from={image}",
                str(path),
            ], timeout=120)
    elif ext not in COVER_FORMATS:
        return runner.Result(
            args=[], code=1, stdout="",
            stderr=(f"{ext.lstrip('.').upper() or '该'} 容器不支持内嵌封面，支持的是 "
                    f"{'、'.join(sorted(e.lstrip('.') for e in COVER_FORMATS))}。"
                    "可以先转成 FLAC 或 MP3 再嵌。"))
    ffmpeg = toolchain.path_of("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg 不可用")
    tmp = path.with_name(path.stem + ".cover-tmp" + path.suffix)
    r = runner.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(path), "-i", str(image),
        "-map", "0:a", "-map", "1:v",
        "-c:a", "copy",
        "-c:v", "mjpeg",
        "-disposition:v", "attached_pic",
        "-metadata:s:v", f"title={picture_type}",
        str(tmp),
    ], timeout=600)
    if r.ok and tmp.exists():
        tmp.replace(path)                # 原子替换
    elif tmp.exists():
        tmp.unlink(missing_ok=True)
    return r


def remove_cover(path: Path) -> runner.Result:
    """删除内嵌封面。"""
    if path.suffix.lower() == ".flac":
        mf = toolchain.path_of("metaflac")
        if mf:
            return runner.run([
                mf, "--remove", "--block-type=PICTURE", str(path),
            ], timeout=120)
    return runner.Result(args=[], code=0, stdout="", stderr="该格式暂不支持删除封面")


# ---------------------------------------------------------------- 封面缓存

def cover_cache_paths(file_id: str) -> tuple[Path, Path]:
    """(抽出的 jpg, mtime+size 缓存戳)。app.py 的 /cover 与任务处理器共用这套命名。"""
    return (config.CACHE / f"cover-{file_id}.jpg",
            config.CACHE / f"cover-{file_id}.key")


def drop_cover_cache(file_id: str) -> None:
    """封面变了就主动扔掉缓存。

    不能只靠 mtime+size 失效：metaflac --remove 默认会把 PICTURE 块留成等长
    padding，文件大小可能一模一样，而嵌入和移除又常发生在同一秒内，
    于是 int(mtime) 也相同 —— 实测「移除封面后 /cover 仍然返回旧图」。
    """
    for p in cover_cache_paths(file_id):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------- 峰值

# 真正支持内嵌封面的容器（实测，见 embed_cover 的说明）：
# aiff 虽然退出码 0，但图片流会被静默丢掉，所以**不在**白名单里。
COVER_FORMATS = {".flac", ".mp3", ".m4a", ".wma"}

CACHE_VERSION = 1

def file_key(path: Path) -> str:
    """缓存键：路径 + 大小 + mtime，内容变了自动失效。"""
    st = path.stat()
    raw = f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def peaks_cache_path(path: Path) -> Path:
    return config.CACHE / f"peaks-{file_key(path)}.json"


def peaks(path: Path, *, buckets: int = 1000, force: bool = False) -> dict:
    """返回归一化峰值数组（每声道一份）。

    生产级实现应逐样本扫描；这里用 ffmpeg 解码成 8kHz 单声道原始 PCM，
    按桶取绝对值最大值，足够画 1000 点概览，且远快于 astats 逐帧。
    """
    cached = peaks_cache_path(path)
    if cached.exists() and not force:
        try:
            d = json.loads(cached.read_text(encoding="utf-8"))
            if d.get("version") == CACHE_VERSION:
                return d
        except Exception:
            pass

    ffmpeg = toolchain.path_of("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg 不可用")

    info = probe(path)
    channels = max(1, min(2, info.channels or 1))

    # 解码为 8kHz / 8bit 无符号单声道原始 PCM：
    # 必须用 run_bytes 取二进制，若按文本解码采样值会被破坏。
    code, data, err = runner.run_bytes([
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-i", str(path),
        "-ac", "1", "-ar", "8000",
        "-f", "u8", "-",
    ], timeout=config.TASK_TIMEOUT)
    if code != 0:
        raise RuntimeError(f"解码失败: {(err or '').strip()[:200] or f'退出码 {code}'}")

    n = len(data)
    if n == 0:
        raise RuntimeError("解码结果为空")

    buckets = max(64, min(buckets, 20000))
    step = max(1, n // buckets)
    out: list[float] = []
    for i in range(0, n, step):
        chunk = data[i:i + step]
        if not chunk:
            continue
        # u8 的静音值是 128
        peak = max(abs(b - 128) for b in chunk) / 128.0
        out.append(round(min(1.0, peak), 4))

    payload: dict[str, Any] = {
        "version": CACHE_VERSION,
        "buckets": len(out),
        "channels": channels,
        "duration": info.duration,
        "sampleRate": info.sample_rate,
        "peaks": out,               # 单声道合并；前端按需镜像成 L/R
        "key": file_key(path),
    }
    try:
        cached.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return payload


def rms(peaks_arr: list[float]) -> float:
    if not peaks_arr:
        return 0.0
    return math.sqrt(sum(p * p for p in peaks_arr) / len(peaks_arr))


# ---------------------------------------------------------------- 响度时间线

# EBU R128 规定的静音底：首帧（门限还没积累样本时）M/S 会是这个值。
# 绘图和 max() 统计都必须先剔除，否则曲线开头掉到谷底、MOMENTARY MAX 被污染。
SILENCE_LUFS = -120.0


def loudness_cache_path(path: Path) -> Path:
    return config.CACHE / f"loudness-{file_key(path)}.json"


def loudness_timeline(path: Path, *, force: bool = False,
                      tag: str = "") -> dict:
    """跑一遍 ebur128 拿到逐帧响度时间线 + 8 项汇总指标（按文件缓存）。

    单趟配方（实测 3:07 的歌 0.7s）：

        ffmpeg -i <src> -map 0:a:0 \\
          -af "ebur128=peak=true:framelog=verbose:metadata=true,
               ametadata=mode=print:file=ebur-meta.txt" -f null -

    两处**必须**照做，都是实测踩出来的（见 响度总览图实现构想.md §5）：

    1. **filter 里不能出现 Windows 绝对路径**：反斜杠会被 ffmpeg 的 filter
       解析器当转义符吃掉（`C:\\Users\\...` → `UsersRedmi...`），报
       "No option name near ..."。所以把 `cwd` 设成 CACHE、`file=` 只给相对名。
    2. **必须显式 `-map 0:a:0`**：本机不少 flac 自带 mjpeg 封面流，
       不显式选音频会多解一路图、甚至失败。

    返回结构与 `peaks()` 同族（`version` / `key` / `cached`），便于前端一视同仁；
    `duration` 之外还有 `t`（秒）与 M/S/I/LRA 四条曲线，以及 8 项指
    标。时间线是 10Hz 的原始密度，前端的降采样是**取段内最大值**而不是平均 ——
    平均会把瞬时峰值削平（报告 §2.3）。
    """
    cached = loudness_cache_path(path)
    if cached.exists() and not force:
        try:
            d = json.loads(cached.read_text(encoding="utf-8"))
            if d.get("version") == CACHE_VERSION:
                d["cached"] = True
                return d
        except Exception:
            pass        # 缓存坏了就重算，不要把坏缓存抛给用户

    ffmpeg = toolchain.path_of("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg 不可用")

    # 中间文件名**每个任务唯一**。
    #
    # ⚠ 不能共用一个固定名字（原来是 `ebur-meta.txt`）：队列是 2 并发，
    # 两个响度任务会同时写/删同一个路径 —— 一个 `unlink` 时另一个正开着它，
    # Windows 直接抛 `PermissionError: [WinError 32] 另一个程序正在使用此文件`。
    # 症状是"批量分析时随机有一个文件失败"，而失败原因看着像磁盘问题。
    # （实测踩过：同一批 2 个文件，一个成功一个失败。）
    suffix = f"ebur-meta-{tag}.txt" if tag else f"ebur-meta-{os.getpid()}.txt"
    meta = config.CACHE / suffix
    meta.unlink(missing_ok=True)

    # 注意 `file=` 只给**文件名**，路径由 cwd 提供（见上面第 1 条）
    r = runner.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats",
        "-i", str(path),
        "-map", "0:a:0",
        "-af", ("ebur128=peak=true:framelog=verbose:metadata=true,"
                f"ametadata=mode=print:file={suffix}"),
        "-f", "null", "-",
    ], timeout=config.TASK_TIMEOUT, cwd=config.CACHE)

    if not r.ok:
        raise RuntimeError(f"响度分析失败: {r.stderr_summary}")

    text = ""
    try:
        text = meta.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    finally:
        meta.unlink(missing_ok=True)      # 中间文件不留着

    data = parse_ebur_metadata(text)
    if not data["t"]:
        raise RuntimeError("ebur128 没有产出任何帧（可能没有音频流）")

    info = probe(path)
    payload: dict[str, Any] = {
        "version": CACHE_VERSION,
        "key": file_key(path),
        "duration": info.duration,
        "sampleRate": info.sample_rate,
        "channels": info.channels,
        "hz": 10,
        "cached": False,
        **data,
    }
    try:
        cached.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return payload


# ametadata 输出里我们要的键 → 结果里的字段名。
# `lavfi.r128.M` 是 400ms 窗的瞬时响度，`S` 是 3s 窗的短时，`I` 是带门限的整合值。
_EBUR_KEYS = {
    "lavfi.r128.M": "M",
    "lavfi.r128.S": "S",
    "lavfi.r128.I": "I",
    "lavfi.r128.LRA": "LRA",
    "lavfi.r128.true_peak": "truePeak",
}


def parse_ebur_metadata(text: str) -> dict:
    """把 ametadata 的 `key=value` 文本解析成时间线 + 汇总指标。

    输出形态（10Hz，每帧一段）：

        frame:0  pts:0  pts_time:0
        lavfi.r128.M=-120.691
        lavfi.r128.S=-120.691
        ...

    **首帧的 -120.691 是 EBU 规定的静音底，必须剔除**（报告 §5.4）：
    它是"门限还没积累到样本"的产物，不是真的这么安静。留着会让曲线开头
    掉到谷底，`max(M)` 倒是不受影响（-120 比谁都小），但画出来很难看。
    `I` **不剔除**：它的左端天然偏低是标准行为，指标卡取的是末值（报告 §5.5）。
    """
    t: list[float] = []
    series: dict[str, list[float]] = {v: [] for v in _EBUR_KEYS.values()}
    cur: dict[str, float] = {}
    # 记录每条序列"第一个不再处于静音底"的帧号（见下面剔除那段）
    ready_at: dict[str, int] = {}

    def flush() -> None:
        if not cur:
            return
        # 一帧里 M/S 缺项就跳过这一帧（宁可少一点，也不要错位）
        if any(k not in cur for k in ("M", "S")):
            cur.clear()
            return
        idx = len(t)
        for nm in ("M", "S"):
            if nm not in ready_at and cur[nm] > SILENCE_LUFS:
                ready_at[nm] = idx
        t.append(cur.get("_t", 0.0))
        for key, name in _EBUR_KEYS.items():
            series[name].append(cur.get(name, 0.0))
        cur.clear()

    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("frame:"):
            flush()
            # 同一行里还带 pts_time:<秒>，取它当时间轴
            for tok in line.split():
                if tok.startswith("pts_time:"):
                    try:
                        cur["_t"] = float(tok.split(":", 1)[1])
                    except ValueError:
                        cur["_t"] = 0.0
                    break
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if k in _EBUR_KEYS:
            try:
                cur[_EBUR_KEYS[k]] = float(v)
            except ValueError:
                pass
    flush()

    # 剔除"静音底"帧。
    #
    # ⚠ 两条序列的窗口长度不同，**不能只剔 M**：
    #   · `M` 是 400ms 窗 → 一般只有第 0 帧在底上
    #   · `S` 是 **3 秒**窗 → 前 ~30 帧（10Hz）都在底上，因为窗还没填满
    # 实测踩过：原来只判 `M[0] <= -120`，结果首帧 `S` 还是 -120.7，
    # 曲线开头掉到谷底（报告 §5.4 说的就是这个坑）。
    # 判据用"这条序列自己什么时候离开底"，而不是固定看第 0 帧。
    #
    # 切割点取 M/S 里**最晚**开始有效的那条，保证三条曲线与 `t` 仍然一一对应
    # （早先写成"各剔各的"会让 M 比 S 长、和 t 错位，图上就是整条曲线平移）。
    cut = max(ready_at.get("M", 0), ready_at.get("S", 0))
    if cut:
        del t[:cut]
        for name in series:
            del series[name][:cut]

    def _max(name: str) -> float:
        vals = [x for x in series.get(name, []) if x > SILENCE_LUFS]
        return round(max(vals), 1) if vals else 0.0

    integrated = round(series["I"][-1], 1) if series["I"] else 0.0
    true_peak = _max("truePeak")
    m_max, s_max = _max("M"), _max("S")
    lra_vals = [x for x in series.get("LRA", []) if x > 0]
    lra = round(lra_vals[-1], 1) if lra_vals else 0.0

    return {
        "t": [round(x, 2) for x in t],
        "M": [round(x, 1) for x in series["M"]],
        "S": [round(x, 1) for x in series["S"]],
        "I": [round(x, 1) for x in series["I"]],
        "truePeak": [round(x, 2) for x in series["truePeak"]],
        "summary": {
            "integrated": integrated,          # INTEGRATED (LUFS)
            "lra": lra,                        # LOUDNESS RANGE (LU)
            # AVERAGE DYNAMICS (PLR) = I − true_peak。报告 §1.3 用它做过自洽性校验
            "plr": round(abs(integrated - true_peak), 1),
            "momentaryMax": m_max,
            "shortTermMax": s_max,
            "truePeakMax": true_peak,
        },
        "frames": len(t),
    }


# ================================================================ 响度总览图（PNG）
#
# 规格全部来自 `响度总览图（LoudnessAnalysis）实现构想.md`（那篇是对原图
# `target/LoudnessAnalysis.svg` 的逐像素逆向 + 本机 ffmpeg 实测）。**别在这里
# 重新发明参数** —— 尤其 `AXIS_Y`：纵轴是**非线性**的，-23~-27 这个"有效响度区"
# 被放大了约 2.9 倍，用一个线性 dB→y 公式画出来的形状跟原图对不上（§1.6）。

# LUFS → 相对绘图区顶部的比例（0=顶，1=底）。**必须分段插值**。
AXIS_Y: tuple[tuple[float, float], ...] = (
    (-13, 90), (-18, 253), (-23, 342), (-27, 597),
    (-36, 784), (-45, 1141), (-54, 1489),
)

LOUD_COLORS = {
    "bg": "#FFFFFF",
    "body": "#A8C0D8",        # 蓝体：主色块（向下填充到图底）
    "head": "#D89890",        # 红带：叠在蓝体之上，只在响处隆起
    "refLine": "#F2B84B",     # 参考线（默认 -23 LUFS）
    "grid": "#E4E7EA",        # 网格线（原图 #F7F7F7 在白底上看不见，压深一点）
    "separator": "#CACECF",   # 绘图区与 footer 之间的横向分隔线
    "text": "#1E1F23",
    "muted": "#8A9099",
}

# 版面（像素）。**比例按 §3.3**：整图约 6.6:1，绘图区约 1:1.07。
PNG_W = 2400
PNG_H = 430
# 宽度下限：再小就装不下 8 张指标卡（而放开下限又会让版面比例失真）。
MIN_PNG_W = 1200
PNG_PAD_L = 96            # 左侧留刻度文字
PNG_PAD_R = 28
PNG_PAD_T = 34
PNG_FOOTER_H = 88


def _axis_frac(lufs: float) -> float:
    """LUFS → 绘图区内的比例（0=顶=最响，1=底=最轻）。分段线性插值。

    超出控制点范围就**夹住**（不是外推）：`-54` 以下没有刻度含义，
    外推会把静音底（-120）画到一个荒唐的位置。
    """
    pts = AXIS_Y
    if lufs >= pts[0][0]:
        lo, hi = pts[0], pts[1]
    elif lufs <= pts[-1][0]:
        lo, hi = pts[-2], pts[-1]
    else:
        lo, hi = pts[-1], pts[-2]
        for i in range(len(pts) - 1):
            if pts[i][0] >= lufs >= pts[i + 1][0]:
                lo, hi = pts[i], pts[i + 1]
                break
    span_v = lo[0] - hi[0]
    if span_v <= 0:
        return 0.0
    t = (lo[0] - max(min(lufs, lo[0]), hi[0])) / span_v
    y0, y1 = lo[1], hi[1]
    raw = y0 + (y1 - y0) * t
    # 归一化到 0..1（用整轴跨度）
    top, bot = pts[0][1], pts[-1][1]
    return max(0.0, min(1.0, (raw - top) / (bot - top)))


def _lufs_ticks() -> list[float]:
    """刻度值（原图是 -13/-18/-23/-27/-36/-45/-54，**等距的是屏幕位置不是值**）。"""
    return [p[0] for p in AXIS_Y]


# 图表文字。**按顺序找第一个装得上的**：
#   · 标题带文件名，中文/日文都可能出现 —— 得试系统 CJK 字体
#   · 刻度与指标全是 ASCII，DejaVu（PIL 自带）就够
# 实测踩过：只用 DejaVu 时中文标题会画成一个个方块。所以标题**找不到
# CJK 字体就退回 ASCII 兜底**（`_ascii_title`），绝不留方块。
_ASCII_FONTS = ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arial.ttf")
_CJK_FONTS = ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "meiryo.ttc",
              "YuGothM.ttc", "NotoSansCJK-Regular.ttc")


def _pick_font(size: int, *, need_cjk: bool = False):
    """挑一个字体。`need_cjk=True` 时**只接受装了 CJK 的**，找不到返回 `None`。"""
    from PIL import ImageFont
    order = _CJK_FONTS if need_cjk else _ASCII_FONTS + _CJK_FONTS
    for name in order:
        try:
            return ImageFont.truetype(name, size)
        except Exception:                              # noqa: BLE001
            continue
    if need_cjk:
        return None
    try:
        return ImageFont.load_default(size=size)
    except Exception:                                  # noqa: BLE001
        return ImageFont.load_default()


def _ascii_title(s: str) -> str:
    """把标题里的非 ASCII 字符换成 `?`（**只在拿不到 CJK 字体时用**）。

    `?` 比一个个"豆腐块"诚实：方块看起来像渲染坏了，`?` 一眼就知道是编码兜底。
    """
    return "".join(ch if ord(ch) < 128 else "?" for ch in s)


def _fmt_lufs(v: float | None) -> str:
    return "—" if v is None else f"{v:.1f}"


def _resample_columns(ts: list[float], vals: list[float], ncols: int,
                      duration: float) -> list[float | None]:
    """按目标列数重采样（**段内取最大值**，不是平均 —— §2.3）。

    平均会把瞬时峰值削平，而这张图的意义就是"看峰值在哪"。
    静音底（`-120.x`，§5.4）先剔掉，否则曲线开头会掉到谷底、
    `max()` 统计也被污染。
    """
    if not ts or not vals or duration <= 0 or ncols <= 0:
        return [None] * max(0, ncols)
    out: list[float | None] = [None] * ncols
    for t, v in zip(ts, vals):
        if v is None or v <= SILENCE_LUFS + 1.0:       # 静音底，不是数据
            continue
        i = int(t / duration * ncols)
        if i < 0:
            i = 0
        elif i >= ncols:
            i = ncols - 1
        cur = out[i]
        if cur is None or v > cur:                     # 段内最大值
            out[i] = v
    return out


def render_loudness_png(data: dict, *, title: str = "",
                        ref_lufs: float = -23.0,
                        high_lufs: float | None = None,
                        width: int = PNG_W) -> bytes:
    """把一份 `loudness_timeline()` 的结果画成 **PNG**（路线 B，§6）。

    `high_lufs` 是"红带"的下界 —— 高于它的部分算"较响"。
    §8.1 说原图的红带语义与字面指标对不齐，建议先定成
    「瞬时响度高于某阈值的部分」并**做成可配**；缺省取
    `Integrated + LRA/2`（响度范围的上半段），这是能从数据里推出来的、
    有明确含义的口径，而不是拍一个常数。

    返回 PNG 字节（调用方落盘）。**纯 PIL 绘制**：不依赖浏览器、
    不依赖系统中文字体、结果是确定性的（同一份数据两次渲染逐字节相同）。

    `width` 按**同一套版面比例**缩放（整图 6.6:1）。这不是"拉伸位图"，
    而是重新算一遍所有坐标 —— 放大会更清晰，缩小也不会糊。
    """
    from PIL import Image, ImageDraw

    # 版面按目标宽度等比缩放（所有像素常量都乘同一个系数）
    #
    # ⚠ 三个"下限"都会**破坏比例**，而"按比例重排"正是这个参数的卖点：
    #   · footer 单独设下限（曾写 60px）→ 小宽度下 footer 占比抬高，
    #     实测 800px 时比例从 5.58 掉到 4.0
    #   · 高度设下限（曾写 200px）→ 同上，800px 时高度被抬到 200
    # 所以现在**只限制宽度下限**：宽度够大，高度自然落在合理区间
    # （1200px 宽 → 215px 高，footer 字号仍有 ~10px，看得清）。
    W = max(MIN_PNG_W, min(8000, int(width)))
    k = W / PNG_W
    H = int(round(PNG_H * k))
    pad_l = max(52, int(round(PNG_PAD_L * k)))
    pad_r = max(14, int(round(PNG_PAD_R * k)))
    pad_t = max(16, int(round(PNG_PAD_T * k)))
    footer_h = int(round(PNG_FOOTER_H * k))
    fs = max(11, int(round(k * 20)))

    summary = (data.get("summary") or {})
    ts = data.get("t") or []
    mv = data.get("M") or []
    duration = float(data.get("duration") or 0.0)
    if duration <= 0 and ts:
        duration = float(ts[-1]) or 1.0

    img = Image.new("RGB", (W, H), LOUD_COLORS["bg"])
    d = ImageDraw.Draw(img)
    f_tick = _pick_font(fs)
    f_small = _pick_font(max(10, int(fs * 0.9)))
    f_label = _pick_font(max(10, int(fs * 0.85)))
    f_val = _pick_font(int(fs * 1.3))
    f_title = _pick_font(int(fs * 1.1))

    plot_l = pad_l
    plot_r = W - pad_r
    plot_t = pad_t
    plot_b = H - footer_h - int(round(26 * k))
    plot_w = plot_r - plot_l
    plot_h = plot_b - plot_t
    if plot_w <= 10 or plot_h <= 10:
        raise RuntimeError("画布太小，无法绘制")

    # ---- 1) 网格线 + 左轴刻度（**非等距**）----
    for lufs in _lufs_ticks():
        y = plot_t + _axis_frac(lufs) * plot_h
        d.line([(plot_l, y), (plot_r, y)], fill=LOUD_COLORS["grid"], width=1)
        txt = f"{lufs:g}"
        bb = d.textbbox((0, 0), txt, font=f_tick)
        d.text((plot_l - int(round(12 * k)) - (bb[2] - bb[0]), y - (bb[3] - bb[1]) / 2 - bb[1]),
               txt, font=f_tick, fill=LOUD_COLORS["muted"])
    # 单位放在**刻度列上方、绘图区之内**：放到绘图区外面（`plot_t - 24`）
    # 会和标题抢同一行（标题起点也是 `PNG_PAD_L`），实测两者直接叠在一起。
    d.text((plot_l - int(round(62 * k)), plot_t + 4), "LUFS", font=f_small,
           fill=LOUD_COLORS["muted"])

    # ---- 2) 参考线（橙色，默认 -23）----
    ref_y = plot_t + _axis_frac(ref_lufs) * plot_h
    d.line([(plot_l, ref_y), (plot_r, ref_y)], fill=LOUD_COLORS["refLine"], width=2)
    d.text((plot_r - int(round(62 * k)), ref_y - 22), f"{ref_lufs:g}", font=f_small,
           fill=LOUD_COLORS["refLine"])

    # ---- 3) 蓝体 + 4) 红带 ----
    # 每列一个最大值（段内最大），从响度曲线**向下填充到图底**
    cols = max(1, min(plot_w, int(round(1200 * k))))
    series = _resample_columns(ts, mv, cols, duration)
    integrated = summary.get("integrated")
    lra = summary.get("lra") or 0.0
    if high_lufs is None:
        base = integrated if isinstance(integrated, (int, float)) else -23.0
        high_lufs = base + float(lra) / 2.0

    def col_x(i: int) -> int:
        return plot_l + int(i * plot_w / cols)

    head_pts: list[tuple[int, int]] = []
    body_pts: list[tuple[int, int]] = []
    for i, v in enumerate(series):
        if v is None:
            continue
        x = col_x(i)
        y = plot_t + _axis_frac(v) * plot_h
        y = max(plot_t, min(plot_b, y))
        body_pts.append((x, y))
        hy = plot_t + _axis_frac(max(v, high_lufs)) * plot_h
        head_pts.append((x, max(plot_t, min(plot_b, hy))))
    if body_pts:
        # 蓝体：折线 + 向下闭合成多边形
        poly = body_pts + [(body_pts[-1][0], plot_b), (body_pts[0][0], plot_b)]
        d.polygon(poly, fill=LOUD_COLORS["body"])
    if head_pts:
        # 红带：`M` 与"较响阈值"之间的窄带（阈值线在 M 之下时带宽为 0）
        band = head_pts + [(x, y) for x, y in reversed(body_pts)]
        if len(band) >= 3:
            d.polygon(band, fill=LOUD_COLORS["head"])

    # ---- 5) 时间刻度（按时长自动选步长，刻度数落在 20~45，§1.4）----
    step = 7
    for cand in (5, 7, 10, 15, 30, 60, 120, 300):
        if 20 <= duration / cand <= 45:
            step = cand
            break
    else:
        step = max(1, int(duration / 30) or 1)
    tk = 0
    while tk * step <= duration:
        sec = tk * step
        x = plot_l + int(sec / duration * plot_w) if duration else plot_l
        d.line([(x, plot_b), (x, plot_b + int(round(6 * k)))], fill=LOUD_COLORS["separator"], width=1)
        label = _mmss(sec)
        bb = d.textbbox((0, 0), label, font=f_small)
        d.text((x - (bb[2] - bb[0]) / 2, plot_b + int(round(9 * k))), label, font=f_small,
               fill=LOUD_COLORS["muted"])
        tk += 1

    # ---- 6) 分隔线 ----
    sep_y = H - footer_h
    d.line([(0, sep_y), (W, sep_y)], fill=LOUD_COLORS["separator"], width=2)

    # ---- 7) footer：指标卡 ----
    # §8.3：原图两个 DIAL 显示 `-`（未启用），这里**保留占位**而不是省略 ——
    # 省略会让每张卡的宽度与位置都变，看起来像另一种排版。
    cards = [
        ("INTEGRATED", _fmt_lufs(summary.get("integrated")), "LUFS"),
        ("LRA", _fmt_lufs(summary.get("lra")), "LU"),
        ("DIAL I", "—", ""),
        ("DIAL LRA", "—", ""),
        ("PLR", _fmt_lufs(summary.get("plr")), "dB"),
        ("MOMENTARY MAX", _fmt_lufs(summary.get("momentaryMax")), "LUFS"),
        ("SHORT-TERM MAX", _fmt_lufs(summary.get("shortTermMax")), "LUFS"),
        ("TRUE PEAK MAX", _fmt_lufs(summary.get("truePeakMax")), "dBTP"),
    ]
    inner_l = int(round(40 * k))
    inner_r = W - int(round(40 * k))
    cw = (inner_r - inner_l) / len(cards)
    for i, (label, val, unit) in enumerate(cards):
        cx = inner_l + i * cw
        d.text((cx, sep_y + int(round(14 * k))), label, font=f_label, fill=LOUD_COLORS["muted"])
        vtxt = f"{val} {unit}".strip()
        d.text((cx, sep_y + int(round(38 * k))), vtxt, font=f_val, fill=LOUD_COLORS["text"])

    # ---- 标题（左边距那块留白正好放它）----
    # 标题带文件名，可能是中日文 —— 单独挑字体，拿不到就退回 ASCII 兜底，
    # 绝不留"豆腐块"（那看起来像渲染坏了）。
    if title:
        tf = _pick_font(int(fs * 1.1), need_cjk=True)
        text = title[:80]
        if tf is None:
            tf = f_title
            text = _ascii_title(text)
        d.text((pad_l, int(round(6 * k))), text, font=tf,
               fill=LOUD_COLORS["text"])

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_loudness_markdown(entries: list[dict], *, detail: str = "summary",                             time_points: int = 5, frame_table: bool = False,
                             ref_lufs: float = -23.0,
                             generated_at: str = "") -> str:
    """把若干份响度分析结果渲染成一份 **Markdown 报告**。

    `entries` 每项形如 `{"name":…, "data": loudness_timeline(...) 的返回}`。

    **只输出结论，不输出过程**（这是刻意的）：
    逐帧 10Hz 数据一首 3 分钟的歌就 1800 行，直接贴进 md 会把结论淹掉。
    所以默认只给汇总表 + 分区统计 + 最响/最轻的几个时间点；
    要看全量请开 `frame_table`（或者直接读 `.cache/loudness-*.json`）。

    `ref_lufs` 只用来判"偏响/偏轻"，不改变任何测量值 ——
    报告里所有数字都是从 ffmpeg 的 ebur128 实测得来的，不做任何换算。
    """
    lines: list[str] = []
    A = lines.append
    n = len(entries)
    A("# 响度分析报告")
    A("")
    if generated_at:
        A(f"> 生成时间：{generated_at}　·　共 {n} 个文件")
        A("")
    A("指标说明：**Integrated** 是整曲的整合响度（LUFS，带门限）；"
      "**LRA** 是响度范围（LU）；**PLR** 是平均动态（`|I − 真峰值|`）；"
      "**瞬时/短时**分别是 400ms / 3s 窗的最大值；**真峰值**按 BS.1770 做 4 倍过采样。")
    A("")

    # ---------------- 汇总表 ----------------
    A("## 汇总")
    A("")
    A("| # | 文件 | 时长 | Integrated | LRA | PLR | 瞬时峰值 | 短时峰值 | 真峰值 | 判定 |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for i, e in enumerate(entries, 1):
        d = e.get("data") or {}
        s = d.get("summary") or {}
        verdict = _loudness_verdict(s.get("integrated"), ref_lufs)
        A(f"| {i} | {_md_cell(e.get('name'))} | {_mmss(d.get('duration'))} "
          f"| {_num(s.get('integrated'))} LUFS | {_num(s.get('lra'))} LU "
          f"| {_num(s.get('plr'))} | {_num(s.get('momentaryMax'))} "
          f"| {_num(s.get('shortTermMax'))} | {_num(s.get('truePeakMax'))} dBTP "
          f"| {verdict} |")
    A("")
    A(f"> 「判定」以参考目标 **{_num(ref_lufs)} LUFS** 为准（可用 `refLufs` 改）。"
      "它是**提示**，不是质量结论 —— 目标值取决于发行渠道。")
    A("")

    # ---------------- 自洽性 ----------------
    A("## 自洽性校验")
    A("")
    A("报告里的数字是 ffmpeg `ebur128` 实测的，所以可以用两条**物理关系**互校。")
    A("")
    A("- `PLR` 按定义等于 `|Integrated − 真峰值|` —— 对不上说明单位混用了")
    A("- `瞬时最大值 ≥ 短时最大值` —— 短时是 3 秒窗、瞬时是 400ms 窗，"
      "窗口越短越容易抓到更高的瞬时值，所以这条**必然成立**")
    A("")
    A("> ⚠ **不要把真峰值和响度比大小**：真峰值是 dBTP（样本峰值 + 4 倍过采样），"
      "响度是 LUFS（带 K 加权与门限），两者不同量纲。"
      "真峰值 > 0 dBTP 是**正常现象**（重限幅素材的交叠采样过冲），"
      "不代表哪里出错 —— 报告第一版就是这么误报的。")
    A("")
    A("| 文件 | PLR 报表 | 自算 \\|I − 真峰值\\| | 差 | 瞬时 ≥ 短时 |")
    A("|---|---|---|---|---|")
    for e in entries:
        s = (e.get("data") or {}).get("summary") or {}
        rep, calc = s.get("plr"), abs((s.get("integrated") or 0.0)
                                      - (s.get("truePeakMax") or 0.0))
        diff = abs((rep or 0.0) - calc)
        # 只比**同一量纲**的两个响度（LUFS），不掺真峰值（dBTP）
        order = (s.get("momentaryMax") or -999) >= (s.get("shortTermMax") or 999)
        A(f"| {_md_cell(e.get('name'))} | {_num(rep)} | {calc:.1f} "
          f"| {'OK' if diff <= 0.3 else f'**{diff:.2f} 偏差**'} "
          f"| {'OK' if order else '**异常**'} |")
    A("")

    # ---------------- 响度分区 ----------------
    A("## 响度分区")
    A("")
    A("按瞬时响度（400ms 窗）把整曲切成三段，看**动态分布**："
      "全是「过轻」说明整体压得太狠或录音电平偏低；全是「超响」说明在响度战争里。")
    A("")
    A("| 文件 | 超响（> −9） | 正常（−30 ~ −9） | 过轻（< −30） |")
    A("|---|---|---|---|")
    for e in entries:
        m = (e.get("data") or {}).get("M") or []
        tot = max(1, len(m))
        loud = sum(1 for x in m if x > -9) / tot
        quiet = sum(1 for x in m if x < -30) / tot
        mid = 1.0 - loud - quiet
        A(f"| {_md_cell(e.get('name'))} | {loud:.1%} | {mid:.1%} | {quiet:.1%} |")
    A("")

    # ---------------- 逐曲明细 ----------------
    if detail == "full":
        A("## 逐曲明细")
        A("")
        for i, e in enumerate(entries, 1):
            d = e.get("data") or {}
            s = d.get("summary") or {}
            A(f"### {i}. {e.get('name')}")
            A("")
            A(f"- 时长：{_mmss(d.get('duration'))}　·　"
              f"帧数：{d.get('frames', 0)}（{d.get('hz', 10)} Hz）")
            A(f"- Integrated **{_num(s.get('integrated'))} LUFS**　·　"
              f"LRA **{_num(s.get('lra'))} LU**　·　PLR **{_num(s.get('plr'))}**")
            A(f"- 瞬时峰值 **{_num(s.get('momentaryMax'))}**　·　"
              f"短时峰值 **{_num(s.get('shortTermMax'))}**　·　"
              f"真峰值 **{_num(s.get('truePeakMax'))} dBTP**")
            if time_points > 0:
                A("")
                A(f"**最响的 {time_points} 个瞬间**（定位爆音/削波风险）：")
                A("")
                for t, v in _extreme_points(d, time_points, loudest=True):
                    A(f"- `{_mmss(t)}`　{v:+.1f} LUFS")
                A("")
                A(f"**最轻的 {time_points} 个瞬间**（定位冷场/漏音）：")
                A("")
                for t, v in _extreme_points(d, time_points, loudest=False):
                    A(f"- `{_mmss(t)}`　{v:+.1f} LUFS")
            A("")

    # ---------------- 逐帧明细（可选） ----------------
    if frame_table:
        A("## 逐帧明细")
        A("")
        A("> 每帧一行（10Hz）。**默认不生成**：3 分钟的歌约 1800 行，会把结论淹掉。")
        A("")
        A("| 文件 | 时间 | M（瞬时） | S（短时） | I（整合） | 真峰值 |")
        A("|---|---|---|---|---|---|")
        for e in entries:
            d = e.get("data") or {}
            t = d.get("t") or []
            M = d.get("M") or []
            S = d.get("S") or []
            I = d.get("I") or []
            P = d.get("truePeak") or []
            for k in range(len(t)):
                A(f"| {_md_cell(e.get('name'))} | {_mmss(t[k])} "
                  f"| {_num(M[k] if k < len(M) else None)} "
                  f"| {_num(S[k] if k < len(S) else None)} "
                  f"| {_num(I[k] if k < len(I) else None)} "
                  f"| {_num(P[k] if k < len(P) else None)} |")
        A("")

    return "\n".join(lines).rstrip() + "\n"


def _extreme_points(data: dict, k: int, *, loudest: bool) -> list[tuple[float, float]]:
    """取 M 曲线上最响/最轻的 k 个时间点。→ `[(秒, LUFS), …]`。

    用**分桶取极值 + 时间去重**，而不是直接 `sorted`：
    相邻帧的 M 值几乎一样，直接排序会返回同一秒里的 5 个点 —— 那对
    "定位哪里爆了"毫无帮助。这里按 1 秒分桶，每桶取极值再排序。
    """
    t = data.get("t") or []
    M = data.get("M") or []
    if not t or not M:
        return []
    n = min(len(t), len(M))
    buckets: dict[int, tuple[float, float]] = {}
    for i in range(n):
        sec = int(t[i])
        v = M[i]
        cur = buckets.get(sec)
        if cur is None or (v > cur[1] if loudest else v < cur[1]):
            buckets[sec] = (t[i], v)
    pts = sorted(buckets.values(), key=lambda p: p[1], reverse=loudest)
    return pts[:k]


def _loudness_verdict(integrated: float | None, ref: float) -> str:
    """相对参考目标给一句提示。**只做提示**，不是质量结论。"""
    if integrated is None:
        return "—"
    d = integrated - ref
    if d >= 3:
        return f"偏响 {d:+.1f} LU"
    if d <= -3:
        return f"偏轻 {d:+.1f} LU"
    return "接近目标"


def _num(v: object, digits: int = 1) -> str:
    try:
        return f"{float(v):.{digits}f}"          # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"


def _mmss(sec: object) -> str:
    try:
        s = float(sec)                            # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    m, r = divmod(int(round(s)), 60)
    return f"{m}:{r:02d}"


def _md_cell(text: object) -> str:
    """把任意文本塞进 Markdown 表格单元格：转义竖线、压掉换行（否则表格会断）。"""
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


# ---------------------------------------------------------------- 完整性

def verify_flac(path: Path) -> runner.Result:
    """flac -t 完整性校验（可选项 §5）。"""
    flac = toolchain.path_of("flac")
    if not flac:
        raise RuntimeError("flac 不可用")
    return runner.run([flac, "-t", "-s", str(path)], timeout=config.TASK_TIMEOUT)
