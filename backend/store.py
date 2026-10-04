"""文件与任务状态管理（需求 §4.5）。

关键设计：**文件状态与任务状态分离，文件状态由任务状态聚合得出**。
  · 文件自身只记录 uploaded / ready / deleted
  · processing / done / failed 是从该文件的任务历史算出来的
  · 任务表是唯一事实来源，文件状态是派生视图

持久化用 SQLite（本地单机足够，无额外服务）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from backend import config


# ---------------------------------------------------------------- 状态枚举

FILE_STATES = ("uploaded", "ready", "processing", "done", "failed", "deleted")
# `skipped` = "我压根没被尝试"（前序步骤失败）。它与 `failed` 的区别是用户唯一
# 能看见的调度信息：**第一环是 failed（红着、指出断点），后面才是 skipped（灰着）**。
# 少了它，用户只会看到一整列红色失败，实际只有第 1 步错了。
TASK_STATES = ("pending", "running", "success", "failed", "cancelled", "skipped")
# 终态：不会再变了。`store.task_settled` / defer 判据都用它。
TASK_FINAL_STATES = ("success", "failed", "cancelled", "skipped")
TASK_TYPES = ("probe", "convert", "tag_edit", "cover_embed", "cover_extract",
              "cover_remove", "peaks", "normalize", "rename", "waveform", "verify",
              "zip", "loudness")

# 文件来源：用户导入的 / 执行链派生出来的中间产物。
# **派生行不进 `list_files` 的默认结果** —— 否则"我导入 3 个文件"跑完一条链
# 会变成列表里 23 个条目，全选/删除/批量的作用域全变（方案 §3.2.1）。
FILE_ORIGINS = ("imported", "derived")

_LOCAL = threading.local()


def _conn() -> sqlite3.Connection:
    """每线程一个连接（sqlite3 连接不可跨线程共享）。"""
    c = getattr(_LOCAL, "conn", None)
    if c is None:
        config.ensure_dirs()
        c = sqlite3.connect(str(config.DB_PATH), timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=10000")
        _LOCAL.conn = c
    return c


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id          TEXT PRIMARY KEY,
    rel_path    TEXT NOT NULL UNIQUE,     -- 相对 uploads/ 的路径
    name        TEXT NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    mtime       REAL    NOT NULL DEFAULT 0,
    state       TEXT    NOT NULL DEFAULT 'uploaded',
    info        TEXT    NOT NULL DEFAULT '{}',   -- probe 结果 JSON
    origin      TEXT    NOT NULL DEFAULT 'imported',  -- imported | derived
    derived_from TEXT,                           -- 派生自哪条任务（origin=derived 时）
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_state ON files(state);
CREATE INDEX IF NOT EXISTS idx_files_origin ON files(origin);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    file_id     TEXT,                     -- 可为空（批量汇总类任务）
    type        TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'pending',
    progress    REAL NOT NULL DEFAULT 0,  -- 0..100
    params      TEXT NOT NULL DEFAULT '{}',
    result      TEXT NOT NULL DEFAULT '{}',
    error       TEXT NOT NULL DEFAULT '',
    batch_id    TEXT,                     -- 同一批操作共用一个 id
    created_at  REAL NOT NULL,
    started_at  REAL,
    ended_at    REAL,
    chain_id    TEXT,                     -- 一次"执行链"提交（同一次提交的所有步共享）
    step_id     TEXT,                     -- 链上的第 i 步（同一步的多文件共享）
    step_idx    INTEGER,                  -- 链序，从 0 开始
    src_task_id TEXT,                     -- 输入 = 哪条任务的产物（只有串行档会填）
    src_output  TEXT,                     -- 上游成功后才回填：产物在 outputs/ 下的相对路径
    chain_steps INTEGER,                  -- 这条链一共几步（汇总类的屏障靠它分辨"前面没步骤"与"还没建出来"）
    chain_mode  TEXT,                     -- 建链时的档位（serial/parallel）；**显式存**，不靠 src_task_id 推
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tasks_file  ON tasks(file_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_tasks_batch ON tasks(batch_id);
CREATE INDEX IF NOT EXISTS idx_tasks_chain ON tasks(chain_id, step_idx);
CREATE INDEX IF NOT EXISTS idx_tasks_src   ON tasks(src_task_id);
"""

# ---------------------------------------------------------------- 增量迁移
#
# 执行链要往两张表里加列（方案 §3.3）。**不能只改上面的 SCHEMA**：
# `CREATE TABLE IF NOT EXISTS` 对已存在的表一个字都不改，
# 所以 `AE_FRESH=0` 起的老库会缺列，一查就 `no such column`。
# 这里按"缺哪个补哪个"走 ALTER TABLE —— SQLite 支持 ADD COLUMN，
# 而且这几列的默认值都能就地确定，不需要重建表。
#
# 新增列时**两处都要加**：SCHEMA 里（给全新库）+ 这里（给老库）。
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    # (表, 列, 建列语句)
    ("tasks", "chain_id",    "ALTER TABLE tasks ADD COLUMN chain_id TEXT"),
    ("tasks", "step_id",     "ALTER TABLE tasks ADD COLUMN step_id TEXT"),
    ("tasks", "step_idx",    "ALTER TABLE tasks ADD COLUMN step_idx INTEGER"),
    ("tasks", "src_task_id", "ALTER TABLE tasks ADD COLUMN src_task_id TEXT"),
    ("tasks", "src_output",  "ALTER TABLE tasks ADD COLUMN src_output TEXT"),
    ("tasks", "chain_steps", "ALTER TABLE tasks ADD COLUMN chain_steps INTEGER"),
    ("tasks", "chain_mode",  "ALTER TABLE tasks ADD COLUMN chain_mode TEXT"),
    ("files", "origin",      "ALTER TABLE files ADD COLUMN origin TEXT NOT NULL DEFAULT 'imported'"),
    ("files", "derived_from", "ALTER TABLE files ADD COLUMN derived_from TEXT"),
)
MIGRATION_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_tasks_chain ON tasks(chain_id, step_idx)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_src ON tasks(src_task_id)",
    "CREATE INDEX IF NOT EXISTS idx_files_origin ON files(origin)",
)

_initialized = False
_init_lock = threading.Lock()
_MIGRATED: list[str] = []          # 本次进程实际补上的列（启动横幅用）


def init_db() -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        c = _conn()
        # ⚠ **顺序不能颠倒**：迁移必须在 executescript(SCHEMA) 之前跑。
        # SCHEMA 里有 `CREATE INDEX ... ON files(origin)` / `ON tasks(chain_id, …)`，
        # 而老库的 `files` 表还没有 origin 列 —— `CREATE TABLE IF NOT EXISTS` 不会
        # 给已存在的表补列，于是那句 CREATE INDEX 直接抛
        # `no such column: origin`，**整个服务起不来**。
        # （这个顺序错误实测踩过：单测里手工调 apply_migrations 全绿，
        #   因为那条路绕开了 init_db。所以测试里也必须走 init_db 这条路。）
        migrated = apply_migrations(c)
        c.executescript(SCHEMA)
        # executescript 要的是"一段脚本"，多条语句必须带分号分隔
        c.executescript(";\n".join(MIGRATION_INDEXES) + ";")
        _MIGRATED.extend(migrated)
        _initialized = True


def take_migration_log() -> list[str]:
    """取走"本次启动补了哪些列"，供启动横幅打印（迁移不能静默发生）。"""
    out = list(_MIGRATED)
    _MIGRATED.clear()
    return out


def apply_migrations(c: sqlite3.Connection) -> list[str]:
    """给已存在的老库补列与索引（新建的库在 SCHEMA 里已经有了，这里是 no-op）。

    返回本次**实际补上**的列，供启动横幅打印 —— 迁移静默发生的话，
    出问题时没人知道库被改过。`AE_FRESH=0` 起老库时能看到一行"补了 N 列"。
    """
    added: list[str] = []
    for table, column, ddl in MIGRATIONS:
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        if not cols:                     # 表还不存在（理论上不会）
            continue
        if column not in cols:
            c.execute(ddl)
            added.append(f"{table}.{column}")
    for ddl in MIGRATION_INDEXES:
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            # 索引引用的列不存在（迁移没跑成）时不该拖垮启动
            pass
    return added


def now() -> float:
    return time.time()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------- 文件

@dataclass
class FileRow:
    id: str
    rel_path: str
    name: str
    size: int
    mtime: float
    state: str
    info: dict[str, Any] = field(default_factory=dict)
    origin: str = "imported"
    derived_from: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0
    # 派生字段（由 aggregate_state 填）
    task_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        """这个文件在磁盘上的真实位置。

        ⚠ `origin='derived'` 的行**不在 uploads/ 下**（它们在 outputs/），
        所以不能无脑 `safe_join(UPLOADS, rel_path)` —— 那会拼出一个不存在的
        路径，表现为"产物明明生成了，下一步却报源文件不存在"。
        """
        if self.origin == "derived":
            return (config.OUTPUTS / self.rel_path.replace("\\", "/")).resolve()
        return config.safe_join(config.UPLOADS, self.rel_path)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "relPath": self.rel_path,
            "name": self.name,
            "size": self.size,
            "state": self.state,
            "info": self.info,
            "tasks": self.task_summary,
            "origin": self.origin,
            "derivedFrom": self.derived_from,
            # audio / image / other —— 前端靠它决定"要不要拉波形"。
            # 库里允许放图片（嵌封面要用），而图片**没有音频流**，拉 /peaks 必然
            # 解码失败；不给前端一个判据，它就只能对每个文件都试一遍，然后失败重试。
            "kind": ("audio" if config.is_audio(self.path)
                     else "image" if config.is_image(self.path) else "other"),
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


def _row_to_file(r: sqlite3.Row) -> FileRow:
    try:
        info = json.loads(r["info"] or "{}")
    except Exception:
        info = {}
    keys = r.keys()
    return FileRow(
        id=r["id"], rel_path=r["rel_path"], name=r["name"], size=r["size"],
        mtime=r["mtime"], state=r["state"], info=info,
        origin=(r["origin"] if "origin" in keys else "imported") or "imported",
        derived_from=(r["derived_from"] if "derived_from" in keys else None),
        created_at=r["created_at"], updated_at=r["updated_at"],
    )


def add_file(rel_path: str, *, size: int = 0, mtime: float = 0.0,
             state: str = "uploaded", info: dict | None = None) -> FileRow:
    """登记一个文件；已存在则更新大小/时间并保留原状态。"""
    init_db()
    p = config.safe_join(config.UPLOADS, rel_path)
    rel = str(p.relative_to(config.UPLOADS.resolve())).replace("\\", "/")
    t = now()
    c = _conn()
    cur = c.execute("SELECT * FROM files WHERE rel_path = ?", (rel,))
    row = cur.fetchone()
    if row:
        # 大小/时间对不上 ⇒ 这个路径上的**内容**可能已经换了（软删除后重新导入
        # 同名文件、或同一路径又拖进来一次）。这一步要在下面两个分支之前算好，
        # 因为两个分支都会把 size/mtime 刷新成新值、之后就再也比不出来了。
        same_content = (int(row["size"] or 0) == int(size)
                        and float(row["mtime"] or 0) == float(mtime))
        # 之前被软删除过（state='deleted'）：重新导入必须让它复活。
        # 否则「从库里删掉 → 再从文件夹拖进来」会一直看不到这个文件，
        # 因为下面那句 UPDATE 刻意保留原状态。
        if row["state"] == "deleted":
            c.execute(
                "UPDATE files SET size=?, mtime=?, state=?, updated_at=? WHERE id=?",
                (size, mtime, state, t, row["id"]),
            )
        else:
            # 已登记过：只刷新大小/时间，保留原状态（别把探测结果打回 uploaded）
            c.execute(
                "UPDATE files SET size=?, mtime=?, updated_at=? WHERE id=?",
                (size, mtime, t, row["id"]),
            )
        if not same_content:
            _forget_measurements(row["id"])
        return get_file(row["id"])                       # type: ignore[return-value]
    fid = new_id("f_")
    c.execute(
        "INSERT INTO files(id, rel_path, name, size, mtime, state, info, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (fid, rel, p.name, size, mtime, state, json.dumps(info or {}, ensure_ascii=False), t, t),
    )
    return get_file(fid)                                 # type: ignore[return-value]


def add_derived_file(*, rel_to_outputs: str, name: str, size: int, mtime: float,
                     derived_from: str, info: dict | None = None) -> FileRow:
    """登记一个**执行链派生出来的产物**（`outputs/` 下的新文件）。

    与 `add_file` 的区别有三处，都不是风格问题：
      · `origin='derived'` —— 它不进 `list_files` 的默认结果（见那里的注释）
      · `rel_path` 相对的是 **`config.OUTPUTS`**，不是 `uploads/`
        （`FileRow.path` 按 origin 分派）
      · 已存在就**直接返回原行**，不刷新大小/时间：同一条产物被重复登记
        （重跑链、重试任务）时应当指向同一个 `file_id`，否则互斥租约、
        `src_task_id` 的指向全都会分成两份
    """
    init_db()
    rel = str(rel_to_outputs).replace("\\", "/").lstrip("/")
    c = _conn()
    row = c.execute(
        "SELECT * FROM files WHERE origin='derived' AND rel_path=?", (rel,)
    ).fetchone()
    if row:
        f = _row_to_file(row)
        f.task_summary = aggregate_state(f.id)
        return f
    # `files.rel_path` 上有 **UNIQUE 约束，而且是跨 origin 的**：
    # `uploads/` 里的 `song.flac` 与 `outputs/` 里的 `song.flac` 共用一个命名空间。
    # 所以产物的 rel_path 可能与一个**导入文件**重名（用户导入的文件就叫
    # `song.norm.flac`，或者 outputs/ 里留着上一轮的产物但库里那行已删）。
    # 不查就 INSERT 会抛 UNIQUE constraint failed —— 而它发生在队列 worker 里，
    # 表现是"任务莫名失败"，非常难查。
    # 这里按 `unique_path` 的同一套规则避让（`-1`/`-2`…），保证一定能插进去。
    # 注意：`rel` 是**文件名**，不是路径，所以直接把后缀插在扩展名前。
    if c.execute("SELECT 1 FROM files WHERE rel_path=? LIMIT 1", (rel,)).fetchone():
        stem, dot, ext = rel.rpartition(".")
        if not dot:
            stem, ext = rel, ""
        for i in range(1, 10000):
            cand = f"{stem}-{i}.{ext}" if ext else f"{stem}-{i}"
            if not c.execute("SELECT 1 FROM files WHERE rel_path=? LIMIT 1",
                             (cand,)).fetchone():
                rel = cand
                name = cand
                break
        else:
            raise RuntimeError(f"无法为派生产物 {rel} 找到不冲突的记录名")
    fid = new_id("f_")
    t = now()
    c.execute(
        "INSERT INTO files(id, rel_path, name, size, mtime, state, info, origin,"
        " derived_from, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (fid, rel, name, size, mtime, "ready",
         json.dumps(info or {}, ensure_ascii=False), "derived", derived_from, t, t),
    )
    return get_file(fid)                                  # type: ignore[return-value]


def get_file(fid: str) -> Optional[FileRow]:
    init_db()
    r = _conn().execute("SELECT * FROM files WHERE id = ?", (fid,)).fetchone()
    if not r:
        return None
    f = _row_to_file(r)
    f.task_summary = aggregate_state(fid)
    f.state = f.task_summary.get("fileState", f.state)
    return f


def list_files(*, include_deleted: bool = False, limit: int = 2000,
               origin: str = "imported") -> list[FileRow]:
    """文件列表。**默认只返回用户导入的文件**（`origin='imported'`）。

    执行链的中间产物（`origin='derived'`）也在 `files` 表里——它们要能被
    `_file_of` 寻址、能被 `zip` 装走、能让任务历史显示"这条任务处理的是哪个文件"。
    但如果让它们出现在列表里，"我导入 3 个文件、链跑完变成 23 个"会立刻
    改变全选/删除/批量的作用域（方案 §3.2.1）。所以默认过滤掉，
    调试时可以用 `origin='all'`（或 `'derived'`）显式要。
    """
    init_db()
    sql = "SELECT * FROM files WHERE 1=1"
    args: list[Any] = []
    if not include_deleted:
        sql += " AND state != 'deleted'"
    if origin in FILE_ORIGINS:
        sql += " AND origin = ?"
        args.append(origin)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    rows = _conn().execute(sql, args).fetchall()
    out = []
    for r in rows:
        f = _row_to_file(r)
        f.task_summary = aggregate_state(f.id)
        f.state = f.task_summary.get("fileState", f.state)
        out.append(f)
    return out


def set_file_state(fid: str, state: str) -> None:
    if state not in FILE_STATES:
        raise ValueError(f"非法文件状态: {state}")
    init_db()
    _conn().execute("UPDATE files SET state=?, updated_at=? WHERE id=?",
                    (state, now(), fid))


def set_file_info(fid: str, info: dict) -> None:
    init_db()
    _conn().execute(
        "UPDATE files SET info=?, state=CASE WHEN state='uploaded' THEN 'ready' ELSE state END,"
        " updated_at=? WHERE id=?",
        (json.dumps(info, ensure_ascii=False), now(), fid),
    )


# 这几个 key 是**内容测量值**：只有整条流解码一遍才拿得到（响度/真峰值/响度范围/
# 采样峰值/平均动态/动态模式），所以 `probe`（ffprobe，只读元数据，不解码）
# **永远测不出来、也就永远刷新不了**。它们由 `h_loudness` / `h_normalize` 写，
# 而"文件换了内容"时只能靠 `_forget_measurements` 显式丢掉 —— 留着就是**上一个文件**
# 的数字。
MEASUREMENT_KEYS = ("loudness", "truePeak", "loudnessRange", "samplePeak", "dra", "drp")


def _load_info(fid: str) -> dict:
    init_db()
    row = _conn().execute("SELECT info FROM files WHERE id=?", (fid,)).fetchone()
    if not row:
        return {}
    try:
        cur = json.loads(row["info"] or "{}")
    except (TypeError, ValueError):
        cur = {}
    return cur if isinstance(cur, dict) else {}


def merge_file_info(fid: str, patch: dict) -> None:
    """把 `patch` **合并**进现有 info，而不是像 `set_file_info` 那样整体覆盖。

    为什么要这个入口：有些处理器只产出**一个**字段（`normalize`/`loudness` → 响度，
    `tags`/`cover` → 重新 probe 一遍元数据），而 `set_file_info` 是整体覆盖 ——
    直接用它会把之前测出来的响度、以及别的处理器写进去的字段一并清掉。
    `info` 是**一个文件的一整份画像**，写它的人各有各的一块，所以按块合并。
    """
    cur = _load_info(fid)
    if not cur and not patch:
        return
    cur.update(patch or {})
    set_file_info(fid, cur)


def _forget_measurements(fid: str) -> None:
    """丢掉 `MEASUREMENT_KEYS`（响度那一组）。

    用在**文件内容被换掉**的时候（软删除后重新导入同名文件、或同一路径又拖进来
    一次）：别的字段 `probe` 会重新读、能自我纠正，而响度那组 probe 测不出来 ——
    留着就变成"卡片上写着上一个文件的 -19.3 LUFS"，比显示 `—` 更糟。
    """
    cur = _load_info(fid)
    if any(k in cur for k in MEASUREMENT_KEYS):
        for k in MEASUREMENT_KEYS:
            cur.pop(k, None)
        set_file_info(fid, cur)


def rel_path_exists(rel: str) -> bool:
    """rel_path 是否已被占用 —— **包括软删除的行**。

    files.rel_path 上有 UNIQUE 约束，而软删除（state='deleted'）只是改了状态、
    行还占着那个 rel_path。所以重命名目标不能只查磁盘上有没有同名文件，
    否则"删掉一个文件、再把另一个改成同名"会抛 UNIQUE constraint failed。
    """
    init_db()
    row = _conn().execute("SELECT 1 FROM files WHERE rel_path=? LIMIT 1", (rel,)).fetchone()
    return row is not None


def rename_file(fid: str, rel_path: str, name: str, size: int, mtime: float) -> None:
    """重命名后更新记录（保持同一 id，任务历史不断）。"""
    init_db()
    _conn().execute(
        "UPDATE files SET rel_path=?, name=?, size=?, mtime=?, updated_at=? WHERE id=?",
        (rel_path, name, size, mtime, now(), fid),
    )


def delete_file(fid: str, *, purge: bool = False) -> None:
    """软删除（默认）或连同记录一起清除。"""
    init_db()
    if purge:
        _conn().execute("DELETE FROM files WHERE id=?", (fid,))
    else:
        set_file_state(fid, "deleted")


# ---------------------------------------------------------------- 任务

@dataclass
class TaskRow:
    id: str
    file_id: Optional[str]
    type: str
    state: str
    progress: float
    params: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    batch_id: Optional[str] = None
    created_at: float = 0.0
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    # 执行链（方案 §3.3）。非链任务这几个都是 None，走的必须是与现在
    # 一模一样的老路径 —— 这是 P0-3 那条"零回归"的根据。
    chain_id: Optional[str] = None
    step_id: Optional[str] = None
    step_idx: Optional[int] = None
    src_task_id: Optional[str] = None
    src_output: Optional[str] = None
    # 这条链一共有几步。汇总类（`打包 ZIP`）的屏障靠它区分
    # "前面真的没有步骤" 与 "前面的步骤还没建出来"（见 `Queue._aggregate_barrier_unready`）。
    chain_steps: Optional[int] = None
    # 建链时的档位（`serial` / `parallel`）。
    # ⚠ **必须显式存下来，不能靠"链上有没有 src_task_id"去推**：
    # `files` 表上有 `ON DELETE CASCADE`，用户删掉一个文件就会连带删掉它的任务；
    # 于是"转 FLAC → 打包"这条串行链在转换任务被删之后，看起来就只剩一个 zip，
    # "有 src_task_id"的判据随之变假 —— 汇总类的回填会被静默跳过。
    # （chain_e2e_check 抓到的就是这个：清理测试文件之后同一条链的行为变了。）
    chain_mode: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "fileId": self.file_id,
            "type": self.type,
            "state": self.state,
            "progress": round(self.progress, 1),
            "params": self.params,
            "result": self.result,
            "error": self.error,
            "batchId": self.batch_id,
            "chainId": self.chain_id,
            "stepIdx": self.step_idx,
            "srcTaskId": self.src_task_id,
            "srcOutput": self.src_output,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "endedAt": self.ended_at,
        }


def _row_to_task(r: sqlite3.Row) -> TaskRow:
    def j(v: Any) -> dict:
        try:
            return json.loads(v or "{}")
        except Exception:
            return {}
    k = r.keys()

    def g(name: str, default: Any = None) -> Any:
        return r[name] if name in k else default

    return TaskRow(
        id=r["id"], file_id=r["file_id"], type=r["type"], state=r["state"],
        progress=r["progress"], params=j(r["params"]), result=j(r["result"]),
        error=r["error"] or "", batch_id=r["batch_id"],
        created_at=r["created_at"], started_at=r["started_at"], ended_at=r["ended_at"],
        chain_id=g("chain_id"), step_id=g("step_id"), step_idx=g("step_idx"),
        src_task_id=g("src_task_id"), src_output=g("src_output"),
        chain_steps=g("chain_steps"), chain_mode=g("chain_mode"),
    )


def create_task(type_: str, *, file_id: str | None = None,
                params: dict | None = None, batch_id: str | None = None,
                state: str = "pending",
                chain_id: str | None = None, step_id: str | None = None,
                step_idx: int | None = None, src_task_id: str | None = None,
                chain_steps: int | None = None, chain_mode: str | None = None) -> TaskRow:
    if type_ not in TASK_TYPES:
        raise ValueError(f"非法任务类型: {type_}")
    if state not in TASK_STATES:
        raise ValueError(f"非法任务状态: {state}")
    init_db()
    tid = new_id("t_")
    _conn().execute(
        "INSERT INTO tasks(id, file_id, type, state, params, batch_id, created_at,"
        " chain_id, step_id, step_idx, src_task_id, chain_steps, chain_mode)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, file_id, type_, state, json.dumps(params or {}, ensure_ascii=False),
         batch_id, now(), chain_id, step_id, step_idx, src_task_id, chain_steps,
         chain_mode),
    )
    return get_task(tid)                                  # type: ignore[return-value]


def get_task(tid: str) -> Optional[TaskRow]:
    init_db()
    r = _conn().execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    return _row_to_task(r) if r else None


def list_tasks(*, file_id: str | None = None, batch_id: str | None = None,
               states: Iterable[str] | None = None, limit: int = 200) -> list[TaskRow]:
    init_db()
    sql = "SELECT * FROM tasks WHERE 1=1"
    args: list[Any] = []
    if file_id:
        sql += " AND file_id=?"; args.append(file_id)
    if batch_id:
        sql += " AND batch_id=?"; args.append(batch_id)
    if states:
        ss = list(states)
        sql += f" AND state IN ({','.join('?' * len(ss))})"; args.extend(ss)
    sql += " ORDER BY created_at DESC LIMIT ?"; args.append(limit)
    return [_row_to_task(r) for r in _conn().execute(sql, args).fetchall()]


def start_task(tid: str) -> None:
    init_db()
    _conn().execute(
        "UPDATE tasks SET state='running', started_at=?, progress=5 WHERE id=?",
        (now(), tid),
    )


def update_progress(tid: str, pct: float) -> None:
    init_db()
    _conn().execute("UPDATE tasks SET progress=? WHERE id=?",
                    (max(0.0, min(100.0, pct)), tid))


def finish_task(tid: str, *, ok: bool, result: dict | None = None,
                error: str = "") -> None:
    init_db()
    _conn().execute(
        "UPDATE tasks SET state=?, progress=?, result=?, error=?, ended_at=? WHERE id=?",
        ("success" if ok else "failed", 100.0 if ok else 0.0,
         json.dumps(result or {}, ensure_ascii=False), error[:2000], now(), tid),
    )


def cancel_task(tid: str) -> None:
    init_db()
    _conn().execute(
        "UPDATE tasks SET state='cancelled', ended_at=? WHERE id=? AND state IN ('pending','running')",
        (now(), tid),
    )


def skip_task(tid: str, error: str = "前序步骤失败") -> None:
    """把任务标成 `skipped`：**没被尝试**过（前序步骤失败）。

    与 `failed` 的分工（方案 §9.7）：
      · `failed`  = "我尝试过但做不了"（例：派生输入的上游失败了 → 真的去解析了）
      · `skipped` = "我压根没被尝试"（例：更上游就断了 → 轮不到我）

    用户看到的是**第一环红、后面灰**，一眼能定位断点；全判 failed 会让人
    以为二十个文件都坏了。只对 `pending` 生效，不覆盖已跑过的任务。
    """
    init_db()
    _conn().execute(
        "UPDATE tasks SET state='skipped', error=?, ended_at=?"
        " WHERE id=? AND state='pending'",
        (error[:2000], now(), tid),
    )


# ---------------------------------------------------------------- 执行链：就绪与租约
#
# 这两组是 `queue._gate` 的判据（方案 §3.4 / §9.3）。**都只读/只写内存中的租约表**，
# 不碰任务状态 —— defer 的任务必须留在 `pending`，否则文件状态会显示"处理中"、
# 进度条永远转圈（§9.3.2 约束 1）。

_LEASES: dict[str, dict[str, str]] = {}      # file_id → {task_id: "read"|"write"}
_LEASE_LOCK = threading.Lock()


def upstream_settled(task: "TaskRow") -> tuple[bool, str]:
    """本任务的上游（`src_task_id`）是否已有结论。→ `(能不能开工, 原因)`。

    原因取值，`queue` 据此决定"等一等"还是"当场结案"：
      · `""`               —— 没有上游，或上游已成功 → 可以开工
      · `"upstream"`       —— 上游还在 pending/running → **defer（能等）**
      · `"expired"`        —— 上游任务不存在（被 purge / 清库）→ 当场判失败
      · `"failed_upstream"`—— 上游失败/取消/跳过 → 当场判失败（§9.7：**不回落**）
      · `"cycle"`          —— 上游指回了自己（不该发生）→ 当场判失败，防死循环
    """
    if not task.src_task_id:
        return True, ""
    if task.src_task_id == task.id:
        return False, "cycle"
    up = get_task(task.src_task_id)
    if up is None:
        return False, "expired"
    if up.state in ("pending", "running"):
        return False, "upstream"
    if up.state != "success":
        return False, "failed_upstream"
    return True, ""


def acquire_file_leases(task: "TaskRow", *, op: str | None = None) -> bool:
    """按接触面给任务加租约：写者独占、读者共享。拿不到就返回 False（调用方 defer）。

    规则 ①（方案 §2.2）的落地：**只有"就地改写 `in`"的 op 需要写租约** ——
    `tags` / `cover` / `remove-cover` / `rename`。`convert`/`normalize` 虽然产出
    新文件，但它们只读输入，所以只需要读租约，两个转换可以并行。

    `op` 缺省时按任务类型反查接触面；查不到（未知类型）按写者处理 —— 宁可保守。
    """
    from backend.cards.contract import CONTRACT, rewrites_input

    op = op or _op_of_task_type(task.type)
    if op is None:
        want = "write"
    else:
        want = "write" if rewrites_input(op) else "read"

    fid = task.file_id
    if not fid:
        return True                       # 汇总类任务（zip）不吃单个文件，另走 barrier

    with _LEASE_LOCK:
        held = _LEASES.setdefault(fid, {})
        # 自己已持有就不重复申请（重试同一任务时可能走到这里）
        if held.get(task.id) == want:
            return True
        others = {tid: m for tid, m in held.items() if tid != task.id}
        if want == "write":
            if others:                    # 写者要求独占
                return False
        else:
            if any(m == "write" for m in others.values()):   # 读者怕写者
                return False
        held[task.id] = want
        return True


def release_file_leases(task_id: str) -> None:
    """释放某任务持有的全部租约。**必须放在 finally 里** ——
    漏一次就等于永久锁死那个文件，而表现是"队列不动"（最难查的一类）。"""
    with _LEASE_LOCK:
        for fid in list(_LEASES):
            _LEASES[fid].pop(task_id, None)
            if not _LEASES[fid]:
                _LEASES.pop(fid, None)


def release_all_leases() -> None:
    """启动/关闭时清空内存租约表（它是内存态，不持久化）。"""
    with _LEASE_LOCK:
        _LEASES.clear()


def held_leases() -> dict[str, dict[str, str]]:
    """只读快照，给测试与诊断用。"""
    with _LEASE_LOCK:
        return {k: dict(v) for k, v in _LEASES.items()}


_TASK_TYPE_TO_OP: dict[str, str] | None = None


def _op_of_task_type(type_: str) -> str | None:
    """任务类型 → 卡片 op 名。两套命名（下划线 vs 连字符）由 specs 里的 `task` 字段桥接。"""
    global _TASK_TYPE_TO_OP
    if _TASK_TYPE_TO_OP is None:
        from backend.cards.specs import OPS

        m: dict[str, str] = {}
        for op, spec in OPS.items():
            m.setdefault(str(spec.get("task") or op), op)
        _TASK_TYPE_TO_OP = m
    return _TASK_TYPE_TO_OP.get(type_)


# ---------------------------------------------------------------- 聚合

def aggregate_state(fid: str) -> dict[str, Any]:
    """由任务历史推出文件状态（需求 §4.5 的核心）。

    规则：
      · 有 running            → processing
      · 有 pending            → processing
      · 否则最近一条 success   → done
      · 否则最近一条 failed    → failed
      · 无任务但已 probe 过     → ready
      · 其余                   → uploaded

    `skipped` **既不算成功也不算失败**（方案 §3.4 的五个穿透点之一）：
    它是"轮不到我"，不该把一个好文件标成 failed。
    但若该文件**一条成功都没有、却有 skipped**，说明它整条链的第一步就断了
    —— 那时报 failed 才诚实（用户要能看出"这个文件没做成"）。
    """
    init_db()
    rows = _conn().execute(
        "SELECT state, type, progress, error, ended_at, created_at FROM tasks"
        " WHERE file_id=? ORDER BY created_at DESC LIMIT 50", (fid,)
    ).fetchall()
    if not rows:
        r = _conn().execute("SELECT state, info FROM files WHERE id=?", (fid,)).fetchone()
        if not r:
            return {}
        st = r["state"]
        return {"fileState": st, "total": 0, "running": 0, "pending": 0,
                "success": 0, "failed": 0, "skipped": 0, "progress": 0.0}

    running = [r for r in rows if r["state"] == "running"]
    pending = [r for r in rows if r["state"] == "pending"]
    success = [r for r in rows if r["state"] == "success"]
    failed = [r for r in rows if r["state"] == "failed"]
    skipped = [r for r in rows if r["state"] == "skipped"]

    if running:
        state = "processing"
        progress = running[0]["progress"]
    elif pending:
        state = "processing"
        progress = 0.0
    elif success and (not failed or success[0]["created_at"] >= failed[0]["created_at"]):
        state, progress = "done", 100.0
    elif failed:
        state, progress = "failed", 0.0
    elif skipped:
        # 一条成功的都没有、只有"轮不到"的任务 → 这个文件没被做成
        state, progress = "failed", 0.0
    else:
        state, progress = "ready", 0.0

    # 数据库里若标记为 deleted，保持软删除
    r = _conn().execute("SELECT state FROM files WHERE id=?", (fid,)).fetchone()
    if r and r["state"] == "deleted":
        state = "deleted"

    return {
        "fileState": state,
        "progress": round(progress, 1),
        "total": len(rows),
        "running": len(running),
        "pending": len(pending),
        "success": len(success),
        "failed": len(failed),
        "skipped": len(skipped),
        "lastError": ((failed or skipped)[0]["error"] if (failed or skipped) else ""),
    }


def batch_summary(batch_id: str) -> dict[str, Any]:
    """批量聚合进度：已完成 12 / 20（需求 §4.6）。

    `skipped` 算进 `done`：它不会再有变化了，进度条不该停在 19/20 等一个
    永远不会跑的任务（方案 §3.4 的五个穿透点之一）。
    """
    init_db()
    rows = _conn().execute(
        "SELECT state, COUNT(*) n FROM tasks WHERE batch_id=? GROUP BY state", (batch_id,)
    ).fetchall()
    counts = {r["state"]: r["n"] for r in rows}
    total = sum(counts.values())
    skipped = counts.get("skipped", 0)
    done = (counts.get("success", 0) + counts.get("failed", 0)
            + counts.get("cancelled", 0) + skipped)
    return {
        "batchId": batch_id,
        "total": total,
        "done": done,
        "success": counts.get("success", 0),
        "failed": counts.get("failed", 0),
        "cancelled": counts.get("cancelled", 0),
        "skipped": skipped,
        "running": counts.get("running", 0),
        "pending": counts.get("pending", 0),
        "label": f"已完成 {done} / {total}",
    }


def queue_snapshot(limit: int = 50) -> dict[str, Any]:
    """左侧任务队列用的快照。"""
    init_db()
    pending = list_tasks(states=["pending"], limit=limit)
    running = list_tasks(states=["running"], limit=limit)
    recent = list_tasks(limit=limit)
    return {
        "pending": [t.as_dict() for t in pending],
        "running": [t.as_dict() for t in running],
        "recent": [t.as_dict() for t in recent],
        "counts": {
            "pending": len(pending),
            "running": len(running),
            "total": _conn().execute("SELECT COUNT(*) n FROM tasks").fetchone()["n"],
        },
    }


def chain_snapshot(chain_id: str) -> dict[str, Any]:
    """一条执行链的整体状态：每步的进度 + 每个文件走到第几步。

    给前端画"顶栏链上的状态点"和"文件卡片上的 N/M 角标"用（方案 §3.7）。
    刻意不放进 `queue_snapshot`：队列面板显示的是"最近的任务"，
    而链的进度要按**步骤**聚合，两者的口径不同。
    """
    init_db()
    rows = _conn().execute(
        "SELECT step_idx, step_id, type, state, file_id FROM tasks"
        " WHERE chain_id=? ORDER BY step_idx", (chain_id,)
    ).fetchall()
    steps: dict[int, dict[str, Any]] = {}
    per_file: dict[str, dict[str, Any]] = {}
    for r in rows:
        idx = r["step_idx"] if r["step_idx"] is not None else -1
        s = steps.setdefault(idx, {"stepIdx": idx, "type": r["type"], "total": 0,
                                   "settled": 0, "success": 0, "failed": 0,
                                   "skipped": 0, "running": 0, "pending": 0})
        s["total"] += 1
        s[r["state"]] = s.get(r["state"], 0) + 1
        if r["state"] in TASK_FINAL_STATES:
            s["settled"] += 1
        fid = r["file_id"]
        if fid:
            pf = per_file.setdefault(fid, {"fileId": fid, "settled": 0,
                                           "total": 0, "failed": 0})
            pf["total"] += 1
            if r["state"] in TASK_FINAL_STATES:
                pf["settled"] += 1
            if r["state"] in ("failed", "skipped"):
                pf["failed"] += 1
    return {
        "chainId": chain_id,
        "steps": [steps[k] for k in sorted(steps)],
        "files": list(per_file.values()),
        "stepCount": len([k for k in steps if k >= 0]),
        # 本次执行的**产物目录**（`执行链打包与串行交接方案.md` §5.3）：
        # 让前端能显示"这次的产物在哪"，也让用户分得清两次执行。
        "outDir": chain_out_dir(chain_id),
    }


def chain_out_dir(chain_id: str) -> str:
    """本次执行的产物目录（相对 `outputs/`）；没有任务时就返回空串。

    目录名由 `chain_id` + 建链时间算出来（`config.run_dir_name`），
    所以**不存在"这个目录还没建"的问题** —— 它是确定性的名字，不是"查到才存在"。
    """
    started = chain_started_at(chain_id)
    if not started:
        return ""
    return config.run_dir_name(chain_id, started)


def file_prev_step_state(chain_id: str, file_id: str, step_idx: int,
                         before_ts: float | None = None) -> str:
    """**这个文件**在链上第 `step_idx-1` 步（它的直接前置步）的状态。

    返回值只有四种，**但它们必须被分开对待**（这是汇总屏障的核心，别再合并）：

      `"missing"` —— 这一行还不存在。两种可能：
                     · 建链还没 INSERT 到（→ 该等，见 `chain_build_in_progress`）
                     · 这一步对这批文件根本没建任务（→ 不该等，永远等不到）
      `"pending"` —— 存在但还没跑。**必须等** —— 它马上就要产出东西了。
      `"running"` —— 存在且正在跑。**必须等。**
      `"settled"` —— 已出终态（success/failed/skipped/cancelled）。不用等。

    ⚠ 第一版把 `missing` 与 `pending/running` 混在同一个 `if` 里，用
    `chain_build_in_progress` 一起短路 —— 于是守卫一松就把"前置还在跑"也放行了，
    症状是 `zip` 在前置 `convert` 还在跑时就装走了源文件（偶发）。
    """
    init_db()
    sql = ("SELECT state FROM tasks WHERE chain_id=? AND file_id=? AND step_idx=?"
           " AND created_at >= ? ORDER BY created_at DESC LIMIT 1"
           if before_ts is not None else
           "SELECT state FROM tasks WHERE chain_id=? AND file_id=? AND step_idx=?"
           " ORDER BY created_at DESC LIMIT 1")
    args = ((chain_id, file_id, step_idx - 1, before_ts) if before_ts is not None
            else (chain_id, file_id, step_idx - 1))
    row = _conn().execute(sql, args).fetchone()
    return row["state"] if row else "missing"


def file_step_unsettled(chain_id: str, file_id: str, step_idx: int,
                        before_ts: float | None = None) -> bool:
    """**这个文件**在链上、早于 `step_idx` 的步骤里，还有没落定的吗。

    与 `tasks_before` 的区别，也是汇总类屏障必须用它的原因：

      · `tasks_before` 只返回**已经存在**的行。建链是逐条 INSERT，而 `zip`
        完全可能在 INSERT 循环还没走到第 0 步时就被 worker 取走 —— 那一刻
        查出来是**空**，屏障于是认为"前面没东西，可以开跑"，结果在转换还没
        建出来时就把源文件装走（症状："偶发，ZIP 里是产物还是源文件看运气"）。
      · 这里对第 0..step_idx-1 步**逐个步骤号查**：这个文件在该步有没有任务。
        缺了 = 建链还没走到 → 也算"没落定"。

    ⚠ **必须按文件查**，不能按整条链查"还有没有没落定的步骤"：
    后者是**步骤级全局屏障** —— 大文件还在跑第 1 步时，小文件的第 2 步就被拦住，
    "每个文件一条自己的流水线"（§3.1.1）当场退化成"等所有人"。
    （这条我实测踩过：改成链级之后，交错断言立刻变红。）
    """
    init_db()
    for i in range(step_idx):
        sql = ("SELECT state FROM tasks WHERE chain_id=? AND file_id=? AND step_idx=?"
               " AND created_at >= ? ORDER BY created_at DESC LIMIT 1"
               if before_ts is not None else
               "SELECT state FROM tasks WHERE chain_id=? AND file_id=? AND step_idx=?"
               " ORDER BY created_at DESC LIMIT 1")
        args = ((chain_id, file_id, i, before_ts) if before_ts is not None
                else (chain_id, file_id, i))
        row = _conn().execute(sql, args).fetchone()
        if row is None:
            return True                 # 这一步还没建出来 → 建链进行中
        if row["state"] in ("pending", "running"):
            return True
    return False


def chain_build_in_progress(chain_id: str, exclude_task_id: str | None = None) -> bool:
    """这条链**除自己之外**还有没有没开始跑的任务 —— 用来判断建链是否还在进行。

    `build_chain` 是逐条 INSERT，而任务在全部建好之前就已经被 worker 取走；
    所以汇总类会看到"第 0 步不存在"。正常情况下那只是"还没建到"，等一会儿就有；
    但如果**建链根本没插入那一步**（那一步对这批文件一条任务都没建，或行被
    级联删除过），它永远不会出现 —— 无条件等会让汇总任务一路推到 `DEFER_LIMIT`，
    报出误导性的"依赖无法满足（上游还没结束）"，而真相是"没有文件可以打包"。

    ⚠ **必须排除"正在过闸的这个任务"**：它自己此刻就是 `pending`
    （`_gate` 在 `start_task` 之前跑，见 §9.3.2），不排除的话这个判据恒为真 ——
    第一版就是这么错的，于是守卫形同虚设。
    """
    init_db()
    sql = "SELECT COUNT(*) n FROM tasks WHERE chain_id=? AND state='pending'"
    args: list[Any] = [chain_id]
    if exclude_task_id:
        sql += " AND id != ?"
        args.append(exclude_task_id)
    r = _conn().execute(sql, args).fetchone()
    return bool(r and r["n"] > 0)


def chain_has_later_step(task: TaskRow) -> bool:
    """这条链上还有没有**排在 `task` 之后**、且本该接手它产物的步骤。

    用途只有一个：`queue._publish_derived` 里判断"产物没人认领"是不是异常。
    **链的最后一步天生没有下游**，那不是异常，不该告警 ——
    原来只判 `chain_id` 是否为真，于是每跑完一条链都会刷一条看着像出错的告警。

    判据：
      · 汇总类（`file_id` 为空）接手全批，所以只要有**更靠后**的步骤就算有；
      · 普通步骤只在**同一个文件**上找更靠后的步骤（别的文件的步骤不接手它）。

    ⚠ 区分不了"建链还没 INSERT"与"这一步真的就是最后一步" —— 两者看到的库
    是一样的。所以这里只用来**抑制误报**：判为"没有后续"就安静，
    判为"有后续"才告警。宁可漏报（那本来就是极端时序），也不要每批链都误报。
    """
    init_db()
    if not task.chain_id:
        return False
    if task.file_id:
        sql = ("SELECT COUNT(*) n FROM tasks WHERE chain_id=? AND step_idx>?"
               " AND file_id=?")
        args: list[Any] = [task.chain_id, task.step_idx, task.file_id]
    else:
        sql = "SELECT COUNT(*) n FROM tasks WHERE chain_id=? AND step_idx>?"
        args = [task.chain_id, task.step_idx]
    r = _conn().execute(sql, args).fetchone()
    return bool(r and r["n"] > 0)


def chain_started_at(chain_id: str) -> float:
    """这条链**最早**那条任务的创建时间 —— 当"锚点"用。

    为什么需要它：`tasks_before` 不能拿"当前任务的 `created_at`"当上界。
    建链是逐条 INSERT，**同一循环里相邻两条的时间戳可能完全相同**
    （`time.time()` 的精度足够粗），于是 `created_at <= 子任务` 会把
    **它的直接上游**排除掉，然后返回**上一条链**的同步骤任务 ——
    判据接着在错误的行上做判断，症状荒谬但很难查：
    "上游明明 failed，第 3 步却判成 failed 而不是 skipped"。

    用整条链的**最早**时间当锚点就没有这个问题：本链所有任务都 >= 它，
    而别的链的任务都在它之前（链是一个接一个建的）。
    """
    init_db()
    r = _conn().execute(
        "SELECT MIN(created_at) t FROM tasks WHERE chain_id=?", (chain_id,)
    ).fetchone()
    return float(r["t"]) if r and r["t"] is not None else 0.0


def tasks_before(chain_id: str, step_idx: int, file_id: str,
                 before_ts: float | None = None) -> list[TaskRow]:
    """同一个文件、同一条链上**早于** `step_idx` 的任务，按链序**从后往前**。

    ⚠ **`before_ts` 不是可选的优化，是正确性的一部分**（这个 bug 实测踩过）：
    只按 `step_idx < N` 过滤会**把别的链的步骤也算进来** —— 同一个文件被
    多条链用过时（重跑、或"改标签 → 转换"和"转换 → 打包"两条链先后跑），
    前一条链的 `step_idx=1` 也满足 `step_idx < 2`，而且它 `created_at` 更早、
    却因为排序只看 `step_idx` 而排在了当前链 `step_idx=0` 的**前面**。
    结果 `_zip_plan` 解算到的是**上一条链的旧任务**（它的 `src_output` 是空的），
    于是 ZIP 里装的是源文件。症状是"偶发/看历史"，极难从现象反推。

    传 `before_ts`（当前任务的 `created_at`）之后，判据变成
    "**这条链上、在这个任务之前**创建的那些步骤"，才是真正的前置。

    `before_ts=None` 时保持旧行为（只按 step_idx），仅用于"确实不在乎跨链"的场合
    —— 目前没有这种场合，新代码请一律传 `created_at`。
    """
    init_db()
    sql = ("SELECT * FROM tasks WHERE chain_id=? AND file_id=? AND step_idx < ?")
    args: list[Any] = [chain_id, file_id, step_idx]
    if before_ts is not None:
        sql += " AND created_at <= ?"
        args.append(before_ts)
    sql += " ORDER BY step_idx DESC"
    rows = _conn().execute(sql, args).fetchall()
    return [_row_to_task(r) for r in rows]


def attach_src_output(src_task_id: str, artifact_file_id: str, rel: str) -> int:
    """上游成功后：把产物交给**同一个文件**的下游步骤（方案 §3.2.2）。

    做两件事：
      1. 下游任务的 `src_output` 回填成产物在 `outputs/` 下的相对路径
         —— `h_zip` 靠它知道"这一步该装什么"
      2. 下游任务的 `file_id` 改指向**派生产物那一行**（而不是原文件）

    **第 2 件事是必须的**，否则派生产物只是"名字存在库里"，而任务的
    `file_id` 仍指着原文件 —— `_file_of` 会解析出源文件，
    "下一步吃到上一步的产物"这句话就落不了地。租约也跟着错：
    互斥该加在**产物**上，加在原文件上等于没加（§3.4 那条"租约加错一侧"）。

    **汇总类（`打包 ZIP`）走一条更宽的路**：它没有单一上游（`src_task_id` 为空），
    而且它吃的是"这一批文件"而不是某一个。所以除了直接下游，还要顺带
    带着这个文件的**后续汇总任务**一起更新 —— 否则 `转 FLAC → 打包` 里的
    打包任务仍然拿着原文件的 `file_id`，装出来的是源文件而不是转好的那个。
    只对 `params.fileIds` 里含这个文件、且 `file_id` 还与上游同一个的那条生效。

    返回被更新的下游任务条数（给日志用）。
    """
    init_db()
    n = 0
    rows = _conn().execute(
        "SELECT id, file_id, params FROM tasks WHERE src_task_id=? AND state='pending'",
        (src_task_id,)
    ).fetchall()
    old_file_id = None
    for t in rows:
        old_file_id = old_file_id or t["file_id"]
        _conn().execute("UPDATE tasks SET src_output=?, file_id=? WHERE id=?",
                        (rel, artifact_file_id, t["id"]))
        n += 1

    # ---- 汇总类（`打包 ZIP`）另走一条路 ----
    # ⚠ 它没有 `src_task_id`（"一次吃全部文件"，没有单一上一环），所以上面那条
    # 按 `src_task_id` 查的语句**一条都找不到它**。
    # 第一版把这个分支挂在 `if old_file_id:` 里面 —— 而汇总类正是"上面找到 0 条"
    # 的情形，于是这个分支**永远不会执行**，症状是 `转 FLAC → 打包` 的 ZIP 里
    # 装的是源文件（chain_e2e_check 抓到的）。
    # 所以它在**外面**，并且自己从任务行取上游文件 id。
    up = get_task(src_task_id)
    cid = up.chain_id if up else None
    if cid and old_file_id is None:
        old_file_id = up.file_id if up else None
    if cid and old_file_id:
        # **只在串行档做**：并行档的语义就是"不传递产物"，给它回填 `src_output`
        # 会让 `打包` 装上上一步的产物 —— 那就把 `转 FLAC → 打包` 在并行档下的
        # 结果也改掉了，而并行档是**存量行为**，不许动。
        # 判据用**建链时写下的 `chain_mode`**，不用"链上还有没有 src_task_id"：
        # 后者会因为 `files` 的级联删除而变假（见 TaskRow.chain_mode 的注释）。
        #
        # 条件用"**还没记过产物**"（`src_output IS NULL`）而不是 `state='pending'`：
        # 汇总任务可能已经拿到 worker（`running`）—— 它此刻才刚开始走屏障，
        # 还没来得及读 `src_output`，这时候回填是**有效且必要**的；
        # 而 `state='pending'` 会把它排除掉，于是 ZIP 装源文件（这个 bug 实测踩过）。
        # 已经跑完的（success/failed）不回填 —— 那会改掉历史记录的语义。
        if (up.chain_mode if up else None) == "serial":
            # 按 **每个文件** 记，不是只记最后一个。
            # 单个 `src_output` 存不下"这一批里每个文件各自的产物" ——
            # 多文件时会被后来的覆盖掉，于是 ZIP 里只有最后一个文件是对的。
            # 所以写进 params 的 `_artifacts`（`{file_id: relPath}`）。
            for t in _conn().execute(
                "SELECT id, params FROM tasks WHERE chain_id=? AND file_id IS NULL"
                " AND src_task_id IS NULL AND state IN ('pending','running')", (cid,)
            ).fetchall():
                try:
                    p = json.loads(t["params"] or "{}")
                except Exception:
                    p = {}
                ids = p.get("fileIds") or []
                if old_file_id not in ids:
                    continue
                arts = p.get("_artifacts") or {}
                # 记的是 **步骤号 + 任务类型**，不只是路径：
                # `result.sources[].from` 要给用户看"这个成员是哪一步产出的"，
                # 只写"chain-final"等于没说（用户没法据此判断 ZIP 里到底是什么）。
                arts[old_file_id] = {
                    "relPath": rel,
                    "step": int(up.step_idx or 0) + 1 if up else 0,
                    "type": up.type if up else "",
                }
                p["_artifacts"] = arts
                _conn().execute("UPDATE tasks SET params=?, src_output=? WHERE id=?",
                                (json.dumps(p, ensure_ascii=False), rel, t["id"]))
                n += 1
    return n


def chain_derived_artifact(chain_id: str, file_id: str, step_idx: int) -> dict | None:
    """这个文件在这条链上、**早于 `step_idx`** 的最后一次派生产物。→ `{relPath, step, type}`。

    为什么"只查产物行"这么重要（这是"ZIP 偶发装源文件"的根治）：

      · 老实现（`chain_final_artifact`，已随打包语义重写删除）沿着**任务行**推导
        （`tasks_before` → `src_output`）。
        那条路要经过"哪个任务算前置""时间戳怎么排序""回填有没有落地"好几个环节，
        任何一个差一点都会悄悄退回源文件 —— 实测表现为"同一用例跑三次，
        两次装源文件、一次装产物"。
      · 这里直接查**派生产物行**（`files.origin='derived'`）：
        它天生带 `derived_from`（产出它的任务），而任务带 `chain_id` / `step_idx`。
        所以"这条链、这个文件的第 i 步之前的产物"是一条**确定性的 JOIN**，
        不依赖任何"谁先跑完"的时序。产物行是权威事实（它代表磁盘上真有这个文件）。

    ⚠ `h_zip` **不再用它**（打包已改成按窗口一次查全，见 `chain_zip_window`）；
    保留是因为测试拿它断言"旁路产物也必须登记成派生产物行"（ZIP 靠这些行收集成员）。
    `file_id` 用不上（派生产物是新行），保留参数是为了调用点语义清楚。
    """
    init_db()
    r = _conn().execute(
        "SELECT f.rel_path, f.name, t.step_idx, t.type"
        " FROM files f JOIN tasks t ON f.derived_from = t.id"
        " WHERE f.origin='derived' AND t.chain_id=? AND t.step_idx < ?"
        " ORDER BY t.step_idx DESC, f.created_at DESC LIMIT 1",
        (chain_id, step_idx)
    ).fetchone()
    if not r:
        return None
    return {"relPath": r["rel_path"], "name": r["name"],
            "step": int(r["step_idx"] or 0) + 1, "type": r["type"]}



# ---------------------------------------------------------------- 打包窗口
#
# `执行链打包与串行交接方案.md` §4：一个打包步骤收集的是**它自己那个窗口**里的产物 ——
# 「上一个打包步骤（不含）」到「本次打包步骤（不含）」。两种档位的内容规则不同：
#   · 串行：每个文件取窗口内**最后一个音频产物** + 窗口内全部旁路产物
#   · 并行：窗口内**全部**非 zip 产物
# 下面这两个函数是窗口的**唯一实现**（`h_zip` 与调度屏障共用），
# 别再在别处按 `step_idx` 手写一遍。
#
# ⚠ 这里**刻意不沿"任务行 + src_output"推导**（老实现 `chain_final_artifact` /
# `chain_input_for` 就是那么干的，已随本次改动删除）：那条路要经过"哪个任务算前置"
# "时间戳怎么排序""回填有没有落地"好几个环节，任何一个差一点都会**悄悄退回源文件**
# —— 实测表现为"同一用例跑三次，两次装源文件、一次装产物"。
# 窗口解算只认**产物行**（`files.origin='derived'`）：它天生带 `derived_from`
# （产出它的任务），而任务带 `chain_id` / `step_idx`，所以这是一条确定性的查询，
# 不依赖任何"谁先跑完"的时序。

def chain_prev_zip_step(chain_id: str, step_idx: int) -> int:
    """这条链上、**早于 `step_idx`** 的最后一个打包步骤；没有就 -1。"""
    init_db()
    r = _conn().execute(
        "SELECT MAX(step_idx) AS s FROM tasks"
        " WHERE chain_id=? AND type='zip' AND step_idx<?",
        (chain_id, step_idx)).fetchone()
    s = r["s"] if r else None
    return -1 if s is None else int(s)


def chain_zip_window(chain_id: str, step_idx: int) -> dict[str, Any]:
    """打包窗口的全部事实（一次查完，给 `h_zip` 与屏障共用）。

    → `{"from": 下界（含）, "to": 上界（不含）,
         "steps":  {step_idx: {"type", "name", "aggregate"}},
         "artifacts": [{"row_id","producing_file_id","rel_path","name",
                        "step","type","produce"}],
         "tasks":  [{"step_idx","file_id","type","state","src_output","ended_at"}]}`

    `produce` 由**接触面**现算（`contract.produces(op_of_task(...))`）——
    这样才能分清"这一步交出的是音频产物还是旁路产物"，而 ZIP 的两种档位
    对这两类的取舍不同（§4.2）。
    """
    from backend.cards import contract as _contract
    from backend.cards.specs import op_of_task

    init_db()
    lo = chain_prev_zip_step(chain_id, step_idx) + 1
    hi = int(step_idx)
    steps: dict[int, dict[str, Any]] = {}
    artifacts: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []

    for r in _conn().execute(
        "SELECT step_idx, type, params FROM tasks"
        " WHERE chain_id=? AND step_idx>=? AND step_idx<? ORDER BY step_idx",
        (chain_id, lo, hi)
    ).fetchall():
        si = int(r["step_idx"] or 0)
        if si in steps:
            continue                       # 同一步的多个文件，取第一条即可
        try:
            params = json.loads(r["params"] or "{}")
        except (TypeError, ValueError):
            params = {}
        name = str(params.get("_step_name") or "").strip()
        op = op_of_task(str(r["type"]), params)
        if not name:
            # 老数据（`_step_name` 是后加的）：退回落成 op 的中文名
            name = _op_label(op) or str(r["type"])
        steps[si] = {"type": str(r["type"]), "name": name,
                     "aggregate": op == "zip"}

    for r in _conn().execute(
        "SELECT f.id AS row_id, f.rel_path, f.name, t.file_id AS producing_file_id,"
        " t.step_idx, t.type, t.params"
        " FROM files f JOIN tasks t ON f.derived_from = t.id"
        " WHERE f.origin='derived' AND t.chain_id=? AND t.step_idx>=? AND t.step_idx<?"
        " ORDER BY t.step_idx",
        (chain_id, lo, hi)
    ).fetchall():
        try:
            params = json.loads(r["params"] or "{}")
        except (TypeError, ValueError):
            params = {}
        op = op_of_task(str(r["type"]), params)
        artifacts.append({
            "row_id": r["row_id"],
            "producing_file_id": r["producing_file_id"],
            "rel_path": r["rel_path"],
            "name": r["name"],
            "step": int(r["step_idx"] or 0),
            "type": str(r["type"]),
            "produce": (_contract.produces(op)
                        if op in _contract.CONTRACT else "sidecar"),
        })

    for r in _conn().execute(
        "SELECT step_idx, file_id, type, state, src_output, ended_at FROM tasks"
        " WHERE chain_id=? AND step_idx>=? AND step_idx<?",
        (chain_id, lo, hi)
    ).fetchall():
        tasks.append({
            "step_idx": int(r["step_idx"] or 0),
            "file_id": r["file_id"],
            "type": str(r["type"]),
            "state": str(r["state"]),
            "src_output": r["src_output"],
            "ended_at": r["ended_at"],
        })

    return {"from": lo, "to": hi, "steps": steps,
            "artifacts": artifacts, "tasks": tasks}


def _op_label(op: str) -> str:
    """op 的中文名（拿不到就空串 —— 宁可显示任务类型，也不要显示内部 op 名）。"""
    if not op:
        return ""
    try:
        from backend.cards.specs import OPS
        return str((OPS.get(op) or {}).get("label") or "")
    except Exception:                      # pragma: no cover - 兜底，不该发生
        return ""


def reset_stale_running() -> int:
    """启动时把上次进程残留的 running 标为 failed（进程已死，任务不会再完成）。"""
    init_db()
    cur = _conn().execute(
        "UPDATE tasks SET state='failed', error='进程重启，任务中断', ended_at=?"
        " WHERE state='running'", (now(),)
    )
    return cur.rowcount or 0


def reset_stale_chain_pending() -> int:
    """启动时把**带 chain_id 的残留 pending** 一并判失败（方案 §9.9）。

    理由：执行链是一条因果链，进程重启后上游已经断了。让它接着跑只会得到
    "半条链"——后面的步骤会因为 `upstream_settled` 返回 `expired`/`failed_upstream`
    而失败，用户看到一堆莫名其妙的报错。不如一次说清："进程重启，执行链中断"。

    不带 `chain_id` 的 pending **保持不动**：那些是各次独立的批量任务，
    worker 起来接着跑就是对的（现有行为）。
    """
    init_db()
    cur = _conn().execute(
        "UPDATE tasks SET state='failed', error='进程重启，执行链中断', ended_at=?"
        " WHERE state='pending' AND chain_id IS NOT NULL", (now(),)
    )
    return cur.rowcount or 0
