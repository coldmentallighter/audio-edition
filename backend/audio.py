"""音频操作：探测信息、读写标签、封面增删、峰值数据。

原则（需求 §4.1）：
  · 改元数据【不重新编码】—— FLAC 走 metaflac，其他格式走 mutagen
  · 探测统一走 ffprobe -of json
  · 峰值走 ffmpeg 解码 + astats，按文件哈希缓存
"""
from __future__ import annotations

import hashlib
import json
import math
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
            return res
        if f.tags is None:
            try:
                f.add_tags()
            except Exception as e:
                # AAC 之类根本不支持标签：必须报出来，不能假装写完
                res.error = f"{path.suffix} 不支持写入标签（{e}）"
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
                    if easy not in tags and fid in f.tags:
                        del f.tags[fid]
                        res.removed += 1
                if "comment" not in tags and "COMM" in f.tags:
                    del f.tags["COMM"]
                    res.removed += 1
            else:
                for k in list(f.tags or {}):
                    if CANON_KEYS.get(str(k).lower()) in UI_FIELDS and k not in tags:
                        try:
                            del f[k]
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


# ---------------------------------------------------------------- 完整性

def verify_flac(path: Path) -> runner.Result:
    """flac -t 完整性校验（可选项 §5）。"""
    flac = toolchain.path_of("flac")
    if not flac:
        raise RuntimeError("flac 不可用")
    return runner.run([flac, "-t", "-s", str(path)], timeout=config.TASK_TIMEOUT)
