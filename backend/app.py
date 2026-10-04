"""AudioEdition 本地后端（FastAPI）。

启动：python -m backend.app   或   run.bat / run.sh

这里只放"应用骨架"：生命周期、CORS、路由装配、静态挂载、入口。
具体接口按域拆在 backend/routers/ 下：

    system   健康检查 / 配置 / 日志 / SSE 事件流
    catalog  操作目录、卡片 CRUD、快照
    files    上传、文件列表、标签、封面、峰值、下载、批量删除
    tasks    任务队列查询与重试/取消
    ops      12 个 op 的提交入口（POST /api/ops/<route>）
"""
from __future__ import annotations

import warnings
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import config, queue as q_mod, store, tasks
from backend.cards import store as cards_store
from backend.routers import catalog, files, ops, system as system_routes, task_queue
from backend.toolchain import toolchain

# 屏蔽第三方库的弃用噪音，保持控制台干净（本地工具，用户要看得清自己的日志）
warnings.filterwarnings("ignore", category=DeprecationWarning)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动/关闭钩子（替代已弃用的 on_event）。"""
    config.ensure_dirs()

    # 「每次打开都是空页面」：先把上一轮的工作区清干净，再建表。
    # 必须在任何请求进来之前做完，否则页面首帧可能读到上一轮的文件。
    # 导入是复制进 uploads/ 的，清掉的只是工作副本。
    if config.FRESH_ON_START:
        wiped = config.wipe_workspace()
        config.reset_db()
        total = sum(wiped.values())
        if total:
            print(f"[startup] 清空工作区 uploads={wiped['uploads']} "
                  f"outputs={wiped['outputs']} cache={wiped['cache']}")
        else:
            print("[startup] 工作区已是空的")

    store.init_db()
    added = store.take_migration_log()
    if added:
        # 迁移**不能静默发生**：出问题时得有人知道库被改过
        print(f"[startup] 数据库补列 {len(added)} 处: {', '.join(added)}")
    stale = store.reset_stale_running()
    if stale:
        print(f"[startup] 清理中断任务 {stale} 条")
    stale_chain = store.reset_stale_chain_pending()
    if stale_chain:
        print(f"[startup] 清理中断的执行链任务 {stale_chain} 条")
    store.release_all_leases()          # 内存态，重启即空（这里显式清一次，防止热重载残留）
    tasks.register_all(q_mod.queue_)
    q_mod.queue_.start()
    tc = toolchain.probe()
    for name, t in tc.as_dict().items():
        mark = "OK  " if t["ok"] else "FAIL"
        print(f"[toolchain] [{mark}] {name:<9} {(t['version'] or t['note'])[:60]}")
    print(f"[startup] 就绪 → http://{config.HOST}:{config.PORT}")
    try:
        yield
    finally:
        q_mod.queue_.stop()


app = FastAPI(title="AudioEdition", version="0.1.0", docs_url="/api/docs",
              lifespan=lifespan)

# 仅本机使用，放开同源限制便于 file:// 调试
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================ 路由装配
# 必须在下面的静态挂载**之前** include，否则 "/" 那个 catch-all 会吞掉 /api/*。
for _r in (system_routes.router, catalog.router, files.router,
           task_queue.router, ops.router):
    app.include_router(_r)


@app.exception_handler(cards_store.CardsFileError)
async def _cards_file_error(_request, exc: cards_store.CardsFileError):
    """`cards.json` 读不出来 → 500 + 人话，**绝不静默当成"你没有卡片"**。

    全局注册而不是逐个端点包：卡片文件被 CRUD / 快照 / 预设三条路径读写，
    漏掉任何一个都等于漏掉一次"把用户卡片写没"的机会。
    见 `backend/cards/store.py` 顶部那段（老板问"卡片怎么一直消失"）。
    """
    return JSONResponse(status_code=500,
                        content={"detail": {"message": str(exc), "kind": "cards-file"}})


# ================================================================ 静态前端

class NoCacheStatic(StaticFiles):
    """静态资源强制每次回源校验。

    StaticFiles 只发 ETag / Last-Modified，**不发 Cache-Control**，
    浏览器于是按"启发式缓存"处理（新鲜期≈文件年龄的 10%）——
    改完 CSS 刷新页面，可能拿到的还是旧文件，表现为"我改了但界面没变"。
    本地工具不存在带宽问题，直接 no-cache（仍然走 304，开销极小）。

    **`index.html` 额外用 `no-store`**：它引用 `app.js` / 各个 css，
    一旦它自己被缓存住，后面引用的是哪一版就完全不由我们决定了。
    这条真踩过 —— 后端修好、后端测试全绿，页面却一直是旧行为，
    排查时把"缓存"当成了"逻辑错"，绕了很久。入口文件不值得为省一次
    304 换来这种不确定性。
    """

    def file_response(self, *args, **kwargs):        # type: ignore[override]
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        # 入口文件额外 no-store
        try:
            full = args[0] if args else kwargs.get("full_path")
            name = str(getattr(full, "name", "") or full or "")
            if name.endswith((".html", ".htm")):
                resp.headers["Cache-Control"] = "no-store, must-revalidate"
                resp.headers["Pragma"] = "no-cache"
        except Exception:                            # noqa: BLE001
            pass                                     # 拿不到路径就退回 no-cache
        return resp


WEB = config.ROOT
if (WEB / "index.html").exists():
    app.mount("/", NoCacheStatic(directory=str(WEB), html=True), name="web")


# ================================================================ 入口

def main() -> None:
    import uvicorn
    config.ensure_dirs()
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
