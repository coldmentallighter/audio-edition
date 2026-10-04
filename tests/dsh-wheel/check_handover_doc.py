"""Verify every concrete claim the handover doc makes against the actual code.

A handover document that states things which are not true is worse than no document.
This checks the assertions in 响度图重构-交接.md mechanically.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

DOC = ROOT / "响度图重构-交接.md"
PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


def main():
    text = DOC.read_text(encoding="utf-8")

    print("== files the doc references must exist ==")
    for rel in ("backend/audio.py", "backend/drp.py", "backend/toolchain.py",
                "backend/theme.py", "theme.css",
                "tests/dsh-wheel/axis_spec.py", "tests/dsh-wheel/check_drp.py",
                "tests/dsh-wheel/drp_truth.py",
                "tests/dsh-wheel/cards_store_check.py",
                "tests/dsh-wheel/check_loudness_metrics.py",
                "tests/dsh-wheel/verify_units.py",
                "tests/dsh-wheel/verify_measure_doc.py",
                "tests/dsh-wheel/README.md",
                "响应度".replace("响应度", "响度总览图（LoudnessAnalysis）实现构想.md"),
                "测量图指标设计.md",
                "音频测量指标设计文档（基于 FFmpeg）.md"):
        check(f"exists: {rel}", (ROOT / rel).exists())

    print()
    print("== axis claims ==")
    import axis_spec as A
    check("axis bottom is -50", A.LUFS_BOTTOM == -50.0, A.LUFS_BOTTOM)
    check("axis top is +0.3", A.LUFS_TOP == 0.3, A.LUFS_TOP)
    check("the knee is at -30 taking 70 % of the plot",
          A.KNEE == -30.0 and A.KNEE_SHARE == 0.70, (A.KNEE, A.KNEE_SHARE))
    check("9 labelled ticks",
          A.LABELLED == (0.0, -3.0, -5.0, -7.0, -10.0, -14.0, -16.0, -23.0, -30.0),
          A.LABELLED)
    check("no grid-line-only ticks left", A.UNLABELLED == (), A.UNLABELLED)
    check("red above -3", A.RED_ABOVE == -3.0, A.RED_ABOVE)
    check("neither end point is labelled",
          A.LUFS_TOP not in A.LABELLED and A.LUFS_BOTTOM not in A.LABELLED)
    check("log shift is 51 at the knee (knee+51 = 21)",
          A.LOG_SHIFT == 51.0 and A.LOG_KNEE == 21.0, (A.LOG_SHIFT, A.LOG_KNEE))
    # the doc's §1.3 accepted-cost numbers, quoted against the page's plot height
    h = A.PLOT_H_REF
    active = (A.frac(-16) - A.frac(0)) * 100
    tail = (A.frac(A.LUFS_BOTTOM) - A.frac(-30)) * 100
    check("0..-16 share is 37.0 %", abs(active - 37.0) < 0.1, active)
    check("-30..-50 share is 30.0 %", abs(tail - 30.0) < 0.1, tail)
    check("the red zone is 7.6 % of the plot",
          abs(A.frac(A.RED_ABOVE) * 100 - 7.6) < 0.1, A.frac(A.RED_ABOVE) * 100)
    check("all 9 labels need 303 pt of plot",
          abs(A.min_plot_height(h) - 303.0) < 2.0, A.min_plot_height(h))
    check("the `0` label overhangs the top edge by 3.66 pt",
          abs(A.top_clearance(h)["deficit"] - 3.66) < 0.1, A.top_clearance(h))
    check("axis_spec invariants all pass", A.check())
    # the doc must no longer describe the superseded pure-log axis as current
    check("the doc marks the old pure-log spec as 作废/留档",
          "已作废" in text and "旧 的纯对数规格" not in text)

    print()
    print("== measurement claims ==")
    from backend import audio
    check("CACHE_VERSION is 5", audio.CACHE_VERSION == 5, audio.CACHE_VERSION)
    check("_to_db exists", callable(getattr(audio, "_to_db", None)))
    check("_percentile exists", callable(getattr(audio, "_percentile", None)))
    check("SILENCE_LUFS is -120", audio.SILENCE_LUFS == -120.0)
    d = audio.parse_ebur_metadata("")
    check("parse_ebur_metadata tolerates empty input", isinstance(d, dict))
    sm = d.get("summary", {})
    for k in ("integrated", "lra", "dra", "plr", "momentaryMax",
              "shortTermMax", "truePeakMax", "samplePeakMax"):
        check(f"summary has {k}", k in sm, sorted(sm))
    src = (ROOT / "backend" / "audio.py").read_text(encoding="utf-8")
    check("ffmpeg call uses peak=sample+true", "peak=sample+true" in src)
    # Only the EXECUTABLE ffmpeg argument counts. `peak=true` legitimately appears in
    # comments explaining why it is wrong, so find the runner.run(...) call that
    # actually carries the ebur128 filter and inspect that argv.
    argv_calls = re.findall(r"runner\.run\(\[(.*?)\]\s*,", src, re.S)
    ebur = [c for c in argv_calls if "ebur128" in c]
    check("exactly one runner.run call carries the ebur128 filter", len(ebur) == 1,
          len(ebur))
    argv = ebur[0] if ebur else ""
    check("that call uses peak=sample+true", "peak=sample+true" in argv)
    check("that call has no bare peak=true",
          "peak=true:" not in argv and '"peak=true"' not in argv,
          argv[-220:])

    print()
    print("== drp claims ==")
    from backend import drp
    check("drp module importable", drp is not None)
    for fn in ("patterns", "extremes", "slope_series", "_merge_runs",
               "_resolve_conflicts", "_complete_linkage"):
        check(f"drp.{fn} exists", callable(getattr(drp, fn, None)))
    check("_merge_runs takes a step argument",
          "step" in _params(drp._merge_runs), _params(drp._merge_runs))
    check("_resolve_conflicts takes min_idx",
          "min_idx" in _params(drp._resolve_conflicts),
          _params(drp._resolve_conflicts))

    print()
    print("== DRP / store wiring (todos 2 and 3 are DONE as of 2026-10) ==")
    check("loudness_timeline calls drp", "pats = drp.patterns(" in src)
    check("DRP failures are surfaced, not swallowed (drpError in summary)",
          "drpError" in src)
    check("the DRP occurrence list is published for the time band",
          "drpOccurrences" in src)
    store = (ROOT / "backend" / "store.py").read_text(encoding="utf-8")
    m = re.search(r"MEASUREMENT_KEYS = \(([^)]*)\)", store)
    keys = m.group(1) if m else ""
    check("MEASUREMENT_KEYS carries samplePeak / dra / drp",
          all(k in keys for k in ("samplePeak", "dra", "drp")), keys)

    print()
    print("== 主题 claims（2026-10：按执行链时的主题上色 + 颜色硬编码）==")
    from backend import theme as theme_mod
    from backend.cards import specs as specs_mod
    from backend import loudness_svg as svg_mod
    check("theme.css 解析出 6 套（3 主题 × 2 模式）", len(theme_mod.available()) == 6,
          theme_mod.available())
    check("THEME_OPS 里有 loudness-image", "loudness-image" in specs_mod.THEME_OPS,
          specs_mod.THEME_OPS)
    check("渲染器能按主题出图（有 theme/mode 参数）",
          {"theme", "mode"} <= set(_params(svg_mod.render_loudness_svg)),
          _params(svg_mod.render_loudness_svg))
    check("渲染器不再输出 CSS 变量（颜色硬编码）",
          "var(" not in svg_mod.render_loudness_svg(
              {"duration": 1.0, "t": [0.0], "S": [-20.0], "truePeak": [-3.0]},
              title="t", theme="t2", mode="dark"))
    app = (ROOT / "app.js").read_text(encoding="utf-8")
    check("客户端把当前主题随提交带上（currentTheme）",
          "function currentTheme()" in app and "themeMode" in app)
    chain_src = (ROOT / "backend" / "chain.py").read_text(encoding="utf-8")
    check("建链时把链级主题注入到 THEME_OPS 的步骤",
          "THEME_OPS" in chain_src and "themeMode" in chain_src)

    print()
    print("== 卡片持久化 claims（老板问「卡片怎么一直消失」）==")
    from backend.cards import store as card_store
    check("自定义卡片有实体文件（cards.json）",
          card_store.CARDS_JSON.name == "cards.json"
          and card_store.CARDS_JSON.parent == ROOT, card_store.CARDS_JSON)
    csrc = (ROOT / "backend" / "cards" / "store.py").read_text(encoding="utf-8")
    check("读失败**不再**伪造空配置（那条会把用户卡片写没）",
          "CardsFileError" in csrc and 'return {"version": 1, "cards": [], '
          not in csrc)
    check("临时文件名唯一（不许共用 cards.json.tmp）",
          "uuid.uuid4()" in csrc and 'with_suffix(".json.tmp")' not in csrc)
    check("替换前留上一代备份（cards.json.bak）", "CARDS_BAK" in csrc)
    check("app 注册了 CardsFileError 的全局处理器（报错而不是装作没卡片）",
          "CardsFileError" in (ROOT / "backend" / "app.py").read_text(encoding="utf-8"))
    smoke = (ROOT / "tests" / "smoke_api.py").read_text(encoding="utf-8")
    check("冒烟不再断言「只剩内置卡片」（那等于要求用户没有卡片）",
          "清理后只剩内置卡片" not in smoke and "_pre_custom_ids" in smoke)

    print()
    print("== documented verification commands must actually pass ==")
    # 只钉"0 failed"，不钉 passed 的条数：条数每次加断言都会变，
    # 而交接文档要保证的是"这条命令跑得过"。
    for script in ("check_loudness_metrics.py", "check_drp.py", "drp_truth.py",
                   "cards_store_check.py"):
        r = subprocess.run([sys.executable, str(HERE / script)],
                           capture_output=True, text=True, cwd=str(HERE),
                           encoding="utf-8", errors="replace")
        ok = "0 failed" in (r.stdout + r.stderr)
        check(f"{script} reports '0 failed'", ok,
              (r.stdout or r.stderr).strip().splitlines()[-1:])
    r = subprocess.run([sys.executable, str(HERE / "axis_spec.py")],
                       capture_output=True, text=True, cwd=str(HERE),
                       encoding="utf-8", errors="replace")
    check("axis_spec.py reports ALL PASS", "ALL PASS" in r.stdout,
          r.stdout.strip().splitlines()[-1:])

    print()
    print(f"result: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def _params(fn) -> list[str]:
    import inspect
    try:
        return list(inspect.signature(fn).parameters)
    except Exception:
        return []


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
