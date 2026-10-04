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

from backend import config, drp, runner
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


def chapters(path: Path) -> list[dict]:
    """读音频**自带的章节**（= 图上的 marker）。

    老板："marker 音频自己会带，有则加载，无则不需要。"

    `ffprobe -show_chapters` 一条路通吃三种真实来源（本机 ffmpeg 9.0.2 实测）：

    | 来源 | 结果 |
    |---|---|
    | Vorbis 注释 `CHAPTER001=00:00:00.000` + `CHAPTER001NAME=Intro` | 带名字 |
    | FLAC `CUESHEET`（`metaflac --import-cuesheet-from`） | **名字是空的** |
    | WAV 的 `cue `/`LIST adtl`（DAW 导出） | 名字是中文的，如 `标记 0` |

    三条口径：

    1. **取每一章的起点**，不取终点 —— 章节首尾相接时取终点会把同一时刻标两遍。
    2. 没有名字就用 `chart_layout.MARKER_DEFAULT_NAME`（`Marker`）。这不是锦上添花：
       CUESHEET 那条路给不出名字，没有兜底就会画出一排空标签。
    3. **没有章节就返回 `[]`** —— 那是常态（本机 `uploads/` 里 5 个 flac 一个都没有），
       不是异常，不要抛错、也不要在时间带上占空行。
    """
    from backend import chart_layout as _layout                    # noqa: PLC0415

    if not path.exists():
        return []
    try:
        ffprobe = toolchain.require("ffprobe")
    except Exception:                                              # noqa: BLE001
        return []

    r = runner.run([ffprobe, "-v", "error", "-show_chapters", "-of", "json",
                    str(path)], timeout=30)
    if not r.ok:
        return []
    try:
        raw = json.loads(r.stdout or "{}").get("chapters") or []
    except json.JSONDecodeError:
        return []

    out: list[dict] = []
    for c in raw:
        try:
            t = float(c.get("start_time"))
        except (TypeError, ValueError):
            continue
        name = str((c.get("tags") or {}).get("title") or "").strip() \
            or _layout.MARKER_DEFAULT_NAME
        out.append({"time": round(t, 3), "name": name})
    out.sort(key=lambda m: m["time"])
    return out


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
    res = TagWriteResult()
    if not path.exists():
        res.error = "文件不存在"
        return res

    # ⚠ **和 read_tags 对称地做一次键名归一化**。
    #
    # read_tags 走 CANON_KEYS，把 `track` / `TRACKNUMBER` / … 一律归到
    # `tracknumber`；write_tags 原来只做了 `k.lower()` —— 前端发的键名只要不是
    # UI_FIELDS 里那一个，后果按容器分成两类，**都很糟**：
    #
    #   · FLAC 走 `_write_tags_metaflac`，那里 `for k in UI_FIELDS` 遍历的是
    #     **白名单**，多出来的 `track` 一条 `--set-tag` 都不会生成，但函数照样
    #     `return res`（written=0、error=""），`h_tag_edit` 拿到 r.ok=True 报成功
    #     —— **用户以为写进去了，实际一个字没改**。若同时 clear_missing=True，
    #     还会执行 `--remove-tag=TRACKNUMBER` 把已有的音轨号删掉。
    #   · MP3 / M4A / WAV 走 mutagen，`f["track"] = [...]` —— easy 模式不认
    #     `track`（标准名是 `tracknumber`），抛 ValueError 被 catch 成 failed。
    #
    # 用 CANON_KEYS 归一是为了**和读端共用同一张表** —— 两边各写一份，
    # 早晚会分叉（读认得、写不认得，或反过来）。
    clean: dict[str, str] = {}
    for k, v in tags.items():
        if v is None:
            continue
        key = str(k).strip().lower()
        clean[CANON_KEYS.get(key, key)] = str(v)

    if path.suffix.lower() == ".flac":
        return _write_tags_metaflac(path, clean, clear_missing)
    return _write_tags_mutagen(path, clean, clear_missing)


def _write_tags_metaflac(path: Path, tags: dict[str, str], clear_missing: bool) -> TagWriteResult:
    mf = toolchain.path_of("metaflac")
    res = TagWriteResult(method="metaflac")
    unknown = [k for k in tags if k not in UI_FIELDS]
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

# 缓存版本。**改动响度数据的含义/单位时就必须 +1**，否则旧缓存会继续供出旧值
# —— `file_key()` 只看路径+大小+mtime，内容没变就命中缓存，代码改了它不知道。
#
# v2: 修了 truePeak / samplePeak 的单位（线性幅度 → dB）、修了 LRA 取到伪值 20、
#     新增 dra 与 samplePeakMax、eBur128 参数 peak=true → peak=sample+true。
#     v1 缓存里那些 "truePeakMax: 0.1" 必须作废。
CACHE_VERSION = 5

#: ebur128 的帧率。时间线、DRP 的窗/步都以它为单位换算。
EBUR_HZ = 10

#: 削波段落的合并间隔（秒）。逐帧峰值是 **10fps（一帧 0.1s）**，一次削波里夹一帧
#: 没顶满就会断成两段 —— 所以取**正好一帧**。
#:
#: 为什么从 0.5 降到 0.1（老板 2026-10 报"爆音标红是个地图炮：一有就开始标，一标就
#: 从头标到尾"）。两个素材各扫一遍，逐帧 `Peak_level >= 0`：
#:
#: | 合并 | INFinite - Stellar.flac（159.6s，raw 99 段） | ariiol - REK421.flac（215.8s，raw 260 段） |
#: |---|---|---|
#: | 0 / 0.05 / **0.1** | 99 段 / 70.8s / 最长 6.7s | 260 段 / 121.5s / 最长 11.6s |
#: | 0.25 | 46 段 / 81.4s / 最长 **32.0s** | 101 段 / 153.3s / 最长 **32.4s** |
#: | 0.5 | 9 段 / 94.5s / 最长 **86.2s** | 32 段 / 177.3s / 最长 33.1s |
#: | 1.0 | 7 段 / 96.2s / 最长 88.2s | 8 段 / 195.0s / 最长 **101.2s** |
#:
#: **0 → 0.1 在两个素材上都逐段完全相同** ⇒ 0.1 只会吸收"一帧没顶满"，绝不会把分开的
#: 爆音糊成一段。0.25 起长段就爆到 32s，0.5 直接把 Stellar 的 99 段糊成 9 段、最长
#: 86.2s —— 那正是老板看到的"一标就标到尾"。（0.5 当初是照 REK421 一首调的，只看了
#: 段数没看**最长段**，于是把"糊得狠"当成了"合得干净"。）
CLIP_MERGE_GAP = 0.1


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
          -af "ebur128=peak=sample+true:framelog=verbose:metadata=true,
               ametadata=mode=print:file=ebur-meta.txt" -f null -

    `peak=sample+true` 不能写成 `peak=true` —— 后者**不输出** `sample_peak`（见下面
    `_EBUR_KEYS` 那段实测）。单趟就能拿到全部 6 项响度指标，不需要额外跑 `astats`。

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
    # 逐帧峰值那一趟的元数据文件（同样每个任务唯一）
    peak_suffix = (f"peak-meta-{tag}.txt" if tag
                   else f"peak-meta-{os.getpid()}.txt")
    peak_meta = config.CACHE / peak_suffix
    peak_meta.unlink(missing_ok=True)

    # 注意 `file=` 只给**文件名**，路径由 cwd 提供（见上面第 1 条）
    #
    # `peak=sample+true` 而不是 `peak=true`：实测（tests/dsh-wheel/verify_units.py）
    # `peak=true` **根本不输出** `lavfi.r128.sample_peak`，只有 `true_peak`；
    # `peak=sample` 则反过来。要同时拿到采样峰值与真峰值必须写 `sample+true`，
    # 这也是"不用另跑一趟 astats"的前提。
    r = runner.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats",
        # `-i` 必须给**绝对路径**：这一趟的 `cwd` 被设成了 `.cache`（上一段那条理由），
        # 相对路径会被解析到 `.cache/` 下面去，报 "No such file or directory"。
        # 实测踩过：`loudness_timeline(Path("uploads/x.flac"))` 直接失败，
        # 而 App 里传的一直是绝对路径，所以这个坑只在脚本/测试里露头。
        "-i", str(Path(path).resolve()),
        "-map", "0:a:0",
        # 两趟元数据用**两个** `ametadata` 实例、写两个文件：
        #
        #  · `ebur128` —— 逐帧响度（10Hz）+ 汇总值。它的 `true_peak` / `sample_peak`
        #    是**到当前为止的最大值**（实测：序列单调不减），所以**不能**拿来定位
        #    "哪一刻爆音"——那样会从第一次越线起把整首标红。
        #  · `astats` —— **逐帧** `Peak_level`（dBFS，按音频帧 ~46.8fps）。这才是能
        #    定位削波时刻的数据源。
        #
        # 两趟在**同一次解码**里做完，没有额外解一遍音频。`measure_perchannel=none`
        # 把输出从 27MB 压到 0.8MB（只留 Overall.Peak_level）。
        "-af", ("ebur128=peak=sample+true:framelog=verbose:metadata=true,"
                f"ametadata=mode=print:file={suffix},"
                "astats=metadata=1:reset=1:measure_perchannel=none"
                ":measure_overall=Peak_level,"
                f"ametadata=mode=print:file={peak_suffix}"),
        "-f", "null", "-",
    ], timeout=config.TASK_TIMEOUT, cwd=config.CACHE)

    if not r.ok:
        raise RuntimeError(f"响度分析失败: {r.stderr_summary}")

    text = peak_text = ""
    for f, keep in ((meta, False), (peak_meta, True)):
        try:
            got = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            got = ""
        finally:
            f.unlink(missing_ok=True)     # 中间文件不留着
        if keep:
            peak_text = got
        else:
            text = got

    data = parse_ebur_metadata(text)
    if not data["t"]:
        raise RuntimeError("ebur128 没有产出任何帧（可能没有音频流）")
    data.update(parse_peak_metadata(peak_text))

    info = probe(path)
    payload: dict[str, Any] = {
        "version": CACHE_VERSION,
        "key": file_key(path),
        "duration": info.duration,
        "sampleRate": info.sample_rate,
        "channels": info.channels,
        "hz": EBUR_HZ,
        "cached": False,
        **data,
    }
    try:
        cached.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    # ---- 削波时段：用**逐帧采样峰值**，不用 ebur128 那个 running max ----
    #
    # 判据：`Peak_level >= 0 dBFS` —— 采样值顶到满刻度，就是真削波。
    # ⚠ 与最初写的"TruePeak > 0 dB"有出入：**逐帧真峰值拿不到**（ebur128 只给
    # "到当前为止的最大值"，实测序列单调不减），拿它定位时段会把第一次越线之后的
    # 整首标红。真峰值仍在 `summary.truePeakMax` 与指标卡上，只是不再用来定位时段。
    clip = _merge_runs(payload.get("peakT") or [], payload.get("peak") or [],
                       lambda v: v >= 0.0)
    payload["summary"]["clipSeconds"] = round(sum(b - a for a, b in clip), 1)
    payload["summary"]["clipCount"] = len(clip)
    payload["summary"]["clips"] = [[round(a, 2), round(b, 2)] for a, b in clip]
    # 削波时段算完了再落一次缓存
    try:
        cached.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return payload


def _merge_runs(ts: list[float], vals: list[float], hit,
                gap: float = CLIP_MERGE_GAP) -> list[tuple[float, float]]:
    """把连续满足 `hit(v)` 的帧合并成时段，**间隔小于 `gap` 的段也并起来**。

    `hit` 是判据（例如 `lambda v: v >= 0.0`）。两个要点：

    * 用**真实时间戳**而不是下标 —— 时间线并不从 0 开始（`S` 有 3 秒窗口预热，
      那几帧是静音底，解析时就丢了）。
    * **必须再并一次**：逐帧峰值是 46.8fps，一次削波里夹一两帧没顶满就会断成两段。
      实测 `ariiol - REK421.flac` 不并是 **260 段、绝大多数只有 21ms**；并成
      0.25s 以上才算"一个削波段落"，图上才是几段粗红线而不是一把梳子。
    """
    runs: list[tuple[float, float]] = []
    start: int | None = None
    for i, v in enumerate(vals):
        if hit(v):
            if start is None:
                start = i
        elif start is not None:
            runs.append((ts[start], ts[i - 1]))
            start = None
    if start is not None and ts:
        runs.append((ts[start], ts[-1]))

    merged: list[list[float]] = []
    for a, b in runs:
        if merged and a - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


# ametadata 输出里我们要的键 → 结果里的字段名。
# `lavfi.r128.M` 是 400ms 窗的瞬时响度，`S` 是 3s 窗的短时，`I` 是带门限的整合值。
_EBUR_KEYS = {
    "lavfi.r128.M": "M",
    "lavfi.r128.S": "S",
    "lavfi.r128.I": "I",
    "lavfi.r128.LRA": "LRA",
    # LRA.low / LRA.high 是那个拐点的上下界。**不能只信 LRA 这一条序列**：
    # 实测（见 tests/dsh-wheel/debug_parse.py）常量正弦的 LRA 序列里会夹着成对的
    # 伪值 20.000 —— `[... 0, 0, 20.000, 20.000, 20.000, 20.000, 0, 0 ...]`，
    # 而同一帧的 low/high 都是 -41.080（真值 0.0）。取"末值"或"最大非零值"都会
    # 拿到那个 20。用 high - low 反算才对得上 ebur128 自己 stderr Summary 的 LRA。
    "lavfi.r128.LRA.low": "LRAlow",
    "lavfi.r128.LRA.high": "LRAhigh",
    "lavfi.r128.true_peak": "truePeak",
    # 也是线性幅度。逐帧看它没意义（每帧重复同一个累计汇总值），
    # 只在 `parse_ebur_metadata` 里取最大值当汇总用 —— 见 summary.samplePeak。
    "lavfi.r128.sample_peak": "samplePeak",
}

# ⚠ `lavfi.r128.true_peak` / `sample_peak` 是**线性幅度**，不是 dB。
#
# 实测（ffmpeg 9.0.2，997Hz 正弦，48kHz pcm_s24le，见 tests/dsh-wheel/verify_units.py）：
#
#     输入电平    ametadata 值    ebur128 stderr    20*log10(值)
#     -20 dB      0.009          -41.1 dBFS        -40.92
#     -10 dB      0.028          -31.1 dBFS        -31.06
#       0 dB      0.088          -21.1 dBFS        -21.11
#
# 三个点严格成 10^(-dB/20)，**确认是线性幅度**。而 ebur128 写在 stderr 的 Summary
# 里那一份是已经转好的 dBFS。
#
# 踩过的坑：以前直接把元数据值当 dBTP 用，于是一首 -21.1 dBFS 的素材会报
# `truePeakMax = 0.1"dBTP"`、`PLR = 21.2`（真值 0.0）。**而且横轴"爆音处
# TruePeak > 0 dB"的判定会永远不触发** —— 线性幅度取到的永远是 0~1。
#
# 这里用 20*log10 自己转，而不是去解析 stderr：`runner.run` 目前不保留 stderr 全量，
# 且 Summary 的格式随 ffmpeg 版本变；而 AVOption 字段名是稳定的。
_LINEAR_FLOOR = 10.0 ** (SILENCE_LUFS / 20.0)      # 1e-6，别再低了


def _to_db(linear: float) -> float:
    """线性幅度 → dB。0 或负值（= 没测到）钳到 `SILENCE_LUFS` 底。"""
    if linear <= _LINEAR_FLOOR:
        return SILENCE_LUFS
    return 20.0 * math.log10(linear)


#: astats 逐帧峰值那一趟的键名。
_ASTATS_PEAK = "lavfi.astats.Overall.Peak_level"


def parse_peak_metadata(text: str) -> dict:
    """解析 astats 那一趟 → **逐帧采样峰值**（dBFS），带自己的时间轴。

    为什么要它：ebur128 的 `true_peak` / `sample_peak` 是**到当前为止的最大值**
    （实测序列单调不减），拿它判"哪一刻爆音"会把第一次越线之后的整首标红。
    `astats=metadata=1:reset=1` 给的才是逐帧值。

    ⚠ 时间基与 ebur128 那趟**不一样**：ebur128 每 100ms 出一个点（10Hz），
    astats 每个音频帧出一个（1024 样本 @48k ≈ 46.8Hz）。所以这里返回独立的
    `peakT`，别跟 `t` 混用。
    """
    ts: list[float] = []
    vals: list[float] = []
    cur_t: float | None = None
    for line in text.splitlines():
        line = line.strip()
        # ⚠ `pts_time` **不在行首**：那一行长这样
        #     `frame:0    pts:0       pts_time:0`
        # 所以必须用 `in` 判断（第一版写成 `startswith("pts_time:")`，
        # 结果一帧都解析不出来 —— peak 序列长度 0，图上永远没有爆音段）。
        if "pts_time:" in line:
            try:
                cur_t = float(line.split("pts_time:", 1)[1].strip())
            except ValueError:
                cur_t = None
        elif line.startswith(_ASTATS_PEAK + "=") and cur_t is not None:
            try:
                vals.append(round(float(line.split("=", 1)[1]), 2))
                ts.append(round(cur_t, 3))
            except ValueError:
                pass
    return {"peakT": ts, "peak": vals}


def _percentile(values: list[float], pct: float) -> float:
    """线性插值百分位。`pct` 取 0~100。空列表返回 0.0。

    用线性插值而不是"取第 k 个"，是为了让结果随序列连续变化 —— 直接取序数
    在 10Hz 的 3 秒窗序列上会跳变（相邻两帧就可能跨过一个整数序号）。
    """
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * max(0.0, min(100.0, pct)) / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[int(pos)]
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


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

    **单位**（这条是踩过坑的，改之前先看 `_to_db` 上面那段实测）：
    `lavfi.r128.true_peak` 是**线性幅度**，不是 dBTP。返回的 `truePeak` 序列与
    `summary.truePeakMax` 都已经转成 dB，可以直接当 dBTP 用。
    `M` / `S` / `I` / `LRA` 本来就是 LUFS/LU，不动。

    返回的 `summary` 里现在有 9 项：`integrated` / `lra` / `dra` / `plr` /
    `momentaryMax` / `shortTermMax` / `truePeakMax` / `samplePeakMax`。
    没有 `samplePeak` 逐帧序列 —— `ametadata` 每帧重复打印同一个累计汇总值，
    存成序列纯属浪费，所以它只出现在 `summary` 里。
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

    def _max_linear(name: str) -> float:
        """线性幅度序列取最大值（**不**和 `SILENCE_LUFS` 比 —— 那是 dB 的门槛）。

        全部无效（序列为空 / 全是 0）时返回 0.0，交给 `_to_db` 钳到底。
        """
        vals = [x for x in series.get(name, []) if x > 0.0]
        return max(vals) if vals else 0.0

    integrated = round(series["I"][-1], 1) if series["I"] else 0.0
    # ⚠ 峰值的 max 必须**在转 dB 之前**做，而且要在**线性**序列上做。
    # 踩过：先 `[round(_to_db(x), 2) for x in series["truePeak"]]` 再 `_max`，
    # 等于在**已四舍五入的 dB 值**上排名 —— 而且序列里混着静音底的 -120，
    # `_max` 拿 `x > SILENCE_LUFS` 过滤（那是 dB 门槛）就把整条序列滤空了，
    # 结果 `truePeakMax` 变成 0.0。线性序列要用 `_max_linear`。
    true_peak = round(_to_db(_max_linear("truePeak")), 1)
    m_max, s_max = _max("M"), _max("S")

    # LRA：用最后一帧的 high − low 反算，**不从 LRA 序列里取值**。
    # 理由见 `_EBUR_KEYS` 里那段 —— LRA 序列夹着 20.000 的伪值。
    # 末帧而不是全序列，因为 low/high 是随门限积累逐步收敛的累计量。
    lra = 0.0
    if series["LRAlow"] and series["LRAhigh"]:
        lra = round(series["LRAhigh"][-1] - series["LRAlow"][-1], 1)

    # DRA = 短时响度（3s 窗）序列的 P95 − P10，单位 LU。
    #
    # 与 LRA 的区别：LRA 由 libebur128 按 EBU Tech 3342 算，带**相对门限**
    # （低于"整体响度 −10 LU"的段落不计入）；DRA 这里是**不带门限**的朴素
    # 百分位散布，口径更直白，数值通常略大于 LRA。两者**不可互换**，所以都留着。
    #
    # 判据用 `SILENCE_LUFS` 底（而不是像 LRA 那样卡 > 0）：短时窗是 3 秒，
    # 开头的 -120.691 是"窗还没填满"的产物，本来就该按底噪算，正好落在 P10 以下
    # 被百分位自然排除，不影响 P95。
    s_vals = [x for x in series.get("S", []) if x > SILENCE_LUFS]
    dra = round(_percentile(s_vals, 95) - _percentile(s_vals, 10), 1) if s_vals else 0.0

    # 采样峰值（dBFS）。同样是线性幅度 → dB。
    # `peak=sample+true` 时 ebur128 每帧都会带上它，所以**不需要另跑一趟 `astats`**
    # （AI 那份测量文档第 2.5 节建议额外跑 astats，实测是多余的：
    #  同一 24-bit 文件 ebur128 的 sample_peak 与 astats 的 Peak level 一致）。
    sample_peak = round(_to_db(_max_linear("samplePeak")), 1)

    out = {
        "t": [round(x, 2) for x in t],
        # M 保持 1 位：它只用来画包络/取峰值，0.1 LU 足够，而 10Hz 下密集存储很贵。
        "M": [round(x, 1) for x in series["M"]],
        # ⚠ S 保留 **2 位**，不放宽不行。短时响度是"动态模式"（DRP）那条算法的
        # 基础曲线，要**求导**。实测（tests/dsh-wheel/probe_drp_span.py）：
        # 1 位小数在 10Hz 上做中心差分（除 0.2s），0.1 LU 的台阶变成 0.5 LU/s 一格，
        # 整条曲线的 |dS/dt| **只有 13 个不同取值** —— 撑不起"行为特征相近"的判定。
        # 放到 2 位后台阶变 0.05 LU/s，取值数上一档。
        # 代价：缓存 JSON 变大（实测这条 2325 帧的素材 +约 9KB）。
        "S": [round(x, 2) for x in series["S"]],
        "I": [round(x, 1) for x in series["I"]],
        # dB，不是线性幅度（见上面 `_to_db` 那段实测）
        "truePeak": [round(_to_db(x), 2) for x in series["truePeak"]],
        "summary": {
            "integrated": integrated,          # INTEGRATED (LUFS)
            "lra": lra,                        # LOUDNESS RANGE (LU)
            "dra": dra,                        # 平均动态 P95−P10 (LU)
            # AVERAGE DYNAMICS (PLR) = I − true_peak。报告 §1.3 用它做过自洽性校验
            "plr": round(abs(integrated - true_peak), 1),
            "momentaryMax": m_max,
            "shortTermMax": s_max,
            "truePeakMax": true_peak,          # dBTP
            "samplePeakMax": sample_peak,      # dBFS
        },
        "frames": len(t),
    }

    # ---- 动态模式（DRP）：接线进 summary ----
    #
    # 放在最后：它比前面几项贵得多（8s 窗 1s 步滑过全曲 + 凝聚式聚类），而且
    # **结果要进缓存** —— 所以只在这一趟里算一次，命中缓存就不再算。
    # 阈值（窗 8s / 步 1s / 容差 0.75 LU / 0.15 LU/s）只在一首素材上调过，
    # 见 tests/dsh-wheel/README.md 的 "Measured vs assumed"。
    #
    # ⚠ 这里**不能静默吞异常**。第一版写的是 `except Exception: pats = []`，而
    # `out["hz"]` 当时根本不存在（`hz` 是外层 payload 才加的）—— KeyError 被吞掉，
    # 结果"这首歌没有动态模式"，看起来像算法结论，其实是接线错误。
    # 现在失败会把原因写进 `summary.drpError`，一眼能看见。
    drp_err = ""
    try:
        pats = drp.patterns(out["S"], out["t"], EBUR_HZ)
    except Exception as e:                                     # noqa: BLE001
        pats, drp_err = [], f"{type(e).__name__}: {e}"
    pmax, pmin = drp.extremes(pats)
    occ = [{"start": o["start"], "end": o["end"], "pattern": int(p["id"][3:])}
           for p in pats for o in p["occurrences"]]
    occ.sort(key=lambda o: o["start"])
    out["summary"].update({
        # 卡片上直接显示的三条（已经是给人看的字符串，渲染器不再加工）。
        # 措辞取短：动态卡只有 200pt 宽，标签「动态模式 DRP」就占掉 ~75pt，
        # 值只剩 ~95pt —— `4 个模式 / 10 次出现`那种长句会被截掉。
        "drp": (f"{len(pats)} 模式 / {len(occ)} 次" if pats else "—"),
        "pmax": (f"{pmax['id']} · {pmax['dr']:.2f} LU" if pmax else "—"),
        "pmin": (f"{pmin['id']} · {pmin['dr']:.2f} LU" if pmin else "—"),
        # 数值版，给测试与后续统计用
        "drpCount": len(pats),
        "drpOccurrenceCount": len(occ),
        # 时间带画 `PT_X` 行用；空列表 = 不出那一行
        "drpOccurrences": occ,
        # 空字符串 = 正常。非空说明 DRP 那一步炸了（不要静默）
        "drpError": drp_err,
    })
    return out


# ================================================================ 字体
#
# ⚠ 这里**只剩字体挑选**了。`render_loudness_png` + `AXIS_Y` + `LOUD_COLORS` +
# `PNG_*` 那整套 **2026-10 已退役** —— 图改成 SVG（`backend/loudness_svg.py`），
# 版式与纵轴分别读 `backend/chart_layout.py` 与 `backend/chart_axis.py`。
# 退役理由：① 那个 `AXIS_Y` 来自已作废的逆向文档（`-13 → -54`、`-23~-27` 放大 2.9 倍），
# 实测真值是 `0 → -54` 线性；② 它的版面（8 张 footer 卡、含 PLR 与两个 DIAL）按新规格
# 是作废设计；③ 两个渲染器必然漂移。要找回旧实现：`git log -- backend/audio.py`。
#
# 保留字体助手是因为 `tests/dsh-wheel/axis_options.py` 的对比图还在用 `_pick_font`。

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
      "**LRA** 是响度范围（LU，EBU Tech 3342 带门限）；"
      "**DRA** 是平均动态（LU，短时响度的 P95−P10，**不带门限**）；"
      "**PLR** 是平均动态（`|I − 真峰值|`）；"
      "**瞬时/短时**分别是 400ms / 3s 窗的最大值；"
      "**真峰值**按 BS.1770 做 4 倍过采样，**采样峰值**不过采样。")
    A("")
    A("> `LRA` 与 `DRA` 是**两个不同的量**，不要互换：前者由 libebur128 按 EBU "
      "Tech 3342 算，会剔除低于「整体响度 −10 LU」的段落；后者是朴素的百分位散布。"
      "同一条素材两者数值可能接近，但口径不同。")
    A("")

    # ---------------- 汇总表 ----------------
    A("## 汇总")
    A("")
    A("| # | 文件 | 时长 | Integrated | LRA | DRA | PLR "
      "| 瞬时峰值 | 短时峰值 | 采样峰值 | 真峰值 | DRP | PMAX | PMIN | 判定 |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, e in enumerate(entries, 1):
        d = e.get("data") or {}
        s = d.get("summary") or {}
        verdict = _loudness_verdict(s.get("integrated"), ref_lufs)
        A(f"| {i} | {_md_cell(e.get('name'))} | {_mmss(d.get('duration'))} "
          f"| {_num(s.get('integrated'))} LUFS | {_num(s.get('lra'))} LU "
          f"| {_num(s.get('dra'))} LU "
          f"| {_num(s.get('plr'))} | {_num(s.get('momentaryMax'))} "
          f"| {_num(s.get('shortTermMax'))} "
          f"| {_num(s.get('samplePeakMax'))} dBFS "
          f"| {_num(s.get('truePeakMax'))} dBTP "
          f"| {_md_cell(s.get('drp') or '—')} "
          f"| {_md_cell(s.get('pmax') or '—')} "
          f"| {_md_cell(s.get('pmin') or '—')} "
          f"| {verdict} |")
    A("")
    A("> `DRP` 是动态模式的「模式数 / 出现次数」；`PMAX` / `PMIN` 是动态范围最大 / "
      "最小的那个模式的编号与它的 DR（LU）。没有检测到模式时三者都是 `—`。")
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
              f"LRA **{_num(s.get('lra'))} LU**　·　DRA **{_num(s.get('dra'))} LU**　·　"
              f"PLR **{_num(s.get('plr'))}**")
            A(f"- 瞬时峰值 **{_num(s.get('momentaryMax'))}**　·　"
              f"短时峰值 **{_num(s.get('shortTermMax'))}**　·　"
              f"采样峰值 **{_num(s.get('samplePeakMax'))} dBFS**　·　"
              f"真峰值 **{_num(s.get('truePeakMax'))} dBTP**")
            A(f"- 动态模式 DRP **{_md_cell(s.get('drp') or '—')}**　·　"
              f"PMAX **{_md_cell(s.get('pmax') or '—')}**　·　"
              f"PMIN **{_md_cell(s.get('pmin') or '—')}**")
            if s.get("drpError"):
                # 不静默：DRP 那一步炸了就说清楚，否则"没检测到模式"会被当成算法结论
                A(f"  > ⚠ DRP 计算失败：`{_md_cell(s['drpError'])}`")
            if s.get("drpOccurrences"):
                A("")
                A(f"**动态模式的每一次出现**（共 {len(s['drpOccurrences'])} 段）：")
                A("")
                for o in s["drpOccurrences"]:
                    A(f"- `PT_{o['pattern']}`　{_mmss(o['start'])} → {_mmss(o['end'])}")
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
