"""按域拆分的路由模块。app.py 在静态挂载**之前** include 它们。

注意 tasks 路由模块叫 `task_queue` 而不是 `tasks` —— 否则
`from backend import tasks`（任务处理器）会和它撞名。
"""
from backend.routers import catalog, files, ops, system, task_queue

__all__ = ["system", "catalog", "files", "task_queue", "ops"]
