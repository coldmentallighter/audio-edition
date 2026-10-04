"""The frozen page layout, plus the checker that re-derives it from the sketch.

**The spec itself moved to `backend/chart_layout.py`** -- the SVG renderer consumes it,
and `backend` must not import from `tests/`. This module now:

* re-exports every constant and helper from `backend.chart_layout`, so all the
  documented commands (`python tests/dsh-wheel/layout_spec.py --from-ai`) keep working;
* keeps the `.ai`-specific half: the CID decoder and `check_against_ai()`, which need
  the pure-stdlib extractor under `tests/_design-extract/tools/`.

    python tests/dsh-wheel/layout_spec.py            # layout report + invariants
    python tests/dsh-wheel/layout_spec.py --from-ai   # ... and re-check against 大致布局.ai
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.chart_layout import *                                     # noqa: F401,F403
from backend.chart_layout import (AI_PATH, BOXES, CANVAS_H, CANVAS_W,   # noqa: F401
                                  CONTENT_BOTTOM, CONTENT_LEFT, CONTENT_RIGHT,
                                  CONTENT_TOP, COVER, DYN_CARD, LOUD_CARD,
                                  META_CARD, META_TEXT, PLOT, TIME_BAND,
                                  axis_label_fit, canvas_height, check,
                                  energy_rms_lufs, format_time, grid_columns,
                                  grid_rows, layout_report, min_plot_height,
                                  plot_height_if_band_grows, time_band_height,
                                  time_band_rows, y_in_plot)

# --------------------------------------------------------------------------- CJK
#: Adobe-GB1 CID -> hanzi。来源：`文件时长/声道数/采样率/位深/文件大小/测量算法`
#: 六行文字在 PDF 里的 CID 序列，与 `.ai` 内 AI11 文本文档里的真实 Unicode 逐行对齐。
CJK_CIDS: dict[int, str] = {
    1168: "采", 1193: "测", 1225: "长", 1398: "大", 1441: "道", 1605: "法",
    2161: "件", 2568: "量", 2673: "率", 3367: "深", 3378: "声", 3400: "时",
    3476: "数", 3544: "算", 3786: "位", 3795: "文", 3948: "小", 4130: "样",
}


def decode_cid_text(s: str) -> str:
    """把提取器给出的乱码还原成真实文字。

    `ai_extract` 对这个字体拿不到 CMap，于是把每个 2 字节 CID 当 UTF-16BE 解 ——
    结果就是 `chr(cid)`。所以：CID 1..95 加 31 得 ASCII，其余查 `CJK_CIDS`。
    """
    out = []
    for ch in s:
        cid = ord(ch)
        if 1 <= cid <= 95:
            out.append(chr(cid + 31))
        elif cid in CJK_CIDS:
            out.append(CJK_CIDS[cid])
        else:
            out.append("?")
    return "".join(out)


# --------------------------------------------------------------------------- AI check
def read_ai(path: Path | None = None) -> dict:
    """Run the `_design-extract` wheel over the sketch and normalise the result.

    Returns `{"page": (w, h), "rects": [...], "lines": [...], "texts": [...]}` with
    duplicates collapsed (the sketch draws several rectangles and every text twice).
    """
    tools = ROOT / "tests" / "_design-extract" / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    import ai_extract                                                  # noqa: PLC0415

    model = ai_extract.build_model(str(path or AI_PATH))
    page = model["page"]
    # Values are kept raw (NOT rounded): comparing rounded tuples makes the result
    # depend on the last bit of a float literal -- `round(206.7105, 3)` is 206.71
    # while the .ai's 206.7105000000002-ish rounds to 206.711. `has()` compares
    # with a tolerance instead.
    rects, lines, texts = set(), set(), set()
    for el in model["elements"]:
        if el["kind"] == "text":
            texts.add((decode_cid_text(el["text"]), el["x"], el["y_top"], el["size"]))
        elif el["w"] == 0.0 or el["h"] == 0.0:
            lines.add((el["x"], el["y_top"], el["w"], el["h"]))
        else:
            rects.add((el["x"], el["y_top"], el["w"], el["h"]))
    return {"page": (page["w"], page["h"]), "rects": rects, "lines": lines,
            "texts": texts}


def has(coll, want, *, tol: float = 0.01) -> bool:
    """Is `want` in `coll`, comparing numbers with a tolerance?

    `want` is `(text?, x, y, ...)`: a leading `str` must match exactly, every other
    field numerically.
    """
    for item in coll:
        if len(item) != len(want):
            continue
        for got, exp in zip(item, want):
            if isinstance(exp, str):
                if got != exp:
                    break
            elif not isinstance(exp, (int, float)) or abs(got - exp) > tol:
                break
        else:
            return True
    return False


def check_against_ai(path: Path | None = None) -> bool:
    """Assert **the sketch** against the `.ai`, then assert the widening rule.

    ⚠ 这里核的是 `SKETCH_*`（草图实测值），**不是**当前生效的版式：老板 2026-10
    要求"图加宽一些"，生效版式的绘图区是 900 宽、而草图是 660。如果直接拿生效值去
    核 `.ai`，这条断言就会因为一次尺寸调整而失效 —— 那等于把"与设计稿一致"这句话废掉。
    所以改成两步：① 草图值 == `.ai`；② 生效值 == 草图值 + 一条**明写的**横向加宽。
    """
    if not (path or AI_PATH).exists():
        print(f"  SKIP  草图不存在：{path or AI_PATH}")
        return True
    ai = read_ai(path)
    ok = True

    def t(name: str, cond: bool, extra: object = "") -> None:
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + name
              + (f"   {extra}" if extra else ""))
        ok = ok and cond

    near = lambda a, b, tol=0.02: abs(a - b) <= tol                   # noqa: E731

    # ---- ① 草图实测值（现在只是"设计稿长这样"的记录）----
    ph = 482.13
    sk = {
        "axis_rail": (20.0, 60.0, 80.0, ph),
        "plot": (100.0, 60.0, SKETCH_PLOT_W, ph),
        "time_band": (100.0, 560.0, SKETCH_TIME_BAND_W, 140.0),
        "meta_card": (SKETCH_META_X, 60.0, 200.0, 304.5),
        "loud_card": (SKETCH_META_X, 364.5, 200.0, 177.63),
        "dyn_card": (SKETCH_META_X, 560.0, 200.0, 140.0),
        "cover": (SKETCH_META_X + 200.0 - 17.949 - 100.0, 86.374, 100.0, 100.0),
    }
    t("canvas matches the .ai MediaBox",
      near(ai["page"][0], SKETCH_CANVAS_W, 0.005)
      and near(ai["page"][1], CANVAS_H, 0.005), ai["page"])
    for key, rect in sk.items():
        t(f"sketch {key} is drawn at {rect[0]:g},{rect[1]:g} {rect[2]:g}x{rect[3]:g}",
          has(ai["rects"], rect))
    for k in range(1, 6):
        # 竖线在 .ai 里的形状是 (x, ytop, 0, h)，横线是 (x, ytop, w, 0) —— 别写反
        x = 100.0 + SKETCH_PLOT_W * k / 6
        t(f"sketch grid column x={x:.1f}", has(ai["lines"], (x, 60.0, 0.0, ph)))
        y = 60.0 + ph * k / 6
        t(f"sketch grid row y={y:.1f}", has(ai["lines"], (100.0, y, SKETCH_PLOT_W, 0.0)))
    for text, x, y, size in META_TEXT:
        # 文本 x 现在相对 META_X；草图里那块卡在 SKETCH_META_X
        skx = x - META_X + SKETCH_META_X
        t(f"sketch text {text!r} at {skx:.2f},{y:.2f} {size:.0f}pt",
          has(ai["texts"], (text, skx, y, size)))
    t("the sketch still contains exactly 11 distinct text runs",
      len(ai["texts"]) == 11, len(ai["texts"]))
    t("every distinct text run in the sketch is accounted for here",
      all(any(has([row], (w[0], w[1] - META_X + SKETCH_META_X, w[2], w[3]))
              for w in META_TEXT) for row in ai["texts"]),
      [r[0] for r in ai["texts"]])

    # ---- ② 生效版式 = 草图 + 一条明写的横向加宽，竖向一个数都不动 ----
    print("  --- 生效版式 vs 草图（只有横向加宽）---")
    t(f"绘图区宽 = {SKETCH_PLOT_W:g} + PLOT_EXTRA_W({PLOT_EXTRA_W:g})",
      near(PLOT.w, SKETCH_PLOT_W + PLOT_EXTRA_W))
    t("竖向：绘图区高 / 时间带高 / 三张卡的高与 y 全部不变",
      near(PLOT.h, ph) and near(TIME_BAND.h, 140.0) and near(TIME_BAND.y, 560.0)
      and near(META_CARD.y, 60.0) and near(META_CARD.h, 304.5)
      and near(LOUD_CARD.y, 364.5) and near(LOUD_CARD.h, 177.63)
      and near(DYN_CARD.y, 560.0) and near(DYN_CARD.h, 140.0)
      and near(COVER.y, 86.374))
    t("横向：标尺列/左边缘不变，右栏整体右移、间距保持 40",
      near(AXIS_RAIL.x, 20.0) and near(AXIS_RAIL.w, 80.0)
      and near(PLOT.x, 100.0) and near(META_CARD.x - PLOT.right, 40.0))
    t("时间带仍与绘图区同宽同左沿",
      near(TIME_BAND.x, PLOT.x) and near(TIME_BAND.w, PLOT.w))
    t("卡片列也加宽了，差值正好是 CARD_EXTRA_W",
      near(CARD_W - SKETCH_CARD_W, CARD_EXTRA_W)
      and near(META_CARD.w, CARD_W),
      f"{SKETCH_CARD_W:g} + {CARD_EXTRA_W:g} = {CARD_W:g}")
    t("画布变宽的量 = 绘图区加宽 + 卡片加宽",
      near(CANVAS_W - SKETCH_CANVAS_W, PLOT_EXTRA_W + CARD_EXTRA_W),
      f"{CANVAS_W:.2f} − {SKETCH_CANVAS_W} = {CANVAS_W - SKETCH_CANVAS_W:.2f}"
      f" vs {PLOT_EXTRA_W + CARD_EXTRA_W:g}")
    t("封面仍贴在卡片右边（偏移不随卡片变宽而漂）",
      near(META_CARD.right - COVER.right, 17.949)
      and near(COVER.x, META_CARD.right - 17.949 - COVER.w),
      f"右距 {META_CARD.right - COVER.right:.3f}")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="响度图版式规格（冻结）")
    ap.add_argument("--from-ai", action="store_true",
                    help="同时把常量与 大致布局.ai 逐项核对")
    ap.add_argument("--ai", help="换一份草图")
    a = ap.parse_args()

    print(layout_report())
    print()
    print("invariants:")
    good = check()
    if a.from_ai or a.ai:
        print()
        print(f"vs {a.ai or AI_PATH.name}:")
        good = check_against_ai(Path(a.ai) if a.ai else None) and good
    print()
    print("ALL PASS" if good else "PROBLEMS")
    sys.exit(0 if good else 1)
