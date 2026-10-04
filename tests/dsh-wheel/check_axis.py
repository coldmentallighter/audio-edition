"""Decisive axis check: do the labels, the grid strokes and the gradient agree?

This does not render anything. It answers the only two questions that matter for
reimplementing the chart:

  1. what LUFS values are labelled, and where do those labels' grid lines sit?
  2. which LUFS value does the body/head colour split land on?

Both are read straight out of the SVG. The reference file is internally inconsistent
about which coordinate space a given element lives in (see the note at the bottom),
so the check uses RATIOS between elements in the same space rather than absolute
pixels -- ratios survive any uniform scaling and are therefore space-independent.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

import reference_geometry as RG                                     # noqa: E402
from decode_text import runs                                        # noqa: E402


def main():
    print("=== 1. axis labels, in document order ===")
    labels = []
    for text, x, y, _ in runs():
        if x < 420 and text and re.fullmatch(r"-?\d+", text):
            labels.append((text, x, y))
    for t, x, y in labels:
        print(f"  {t:>5}   x={x:>7.1f}  y={y:>9.1f}")

    grid_y = RG.grid_lines()
    print(f"\n{len(grid_y)} grid strokes found")
    assert len(grid_y) == len(labels), (len(grid_y), len(labels))

    print("\n=== 2. label value vs grid-line spacing ===")
    # If the axis is linear, (label gap) must be proportional to (value gap).
    rows = []
    for (lab, lx, ly), gy in zip(labels, grid_y):
        rows.append((float(lab), gy))
    print(f"{'value':>6} {'grid y':>10} {'d value':>9} {'d y':>9} {'dy/dv':>10}")
    ratios = []
    for i, ((v0, y0), (v1, y1)) in enumerate(zip(rows, rows[1:])):
        dv = v1 - v0
        dy = y1 - y0
        r = dy / dv
        ratios.append(r)
        print(f"{v1:>6.0f} {y1:>10.2f} {dv:>9.0f} {dy:>9.2f} {r:>10.4f}")
    print()
    lo, hi = min(ratios), max(ratios)
    print(f"  ratio spread: {lo:.4f} .. {hi:.4f}   "
          f"(relative spread {(hi - lo) / lo * 100:.3f} %)")
    print(f"  => axis is {'LINEAR' if (hi - lo) / lo < 0.001 else 'PIECEWISE / NON-LINEAR'}")

    print("\n=== 3. where the body/head colour split lands ===")
    g = RG.gradients()["linear-pattern-0"]
    y0, y1 = g["p1"][1], g["p2"][1]
    split = y0 + 0.616667 * (y1 - y0)
    print(f"  gradient runs y {y0:.1f} -> {y1:.1f}")
    print(f"  hard stop at 0.616667 -> y {split:.2f}")
    # express the split as a FRACTION of the axis, then convert via the table
    v_top, gy_top = rows[0]
    v_bot, gy_bot = rows[-1]
    frac = (split - gy_top) / (gy_bot - gy_top)
    lufs = v_top + frac * (v_bot - v_top)
    print(f"  as a fraction of the labelled axis: {frac:.4f}")
    print(f"  => split is at {lufs:.2f} LUFS")
    print()
    near = min(rows, key=lambda r: abs(r[1] - split))
    print(f"  nearest labelled line: {near[0]:.0f} LUFS at y {near[1]:.2f} "
          f"(split is {abs(near[1] - split):.2f} units away)")
    print()
    if abs(lufs - (-23.0)) < 0.35:
        print("  CONCLUSION: the split coincides with the -23 LUFS accent line.")
        print("              body (pale blue) below -23, head (coral) above it.")
    else:
        print(f"  CONCLUSION: the split does NOT coincide with a labelled line "
              f"({lufs:.2f} LUFS).")

    print("\n=== note on coordinate spaces ===")
    print("  The file is internally inconsistent, so do not 'fix' one element against")
    print("  another without checking:")
    e = RG.envelope()
    print(f"    envelope path      x {min(p[0] for p in e):.0f}..{max(p[0] for p in e):.0f}"
          f"   y {min(p[1] for p in e):.1f}..{max(p[1] for p in e):.1f}   (raw)")
    print(f"    grid strokes       y {grid_y[0]:.1f}..{grid_y[-1]:.1f}   (raw)")
    print(f"    clipPath rects     {RG.clip_rects()['clip-1']}   (device)")
    print("  The envelope, the grid strokes and the labels agree with EACH OTHER;")
    print("  the clipPath rects are in device units, 8x larger. Ratios between the")
    print("  first three are therefore trustworthy, absolute clip pixels are not.")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
