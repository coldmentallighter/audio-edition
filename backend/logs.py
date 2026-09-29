"""运行日志环形缓冲。

后端此前没有任何日志出口，前端只能显示写死的 7 行假日志。
这里提供进程内的环形缓冲 + /api/logs 出口，任务生命周期由 queue 写入。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime
from typing import Any

MAX_LINES = 2000

_buf: deque[dict[str, Any]] = deque(maxlen=MAX_LINES)
_lock = threading.Lock()
_seq = 0


def log(message: str, kind: str = "", *, source: str = "app") -> dict[str, Any]:
    """写一行日志。kind: '' | ok | err | warn"""
    global _seq
    with _lock:
        _seq += 1
        entry = {
            "seq": _seq,
            "ts": time.time(),
            "time": datetime.now().strftime("%H:%M:%S"),
            "kind": kind,
            "source": source,
            "message": message,
        }
        _buf.append(entry)
        return entry


def ok(message: str, **kw) -> dict:
    return log(message, "ok", **kw)


def err(message: str, **kw) -> dict:
    return log(message, "err", **kw)


def warn(message: str, **kw) -> dict:
    return log(message, "warn", **kw)


def tail(since: int = 0, limit: int = 500) -> list[dict[str, Any]]:
    """取 seq 大于 since 的日志（增量拉取用）。"""
    with _lock:
        items = [e for e in _buf if e["seq"] > since]
    return items[-limit:]


def clear() -> int:
    with _lock:
        n = len(_buf)
        _buf.clear()
    return n


def count() -> int:
    with _lock:
        return len(_buf)
