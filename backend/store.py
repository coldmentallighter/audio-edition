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
TASK_STATES = ("pending", "running", "success", "failed", "cancelled")
TASK_TYPES = ("probe", "convert", "tag_edit", "cover_embed", "cover_extract",
              "cover_remove", "peaks", "normalize", "rename", "waveform", "verify", "zip")

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
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_state ON files(state);

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
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tasks_file  ON tasks(file_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_tasks_batch ON tasks(batch_id);
"""

_initialized = False
_init_lock = threading.Lock()


def init_db() -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        c = _conn()
        c.executescript(SCHEMA)
        _initialized = True


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
    created_at: float = 0.0
    updated_at: float = 0.0
    # 派生字段（由 aggregate_state 填）
    task_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> Path:
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
    return FileRow(
        id=r["id"], rel_path=r["rel_path"], name=r["name"], size=r["size"],
        mtime=r["mtime"], state=r["state"], info=info,
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
        return get_file(row["id"])                       # type: ignore[return-value]
    fid = new_id("f_")
    c.execute(
        "INSERT INTO files(id, rel_path, name, size, mtime, state, info, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (fid, rel, p.name, size, mtime, state, json.dumps(info or {}, ensure_ascii=False), t, t),
    )
    return get_file(fid)                                 # type: ignore[return-value]


def get_file(fid: str) -> Optional[FileRow]:
    init_db()
    r = _conn().execute("SELECT * FROM files WHERE id = ?", (fid,)).fetchone()
    if not r:
        return None
    f = _row_to_file(r)
    f.task_summary = aggregate_state(fid)
    f.state = f.task_summary.get("fileState", f.state)
    return f


def list_files(*, include_deleted: bool = False, limit: int = 2000) -> list[FileRow]:
    init_db()
    sql = "SELECT * FROM files"
    if not include_deleted:
        sql += " WHERE state != 'deleted'"
    sql += " ORDER BY created_at DESC LIMIT ?"
    rows = _conn().execute(sql, (limit,)).fetchall()
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
    return TaskRow(
        id=r["id"], file_id=r["file_id"], type=r["type"], state=r["state"],
        progress=r["progress"], params=j(r["params"]), result=j(r["result"]),
        error=r["error"] or "", batch_id=r["batch_id"],
        created_at=r["created_at"], started_at=r["started_at"], ended_at=r["ended_at"],
    )


def create_task(type_: str, *, file_id: str | None = None,
                params: dict | None = None, batch_id: str | None = None,
                state: str = "pending") -> TaskRow:
    if type_ not in TASK_TYPES:
        raise ValueError(f"非法任务类型: {type_}")
    if state not in TASK_STATES:
        raise ValueError(f"非法任务状态: {state}")
    init_db()
    tid = new_id("t_")
    _conn().execute(
        "INSERT INTO tasks(id, file_id, type, state, params, batch_id, created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (tid, file_id, type_, state, json.dumps(params or {}, ensure_ascii=False),
         batch_id, now()),
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
        if st in ("deleted", "uploaded", "ready"):
            return {"fileState": st, "total": 0, "running": 0, "pending": 0,
                    "success": 0, "failed": 0, "progress": 0.0}
        return {"fileState": st, "total": 0, "running": 0, "pending": 0,
                "success": 0, "failed": 0, "progress": 0.0}

    running = [r for r in rows if r["state"] == "running"]
    pending = [r for r in rows if r["state"] == "pending"]
    success = [r for r in rows if r["state"] == "success"]
    failed = [r for r in rows if r["state"] == "failed"]

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
        "lastError": (failed[0]["error"] if failed else ""),
    }


def batch_summary(batch_id: str) -> dict[str, Any]:
    """批量聚合进度：已完成 12 / 20（需求 §4.6）。"""
    init_db()
    rows = _conn().execute(
        "SELECT state, COUNT(*) n FROM tasks WHERE batch_id=? GROUP BY state", (batch_id,)
    ).fetchall()
    counts = {r["state"]: r["n"] for r in rows}
    total = sum(counts.values())
    done = counts.get("success", 0) + counts.get("failed", 0) + counts.get("cancelled", 0)
    return {
        "batchId": batch_id,
        "total": total,
        "done": done,
        "success": counts.get("success", 0),
        "failed": counts.get("failed", 0),
        "cancelled": counts.get("cancelled", 0),
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


def reset_stale_running() -> int:
    """启动时把上次进程残留的 running 标为 failed（进程已死，任务不会再完成）。"""
    init_db()
    cur = _conn().execute(
        "UPDATE tasks SET state='failed', error='进程重启，任务中断', ended_at=?"
        " WHERE state='running'", (now(),)
    )
    return cur.rowcount or 0
