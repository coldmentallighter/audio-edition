"""串行档的**交接**规则自检（`执行链打包与串行交接方案.md` §3，规则 R1–R8）。

用户实测报的现象：串行链把「响度分析报告 → 导出响度分析图」接起来，第 2 步失败：

    Error opening input file ...\\outputs\\loudness\\….loudness.md
    Invalid data found when processing input

根因不在那一对卡片：`contract.py` 明写 `sidecar`（波形图 / 响度图 / 响度报告 /
提取出的封面 / zip）**不参与输入解析**，而 `queue._publish_derived` 的判据只有
`result.relPath` —— 于是旁路产物被当成"新的输入"交了下去。**52 组可选组合必坏**
（4 个旁路 op × 13 个下游），报错还是 ffmpeg 原文。

这个脚本把两件事钉死（都是**枚举**，不是抽样）：

  1. 旁路产物的下游**永远拿不到**那个 `.md/.png/.jpg`（`file_id` 不动）
  2. `derived`（转换 / 标准化）的下游**必须**拿到产物（反向对照 ——
     防"一律不交接"这种粗暴修法把链的正功能一起关掉）
  3. 旁路产物**仍然要登记产物行**（ZIP 靠它收集成员，`登记≠交接`）
  4. 提交一个 `file_id` 指向 `.md` 的任务时，报错是**人话**（R8）

用独立临时库与临时目录，不碰工作区；不需要服务，也不跑 ffmpeg。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend import chain, config, store                          # noqa: E402
from backend import queue as q_mod                                # noqa: E402
from backend import tasks as tasks_mod                            # noqa: E402
from backend.cards import boundary, contract                      # noqa: E402
from backend.cards.specs import OPS                               # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


_tmp = Path(tempfile.mkdtemp(prefix="ae-handoff-"))
_orig = (config.DB_PATH, config.UPLOADS, config.OUTPUTS)
config.DB_PATH = _tmp / "t.db"
config.UPLOADS = _tmp / "up"
config.OUTPUTS = _tmp / "out"
for _d in (config.UPLOADS, config.OUTPUTS):
    _d.mkdir(parents=True, exist_ok=True)

# 旁路产物用什么扩展名：`.md`/`.svg`/`.png`/`.jpg` 都试一遍（判据不该只看后缀，
# 但报错文案会用到它们）。`loudness-image` 2026-10 起出 **SVG**。
SIDECAR_REL = {
    "extract-cover": "covers/x.jpg",
    "waveform": "waveforms/x.png",
    "loudness-image": "loudness/x.loudness.svg",
    "loudness-report": "loudness/x.loudness.md",
    "zip": "zips/x.zip",
}
DERIVED_REL = {"convert": "x.flac", "normalize": "x.norm.flac"}


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


def _mkfile(name: str = "song.flac") -> store.FileRow:
    p = config.UPLOADS / name
    p.write_bytes(b"x" * 16)
    return store.add_file(name, size=16, mtime=p.stat().st_mtime)


def _uniq_rel(rel: str, n: int) -> str:
    """给每对组合一个**唯一**的产物路径（`files.rel_path` 是跨 origin 唯一的）。"""
    head, dot, ext = rel.rpartition(".")
    return f"{head}-{n}.{ext}" if dot else f"{rel}-{n}"


def _publish(task, rel: str) -> None:
    """模拟上游成功：走**真实**的 `_publish_derived`（判据就在它里面）。"""
    out = config.OUTPUTS / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"payload")
    q_mod.queue_._publish_derived(task, {"relPath": rel,
                                         "output": f"outputs/{rel}"})


try:
    _reset_store()
    store.init_db()
    f = _mkfile()

    # ---------------------------------------------------------------- 1
    print("== 1. 枚举：旁路产物（sidecar）绝不交接给下一步 ==")
    # 判据按**契约**取，不写死 op 名 —— 将来加了新的旁路 op 会被自动纳入
    sidecar_ops = [op for op in OPS if contract.produces(op) == "sidecar"]
    derived_ops = [op for op in OPS if contract.produces(op) == "derived"]
    check("旁路产物的 op 就是那五个（含 zip）",
          sorted(sidecar_ops) == ["extract-cover", "loudness-image",
                                  "loudness-report", "waveform", "zip"],
          sidecar_ops)
    check("会交接的 op 只有转换与标准化",
          sorted(derived_ops) == ["convert", "normalize"], derived_ops)

    checked = 0
    bad: list[tuple[str, str, str]] = []
    for a in sidecar_ops:
        if a == "zip":
            continue          # 汇总类单独钉（见本节末尾），口径见下
        for b in OPS:
            # `zip` 是汇总类：它没有 `src_task_id`（"一次吃全部文件"），
            # 成员收集由 §ZIP 那套用例覆盖，这里不重复。
            if b == a or b == "zip":
                continue
            ok, _why = boundary.availability(a, b)
            if not ok:
                continue                      # 建不出来的组合不在这里管（P1 解禁）
            r = chain.build_chain({"mode": "serial", "fileIds": [f.id],
                                   "steps": [{"op": a}, {"op": b}]})
            ta, tb = (store.get_task(t) for t in r["taskIds"])
            # ⚠ 产物路径**必须每对唯一**：`files.rel_path` 上有跨 origin 的
            # UNIQUE 约束，而 `add_derived_file` 遇到已存在的路径会**直接返回
            # 那一行**（它属于上一条链）—— 复用同一个 `covers/x.jpg` 会让
            # 后面的链查不到自己的产物行，测试自己造出假失败。
            rel = _uniq_rel(SIDECAR_REL[a], checked)
            _publish(ta, rel)
            tb2 = store.get_task(tb.id)
            checked += 1
            if tb2.file_id != f.id or tb2.src_output:
                bad.append((a, b, f"{tb2.file_id} / {tb2.src_output!r}"))
            # 反向要求：**登记**必须发生（ZIP 靠这些行收集成员）
            if not store.chain_derived_artifact(r["chainId"], f.id, 1):
                bad.append((a, b, "旁路产物没有登记成派生行（ZIP 会漏成员）"))
    check(f"枚举 {checked} 组旁路组合：下游的输入一律不动", not bad, bad[:6])
    check("组数就是方案里的 52 组（4 个可建链的旁路 op × 13 个下游）",
          checked == 52, checked)

    # `zip` 单独钉：它是**汇总类**（没有 `src_task_id`、产物是压缩包），
    # 不混进上面那 52 组（那些是 per-file 的旁路产物），但"绝不交接"同样适用。
    # `zip → zip` 是**非平凡**的一条：`attach_src_output` 里那段"给汇总任务
    # 回填 `_artifacts`/`src_output`"的分支正是为它写的 —— P0 之后
    # `_publish_derived` 对 sidecar 直接返回，那段分支再也碰不到 zip。
    for steps, tag in (([{"op": "zip"}, {"op": "probe"}], "打包 → 探测"),
                       ([{"op": "zip"}, {"op": "zip"}], "打包 → 打包")):
        r = chain.build_chain({"mode": "serial", "fileIds": [f.id], "steps": steps})
        tz = store.get_task(r["taskIds"][0])
        _publish(tz, _uniq_rel("zips/batch.zip", 700 + len(tag)))
        after = [store.get_task(t) for t in r["taskIds"][1:]]
        # 汇总类下游（`打包 → 打包` 的第二张）**本来就没有 `file_id`**（它吃全批），
        # 所以判据是"要么还是当前文件、要么本来就是 None"，重点是
        # `src_output` / `_artifacts` 都不许被写进去。
        check(f"{tag}：下游没有拿到压缩包当输入（`src_output` 也没被回填）",
              all(not t.src_output and not t.params.get("_artifacts")
                  and t.file_id in (None, f.id) for t in after),
              [(t.file_id, t.src_output, t.params.get("_artifacts")) for t in after])

    # ---------------------------------------------------------------- 2
    print()
    print("== 2. 反向对照：转换 / 标准化的下游**必须**拿到产物 ==")
    miss: list[tuple[str, str]] = []
    n_derived = 0
    for a in derived_ops:
        for b in OPS:
            if b == a or b == "zip":
                continue
            ok, _why = boundary.availability(a, b)
            if not ok:
                continue
            r = chain.build_chain({"mode": "serial", "fileIds": [f.id],
                                   "steps": [{"op": a}, {"op": b}]})
            ta, tb = (store.get_task(t) for t in r["taskIds"])
            _publish(ta, _uniq_rel(DERIVED_REL[a], 900 + n_derived))
            tb2 = store.get_task(tb.id)
            n_derived += 1
            if tb2.file_id == f.id or not tb2.src_output:
                miss.append((a, b))
    check(f"枚举 {n_derived} 组 derived 组合：下游都换到了产物行", not miss, miss[:6])
    check("组数就是方案里的 26 组（2 个 derived op × 13 个下游）",
          n_derived == 26, n_derived)

    # ---------------------------------------------------------------- 3
    print()
    print("== 3. `consumes=False` 的两个：按**产物类型**判，不按它判 ==")
    # 这条是"别把 consumes 当成交接开关"的钉子：
    # `转 FLAC → 完整性校验` 校验的应当是**产物**（想要的语义），
    # 而 `波形 → 完整性校验` 只能校验原文件（旁路产物喂不进去）。
    r = chain.build_chain({"mode": "serial", "fileIds": [f.id],
                           "steps": [{"op": "convert", "params": {"format": "flac"}},
                                     {"op": "verify"}]})
    cv, vf = (store.get_task(t) for t in r["taskIds"])
    _publish(cv, "x.flac")
    check("转换 → 校验：校验的是**产物**（保留原语义）",
          store.get_task(vf.id).file_id != f.id, store.get_task(vf.id).file_id)
    r = chain.build_chain({"mode": "serial", "fileIds": [f.id],
                           "steps": [{"op": "waveform"}, {"op": "verify"}]})
    wf, vf2 = (store.get_task(t) for t in r["taskIds"])
    check("波形 → 校验：`src_task_id` 仍然连（串行依次跑）", vf2.src_task_id == wf.id)
    _publish(wf, "waveforms/x.png")
    check("波形 → 校验：校验的**不是**那张 PNG",
          store.get_task(vf2.id).file_id == f.id,
          store.get_task(vf2.id).file_id)

    # ---------------------------------------------------------------- 4
    print()
    print("== 4. R8：拿到非音频非图片时，报错是人话（不是 ffmpeg 原文）==")
    decoy = config.OUTPUTS / "loudness" / "song.loudness.md"
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_text("# 报告\n", encoding="utf-8")
    md_row = store.add_derived_file(rel_to_outputs="loudness/song.loudness.md",
                                    name="song.loudness.md", size=8, mtime=1.0,
                                    derived_from="t_none")
    t_bad = store.create_task("loudness", file_id=md_row.id,
                              params={"_op": "loudness-report"})
    try:
        tasks_mod._file_of(store.get_task(t_bad.id))
        check("`_file_of` 对 .md 输入直接报错", False, "没报错")
    except RuntimeError as e:
        msg = str(e)
        check("报错说清了「上一步交出的是旁路产物」", "旁路产物" in msg, msg)
        check("报错给了出路（移出链 / 切并行档）",
              "并行" in msg or "移出链" in msg, msg)
        check("报错里带上文件名，而不是 ffmpeg 的原文",
              "song.loudness.md" in msg and "Invalid data" not in msg, msg)

    # 真跑一次：队列把 RuntimeError 变成任务失败，错误文案就是上面那句
    tasks_mod.register_all(q_mod.queue_)
    q_mod.queue_.start()
    try:
        store.create_task("probe", file_id=f.id)          # 让队列有活干前先热身
        t_run = store.create_task("loudness", file_id=md_row.id,
                                  params={"_op": "loudness-report"})
        q_mod.queue_.submit(t_run.id)
        # 等待预算给宽一点：队列只有 2 个 worker，而前面几节会真跑响度类任务 ——
        # `CACHE_VERSION` 一升（3，2026-10）所有缓存作废，冷缓存下那是"整曲解码 +
        # DRP 聚类"，两个 worker 可能都被占住。这里的断言是**"必须失败、不能挂住"**，
        # 不是"要多快"，所以 20s 那种紧预算只会测出抖动。
        end = time.time() + 60
        row = None
        while time.time() < end:
            row = store.get_task(t_run.id)
            if row and row.state in ("success", "failed", "skipped", "cancelled"):
                break
            time.sleep(0.1)
        check("任务以失败结案（而不是卡住）",
              bool(row) and row.state == "failed", (row.state if row else None))
        check("任务的 error 就是那句人话",
              bool(row) and "旁路产物" in (row.error or ""),
              (row.error or "")[:120])
    finally:
        q_mod.queue_.stop()

    # ---------------------------------------------------------------- 5
    print()
    print("== 5. 图片输入不受影响（探测封面图是合法用法）==")
    img = config.UPLOADS / "cover.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 8)
    img_row = store.add_file("cover.png", size=img.stat().st_size,
                             mtime=img.stat().st_mtime)
    t_img = store.create_task("probe", file_id=img_row.id)
    try:
        tasks_mod._file_of(store.get_task(t_img.id))
        check("probe 读图片不被人话守卫拦住", True)
    except RuntimeError as e:
        check("probe 读图片不被人话守卫拦住", False, e)

finally:
    _reset_store()
    config.DB_PATH, config.UPLOADS, config.OUTPUTS = _orig
    shutil.rmtree(_tmp, ignore_errors=True)

print()
print(f"结果：{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
