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
from backend import loudness_svg                                   # noqa: E402
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

# 链上产物在「本次执行的目录」里（`outputs/upload-<日期>-<尾号>/loudness/`），
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
    # ⚠ 两个坑一起踩过，都在这个通配里：
    #   ① 要写成 `*.loudness*.md`，不能写 `*.loudness.md` —— `unique_path` 把
    #      去重后缀插在**扩展名之前**（`sine.loudness-1.md`），写死了数不到第二个；
    #   ② 得用 `rglob`：**链上**产物落进"本次执行的目录"
    #      （`outputs/upload-<日期>-<chain 尾号>/loudness/…`，方案 §5），
    #      而本节跑的全是链；非链任务（§4）才仍在 `outputs/loudness/` 下。
    return sorted(config.OUTPUTS.rglob("*.loudness*.md"))


def _svg() -> list[Path]:
    return sorted(config.OUTPUTS.rglob("*.loudness*.svg"))


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
    check("链上产物里真的出现了 .md", len(_md()) == 1, _md())
    if _md():
        body = _md()[0].read_text(encoding="utf-8")
        check("报告成型（`detail=full` 才有逐曲明细）",
              "## 汇总" in body and "## 逐曲明细" in body, body[:120])
        check("任务 result 里带上了产物路径（前端据此给下载/预览）",
              str((t.result or {}).get("output", "")).endswith(".loudness.md"),
              t.result)

    # ---------------------------------------------------------------- 2
    print()
    print("== 2. 链上的「导出响度分析图」真出一张 SVG ==")
    r2 = chain.build_chain({"mode": "parallel", "fileIds": [f.id],
                            "theme": "t2", "themeMode": "dark",
                            "steps": [{"op": "loudness-image", "name": "导出响度分析图",
                                       "params": {"force": True}}]})
    t2 = _wait(r2["taskIds"])[0]
    check("这一步的任务带上了 `_op`",
          (t2.params or {}).get("_op") == "loudness-image", t2.params)
    check("任务成功", t2.state == "success", (t2.state, t2.error))
    check("链上产物里真的出现了 .svg", len(_svg()) == 1, _svg())
    # SVG 比 PNG 小得多是正常的（矢量 vs 位图）；这里断言的是"它真是一份完整的图"，
    # 而不是"字节数够大"。
    _doc = _svg()[0].read_text(encoding="utf-8") if _svg() else ""
    check("SVG 有完整的根元素", _doc.startswith("<svg") and _doc.rstrip().endswith("</svg>"),
          len(_doc))
    check("SVG 里四个图层都在", all(k in _doc for k in (
        'id="filename"', 'id="axis-rail"', 'id="plot"', 'id="time-band"',
        'id="cards"')))
    check("SVG 用的是 F 轴（9 个刻度线都在）", _doc.count('class="grid"') == 9,
          _doc.count('class="grid"'))
    # 主题：链级参数要落到这一步的任务上，并且颜色真的按它算成实色（老板 2026-10）
    check("链级 `theme`/`themeMode` 落到了这一步的任务参数里",
          (t2.params or {}).get("theme") == "t2"
          and (t2.params or {}).get("themeMode") == "dark", t2.params)
    check("颜色已硬编码：整份文件没有 `var(`", "var(" not in _doc)
    check("深色主题真的生效了（画布 = t2 深色的 `--bg-app`）",
          f'fill="{loudness_svg.chart_palette("t2", "dark")["bg"]}"' in _doc,
          loudness_svg.chart_palette("t2", "dark")["bg"])

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
    check("没有多出第二个 .md（本次执行的目录里）", len(_md()) == 1, _md())
    check("没有多出第二个 .svg", len(_svg()) == 1, _svg())

    # ---------------------------------------------------------------- 4
    print()
    print("== 4. 测量值落进文件记录，而且 probe / 改标签之后还在 ==")
    # 卡片上那行「Loudness … LUFS」的数据来源。用户实测报的是它一直显示
    # `undefined LUFS`：后端测完只塞进任务结果、从不落库；前端也压根没读
    # `info.loudness`（前端那半由 `tests/loudness_display_check.py` 钉）。
    # 这里钉**后端这半**，顺带钉住最容易复发的那一步：`probe` 与改标签
    # 都会重写文件记录，而它们**读不出响度**（ffprobe 不解码），
    # 所以只能合并、不能整体覆盖 —— 否则"重新探测一下"数字就没了。
    def _info() -> dict:
        return store.get_file(f.id).info or {}

    measured = _info().get("loudness")
    check("响度落进了 info（`loudness`）", isinstance(measured, (int, float)),
          _info())
    check("同一组里还有真峰值与响度范围",
          isinstance(_info().get("truePeak"), (int, float))
          and isinstance(_info().get("loudnessRange"), (int, float)), _info())

    def _run(type_: str, **params):
        t = store.create_task(type_, file_id=f.id, params=params or {})
        q_mod.queue_.submit(t.id)
        return _wait([t.id])[0]

    pr = _run("probe")
    check("probe 任务成功", pr.state == "success", (pr.state, pr.error))
    check("「重新探测」之后响度还在（probe 必须合并）",
          _info().get("loudness") == measured, (measured, _info().get("loudness")))
    check("但探测结果刷新了（info 不是空壳）", bool(_info().get("format")), _info())

    tg = _run("tag_edit", tags={"title": "改过的标题"})
    check("改标签任务成功", tg.state == "success", (tg.state, tg.error))
    check("改标签之后响度还在（改标签会重写文件，但音频没动）",
          _info().get("loudness") == measured, (measured, _info().get("loudness")))
    check("标签真的写进去了",
          str((_info().get("tags") or {}).get("title") or "") == "改过的标题",
          _info().get("tags"))

    # 响度标准化那条路也要落库：它第一遍（loudnorm 测量趟）就把响度测出来了，
    # 以前同样只塞进任务结果。
    nz = _run("normalize", targetLufs=-16)
    check("标准化任务成功", nz.state == "success", (nz.state, nz.error))
    nz_measured = (nz.result or {}).get("measured") or {}
    check("标准化之后响度也落库了（等于 loudnorm 实测的 input_i）",
          isinstance(_info().get("loudness"), (int, float))
          and abs(_info()["loudness"] - float(nz_measured.get("input_i"))) < 0.01,
          (_info().get("loudness"), nz_measured.get("input_i")))

    # 内容真的换了（同一路径又拖进来一个**不同**的文件）：测量值必须丢掉 ——
    # probe 永远纠正不了过期响度，留着就是"上一个文件的数字"，比 `—` 更糟。
    store.add_file("sine.flac", size=src.stat().st_size + 1,
                   mtime=src.stat().st_mtime)
    check("内容换了（大小不同）→ 过期测量值被丢掉",
          "loudness" not in _info(), _info())
    check("丢掉测量值不连累元数据", bool(_info().get("format")), _info())

    # ---------------------------------------------------------------- 5
    print()
    print("== 5. 用户实测那条失败链：**串行**档「响度报告 → 导出响度分析图」==")
    # 现象（原始报错，来自真实使用）：
    #   Error opening input file ...\outputs\loudness\….loudness.md
    #   Invalid data found when processing input
    # 根因：串行档把上游产物交给下一步，而 `.md` 是**旁路产物**，不该参与输入解析
    # （`contract.py` 早就这么写，是运行时没照做）。修法见
    # `queue._publish_derived`（只交接 `produce='derived'`）与方案 §3 R1/R5。
    # 这一节必须**真跑一条串行链**：`chain_handoff_check.py` 钉的是"交接判据"，
    # 而这里钉的是"用户那条链现在真的能跑完、两样产物都在"。
    n_md, n_png = len(_md()), len(_svg())
    r5 = chain.build_chain({"mode": "serial", "fileIds": [f.id],
                            "steps": [{"op": "loudness-report", "name": "响度分析报告",
                                       "params": {"_op": "loudness-report"}},
                                      {"op": "loudness-image", "name": "导出响度分析图",
                                       "params": {"force": True}}]})
    rows5 = _wait(r5["taskIds"])
    check("串行链两步都成功（第 2 步不再拿着 .md 去解码）",
          all(r.state == "success" for r in rows5),
          [(r.step_idx, r.state, (r.error or "")[:90]) for r in rows5])
    check("第 2 步的输入**没有**被换成报告文件",
          all(not (r.src_output or "").endswith(".md") for r in rows5),
          [(r.step_idx, r.src_output) for r in rows5])
    check("报告与图各多出一份（两步都真出了东西）",
          len(_md()) == n_md + 1 and len(_svg()) == n_png + 1,
          (len(_md()), n_md, len(_svg()), n_png,
           [(r.step_idx, (r.result or {}).get("output")) for r in rows5]))

finally:
    q_mod.queue_.stop()
    _reset_store()
    config.ROOT, config.DB_PATH, config.UPLOADS, config.OUTPUTS, config.CACHE = _orig
    shutil.rmtree(_tmp, ignore_errors=True)

print()
print(f"结果：{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
