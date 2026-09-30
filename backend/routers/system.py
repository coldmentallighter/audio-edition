"""健康检查、配置、日志、SSE 事件流。"""
from __future__ import annotations

import json
import time as _time

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from backend import config, logs, queue as q_mod, store
from backend.toolchain import toolchain

router = APIRouter()

# 浏览器最后一次心跳时间（前端轮询 /api/heartbeat 时更新）
LAST_BROWSER_SEEN = 0.0

# ================================================================ 基础

@router.get("/api/health")
def health() -> dict:
    tc = toolchain.probe()
    return {
        "ok": tc.all_ok,
        "tools": tc.as_dict(),
        "server": config.SERVER.as_dict(),
        "queue": q_mod.queue_.stats(),
        "browser_active": (_time.time() - LAST_BROWSER_SEEN) < 5.0,
    }

@router.get("/api/heartbeat")
async def heartbeat() -> dict:
    global LAST_BROWSER_SEEN
    LAST_BROWSER_SEEN = _time.time()
    return {"ok": True}

@router.get("/api/config")
def get_config() -> dict:
    return config.SERVER.as_dict()


# ================================================================ 日志

@router.get("/api/logs")
def get_logs(since: int = 0, limit: int = Query(500, ge=1, le=2000)) -> dict:
    """增量拉取运行日志（前端日志面板的真实来源）。"""
    items = logs.tail(since=since, limit=limit)
    return {
        "logs": items,
        "lastSeq": items[-1]["seq"] if items else since,
        "total": logs.count(),
    }


@router.delete("/api/logs")
def clear_logs() -> dict:
    return {"cleared": logs.clear()}


# ================================================================ 功能卡片

# 卡片目录、参数规格、自定义卡片存取都在 backend/cards.py。
# 前端不再写死任何卡片：内置 + 自定义都由 /api/cards 统一给出。

@router.get("/api/events")
async def events(request: Request, interval: float = Query(1.0, ge=0.2, le=5.0)):
    """SSE：周期性推送队列与文件状态快照。

    比 WebSocket 简单，本地单机足够（需求 §3）。
    """
    async def gen():
        last = ""
        while True:
            if await request.is_disconnected():
                break
            snap = {
                "queue": store.queue_snapshot(limit=30),
                "files": [
                    {"id": f.id, "state": f.state, "tasks": f.task_summary}
                    for f in store.list_files(limit=500)
                ],
            }
            payload = json.dumps(snap, ensure_ascii=False)
            if payload != last:                 # 无变化不重复推
                last = payload
                yield f"data: {payload}\n\n"
            else:
                yield ": keepalive\n\n"
            import asyncio
            await asyncio.sleep(interval)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
