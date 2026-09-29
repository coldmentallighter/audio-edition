"""任务队列：有界并发的后台执行器。

需求约束：
  · 批量任务不能阻塞 WebUI（§5）—— 全部丢到线程池
  · 并发数受限（§4.8）—— 默认 2
  · 每个文件生成独立任务，统一进队列（§4.6）
  · 支持取消、失败重试、聚合进度
"""
from __future__ import annotations

import queue
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from backend import config, logs, store

# 任务处理器签名： (task, ctx) -> (ok, result, error)
Handler = Callable[[store.TaskRow, "Context"], tuple[bool, dict, str]]


@dataclass
class Context:
    """交给处理器的最小上下文。"""
    task_id: str
    cancel: threading.Event

    def progress(self, pct: float) -> None:
        store.update_progress(self.task_id, pct)

    @property
    def cancelled(self) -> bool:
        return self.cancel.is_set()


class Queue:
    def __init__(self, workers: int = config.MAX_CONCURRENCY) -> None:
        self._q: queue.Queue[str] = queue.Queue()
        self._handlers: dict[str, Handler] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._workers: list[threading.Thread] = []
        self._n = max(1, workers)
        self._running = False
        self._active = 0

    # ---------- 注册 ----------

    def register(self, type_: str, handler: Handler) -> None:
        self._handlers[type_] = handler

    def handlers(self) -> list[str]:
        return sorted(self._handlers)

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        for i in range(self._n):
            t = threading.Thread(target=self._loop, name=f"ae-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)

    def stop(self) -> None:
        self._running = False
        for _ in self._workers:
            self._q.put("")            # 哨兵唤醒
        for t in self._workers:
            t.join(timeout=2)
        self._workers.clear()

    # ---------- 入队 ----------

    def submit(self, task_id: str) -> None:
        """把已入库的任务放进执行队列。"""
        self._q.put(task_id)

    def cancel(self, task_id: str) -> None:
        with self._lock:
            ev = self._cancels.get(task_id)
        if ev:
            ev.set()
        store.cancel_task(task_id)

    # ---------- 工作循环 ----------

    def _loop(self) -> None:
        while self._running:
            try:
                tid = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if not tid:
                continue
            self._run_one(tid)

    def _run_one(self, tid: str) -> None:
        task = store.get_task(tid)
        if not task:
            return
        if task.state not in ("pending", "running"):
            return                       # 已取消
        handler = self._handlers.get(task.type)
        if not handler:
            store.finish_task(tid, ok=False, error=f"没有 {task.type} 的处理器")
            logs.err(f"✗ {task.type} 无处理器")
            return

        ev = threading.Event()
        with self._lock:
            self._cancels[tid] = ev
            self._active += 1
        store.start_task(tid)

        label = self._label(task)
        logs.log(f"▶ {task.type}  {label}")

        ctx = Context(task_id=tid, cancel=ev)
        ok, result, error = False, {}, ""
        t0 = time.time()
        try:
            ok, result, error = handler(task, ctx)
        except Exception as e:
            ok = False
            error = f"{type(e).__name__}: {e}"
            result = {"traceback": traceback.format_exc()[-1500:]}
            logs.err(f"✗ {task.type}  {label}  {error}")
        finally:
            dur = time.time() - t0
            if ev.is_set():
                store.cancel_task(tid)
                logs.warn(f"⊘ {task.type}  {label}  已取消")
            else:
                store.finish_task(tid, ok=ok, result=result, error=error)
                if ok:
                    out = (result or {}).get("output")
                    extra = f" → {out}" if out else ""
                    logs.ok(f"✓ {task.type}  {label}  {dur:.1f}s{extra}")
                else:
                    logs.err(f"✗ {task.type}  {label}  {error}")
            with self._lock:
                self._cancels.pop(tid, None)
                self._active -= 1
            self._q.task_done()

    @staticmethod
    def _label(task: store.TaskRow) -> str:
        """日志里显示可读的文件名，而不是 id。"""
        if not task.file_id:
            return task.type
        f = store.get_file(task.file_id)
        return f.name if f else task.file_id[:8]

    # ---------- 统计 ----------

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    def stats(self) -> dict[str, Any]:
        return {
            "workers": self._n,
            "active": self.active,
            "queued": self._q.qsize(),
            "registered": self.handlers(),
        }


# 全局单例
queue_ = Queue()


# ---------------------------------------------------------------- 批量辅助

def new_batch_id() -> str:
    return store.new_id("b_")


def submit_batch(type_: str, file_ids: list[str], params_for: Callable[[str], dict],
                 batch_id: str | None = None) -> dict[str, Any]:
    """为一个批量的每个文件各建一个任务并入队（需求 §4.6）。"""
    bid = batch_id or new_batch_id()
    created = []
    for fid in file_ids:
        t = store.create_task(type_, file_id=fid, params=params_for(fid), batch_id=bid)
        created.append(t.id)
    for tid in created:
        queue_.submit(tid)
    return {"batchId": bid, "taskIds": created, **store.batch_summary(bid)}


def retry_task(tid: str) -> Optional[str]:
    """失败任务重试：复制出一条新任务（需求 §4.5）。"""
    old = store.get_task(tid)
    if not old or old.state not in ("failed", "cancelled"):
        return None
    new = store.create_task(old.type, file_id=old.file_id,
                            params=old.params, batch_id=old.batch_id)
    queue_.submit(new.id)
    return new.id
