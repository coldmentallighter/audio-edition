"""运行配置与路径约束。

安全边界（对应需求 §5「命令参数白名单，避免命令注入」）：
  · 用户永远不能指定输出路径，只由系统在 outputs/ 下生成
  · 所有落盘路径必须经 safe_join 收敛到受管目录内，杜绝目录穿越
  · 上传只允许白名单内的扩展名
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# 项目根 = 本文件的上上级（backend/ 的父目录）
ROOT = Path(__file__).resolve().parent.parent

# 受管目录
UPLOADS = ROOT / "uploads"
OUTPUTS = ROOT / "outputs"
CACHE = ROOT / ".cache"
DB_PATH = ROOT / "audioedition.db"

# 音频扩展名白名单（需求 §4.2 的常见格式互转）
AUDIO_EXT = {
    ".flac", ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif", ".wma",
}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024      # 单文件 4GB
MAX_BATCH_FILES = 500                           # 单次批量上限

# 任务
MAX_CONCURRENCY = 2                             # 并发数上限（需求：不阻塞 WebUI）
TASK_TIMEOUT = 60 * 60                          # 单任务超时 1 小时

HOST = os.environ.get("AE_HOST", "127.0.0.1")
PORT = int(os.environ.get("AE_PORT", "8765"))

# 启动时是否清空工作区（需求：每次打开都是空页面）。
# 想保留上一轮的文件做调试，就设 AE_FRESH=0。
FRESH_ON_START = os.environ.get("AE_FRESH", "1").strip().lower() not in ("0", "false", "no", "off")

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def ensure_dirs() -> None:
    for d in (UPLOADS, OUTPUTS, CACHE):
        d.mkdir(parents=True, exist_ok=True)


def sanitize_name(name: str, fallback: str = "untitled") -> str:
    """把任意字符串收敛成安全的单段文件名（不含路径分隔符）。"""
    name = unicodedata.normalize("NFC", str(name or ""))
    # 只取最后一段，丢掉任何目录成分（浏览器可能传 webkitRelativePath）
    name = name.replace("\\", "/").split("/")[-1]
    name = _ILLEGAL.sub("_", name).strip(" .")
    if not name:
        return fallback
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.upper() in _RESERVED:
        stem = f"_{stem}"
    stem = stem[:120] or fallback
    return f"{stem}.{ext}" if ext else stem


def sanitize_relpath(rel: str) -> Path:
    """把上传时的相对路径（可含子目录）转成安全的相对 Path。

    逐段清洗，丢弃 . / .. 与绝对成分，保证结果一定落在目标目录内部。
    """
    rel = unicodedata.normalize("NFC", str(rel or ""))
    rel = rel.replace("\\", "/")
    parts: list[str] = []
    for seg in rel.split("/"):
        seg = seg.strip()
        if not seg or seg in (".", ".."):
            continue
        seg = _ILLEGAL.sub("_", seg).strip(" .")
        if seg:
            parts.append(seg[:120])
    return Path(*parts) if parts else Path(sanitize_name(rel))


def safe_join(base: Path, rel: str | Path) -> Path:
    """把 rel 解析到 base 内部；任何越界都会被拒绝。"""
    base = base.resolve()
    cand = (base / sanitize_relpath(str(rel))).resolve()
    if base != cand and base not in cand.parents:
        raise ValueError(f"路径越界: {rel}")
    return cand


def prune_empty_dirs(start: Path, stop: Path) -> int:
    """从 start 往上删空目录，直到 stop（不含 stop）。

    删文件不会带走目录：上传过 covers/ 或 子目录/ 之后即使文件全删了，
    目录还留在 uploads/ 里，`Get-ChildItem -File` 看不见但确实占着位置。
    """
    stop = stop.resolve()
    removed = 0
    cur = start.resolve()
    while cur != stop and stop in cur.parents:
        try:
            if any(cur.iterdir()):
                break
            cur.rmdir()
            removed += 1
        except OSError:
            break
        cur = cur.parent
    return removed


def unique_path(p: Path) -> Path:
    """若已存在则追加 -1 / -2 …，绝不覆盖已有文件。"""
    if not p.exists():
        return p
    stem, suf = p.stem, p.suffix
    for i in range(1, 10000):
        q = p.with_name(f"{stem}-{i}{suf}")
        if not q.exists():
            return q
    raise RuntimeError(f"无法为 {p.name} 找到可用文件名")


def is_audio(p: Path) -> bool:
    return p.suffix.lower() in AUDIO_EXT


def is_image(p: Path) -> bool:
    return p.suffix.lower() in IMAGE_EXT


def wipe_workspace() -> dict:
    """清空工作区：uploads/ outputs/ .cache/ 的内容 + 库里的文件与任务记录。

    需求：「每次打开都是空页面」。导入是**复制**进 uploads/ 的，
    所以清掉的只是工作副本，用户磁盘上的原文件不受影响。

    在 lifespan 里、任何请求进来之前调用，保证页面首帧一定是空的。
    """
    import shutil as _shutil

    removed = {"uploads": 0, "outputs": 0, "cache": 0}
    for key, d in (("uploads", UPLOADS), ("outputs", OUTPUTS), ("cache", CACHE)):
        if not d.exists():
            continue
        for child in d.iterdir():
            try:
                if child.is_dir():
                    _shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
                removed[key] += 1
            except OSError:
                # 文件被别的进程占用（如播放器）不该拖垮启动
                pass
    return removed


def reset_db() -> None:
    """删掉数据库文件，让 store 重新建一张空表。

    比 DELETE FROM 更干净：连自增序列、WAL 里的历史一起清掉。
    """
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


@dataclass(frozen=True)
class ServerInfo:
    root: Path = ROOT
    uploads: Path = UPLOADS
    outputs: Path = OUTPUTS
    host: str = HOST
    port: int = PORT

    def as_dict(self) -> dict:
        return {
            "root": str(self.root),
            "uploads": str(self.uploads),
            "outputs": str(self.outputs),
            "host": self.host,
            "port": self.port,
            "maxUploadBytes": MAX_UPLOAD_BYTES,
            "maxBatchFiles": MAX_BATCH_FILES,
            "maxConcurrency": MAX_CONCURRENCY,
            "freshOnStart": FRESH_ON_START,
            "audioExt": sorted(AUDIO_EXT),
            "imageExt": sorted(IMAGE_EXT),
        }


SERVER = ServerInfo()
