"""运行配置与路径约束。

安全边界（对应需求 §5「命令参数白名单，避免命令注入」）：
  · 用户永远不能指定输出路径，只由系统在 outputs/ 下生成
  · 所有落盘路径必须经 safe_join 收敛到受管目录内，杜绝目录穿越
  · 上传只允许白名单内的扩展名
"""
from __future__ import annotations

import datetime
import os
import re
import time
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

# 「回收站」：**任何删除都先移到这里**，不再直接 unlink。
#
# 为什么改（2026-10 事故）：启动时的 `wipe_workspace()` 原本是 `unlink` / `rmtree`，
# 不进系统回收站。我为了跑一次冒烟测试起了一次 `backend.app`（忘了带 `AE_FRESH=0`），
# 结果把 `uploads/` 里 6 个音频（约 311MB，含一个刚导入的 DAW marker wav）
# 一次性永久删掉了。
#
# 现在的口径：`uploads/` / `outputs/` / `.cache/` / 数据库文件都**移**进这里，
# 保留原目录结构，出事了能捞回来。清理策略见 `prune_trash()`。
TRASH = ROOT / ".trash"

#: 回收站保留多少天。超过就真删（否则它自己会无限涨）。
TRASH_KEEP_DAYS = 30

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
    for d in (UPLOADS, OUTPUTS, CACHE, TRASH):
        d.mkdir(parents=True, exist_ok=True)


def move_to_trash(p: Path, *, bucket: str = "", reason: str = "") -> Path | None:
    """把 `p` **移**进回收站，返回落点；失败返回 `None`（绝不抛）。

    口径：

    * 保留目录结构 —— `uploads/专辑/歌.flac` → `.trash/uploads/专辑/歌.flac`，
      所以捞回来时一眼知道它原来在哪。
    * 同名冲突加时间戳后缀（`__20261004-175631`），不会覆盖回收站里的旧货。
    * `bucket` / `reason` 只写进 `.trash/_log.txt`，方便事后追"这是谁删的"。

    ⚠ 用 `shutil.move` 而不是 `os.rename`：跨盘（`%TEMP%` 或另一个分区）时
    rename 会抛 `OSError`。文件被播放器占着时 Windows 上也可能失败 —— 那种情况
    返回 `None`，调用方自己决定要不要退回 unlink。
    """
    import shutil as _shutil

    src = Path(p)
    if not src.exists():
        return None
    try:
        rel = src.resolve().relative_to(ROOT.resolve())
    except ValueError:
        rel = Path(src.name)                      # 不在项目里：只留个名字
    dest = TRASH / rel
    if dest.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = dest.with_name(f"{dest.stem}__{stamp}{dest.suffix}")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _shutil.move(str(src), str(dest))
    except OSError:
        return None
    try:
        with (TRASH / "_log.txt").open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{bucket or '-'}\t"
                     f"{rel.as_posix()}\t{reason or '-'}\n")
    except OSError:
        pass
    return dest


def prune_trash(days: int | None = None) -> int:
    """把回收站里超过 `TRASH_KEEP_DAYS` 天的东西真删掉。返回删掉的条目数。"""
    import shutil as _shutil

    keep = TRASH_KEEP_DAYS if days is None else days
    if keep <= 0 or not TRASH.exists():
        return 0
    cutoff = time.time() - keep * 86400
    n = 0
    for child in TRASH.iterdir():
        if child.name in ("_log.txt",):
            continue
        try:
            if child.stat().st_mtime >= cutoff:
                continue
            if child.is_dir():
                _shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
            n += 1
        except OSError:
            pass
    return n


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


# ---------------------------------------------------------------- 执行链产物归属
#
# `执行链打包与串行交接方案.md` §4.5 / §5：**每次执行链一个产物目录**，
# 打包出来的 ZIP 按 `upload-<日期>-<卡片名…>.zip` 命名。
# 这两个名字**只在后端算一次**（前端只显示）—— 两处各算一遍必然分叉。

# ZIP 名前缀。**字面量**（不是"源文件所在目录名"）：标识"这是由 uploads/ 里的
# 文件跑出来的包"。想换口径只改这一处。
ZIP_PREFIX = "upload"
# 目录名/文件名里每个卡片名最多留几个字（中文卡片名 + MAX_PATH 余量）
_ZIP_NAME_PART = 12
# 整个 ZIP 文件名（不含扩展名）的上限
_ZIP_NAME_MAX = 100


def run_date(ts: float) -> str:
    """`YYYYMMDD`（本地时区）—— 打包名与产物目录名共用。"""
    return datetime.datetime.fromtimestamp(float(ts or 0)).strftime("%Y%m%d")


def zip_filename(date_str: str, step_names: list[str]) -> str:
    """`upload-<日期>-<卡片名1>-<卡片名2>….zip`。

    `step_names` 是**被装进这个包的产物各自的来源步骤名**（按链上顺序）。
    全是源文件时传 `["源文件"]`。

    **相邻重名合并成 `名字x2`**：`转 FLAC → 转 FLAC → 打包` 里两个产物同名，
    写两遍只会让人以为包里有两份不同的东西。
    名字要过一遍 `sanitize_name` 的同一套清洗（去掉路径分隔符与非法字符）——
    卡片名是用户随便起的，`a/b:c` 这种会把文件名弄坏。
    """
    parts: list[str] = []
    for raw in (step_names or []):
        nm = _ILLEGAL.sub("_", unicodedata.normalize("NFC", str(raw or "")))
        nm = nm.replace("\\", "/").split("/")[-1].strip(" .")
        nm = nm[:_ZIP_NAME_PART]
        if not nm:
            continue
        if parts and parts[-1] == nm:
            # 相邻重复：不写两遍。已经带后缀的继续累加（`x2` → `x3`）
            m = re.fullmatch(r"(.+?)x(\d+)", parts[-1])
            if m:
                parts[-1] = f"{m.group(1)}x{int(m.group(2)) + 1}"
            else:
                parts[-1] = f"{nm}x2"
            continue
        parts.append(nm)
    if not parts:
        parts = ["batch"]
    base = "-".join([ZIP_PREFIX, str(date_str)] + parts)[:_ZIP_NAME_MAX]
    return f"{base}.zip"


def run_dir_name(chain_id: str, ts: float) -> str:
    """本次执行的产物目录名：`upload-<YYYYMMDD>-<chain_id 末 6>`。

    ⚠ 用 `chain_id` 而不是"当日第几次"：建链不是跨链事务，两次并发建链
    可能算出同一个序号 → **两次执行的产物落进同一个目录**，正好违反
    "每次执行的产物不许混在一起"这条要求。`chain_id` 天生唯一。
    """
    tail = re.sub(r"[^A-Za-z0-9]", "", str(chain_id or ""))[-6:] or "run"
    return f"{ZIP_PREFIX}-{run_date(ts)}-{tail}"


def is_audio(p: Path) -> bool:
    return p.suffix.lower() in AUDIO_EXT


def is_image(p: Path) -> bool:
    return p.suffix.lower() in IMAGE_EXT


def wipe_workspace() -> dict:
    """清空工作区：uploads/ outputs/ .cache/ 的内容 + 库里的文件与任务记录。

    需求：「每次打开都是空页面」。导入是**复制**进 uploads/ 的，
    所以清掉的只是工作副本，用户磁盘上的原文件不受影响。

    在 lifespan 里、任何请求进来之前调用，保证页面首帧一定是空的。

    ⚠ **2026-10 起不再是真删**：所有东西都**移进 `.trash/`**（保留原目录结构）。
    原来这里是 `unlink` / `rmtree`，不进系统回收站 —— 结果有人（我）忘了带
    `AE_FRESH=0` 起一次服务，就把 `uploads/` 里 6 个音频永久删掉了。
    现在同样的手滑只是丢一次 `shutil.move`，捞得回来。
    """
    removed = {"uploads": 0, "outputs": 0, "cache": 0}
    for key, d in (("uploads", UPLOADS), ("outputs", OUTPUTS), ("cache", CACHE)):
        if not d.exists():
            continue
        for child in d.iterdir():
            try:
                if move_to_trash(child, bucket=key, reason="wipe_workspace") is None:
                    continue
                removed[key] += 1
            except OSError:
                # 文件被别的进程占用（如播放器）不该拖垮启动
                pass
    prune_trash()
    return removed


def reset_db() -> None:
    """删掉数据库文件，让 store 重新建一张空表。

    比 DELETE FROM 更干净：连自增序列、WAL 里的历史一起清掉。

    同样**改为移进 `.trash/`** —— 库没了等于文件列表和任务历史全丢，
    和音频一样值得留一份。
    """
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        try:
            if not move_to_trash(p, bucket="db", reason="reset_db"):
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
