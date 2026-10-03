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
# 一条执行链最多几步。12 个内置 op 排两轮都用不到 20，
# 给 32 是"够用且能挡住手滑/脚本灌进来的畸形链"。
MAX_CHAIN_STEPS = 32

# 任务
#
# **并发数上限**（需求：不阻塞 WebUI）。可用 `AE_CONCURRENCY` 覆盖：
# 文件级流水线的收益与 slot 数正相关（一个任务只等**它自己那个文件**的上一步，
# 所以 slot 越多、能同时推进的文件越多），值得让人按机器调。
#
# 上限 16 是**故意压住**的：这不是"越高越好"的旋钮 —— 每个 slot 都可能起一个
# ffmpeg 进程，8 个 slot 同时转码就能把一台普通机器打满，然后**整机**（含 WebUI）
# 一起卡，而那正是这条需求要防的事。真要跑极限请改代码，别指望一个环境变量。
MAX_CONCURRENCY = max(1, min(16, int(os.environ.get("AE_CONCURRENCY", "2") or 2)))
TASK_TIMEOUT = 60 * 60                          # 单任务超时 1 小时

# 长任务**最多占几个 worker**（`0` = 不限制）。
#
# 为什么需要：`normalize`（loudnorm 两遍法）与 `zip` 这类任务一跑就是几十秒到十几
# 分钟。默认 2 个 slot 时，两个长任务一起上就把队列占满 —— 期间**短任务全部排队**，
# 界面看着像卡死（进度条不动、点什么都要等）。留一个 slot 给短任务，页面就一直是
# "有反应"的：短任务穿插跑完，长任务用剩下的槽位慢慢推。
#
# 默认值是 `max(1, 并发数 - 1)`，但**要在 `Queue` 里按实际 worker 数算**，
# 不能在这里按 `MAX_CONCURRENCY` 烤死 —— 测试会 `Queue(workers=4)`，
# 烤死的话它只拿到 1 个长任务槽（本该 3 个），而"限制生效了"这件事
# 看起来完全正常，只有对比并发数才发现不对。
# 所以这里只在**显式设了 `AE_LONG_SLOTS`** 时给一个数，否则是 `None`
# （= "按 n-1 推导"）：
#   · 并发 2 → 长任务最多 1 个（留 1 个给短任务）
#   · 并发 1 → 限制自动失效（只有 1 个 slot，"留一个"等于不让长任务跑，
#     那是死锁级错误）
#   · 设 0 → 关掉这条限制
_LONG_SLOTS_RAW = os.environ.get("AE_LONG_SLOTS")
LONG_TASK_SLOTS: int | None
if _LONG_SLOTS_RAW is None or str(_LONG_SLOTS_RAW).strip() == "":
    LONG_TASK_SLOTS = None                     # → `Queue` 按 `max(1, n-1)` 算
else:
    try:
        LONG_TASK_SLOTS = max(0, int(_LONG_SLOTS_RAW))
    except ValueError:
        LONG_TASK_SLOTS = None

# "长任务"的判定：按**任务类型**列出来，不猜时长 ——
# 时长要在跑的过程中才知道，而准入必须在**开始之前**做决定。
# 名单来源（都是"要完整读一遍文件"的量级）：
#   · `normalize` —— loudnorm 两遍法 = 完整解码两趟
#   · `waveform`  —— 整曲解码画波形
#   · `loudness`  —— 整曲 ebur128（三个响度 op 共用这个类型）
#   · `zip`       —— 等全批 + 压缩，可能几十个文件
#   · `verify`    —— 整曲重新解码算校验值
# 不在名单里的（`convert` / `tags` / `cover` / `probe` / `peaks` …）都能在
# 秒级跑完，本来就该优先占槽 —— 把短任务也限住就失去意义了。
LONG_TASK_TYPES = frozenset({"normalize", "zip", "waveform", "loudness", "verify"})

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
            # 长任务槽位（§3.5）。`0` = 不限制；`None` 在这里已经解析成具体数字，
            # 让前端/探针读到的就是**实际生效**的值，而不是"看情况"。
            "longTaskSlots": (LONG_TASK_SLOTS if LONG_TASK_SLOTS is not None
                              else max(0, MAX_CONCURRENCY - 1)
                              if MAX_CONCURRENCY > 1 else 0),
            "longTaskTypes": sorted(LONG_TASK_TYPES),
            "freshOnStart": FRESH_ON_START,
            "audioExt": sorted(AUDIO_EXT),
            "imageExt": sorted(IMAGE_EXT),
        }


SERVER = ServerInfo()
