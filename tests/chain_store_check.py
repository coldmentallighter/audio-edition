"""P0-2 自检：库结构、派生产物寻址、skipped 语义、内存租约。

对应《执行链并发方案.md》§3.2.1 / §3.3 / §3.4 / §9.9 里**不需要服务**就能证伪的那些：
  · 老库能原地补列（AE_FRESH=0 起老库不会 no such column）
  · 派生行**不进** list_files 默认结果，但 get_file 找得到、能寻址到 outputs/
  · `FileRow.path` 按 origin 分派（派生行不能拼到 uploads/ 下）
  · `skipped` 的聚合口径：不该把好文件标成 failed，但"只有 skipped"要报 failed
  · `batch_summary` 把 skipped 算进 done（进度条不该停在 19/20）
  · 租约：写者独占、读者共享、申请不到要能说"等一等"、释放要干净
  · `reset_stale_chain_pending`：带 chain_id 的残留 pending 判失败，其余不动

用**独立的临时库**跑，绝不碰工作区的 audioedition.db。
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend import config, store                                 # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


# ---------------------------------------------------------------- 独立临时库
_tmp = Path(tempfile.mkdtemp(prefix="ae-p02-"))
_orig_db = config.DB_PATH
config.DB_PATH = _tmp / "t.db"


def _reset_store() -> None:
    """把 store 的模块级状态清干净，让它连到新的 DB_PATH。"""
    store._initialized = False
    store.release_all_leases()
    c = getattr(store._LOCAL, "conn", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
        del store._LOCAL.conn


try:
    _reset_store()
    store.init_db()

    print("== 1. 建表：新列都在 ==")
    cols_t = {r["name"] for r in store._conn().execute("PRAGMA table_info(tasks)")}
    cols_f = {r["name"] for r in store._conn().execute("PRAGMA table_info(files)")}
    want_t = {"chain_id", "step_id", "step_idx", "src_task_id", "src_output"}
    want_f = {"origin", "derived_from"}
    check("tasks 表有执行链五列", want_t <= cols_t, want_t - cols_t)
    check("files 表有 origin / derived_from", want_f <= cols_f, want_f - cols_f)
    check("skipped 是合法任务状态", "skipped" in store.TASK_STATES)
    check("skipped 在终态集合里", "skipped" in store.TASK_FINAL_STATES)

    print()
    print("== 2. 迁移：老库原地补列 ==")
    # ⚠ 必须走 **init_db()** 这条路，而不是直接调 apply_migrations()。
    # 第一版就是直接调的，于是漏掉了一个真实的启动崩溃：
    # `SCHEMA` 里有 `CREATE INDEX ... ON files(origin)`，若 executescript
    # 跑在迁移**之前**，老库会因为"没有 origin 列"整条起不来。
    # 单测全绿而服务起不来，就是因为测试绕开了 init_db。
    olddir = _tmp / "old"
    olddir.mkdir()
    olddb = olddir / "old.db"
    oc = sqlite3.connect(str(olddb))
    oc.executescript("""
    CREATE TABLE files (id TEXT PRIMARY KEY, rel_path TEXT NOT NULL UNIQUE,
      name TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, mtime REAL NOT NULL DEFAULT 0,
      state TEXT NOT NULL DEFAULT 'uploaded', info TEXT NOT NULL DEFAULT '{}',
      created_at REAL NOT NULL, updated_at REAL NOT NULL);
    CREATE TABLE tasks (id TEXT PRIMARY KEY, file_id TEXT, type TEXT NOT NULL,
      state TEXT NOT NULL DEFAULT 'pending', progress REAL NOT NULL DEFAULT 0,
      params TEXT NOT NULL DEFAULT '{}', result TEXT NOT NULL DEFAULT '{}',
      error TEXT NOT NULL DEFAULT '', batch_id TEXT, created_at REAL NOT NULL,
      started_at REAL, ended_at REAL);
    INSERT INTO files(id,rel_path,name,size,mtime,state,info,created_at,updated_at)
      VALUES('f_old','x.flac','x.flac',1,1.0,'ready','{}',1,1);
    INSERT INTO tasks(id,file_id,type,state,created_at) VALUES('t_old','f_old','convert','pending',1);
    """)
    oc.commit()
    oc.close()
    # 把 store 指到这个老库上，然后**像 app.py 那样**初始化
    config.DB_PATH = olddb
    _reset_store()
    try:
        store.init_db()                       # ← 这一步以前会抛 no such column: origin
        init_ok = True
        err = ""
    except Exception as e:                    # noqa: BLE001
        init_ok, err = False, f"{type(e).__name__}: {e}"
    check("老库能走通 init_db()（迁移在 SCHEMA 之前跑）", init_ok, err)
    added = store.take_migration_log() if init_ok else []
    # 9 处：tasks 的 7 列（含后来补的 chain_steps / chain_mode）+ files 的 2 列。
    # 加列时这里要一起改 —— 它守的是"迁移真的把老库补齐了"，不是这个数字本身。
    check("补列数 9 处", len(added) == 9, added)
    check("补列可重复执行（再 init 一次为空）",
          store.apply_migrations(store._conn()) == [])
    if init_ok:
        f_old = store.get_file("f_old")
        check("老行可读，origin 默认 imported",
              f_old is not None and f_old.origin == "imported",
              f_old.origin if f_old else None)
        check("老任务可读，src_task_id 为 None（老任务不是链任务）",
              store.get_task("t_old").src_task_id is None)
        check("老库的 list_files 仍然只返回导入文件",
              [x.name for x in store.list_files()] == ["x.flac"],
              [x.name for x in store.list_files()])
    # 回到测试自己的库继续后面的小节
    config.DB_PATH = _tmp / "t.db"
    _reset_store()
    store.init_db()

    print()
    print("== 3. 派生产物：寻址与列表过滤 ==")
    imp = store.add_file("song.flac", size=100, mtime=1.0)
    check("导入行 origin=imported", imp.origin == "imported", imp.origin)
    der = store.add_derived_file(rel_to_outputs="song.norm.flac", name="song.norm.flac",
                                 size=200, mtime=2.0, derived_from="t_fake")
    check("派生行 origin=derived", der.origin == "derived", der.origin)
    check("派生行记得自己从哪来", der.derived_from == "t_fake", der.derived_from)
    # 关键：寻址必须落在 outputs/，不能拼到 uploads/ 下（那会指向不存在的路径）
    check("派生行的 path 落在 outputs/ 下",
          config.OUTPUTS.resolve() in der.path.parents, der.path)
    check("导入行的 path 落在 uploads/ 下",
          config.UPLOADS.resolve() in imp.path.parents, imp.path)
    names = [f.name for f in store.list_files()]
    check("list_files 默认**不含**派生行", names == ["song.flac"], names)
    allnames = [f.name for f in store.list_files(origin="all")]
    check("origin='all' 能拿到两类", set(allnames) == {"song.flac", "song.norm.flac"}, allnames)
    check("get_file 仍能找到派生行（_file_of 靠它）",
          store.get_file(der.id) is not None)
    check("重复登记同一产物返回同一行（不分裂 file_id）",
          store.add_derived_file(rel_to_outputs="song.norm.flac", name="song.norm.flac",
                                 size=200, mtime=2.0, derived_from="t_fake").id == der.id)
    check("派生行的 kind 能判出来（后端要知道是不是音频）",
          der.as_dict()["kind"] == "audio", der.as_dict()["kind"])
    # rel_path 上的 UNIQUE 是**跨 origin** 的：uploads/ 与 outputs/ 共用一个命名空间。
    # 产物与某个导入文件重名时必须能避让，否则 INSERT 直接抛
    # `UNIQUE constraint failed: files.rel_path` —— 而它发生在队列 worker 里，
    # 表现是"任务莫名失败"（这个 bug 实测踩过）。
    clash = store.add_derived_file(rel_to_outputs="song.flac", name="song.flac",
                                   size=300, mtime=3.0, derived_from="t_fake2")
    check("产物与导入文件重名时能避让（不抛 UNIQUE）",
          clash is not None and clash.rel_path != "song.flac",
          clash.rel_path if clash else None)
    check("避让后的名字仍然指向 outputs/",
          config.OUTPUTS.resolve() in clash.path.parents, clash.path)
    check("list_files 仍然只看到导入的那一个 song.flac",
          [x.name for x in store.list_files()] == ["song.flac"],
          [x.name for x in store.list_files()])

    print()
    print("== 4. skipped 的聚合口径 ==")
    # 4a. 成功 + 跳过 → done（不该被 skipped 拖成 failed）
    t1 = store.create_task("convert", file_id=imp.id, chain_id="c1", step_idx=0)
    store.finish_task(t1.id, ok=True, result={"output": "outputs/a.flac"})
    t2 = store.create_task("normalize", file_id=imp.id, chain_id="c1", step_idx=1,
                           src_task_id=t1.id)
    store.skip_task(t2.id)
    agg = store.aggregate_state(imp.id)
    check("有 success 又有 skipped → done（不误判成 failed）",
          agg["fileState"] == "done", agg)
    check("聚合里单独统计了 skipped", agg["skipped"] == 1, agg)
    # 4b. 一条成功都没有、只有 skipped → failed（这个文件确实没做成）
    f2 = store.add_file("only-skip.flac", size=1, mtime=1.0)
    t3 = store.create_task("normalize", file_id=f2.id, chain_id="c2", step_idx=1)
    store.skip_task(t3.id)
    check("只有 skipped → failed（用户要能看出没做成）",
          store.aggregate_state(f2.id)["fileState"] == "failed",
          store.aggregate_state(f2.id))
    # 4c. skip_task 不覆盖已经跑过的任务
    t4 = store.create_task("peaks", file_id=f2.id)
    store.finish_task(t4.id, ok=True)
    store.skip_task(t4.id)
    check("skip_task 不覆盖 success", store.get_task(t4.id).state == "success",
          store.get_task(t4.id).state)
    # 4d. batch_summary 把 skipped 算进 done
    bs = store.batch_summary(t1.batch_id or "")
    check("batch_summary 有 skipped 字段", "skipped" in bs, bs)
    t5 = store.create_task("convert", file_id=imp.id, batch_id="b_x")
    t6 = store.create_task("normalize", file_id=imp.id, batch_id="b_x")
    store.finish_task(t5.id, ok=True)
    store.skip_task(t6.id)
    bs2 = store.batch_summary("b_x")
    check("skipped 算进 done（进度条不停在 1/2）", bs2["done"] == 2, bs2)

    print()
    print("== 5. 就绪判据 upstream_settled ==")
    chain = "c3"
    base = store.create_task("tag_edit", file_id=imp.id, chain_id=chain, step_idx=0)
    nxt = store.create_task("convert", file_id=imp.id, chain_id=chain, step_idx=1,
                            src_task_id=base.id)
    ok, why = store.upstream_settled(nxt)
    check("上游还 pending → 不能开工，原因是 upstream（能等）",
          (not ok) and why == "upstream", (ok, why))
    store.start_task(base.id)
    check("上游 running → 仍要等", store.upstream_settled(nxt)[1] == "upstream")
    store.finish_task(base.id, ok=True)
    check("上游成功 → 可以开工", store.upstream_settled(nxt) == (True, ""))
    # 上游失败 → 当场结案（不是 defer）
    base2 = store.create_task("tag_edit", file_id=imp.id, chain_id=chain, step_idx=2)
    nxt2 = store.create_task("convert", file_id=imp.id, chain_id=chain, step_idx=3,
                             src_task_id=base2.id)
    store.finish_task(base2.id, ok=False, error="boom")
    check("上游失败 → failed_upstream（当场结案，不回落）",
          store.upstream_settled(nxt2)[1] == "failed_upstream",
          store.upstream_settled(nxt2))
    # 上游不存在 → expired
    orphan = store.create_task("convert", file_id=imp.id, chain_id=chain, step_idx=4,
                               src_task_id="t_不存在")
    check("上游不存在 → expired", store.upstream_settled(orphan)[1] == "expired",
          store.upstream_settled(orphan))
    # 无上游 → 直接放行
    check("没有 src_task_id → 直接放行", store.upstream_settled(base) == (True, ""))
    # 指回自己 → cycle（防死循环）
    cyc = store.create_task("convert", file_id=imp.id, chain_id=chain, step_idx=5)
    store._conn().execute("UPDATE tasks SET src_task_id=? WHERE id=?", (cyc.id, cyc.id))
    check("src_task_id 指回自己 → cycle", store.upstream_settled(store.get_task(cyc.id))[1] == "cycle")

    print()
    print("== 6. 内存租约：写者独占、读者共享 ==")
    store.release_all_leases()
    A = store.create_task("tag_edit", file_id=imp.id)            # 就地写者
    R1 = store.create_task("peaks", file_id=imp.id)          # 读者
    R2 = store.create_task("verify", file_id=imp.id)         # 读者
    W2 = store.create_task("rename", file_id=imp.id)         # 另一个就地写者
    CV = store.create_task("convert", file_id=imp.id)        # 只读输入（产出新文件）
    check("读者先拿到读租约", store.acquire_file_leases(R1))
    check("另一个读者也能拿（读-读共享）", store.acquire_file_leases(R2))
    check("写者拿不到（有人正在读）", not store.acquire_file_leases(A))
    check("convert 也是读（它不改输入）", store.acquire_file_leases(CV))
    store.release_file_leases(R1.id)
    store.release_file_leases(R2.id)
    store.release_file_leases(CV.id)
    check("读者释放后写者能拿", store.acquire_file_leases(A))
    check("两个写者互斥", not store.acquire_file_leases(W2))
    check("写者在场时读者拿不到", not store.acquire_file_leases(R1))
    check("同一个任务重复申请视为已持有", store.acquire_file_leases(A))
    store.release_file_leases(A.id)
    check("释放干净（租约表为空）", store.held_leases() == {}, store.held_leases())
    check("没有 file_id 的任务不占租约（zip 走 barrier）",
          store.acquire_file_leases(store.create_task("zip")))
    store.release_all_leases()

    print()
    print("== 7. 重启清理：带 chain_id 的残留 pending 判失败 ==")
    p1 = store.create_task("convert", file_id=imp.id, chain_id="c9", step_idx=0)
    p2 = store.create_task("convert", file_id=imp.id)          # 无链 → 保持 pending
    r1 = store.create_task("convert", file_id=imp.id)
    store.start_task(r1.id)                                    # 残留 running
    # 先数一遍"库里有多少带链的 pending"，再看清理掉了几个：
    # 前面几节也留下过带链的 pending，所以**不能断言绝对条数**（第一版就这么错过——
    # 报的是 5，不是 1，而 5 恰好是对的）
    def _chained_pending() -> int:
        return store._conn().execute(
            "SELECT COUNT(*) n FROM tasks WHERE state='pending' AND chain_id IS NOT NULL"
        ).fetchone()["n"]

    before = _chained_pending()
    n_run = store.reset_stale_running()
    n_chain = store.reset_stale_chain_pending()
    check("残留 running 被判失败", n_run >= 1 and store.get_task(r1.id).state == "failed")
    check("带 chain_id 的 pending 被判失败", store.get_task(p1.id).state == "failed",
          store.get_task(p1.id).state)
    check("清理条数 = 清理前的带链 pending 数", n_chain == before, (n_chain, before))
    check("清理后不再有带链的 pending", _chained_pending() == 0, _chained_pending())
    check("不带 chain_id 的 pending **不动**", store.get_task(p2.id).state == "pending",
          store.get_task(p2.id).state)
    check("失败原因写明是执行链中断",
          "执行链中断" in (store.get_task(p1.id).error or ""),
          store.get_task(p1.id).error)

    print()
    print("== 8. chain_snapshot：每步与每文件的进度 ==")
    cc = "c10"
    for i, fid in enumerate([imp.id, f2.id]):
        t = store.create_task("convert", file_id=fid, chain_id=cc, step_idx=0)
        store.finish_task(t.id, ok=True)
    t_b = store.create_task("normalize", file_id=imp.id, chain_id=cc, step_idx=1)
    store.skip_task(t_b.id)
    t_c = store.create_task("normalize", file_id=f2.id, chain_id=cc, step_idx=1)
    store.finish_task(t_c.id, ok=True)
    snap = store.chain_snapshot(cc)
    check("两个步骤都在", len(snap["steps"]) == 2, snap["steps"])
    check("stepCount = 2", snap["stepCount"] == 2)
    s0, s1 = snap["steps"]
    check("第 1 步已全部结束（2/2）", s0["settled"] == 2 and s0["total"] == 2, s0)
    check("第 2 步：1 跳过 1 成功", s1["skipped"] == 1 and s1["success"] == 1, s1)
    check("每个文件的进度都在", {f["fileId"] for f in snap["files"]} == {imp.id, f2.id})

    # ---------------------------------------------------------------- 9
    print()
    print("== 9. chain_has_later_step：区分「链到头了」与「下游没认领」==")
    # 用途只有一个：`queue._publish_derived` 判断"产物没人认领"是不是异常。
    # **链的最后一步天生没有下游**，那不是异常 —— 原来只判 `chain_id` 是否为真，
    # 于是每跑完一条链都刷一条看着像出错的告警（用户会来问"这是什么问题"）。
    c9 = "c11"
    a = store.create_task("convert", file_id=imp.id, chain_id=c9, step_idx=0)
    b = store.create_task("normalize", file_id=imp.id, chain_id=c9, step_idx=1)
    c = store.create_task("convert", file_id=imp.id, chain_id=c9, step_idx=2)
    check("第 1 步：后面还有两步 → True",
          store.chain_has_later_step(a) is True)
    check("第 2 步：后面还有一步 → True",
          store.chain_has_later_step(b) is True)
    check("**最后一步：没有后续 → False**（所以不告警）",
          store.chain_has_later_step(c) is False)
    # 别的文件的步骤不算"接手它的产物"
    other = store.create_task("convert", file_id=f2.id, chain_id=c9, step_idx=9)
    check("只在**同一个文件**上找后续（别的文件的步骤不算）",
          store.chain_has_later_step(c) is False)
    # 汇总类（没有 file_id）接手全批 —— 库里只要有**更靠后**的步骤就算，
    # 不再按 file_id 过滤（它本来就不是某一个文件的下游）。
    z = store.create_task("zip", chain_id=c9, step_idx=3)
    check("汇总类：后面还有别的文件的步骤 → True（它吃全批，不过滤 file_id）",
          store.chain_has_later_step(z) is True)
    # 真链里 zip 是最后的步骤，后面不会有东西 —— 那时应当 False（不告警）
    z2 = store.create_task("zip", chain_id=c9, step_idx=99)
    check("汇总类是最后一步 → False（所以不告警）",
          store.chain_has_later_step(z2) is False)
    _ = (other, b)
    # 没有链的任务（单点执行）：不适用，一律 False
    solo = store.create_task("convert", file_id=imp.id)
    check("非链任务 → False", store.chain_has_later_step(solo) is False)
    check("不存在的链 → False",
          store.chain_has_later_step(
              store.create_task("convert", file_id=imp.id, chain_id="c_无",
                                step_idx=0)) is False)

    # ---------------------------------------------------------------- 10
    print()
    print("== 10. info 是**逐块合并**的：响度那组不许被 probe 擦掉 ==")
    # 用户实测报的：卡片上一直是「undefined LUFS」。根因有两半 ——
    # ① 写 info 的每个人都在用 `set_file_info` 整体覆盖，于是"改个标签/探测一下"
    #    就把已经测出来的响度冲掉；② 测出来的响度压根没落库。
    # 这里钉的是 ① 的机制：整份覆盖 vs 按块合并，以及"文件换了内容"时
    # 必须把 probe 永远测不出来的那组值丢掉（否则卡片上是**上一个文件**的数字）。
    m = store.add_file("meas.flac", size=999, mtime=5.0)
    store.merge_file_info(m.id, {"format": "FLAC", "sampleRate": 44100,
                                 "loudness": -19.34, "truePeak": -1.2,
                                 "loudnessRange": 6.4})
    info = store.get_file(m.id).info
    check("合并写入了响度那一组",
          info.get("loudness") == -19.34 and info.get("truePeak") == -1.2,
          info)
    check("先写的元数据字段也还在",
          info.get("format") == "FLAC" and info.get("sampleRate") == 44100, info)

    # probe 那条路：probe 的 dict 里**没有**响度，整体覆盖就会把它擦掉 ——
    # 所以 handler 用的是 merge。这里直接验合并语义。
    store.merge_file_info(m.id, {"format": "FLAC", "duration": 12.5})
    info = store.get_file(m.id).info
    check("probe 式合并之后响度还在（整体覆盖会丢）",
          info.get("loudness") == -19.34 and info.get("duration") == 12.5, info)

    # 对照：set_file_info 是**整体覆盖**，语义必须保持原样（别顺手改成合并）
    store.set_file_info(m.id, {"format": "FLAC"})
    check("set_file_info 仍然是整体覆盖（它的语义是「这一份就是全部」）",
          "loudness" not in store.get_file(m.id).info)
    store.merge_file_info(m.id, {"loudness": -19.34, "truePeak": -1.2,
                                 "loudnessRange": 6.4})

    # 同一路径又拖进来一次：大小/时间没变 ⇒ 还是同一份内容，测量值有效
    store.add_file("meas.flac", size=999, mtime=5.0)
    check("重传同一份内容（大小/时间相同）→ 测量值保留",
          store.get_file(m.id).info.get("loudness") == -19.34)
    # 内容换了（大小不同）⇒ 必须丢掉：probe 永远测不出响度，"重新探测"也纠正不了
    store.add_file("meas.flac", size=1234, mtime=5.0)
    info2 = store.get_file(m.id).info
    check("内容换了（大小不同）→ 测量值被丢掉",
          "loudness" not in info2 and "truePeak" not in info2
          and "loudnessRange" not in info2, info2)
    check("丢测量值时**不**连累元数据字段", info2.get("format") == "FLAC", info2)

    # 软删除后重新导入同名文件（复活原行）：同样要清
    store.merge_file_info(m.id, {"loudness": -20.0})
    store.delete_file(m.id)
    store.add_file("meas.flac", size=4321, mtime=9.0)
    check("软删除后重导同名文件（内容不同）→ 测量值被丢掉",
          "loudness" not in store.get_file(m.id).info, store.get_file(m.id).info)

    check("MEASUREMENT_KEYS 覆盖全部内容测量值（写它的人与清它的人共用这一份）",
          set(store.MEASUREMENT_KEYS) == {"loudness", "truePeak", "loudnessRange",
                                          "samplePeak", "dra", "drp"},
          store.MEASUREMENT_KEYS)
    store.merge_file_info("f_不存在", {"loudness": -1.0})
    check("对不存在的文件合并 → 静默返回，不抛", True)

finally:
    _reset_store()
    config.DB_PATH = _orig_db
    shutil.rmtree(_tmp, ignore_errors=True)

print()
print(f"结果：{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
