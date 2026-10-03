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

# ---------------------------------------------------------------- 准入常量
#
# 闸门给出的结论（方案 §9.3.3）。`"ready"` 之外一律不进 `start_task`。
REASON_READY = "ready"
REASON_UPSTREAM = "upstream"                # 上游还在跑 → **能等**，放回队尾
REASON_LEASE = "lease"                      # 同文件写者占着 → **能等**，放回队尾
REASON_SLOT = "slot"                        # 长任务槽位满了 → **能等**，放回队尾
REASON_EXPIRED = "expired"                  # 上游任务没了 → 当场结案
REASON_FAILED_UPSTREAM = "failed_upstream"   # 上游失败/取消/跳过 → 当场结案（不回落）
REASON_CYCLE = "cycle"                      # src_task_id 指回自己 → 当场结案（防死循环）
# **能等**的是前三个；后三个必须当场结案，否则会无限推迟（§9.3.5）
CAN_WAIT = (REASON_UPSTREAM, REASON_LEASE, REASON_SLOT)
# 推迟截止：兜住"依赖永远不可能满足"这种僵死。内存记账，重启即清。
#
# ⚠⚠ **必须按"等了多久"算，不能按"推迟了多少次"算。** 原来是 `DEFER_LIMIT = 200`
# 次 —— 而退避是 0.05s，于是**200 次 ≈ 10~20 秒**就把一个还在正常等待的任务判死。
# 这在串行链上是**必然踩**的：第 N 步要等前面 N-1 步跑完，而 `标准化` 一个 3 分钟
# 的文件就要 5 秒，4 步链轻松超过 20 秒 —— 现象是"链跑到最后一步突然失败：
# 依赖无法满足（上游还没结束）"，而上游**明明成功了**（就在它被判死的 1 秒后）。
# 用户实测踩到过（4 步链，第 4 步 failed、前 3 步 success）。
#
# 时间口径还顺带把另一个隐患修了：退避一旦改成指数增长，计数口径就更没意义了
# （同样的次数可能对应几秒，也可能对应几小时）。
DEFER_DEADLINE = 30 * 60          # 单个任务最多等 30 分钟
# 被推迟之后隔多久才允许再探测（秒）。指数增长，封顶 1 秒。
# 取舍：太小 → 队列极短时空转；太大 → 上游一结束还要多等一会儿才接上。
DEFER_BACKOFF = 0.05
DEFER_BACKOFF_MAX = 1.0

# 汇总类屏障为"产物交付"额外等待的时限（秒）。
# `finish_task(success)` 与 `_publish_derived` 之间有窗口，屏障要等那一下；
# 但**不能无限等**（前置失败、或历史遗留的"成功但没产物"的任务永远不会交付）。
# 2 秒远大于那个窗口（实测是毫秒级），又远小于用户的耐心。
ATTACH_GRACE = 2.0
REASON_CN = {
    REASON_UPSTREAM: "上游还没结束",
    REASON_LEASE: "同一个文件上有别的步骤在跑",
    REASON_SLOT: "长任务槽位已满，先让短任务跑",
    REASON_EXPIRED: "上游任务不存在",
    REASON_FAILED_UPSTREAM: "上一步失败",
    REASON_CYCLE: "依赖成环",
}

# 级联失败的统一前缀。**必须是一个稳定的前缀**，因为 `_is_cascade` 靠它判断
# "这条 failed 是它自己跑坏的，还是因为上游坏了而没能跑"：
#   · 自己跑坏 → 它就是断点 → 下游判 `failed`（红着，指出断点）
#   · 因为上游坏而没跑 → 它也是"轮不到" → 更下游判 `skipped`（灰着）
# 第一版没加前缀、靠"上游的文案里有没有'上一步失败'"来猜，于是三步链的第 3 步
# 跟着第 2 步一起变红（§3.6 要求的是"第一环红、其后灰"）。
CASCADE_PREFIX = "前序步骤失败"


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


def _produces_derived(task_type: str) -> bool:
    """这个任务类型会不会产出"下一步要吃的那个新文件"（`produce='derived'`）。

    用接触面表反查，而不是写死任务类型名 —— 加 op 时不用改这里。
    """
    from backend.cards.contract import CONTRACT, produces
    from backend.cards.specs import OPS

    for op, spec in OPS.items():
        if str(spec.get("task") or op) == task_type and op in CONTRACT:
            return produces(op) == "derived"
    return False


class Queue:
    def __init__(self, workers: int = config.MAX_CONCURRENCY,
                 long_slots: int | None = None) -> None:
        self._q: queue.Queue[str] = queue.Queue()
        self._handlers: dict[str, Handler] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._workers: list[threading.Thread] = []
        self._n = max(1, workers)
        self._running = False
        self._active = 0
        # 长任务**最多占几个 worker**（§3.5）。`0` = 不限制。
        # 下限是 1，且**并发为 1 时自动失效** —— "留一个给短任务"在只有
        # 一个 slot 时等于不让长任务跑，那是死锁（长任务永远排不上）。
        #
        # `long_slots=None` → 按**实际 worker 数**推 `max(1, n-1)`。
        # 别用 `config.LONG_TASK_SLOTS` 当默认值：它是按默认并发算的，
        # `Queue(workers=4)` 时会给成 1（本该 3）。
        if long_slots is None:
            long_slots = config.LONG_TASK_SLOTS
        if self._n <= 1 or (long_slots is not None and long_slots <= 0):
            self._long_slots = 0
        else:
            cap = long_slots if long_slots is not None else max(1, self._n - 1)
            self._long_slots = max(1, min(cap, self._n))
        # 当前正在跑的长任务数。**只在这里加减**，别的路径一律不碰它 ——
        # 漏减就是"长任务再也排不上"（表现和死锁一样），
        # 所以 `_run_one` 的 `finally` 里必须成对地减。
        self._long_active = 0
        # ---------- 执行链的准入记账（方案 §9.3，全是内存态）----------
        # tid → 下次才允许再探测的时间戳。
        #
        # **为什么是"退避时间"而不是"本轮探测过的集合"**（这是实现时才想清楚的）：
        # 集合方案有个致命缺陷 —— 集合只在"有任务跑完"时才清空，
        # 而一个被推迟的任务可能**永远等不到那一刻**（比如解锁它的上游任务
        # 是被外部标成失败的，没有任何 worker 会去清）。那样任务就永远停在
        # `pending`，队列看着像卡死。写成时间戳之后，"过一会儿总会再试一次"
        # 是天然成立的，不依赖任何事件。
        #
        # 它同时解决了第二个问题：队列极短时（只剩一个够不着的任务），
        # worker 会在"取出→放回"上打满一个核 —— 退避期内直接跳过，不空转。
        self._defer_until: dict[str, float] = {}
        # tid → 被推迟的次数。**只用来算退避时长**（指数增长），不再用来判死。
        # **不持久化**：重启后 reset_stale_* 本来就把残留任务判失败了。
        self._defer_count: dict[str, int] = {}
        # tid → 第一次被推迟的时刻。`DEFER_DEADLINE` 按它算 ——
        # "等了多久"才是"依赖等得起等不起"的正确口径（见 DEFER_DEADLINE 注释）。
        self._defer_since: dict[str, float] = {}

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
            t = threading.Thread(target=self._loop, args=(i,),
                                 name=f"ae-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)

    def stop(self) -> None:
        self._running = False
        for _ in self._workers:
            self._q.put("")            # 哨兵唤醒
        for t in self._workers:
            t.join(timeout=2)
        self._workers.clear()
        self._defer_count.clear()
        self._defer_until.clear()
        self._defer_since.clear()
        store.release_all_leases()

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

    def _loop(self, wid: int = 0) -> None:
        """取任务 → 过闸 → 跑。

        防空的机制是 `_defer_until` 那张退避表（见 `__init__` 里的注释）：
        被推迟过的任务在退避期内**直接跳过**，既不打满 CPU，也不会把它
        永久漏掉 —— 退避期一过就会再试。
        """
        while self._running:
            try:
                tid = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if not tid:
                continue
            if time.monotonic() < self._defer_until.get(tid, 0.0):
                # 还在退避期：原样放回队尾。
                # **这个 sleep 别省**：没有它，"取出→放回"是个纯 Python 忙循环。
                self._q.put(tid)
                time.sleep(DEFER_BACKOFF)
                continue
            self._defer_until.pop(tid, None)
            self._run_one(tid)

    def _gate(self, task: store.TaskRow) -> str:
        """开工前的闸门（方案 §3.4 / §9.3.3）。判据按代价从低到高。

        返回值是模块里的 `REASON_*` 常量；只有 `REASON_READY` 才允许往下跑。
        **这个函数一行数据库状态都不许改** —— defer 的任务必须留在 `pending`。
        """
        # ① 上游就绪（只有串行档会填 src_task_id，所以并行档这一步恒过）
        settled, why = store.upstream_settled(task)
        if not settled:
            return why if why in ("expired", "failed_upstream", "cycle") else REASON_UPSTREAM
        # ①b 汇总类的全局屏障（§3.2.3）：它没有单一上游，要等**所有**前置步骤
        if self._aggregate_barrier_unready(task):
            return REASON_UPSTREAM
        # ①c 长任务的槽位限制（§3.5）：长任务最多占 n-1 个 worker。
        # 放在最后 —— 它比上游/屏障更"软"（上游没到永远等不到，
        # 而槽位只是"再等等就轮到你"），先判确定性的那两个能少做无用的 defer。
        if self._long_slot_busy(task):
            return REASON_SLOT
        # ② 同文件租约：写者独占、读者共享
        if not store.acquire_file_leases(task):
            return REASON_LEASE
        return REASON_READY

    def _long_slot_busy(self, task: store.TaskRow) -> bool:
        """长任务还排得上吗（§3.5 的"别让长任务占满全部槽位"）。

        `self._long_slots == 0` 表示不限制（并发为 1、或配置成 0）。
        判据只看**任务类型**（`config.LONG_TASK_TYPES`）：时长要跑起来才知道，
        而准入必须在开始前决定 —— 所以这是一张名单，不是一次测量。

        ⚠ 注意它**不是**"长任务串行"：`_long_slots` 是 1 时确实等于串行，
        但那是默认并发 2 下的结果；`AE_CONCURRENCY=4` 时会自动放到 3。
        """
        if not self._long_slots:
            return False
        if task.type not in config.LONG_TASK_TYPES:
            return False
        with self._lock:
            return self._long_active >= self._long_slots

    @staticmethod
    def _is_cascade(task: store.TaskRow) -> bool:
        """这条 `failed` 是不是"因为上游失败而没能跑"（而不是自己跑坏了）。

        判据是 `CASCADE_PREFIX` 这个**由调度器写下的稳定前缀** ——
        跑坏的 handler 不可能产生它。
        """
        return CASCADE_PREFIX in (task.error or "")

    @classmethod
    def _immediate_upstream_failed(cls, task: store.TaskRow) -> bool:
        """失败的是**直接**上游，还是更上游？

        §3.6 要求：**第一环 failed（红着，指出断点），其后一路 skipped（灰着）**。
        所以判据不能只看"上游是不是 failed"，还要看**那个 failed 是不是它自己跑坏的**：

          · 上游 `failed` 且**不是级联** → 它就是断点 → 本步 failed
          · 上游 `failed` 但是**级联**   → 断点在更上面 → 本步 skipped
          · 上游 `skipped` / `cancelled` → 本步 skipped

        第一版只判了"上游是不是 failed"，于是三步链的第 3 步跟着第 2 步一起变红 ——
        用户看到"两个都坏了"，而实际只有第 2 步坏（这是 chain_e2e_check 抓到的）。
        """
        up = store.get_task(task.src_task_id) if task.src_task_id else None
        if up is None:
            return False
        if up.state == "failed":
            return not cls._is_cascade(up)
        return False

    def _aggregate_barrier_unready(self, task: store.TaskRow) -> bool:
        """汇总类任务（`打包 ZIP`）的**全局屏障**：它要等链上所有前置步骤落定。

        ⚠ **为什么不能只看"库里已经有哪些前置任务"**（第一版就是这么错的）：
        建链是"逐条 INSERT"，而任务**在全部建好之后才一起入队**……
        看起来没问题，但 `zip` 完全可能在建链还没走到它自己那一步之前
        就已经被 worker 取走（建链的 INSERT 与 worker 的 SELECT 是两条独立连接）。
        那一刻"这个文件的前置任务"查出来是**空**，屏障于是认为"前面没东西，可以开跑"，
        结果它在转换还没建出来时就把**源文件**装走了 —— 症状是
        "ZIP 里是原文件而不是产物"，而且**偶发**（看谁先跑）。

        所以判据必须用**建链时就写死的 `chain_steps`**（这条链一共几步）：
          · `step_idx > 0` 但前置步骤在库里数不满 → 还没轮到它 → 等
          · 前置步骤齐了但还有 pending/running → 等

        这条也顺带说明为什么"约束要从数据里推、而不是从时序里猜"。
        """
        if not (task.chain_id and task.step_idx is not None):
            return False
        if task.step_idx == 0:
            return False                    # 它自己是第一项 → 装源文件，无需等
        now = time.time()
        anchor = store.chain_started_at(task.chain_id)
        for fid in (task.params.get("fileIds") or []):
            state = store.file_prev_step_state(task.chain_id, fid, task.step_idx,
                                               before_ts=anchor)
            # ① 前置还在排队/在跑 → **无条件等**。
            #    这一条与"建链进行中"无关；上一轮把它和 ② 混在一个 `if` 里、
            #    用 `chain_build_in_progress` 一起短路，于是守卫一松就在这里放行，
            #    症状正是"偶发：没有任何文件可以打包：X（第 1 步（convert）还在跑）"。
            if state in ("pending", "running"):
                return True
            # ② 前置行**缺失** → 只在"建链可能还在插入"时等。
            #    建链早就结束却仍然缺，说明那一步对这批文件没建任务，等也等不到。
            #    排除自己：`_gate` 在 `start_task` 之前跑，**它自己此刻就是 pending**。
            if state == "missing" and store.chain_build_in_progress(
                    task.chain_id, exclude_task_id=task.id):
                return True
            # ③ 刚成功、产物还没交付 → 等一下那次回填（带时限：
            #    "成功但本来就不产出文件"的步骤永远不会交付，不能无限等）
            prev = store.tasks_before(task.chain_id, task.step_idx, fid,
                                      before_ts=anchor)
            for p in prev:
                if (p.state == "success" and not p.src_output
                        and _produces_derived(p.type)
                        and (now - (p.ended_at or 0)) < ATTACH_GRACE):
                    return True
        return False

    def _requeue(self, task: store.TaskRow, reason: str) -> None:
        """没就绪 → 放回**队尾** + 记账。绝不调用 `start_task`、绝不 `task_done`。

        ⚠ **必须把租约还回去**：`_gate` 走到 ② 时已经拿到租约了，
        若这里不释放，下次探测时它自己的租约会被当成"别人在写"，
        于是**永远推迟**（这个 bug 实测踩过：任务一直停在 pending，
        看起来像队列卡死）。而且它会连带挡住整个文件的其它任务。
        """
        tid = task.id
        store.release_file_leases(tid)
        n = self._defer_count.get(tid, 0) + 1
        started = self._defer_since.setdefault(tid, time.monotonic())
        waited = time.monotonic() - started
        if waited > DEFER_DEADLINE:
            self._defer_count.pop(tid, None)
            self._defer_until.pop(tid, None)
            self._defer_since.pop(tid, None)
            store.finish_task(tid, ok=False,
                              error=f"依赖无法满足（{REASON_CN.get(reason, reason)}）")
            logs.warn(f"⊘ {task.type} 依赖无法满足，已判失败"
                      f"（等了 {waited:.0f}s，推迟 {n} 次）")
            return
        self._defer_count[tid] = n
        # 退避指数增长、封顶 1 秒：等待初期灵敏（上游一结束就接上），
        # 长时间等待时也不再空转（30 分钟最多约 1800 次探测，而不是 36000 次）。
        backoff = min(DEFER_BACKOFF * (2 ** min(n - 1, 20)), DEFER_BACKOFF_MAX)
        self._defer_until[tid] = time.monotonic() + backoff
        self._q.put(tid)               # 队尾，不是队首

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

        # ── 闸门必须在 start_task **之前**（§9.3.2 约束 1）──────────────
        # 写在后面的话，没就绪的任务会被标成 running，而 aggregate_state
        # 只要有 running 就返回 processing —— 文件状态显示"处理中"、进度条卡住，
        # UI **永远**转圈。
        reason = self._gate(task)
        if reason in (REASON_EXPIRED, REASON_CYCLE):
            # 当场结案：上游没了 / 成环 —— 这是"我尝试过但做不了"
            store.finish_task(tid, ok=False,
                              error=f"依赖无法满足（{REASON_CN.get(reason, reason)}）")
            logs.warn(f"⊘ {task.type} {REASON_CN.get(reason, reason)}，已判失败")
            return
        if reason == REASON_FAILED_UPSTREAM:
            # 直接上游失败 vs 更上游失败 —— **两者要分开**（§3.6）：
            #   · 直接上游自己跑坏了 → 本步 failed（红着，指出断点）
            #   · 上游也是级联失败   → 本步 skipped（灰着，一路灰到底）
            # 第一版只判"上游是不是 failed"，于是三步链的第 3 步跟着第 2 步
            # 一起变红，用户看到"两个都坏了"而实际只坏了一个。
            if self._immediate_upstream_failed(task):
                store.finish_task(tid, ok=False, error=CASCADE_PREFIX)
                logs.err(f"✗ {task.type} {CASCADE_PREFIX}（上一步失败）")
            else:
                store.skip_task(tid, CASCADE_PREFIX)
                logs.warn(f"⊘ {task.type} {CASCADE_PREFIX}，已跳过")
            return
        if reason != REASON_READY:
            self._requeue(task, reason)
            return
        # ────────────────────────────────────────────────────────────

        ev = threading.Event()
        # 长任务计数**必须在同一个 `with self._lock` 里加**，而且只在
        # "过了闸、真的要跑"这一刻加 —— 在 `_gate` 里加就错了：
        # `_gate` 后面还有租约那一步，租约失败会 `_requeue`（不跑），
        # 那样计数只增不减，长任务槽位很快永久占满。
        counts_long = bool(self._long_slots
                           and task.type in config.LONG_TASK_TYPES)
        with self._lock:
            self._cancels[tid] = ev
            self._active += 1
            if counts_long:
                self._long_active += 1
        store.start_task(tid)
        # 真正开工之后就可以清掉推迟记账了：它已经不再是"等不到"的状态
        self._defer_count.pop(tid, None)
        self._defer_since.pop(tid, None)

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
                    # 上游成功 → 登记派生产物 + 回填下游的 src_output（§3.2.2）
                    try:
                        self._publish_derived(task, result)
                    except Exception as e:            # noqa: BLE001
                        logs.warn(f"⚠ {task.type} 派生产物登记失败：{e}")
                else:
                    logs.err(f"✗ {task.type}  {label}  {error}")
            with self._lock:
                self._cancels.pop(tid, None)
                self._active -= 1
                # **必须成对地减**（加的时候在同一个 `_long_slots` 条件下）。
                # 漏减的表现是"长任务再也排不上" —— 和死锁一模一样，
                # 而且只有长任务会卡，短任务照跑，所以看起来像"标准化卡住了"。
                if counts_long:
                    self._long_active = max(0, self._long_active - 1)
            # 租约必须在这里释放：漏一次就等于永久锁死那个文件，
            # 表现是"队列不动"（最难查的一类）
            store.release_file_leases(tid)
            self._q.task_done()

    def _publish_derived(self, task: store.TaskRow, result: dict) -> None:
        """上游成功后：建派生行、回填下游的 `src_output`（方案 §3.2.2 的"上游成功时"）。

        时序上必须在这一刻做（而不是建链时）：派生行只能在**上游真的成功了**
        之后才存在，否则会出现指向空气的 `src_output`。
        """
        rel = (result or {}).get("relPath")
        if not rel or not task.file_id:
            return
        out = config.OUTPUTS / str(rel).replace("\\", "/")
        try:
            size = out.stat().st_size
            mtime = out.stat().st_mtime
        except OSError:
            return
        f = store.add_derived_file(rel_to_outputs=str(rel), name=out.name,
                                   size=size, mtime=mtime, derived_from=task.id)
        n = store.attach_src_output(task.id, f.id, str(rel))
        if n:
            logs.log(f"  ↳ 产物 {out.name} 已登记，{n} 个下游步骤改用它")
        elif task.chain_id and store.chain_has_later_step(task):
            # 链上**确实还有后续步骤**，却没人认领这个产物 —— 这才是异常：
            # 通常意味着建链还没走到下游那一步（任务行还没 INSERT）。
            # 要留痕：否则表现为"ZIP 装的是源文件"，而那种错误肉眼看不出
            # （ZIP 打得开、文件也在，就是内容不对）。
            #
            # ⚠ **链的最后一步不该走这里**（它天生没有下游）。原来只判
            # `task.chain_id` 是否为真，于是**每一批链跑完都会刷一条告警**，
            # 而那条告警看着像出错、其实完全正常 —— 用户会来问"这是什么问题"。
            logs.warn(f"⚠ {task.type} 的产物 {out.name} 没有下游认领"
                      f"（链 {task.chain_id[:10]} 可能还在建）")

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
    """失败任务重试：复制出一条新任务（需求 §4.5）。

    `skipped` 也允许重试（方案 §3.6）：它虽然"没被尝试过"，但对用户来说
    和失败一样需要一次重来的机会 —— 而且**只有它能重试时整条链才救得回来**
    （上游重试成功后，下游那些 skipped 才有意义）。
    """
    old = store.get_task(tid)
    if not old or old.state not in ("failed", "cancelled", "skipped"):
        return None
    new = store.create_task(old.type, file_id=old.file_id,
                            params=old.params, batch_id=old.batch_id,
                            chain_id=old.chain_id, step_id=old.step_id,
                            step_idx=old.step_idx, src_task_id=old.src_task_id)
    queue_.submit(new.id)
    return new.id
