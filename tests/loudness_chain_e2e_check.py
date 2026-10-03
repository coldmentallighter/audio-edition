"""**执行链 → 响度报告**端到端自检：链上的分析卡片必须真的落下产物。

对应《响度总览图（LoudnessAnalysis）实现构想.md》与执行链方案 §3.2，
但钉的是一条**用户实测踩出来的**回归：

  「响度分析报告」在执行链里跑，任务 `success`、进度 100，**却什么都不产出**
  —— `outputs/loudness/` 是空的，也没有任何 error。同一个卡片**单跑**（链上
  单击那一步走 `POST /api/ops/loudness-report`）却好得很。

  根因是**两条建任务的路径只接线了一条**：`loudness` / `loudness-image` /
  `loudness-report` 三个 op 共用任务类型 `loudness`，handler 靠服务端注入的
  `params["_op"]` 分流；路由那条路注入了，`chain.build_chain` 那条直接把 step 的
  params 拷给任务 —— 于是链上的报告退化成"只算一遍响度写缓存"（那正是
  `loudness` 的语义），任务照旧成功。

  修法是把注入收进 `specs.task_params`，两条路都走它（见该函数的注释）。
  `tests/chain_build_check.py` §6c 钉"建出来的任务带 `_op`"（秒级、不需要
  ffmpeg）；这里钉**整条链真的把文件写出来**，两者互补：只钉参数的话，
  handler 哪天不再认 `_op` 也没人发现。

用**临时生成的 3 秒正弦 flac**、临时工作区真跑（真队列 + 真 ffmpeg ebur128），
不碰工作区的 audioedition.db / uploads / outputs / .cache。

用法：python tests/loudness_chain_e2e_check.py
"""
from __future__ import annotations

import shutil
import subprocess
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

PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


_tmp = Path(tempfile.mkdtemp(prefix="ae-loud-e2e-"))
# `ROOT` 要连 `OUTPUTS` 一起换：`tasks._artifact()` 把产物路径算成**相对项目根**
# 的形式，只换 OUTPUTS 会让它 `relative_to` 失败（那是隔离手法带来的，不是产品问题）。
_orig = (config.ROOT, config.DB_PATH, config.UPLOADS, config.OUTPUTS, config.CACHE)
config.ROOT = _tmp
config.DB_PATH = _tmp / "t.db"
config.UPLOADS = _tmp / "up"
config.OUTPUTS = _tmp / "out"
config.CACHE = _tmp / "cache"
for _d in (config.UPLOADS, config.OUTPUTS, config.CACHE):
    _d.mkdir(parents=True, exist_ok=True)

LOUD = config.OUTPUTS / "loudness"
WAIT_S = 180.0


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


def _wait(tids: list[str], timeout: float = WAIT_S) -> list:
    """等到这一批任务全部落定（链是一步入队的，但执行是异步的）。"""
    end = time.time() + timeout
    while time.time() < end:
        rows = [store.get_task(t) for t in tids]
        if rows and all(r and r.state in ("success", "failed", "cancelled", "skipped")
                        for r in rows):
            return rows
        time.sleep(0.2)
    return [store.get_task(t) for t in tids]


def _md() -> list[Path]:
    return sorted(LOUD.glob("*.loudness.md"))


def _png() -> list[Path]:
    return sorted(LOUD.glob("*.loudness.png"))


try:
    _reset_store()
    store.init_db()

    # 真源文件：3 秒 440Hz 正弦（压到 -12dB，免得真峰值顶到 0 附近）
    src = config.UPLOADS / "sine.flac"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-af", "volume=-12dB", "-c:a", "flac", str(src)],
        check=True, timeout=120)
    f = store.add_file("sine.flac", size=src.stat().st_size, mtime=src.stat().st_mtime)

    tasks_mod.register_all(q_mod.queue_)     # 服务启动时也是这一步
    q_mod.queue_.start()

    # ---------------------------------------------------------------- 1
    print("== 1. 链上的「响度分析报告」真出一份 Markdown ==")
    r = chain.build_chain({"mode": "serial", "fileIds": [f.id],
                           "steps": [{"op": "loudness-report", "name": "响度分析报告",
                                      "params": {"detail": "full", "force": True}}]})
    t = _wait(r["taskIds"])[0]
    check("这一步的任务带上了 `_op`（分流键是服务端注入的）",
          (t.params or {}).get("_op") == "loudness-report", t.params)
    check("任务成功", t.state == "success", (t.state, t.error))
    check("outputs/loudness/ 里真的出现了 .md", len(_md()) == 1, _md())
    if _md():
        body = _md()[0].read_text(encoding="utf-8")
        check("报告成型（`detail=full` 才有逐曲明细）",
              "## 汇总" in body and "## 逐曲明细" in body, body[:120])
        check("任务 result 里带上了产物路径（前端据此给下载/预览）",
              str((t.result or {}).get("output", "")).endswith(".loudness.md"),
              t.result)

    # ---------------------------------------------------------------- 2
    print()
    print("== 2. 链上的「导出响度分析图」真出一张 PNG ==")
    r2 = chain.build_chain({"mode": "parallel", "fileIds": [f.id],
                            "steps": [{"op": "loudness-image", "name": "导出响度分析图",
                                       "params": {"force": True}}]})
    t2 = _wait(r2["taskIds"])[0]
    check("这一步的任务带上了 `_op`",
          (t2.params or {}).get("_op") == "loudness-image", t2.params)
    check("任务成功", t2.state == "success", (t2.state, t2.error))
    check("outputs/loudness/ 里真的出现了 .png", len(_png()) == 1, _png())
    check("PNG 不是空文件", bool(_png()) and _png()[0].stat().st_size > 8_000,
          _png()[0].stat().st_size if _png() else None)

    # ---------------------------------------------------------------- 3
    print()
    print("== 3. 反向对照：`loudness`（总览图）不该多落任何文件 ==")
    # 它是家族里唯一**不写侧车**的语义 —— 前端聚焦时自己画 Canvas。
    # 这条防的是"修 `_op` 时手一抖，把三个分支都写成落文件"。
    r3 = chain.build_chain({"mode": "parallel", "fileIds": [f.id],
                            "steps": [{"op": "loudness", "params": {"force": True}}]})
    t3 = _wait(r3["taskIds"])[0]
    check("这一步的任务同样带 `_op`", (t3.params or {}).get("_op") == "loudness",
          t3.params)
    check("任务成功", t3.state == "success", (t3.state, t3.error))
    check("没有多出第二个 .md", len(_md()) == 1, _md())
    check("没有多出第二个 .png", len(_png()) == 1, _png())

finally:
    q_mod.queue_.stop()
    _reset_store()
    config.ROOT, config.DB_PATH, config.UPLOADS, config.OUTPUTS, config.CACHE = _orig
    shutil.rmtree(_tmp, ignore_errors=True)

print()
print(f"结果：{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
