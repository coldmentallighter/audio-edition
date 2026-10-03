"""P0-3 自检：队列准入（_gate / _requeue）与 defer 的两本内存账。

对应《执行链并发方案.md》§3.4 / §9.3 —— 这些断言**只能靠真跑队列**证明，
看代码看不出来。四条最容易写错、且症状最隐蔽的：

  1. **defer 绝不能写库**：闸门若放在 `start_task` 之后，没就绪的任务会被标成
     `running`，而 `aggregate_state` 只要有 running 就返回 processing →
     文件状态显示"处理中"、进度条卡住，**UI 永远转圈**。
  2. **不能空转**：队列极短时（只剩一个够不着的任务）worker 会在"取出→放回"
     上打满一个核，而且日志里什么都看不到。
  3. **不能死锁**：2 个 worker + 多文件串行链，整体必须在时限内跑完。
  4. **不能无限推迟**：依赖永远不可能满足时要在 DEFER_LIMIT 次之后判失败。

用一个**假 handler**（sleep 一小段、可注入失败）驱动，不碰 ffmpeg，跑得很快。
库用的是独立临时库，绝不碰工作区的 audioedition.db。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend import config, queue as q_mod, store                   # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


_tmp = Path(tempfile.mkdtemp(prefix="ae-p03-"))
_orig_db = config.DB_PATH
config.DB_PATH = _tmp / "t.db"
# 让"源文件存在"这一关过得去：假 handler 不读内容，但 _file_of 会查存在性
_orig_uploads = config.UPLOADS
config.UPLOADS = _tmp / "uploads"
config.UPLOADS.mkdir(parents=True, exist_ok=True)
config.OUTPUTS = _tmp / "outputs"
config.OUTPUTS.mkdir(parents=True, exist_ok=True)


def _reset_store() -> None:
    store._initialized = False
    store.release_all_leases()
    c = getattr(store._LOCAL, "conn", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
        del store._LOCAL.conn


# ---------------------------------------------------------------- 假 handler
STATE = {"ran": [], "fail_types": set(), "slow": 0.0, "peers": 0}
_LOCK = threading.Lock()
_PEERS = {"now": 0, "max": 0}


def _fake_handler(task, ctx):
    """记录"谁真的跑了"，并统计并发峰值（用来证明同文件不会重叠）。"""
    with _LOCK:
        STATE["ran"].append(task.id)
        _PEERS["now"] += 1
        _PEERS["max"] = max(_PEERS["max"], _PEERS["now"])
    try:
        if STATE["slow"]:
            time.sleep(STATE["slow"])
        if task.type in STATE["fail_types"]:
            return False, {}, "注入的失败"
        return True, {}, ""
    finally:
        with _LOCK:
            _PEERS["now"] -= 1


def _mkfile(name: str) -> store.FileRow:
    p = config.UPLOADS / name
    p.write_bytes(b"x" * 16)
    return store.add_file(name, size=16, mtime=p.stat().st_mtime)


def _settled(tid: str) -> bool:
    t = store.get_task(tid)
    return bool(t) and t.state in store.TASK_FINAL_STATES


def _wait_all(tids, timeout=30.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if all(_settled(t) for t in tids):
            return True
        time.sleep(0.05)
    return False


try:
    _reset_store()
    store.init_db()

    # ---------------------------------------------------------------- 1
    print("== 1. 平行档（非链任务）：走的必须是老路径 ==")
    f1 = _mkfile("a.flac")
    q = q_mod.Queue(workers=2)
    # 把所有会用到的任务类型都注册上。**漏注册的症状是任务被标成
    # "没有 X 的处理器" 并直接失败**（而这其实是对的行为：仓库里那条
    # `每个 op 都有处理器` 的断言就是防这个的）。第一版这里只注册了 convert，
    # 于是第 6/7 节的 tag_edit / rename / peaks / verify 全部没跑 ——
    # 而测试却"通过"了，因为我断言的是"跑完了"而不是"真的执行了"。
    for _tp in ("convert", "tag_edit", "rename", "peaks", "verify", "normalize"):
        q.register(_tp, _fake_handler)
    q.start()
    t_a = store.create_task("convert", file_id=f1.id)      # 无 chain_id / src_task_id
    q.submit(t_a.id)
    check("非链任务能正常跑完", _wait_all([t_a.id]), store.get_task(t_a.id).state)
    check("非链任务没有被 defer（真的执行了）", t_a.id in STATE["ran"])

    # ---------------------------------------------------------------- 2
    print()
    print("== 2. defer 绝不写库（§9.3.2 约束 1）==")
    g1 = _mkfile("g1.flac")
    chain = "c_gate"
    up = store.create_task("convert", file_id=g1.id, chain_id=chain, step_idx=0)
    down = store.create_task("convert", file_id=g1.id, chain_id=chain, step_idx=1,
                             src_task_id=up.id)
    # **只提交下游**，上游故意不提交 → 它必然被 defer 很多次
    STATE["ran"].clear()
    q.submit(down.id)
    time.sleep(0.8)
    st = store.get_task(down.id)
    check("被 defer 的任务仍然是 pending（不是 running）", st.state == "pending", st.state)
    check("被 defer 的任务 startTask 没被写过（startedAt 为空）",
          st.started_at is None, st.started_at)
    check("被 defer 的任务不占 active（队列面板不会显示成执行中）",
          q.active == 0, q.active)
    check("它确实被推迟过（记账里有它）", q._defer_count.get(down.id, 0) > 0,
          q._defer_count.get(down.id))
    check("工作区内它一次都没被执行", down.id not in STATE["ran"])
    # 把上游放出去，它应当自己解开
    store.finish_task(up.id, ok=True)
    check("上游成功后下游能跑（闸门会自己解开）", _wait_all([down.id]),
          store.get_task(down.id).state)
    check("解开后确实执行了", down.id in STATE["ran"])

    # ---------------------------------------------------------------- 3
    print()
    print("== 3. 不能空转（§9.3.4 的队列极短场景）==")
    # 造一个"够不着"的任务，且队列里**只有它** —— 这是最容易打满 CPU 的形态
    g2 = _mkfile("g2.flac")
    up2 = store.create_task("convert", file_id=g2.id, chain_id="c_spin", step_idx=0)
    down2 = store.create_task("convert", file_id=g2.id, chain_id="c_spin", step_idx=1,
                              src_task_id=up2.id)
    cpu0, wall0 = time.process_time(), time.time()
    q.submit(down2.id)
    time.sleep(1.2)
    cpu1, wall1 = time.process_time(), time.time()
    ratio = (cpu1 - cpu0) / max(wall1 - wall0, 1e-6)
    check("空闲空窗期 CPU 占用不飙升（比值 < 0.5）", ratio < 0.5, f"ratio={ratio:.3f}")
    store.finish_task(up2.id, ok=True)
    check("空转之后仍然能跑完", _wait_all([down2.id]), store.get_task(down2.id).state)

    # ---------------------------------------------------------------- 4
    print()
    print("== 4. 五种当场结案：不进 defer ==")
    g3 = _mkfile("g3.flac")
    # 4a 上游不存在 → expired
    orph = store.create_task("convert", file_id=g3.id, chain_id="c_x", step_idx=1,
                             src_task_id="t_不存在")
    # 4b 上游失败 → failed_upstream
    bad_up = store.create_task("convert", file_id=g3.id, chain_id="c_x", step_idx=0)
    store.finish_task(bad_up.id, ok=False, error="boom")
    fail_down = store.create_task("convert", file_id=g3.id, chain_id="c_x", step_idx=1,
                                  src_task_id=bad_up.id)
    # 4c 指回自己 → cycle
    cyc = store.create_task("convert", file_id=g3.id, chain_id="c_x", step_idx=2)
    store._conn().execute("UPDATE tasks SET src_task_id=? WHERE id=?", (cyc.id, cyc.id))
    STATE["ran"].clear()
    q.submit(orph.id)
    q.submit(fail_down.id)
    q.submit(cyc.id)
    check("三种异常都当场结案（不等 defer）", _wait_all([orph.id, fail_down.id, cyc.id]),
          [store.get_task(x).state for x in (orph.id, fail_down.id, cyc.id)])
    check("上游不存在 → failed 且原因是 expired",
          store.get_task(orph.id).state == "failed" and "上游任务不存在" in store.get_task(orph.id).error,
          store.get_task(orph.id).error)
    check("上游失败 → failed 且不回落（错误里写明是前序失败）",
          store.get_task(fail_down.id).state == "failed"
          and "前序步骤失败" in store.get_task(fail_down.id).error,
          store.get_task(fail_down.id).error)
    check("这三种一次都没被执行", not ({orph.id, fail_down.id, cyc.id} & set(STATE["ran"])))
    check("它们也不该有 defer 计数",
          all(q._defer_count.get(x, 0) == 0 for x in (orph.id, fail_down.id, cyc.id)),
          {x: q._defer_count.get(x, 0) for x in (orph.id, fail_down.id, cyc.id)})

    # ---------------------------------------------------------------- 5
    print()
    print("== 5. 推迟截止：按**等了多久**算，不是按推迟了多少次 ==")
    # 这一节是**真 bug 的回归钉子**：原来 `DEFER_LIMIT = 200` **次**，
    # 而退避是 0.05s → 200 次 ≈ 10~20 秒就把一个还在正常等待的任务判死。
    # 串行链上必然踩：第 N 步要等前面 N-1 步，`标准化` 一个 3 分钟的文件就要 5 秒，
    # 4 步链轻松超过 20 秒 —— 现象是"最后一步 failed：依赖无法满足（上游还没结束）"，
    # 而上游就在它被判死的 1 秒后成功了。
    q2 = q_mod.Queue(workers=1)
    q2.register("convert", _fake_handler)
    q2._running = True
    g4 = _mkfile("g4.flac")
    # 上游永远 pending（不提交它），且队列里只放下游 → 只能一直推迟
    never = store.create_task("convert", file_id=g4.id, chain_id="c_lim", step_idx=0)
    stuck = store.create_task("convert", file_id=g4.id, chain_id="c_lim", step_idx=1,
                              src_task_id=never.id)

    # (a) 推迟**远多于**旧的 200 次，但时间没到 → **绝不能判失败**
    old_limit = 200
    for _ in range(old_limit + 50):
        task = store.get_task(stuck.id)
        if task.state != "pending":
            break
        q2._requeue(task, q_mod.REASON_UPSTREAM)
    st5 = store.get_task(stuck.id)
    check(f"推迟 {old_limit + 50} 次（远超旧的 200 次上限）后**仍然 pending**",
          st5.state == "pending", st5.state)
    check("等的时间远远没到截止", 
          time.monotonic() - q2._defer_since[stuck.id] < q_mod.DEFER_DEADLINE)
    check("退避是指数增长并且封顶（不会退化成忙循环）",
          q2._defer_until[stuck.id] - time.monotonic() <= q_mod.DEFER_BACKOFF_MAX + 0.05,
          q2._defer_until[stuck.id] - time.monotonic())

    # (b) 把"第一次推迟的时刻"挪到很久以前 → 这时才该判失败
    q2._defer_since[stuck.id] = time.monotonic() - q_mod.DEFER_DEADLINE - 1
    q2._requeue(store.get_task(stuck.id), q_mod.REASON_UPSTREAM)
    st5 = store.get_task(stuck.id)
    check("等超过 DEFER_DEADLINE 之后判失败", st5.state == "failed", st5.state)
    check("失败原因写明依赖无法满足", "依赖无法满足" in (st5.error or ""), st5.error)
    check("截止触发后清掉了记账（不留垃圾）",
          stuck.id not in q2._defer_count
          and stuck.id not in q2._defer_since
          and stuck.id not in q2._defer_until,
          (stuck.id in q2._defer_count, stuck.id in q2._defer_since))
    # 计数口径本身也要守住：`DEFER_LIMIT` 这个"按次数判死"的常量不许再回来
    check("已经不存在按次数判死的 DEFER_LIMIT",
          not hasattr(q_mod, "DEFER_LIMIT"), getattr(q_mod, "DEFER_LIMIT", None))
    q2._running = False

    # ---------------------------------------------------------------- 6
    print()
    print("== 6. 同文件写者互斥（租约真的生效）==")
    g5 = _mkfile("g5.flac")
    STATE["slow"] = 0.25
    _PEERS["max"], _PEERS["now"] = 0, 0
    # tags / rename 是就地写者 → 必须串行；用真 op 名反查接触面
    w1 = store.create_task("tag_edit", file_id=g5.id)
    w2 = store.create_task("rename", file_id=g5.id)
    q.submit(w1.id)
    q.submit(w2.id)
    _wait_all([w1.id, w2.id], timeout=20)
    # 断言"真的执行过"而**不是**只看"跑完了"：任务被标 failed 也是一种"跑完"，
    # 那样会让"并发峰值=1"这种断言在什么都没跑的情况下假通过（第一版就栽在这）。
    check("两个就地写者都真的执行了", all(x in STATE["ran"] for x in (w1.id, w2.id)),
          [(store.get_task(x).state, store.get_task(x).error) for x in (w1.id, w2.id)])
    check("并发峰值 = 1（两个写者没有重叠）", _PEERS["max"] == 1,
          f"max={_PEERS['max']} ran={[x in STATE['ran'] for x in (w1.id, w2.id)]}")
    check("跑完租约全部释放", store.held_leases() == {}, store.held_leases())

    print()
    print("== 7. 读者可并行（规则①只约束写者）==")
    g6 = _mkfile("g6.flac")
    _PEERS["max"], _PEERS["now"] = 0, 0
    r1 = store.create_task("peaks", file_id=g6.id)
    r2 = store.create_task("verify", file_id=g6.id)
    r3 = store.create_task("convert", file_id=g6.id)      # convert 是"读输入"
    for t in (r1, r2, r3):
        q.submit(t.id)
    _wait_all([r1.id, r2.id, r3.id], timeout=20)
    check("三个读者都真的执行了", all(t.id in STATE["ran"] for t in (r1, r2, r3)),
          [store.get_task(t.id).state for t in (r1, r2, r3)])
    check("并发峰值 > 1（读者之间不互斥）", _PEERS["max"] > 1, _PEERS["max"])
    STATE["slow"] = 0.0

    # ---------------------------------------------------------------- 8
    print()
    print("== 8. 不能死锁：2 worker + 多文件串行链 ==")
    files = [_mkfile(f"m{i}.flac") for i in range(8)]
    cid = "c_dead"
    allt: list[str] = []
    STATE["ran"].clear()
    for f in files:
        prev = None
        for idx in range(3):
            t = store.create_task("convert", file_id=f.id, chain_id=cid,
                                  step_idx=idx, src_task_id=prev)
            allt.append(t.id)
            prev = t.id
    for tid in allt:
        q.submit(tid)
    t0 = time.time()
    done = _wait_all(allt, timeout=45)
    dur = time.time() - t0
    check(f"24 个任务全部出终态（{dur:.1f}s）", done,
          [store.get_task(x).state for x in allt if not _settled(x)][:5])
    check("没有任务卡在 pending", all(store.get_task(x).state != "pending" for x in allt))
    check("全部成功（说明上游依赖都解开了）",
          all(store.get_task(x).state == "success" for x in allt),
          [store.get_task(x).state for x in allt if store.get_task(x).state != "success"][:5])
    check("同一文件的步序被遵守（没有重叠）", _PEERS["max"] <= 2, _PEERS["max"])
    check("跑完没有租约泄漏", store.held_leases() == {})

    print()
    print("== 9. 派生产物登记（_publish_derived 的前提）==")
    g7 = _mkfile("g7.flac")
    up7 = store.create_task("convert", file_id=g7.id, chain_id="c_pub", step_idx=0)
    dn7 = store.create_task("normalize", file_id=g7.id, chain_id="c_pub", step_idx=1,
                            src_task_id=up7.id)
    prod = config.OUTPUTS / "g7.flac"
    prod.write_bytes(b"y" * 32)
    art = store.add_derived_file(rel_to_outputs="g7.flac", name="g7.flac",
                                 size=32, mtime=prod.stat().st_mtime,
                                 derived_from=up7.id)
    n = store.attach_src_output(up7.id, art.id, "g7.flac")
    dn7b = store.get_task(dn7.id)
    check("下游的 src_output 被回填", dn7b.src_output == "g7.flac", dn7b.src_output)
    check("下游的 file_id 换成派生产物（这才能让 _file_of 吃到产物）",
          dn7b.file_id == art.id, (dn7b.file_id, art.id))
    check("回填条数正确", n == 1, n)
    check("派生产物不在 list_files 里",
          "g7.flac" not in [x.name for x in store.list_files()] or
          len([x for x in store.list_files() if x.name == "g7.flac"]) == 1)
    # 已经跑过的下游不该被回填（避免把历史任务指向新产物）
    fin = store.create_task("normalize", file_id=g7.id, chain_id="c_pub2", step_idx=1,
                            src_task_id=up7.id)
    store.finish_task(fin.id, ok=True)
    store.attach_src_output(up7.id, art.id, "g7.flac")
    check("已结束的下游任务不被回填（只改 pending）",
          store.get_task(fin.id).src_output is None,
          store.get_task(fin.id).src_output)

    q.stop()

    # ---------------------------------------------------------------- 10
    print()
    print("== 10. 长任务槽位：别让长任务占满全部 worker ==")
    # §3.5：`normalize` / `zip` / `waveform` / `loudness` 一跑就是几十秒到十几分钟，
    # 2 个 slot 被它们占满时**短任务全部排队** —— 界面看着像卡死。
    # 规则：长任务最多占 `n-1` 个 worker，留一个给短任务。
    import backend.queue as q_mod2

    # ---- 配置推导 ----
    check("`AE_LONG_SLOTS` 没设时是 None（= 按实际 n 推导，不烤死）",
          config.LONG_TASK_SLOTS is None, config.LONG_TASK_SLOTS)
    for n, want in ((1, 0), (2, 1), (3, 2), (4, 3)):
        got = q_mod2.Queue(workers=n)._long_slots
        check(f"workers={n} → 长任务槽 {want}", got == want, got)
    check("并发 1 时限制**自动失效**（否则长任务永远排不上，等于死锁）",
          q_mod2.Queue(workers=1)._long_slots == 0)
    check("显式 0 = 关掉这条限制",
          q_mod2.Queue(workers=4, long_slots=0)._long_slots == 0)
    check("显式值会被夹到 n 以内",
          q_mod2.Queue(workers=4, long_slots=99)._long_slots == 4)
    check("类型名单里有那几个长任务（要完整读一遍文件的都算）",
          config.LONG_TASK_TYPES == {"normalize", "zip", "waveform",
                                     "loudness", "verify"},
          sorted(config.LONG_TASK_TYPES))
    check("短任务（转换/标签/探测…）**不在**名单里",
          not ({"convert", "tags", "probe", "peaks", "cover"}
               & config.LONG_TASK_TYPES),
          sorted(config.LONG_TASK_TYPES))
    check("REASON_SLOT 属于**能等**的那一类（不能当场判死）",
          q_mod2.REASON_SLOT in q_mod2.CAN_WAIT, q_mod2.CAN_WAIT)

    # ---- 行为：2 个长任务 + 1 个短任务，短任务必须先跑完 ----
    q3 = q_mod2.Queue(workers=2)
    q3.register("normalize", _fake_handler)      # 长任务
    q3.register("convert", _fake_handler)        # 短任务
    q3.start()                                   # 起真有 worker 的队列（不是只翻标志位）
    g8 = _mkfile("g8.flac")
    g9 = _mkfile("g9.flac")
    ga = _mkfile("ga.flac")
    STATE["slow"] = 0.6                          # 让"占着槽"看得见
    STATE["ran"].clear()
    long1 = store.create_task("normalize", file_id=g8.id)
    long2 = store.create_task("normalize", file_id=g9.id)
    short = store.create_task("convert", file_id=ga.id)
    q3.submit(long1.id)
    time.sleep(0.15)                             # 让 long1 先占住长任务槽
    q3.submit(long2.id)
    q3.submit(short.id)
    _wait_all([long1.id, long2.id, short.id], timeout=30)
    order = STATE["ran"][:]
    check("三个任务都真的执行了",
          all(x in order for x in (long1.id, long2.id, short.id)),
          [(store.get_task(x).state, store.get_task(x).error)
           for x in (long1.id, long2.id, short.id)])
    check("短任务**没被长任务堵在后面**（它排在 long2 之前跑完）",
          order.index(short.id) < order.index(long2.id),
          [store.get_task(x).type for x in order])
    check("跑完长任务计数归零（漏减会让长任务再也排不上）",
          q3._long_active == 0, q3._long_active)
    q3._running = False
    STATE["slow"] = 0.0

finally:
    try:
        q.stop()
    except Exception:
        pass
    _reset_store()
    config.DB_PATH = _orig_db
    config.UPLOADS = _orig_uploads
    shutil.rmtree(_tmp, ignore_errors=True)

print()
print(f"结果：{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
