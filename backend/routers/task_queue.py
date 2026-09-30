"""任务队列的只读查询与重试/取消。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from backend import queue as q_mod, store

router = APIRouter()

@router.get("/api/tasks")
def list_tasks(fileId: Optional[str] = None, batchId: Optional[str] = None,
               states: Optional[str] = None, limit: int = Query(100, ge=1, le=1000)) -> dict:
    st = states.split(",") if states else None
    rows = store.list_tasks(file_id=fileId, batch_id=batchId, states=st, limit=limit)
    return {"tasks": [t.as_dict() for t in rows]}


@router.get("/api/tasks/queue")
def task_queue() -> dict:
    return store.queue_snapshot()


@router.get("/api/tasks/{tid}")
def get_task(tid: str) -> dict:
    t = store.get_task(tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    return t.as_dict()


@router.post("/api/tasks/{tid}/cancel")
def cancel_task(tid: str) -> dict:
    t = store.get_task(tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    q_mod.queue_.cancel(tid)
    return {"ok": True}


@router.post("/api/tasks/{tid}/retry")
def retry_task(tid: str) -> dict:
    nid = q_mod.retry_task(tid)
    if not nid:
        raise HTTPException(400, "只有失败或已取消的任务可以重试")
    return {"ok": True, "taskId": nid}


# ================================================================ 操作
