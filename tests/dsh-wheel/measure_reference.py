"""Measure the rendered reference: where exactly do the grid lines and envelope sit?

This is the tie-breaker. The SVG's elements disagree about coordinate space, so the
only reliable answer comes from actually drawing the file and reading pixels. The
renderer used here (render_reference.py) reproduces the reference's appearance, which
the retired `visual_compare.py` used to cross-check.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT = ROOT / ".cache" / "_look"
# a missing output dir must not crash a verification run
OUT.mkdir(parents=True, exist_ok=True)
SCALE = 0.25          # 32000*0.25 = 8000 px wide, plenty for measurement


def ensure() -> Path:
    p = OUT / "cq_measure.png"
    expect = (int(32000 * SCALE), int(8640 * SCALE))
    if p.exists():
        try:
            with Image.open(p) as im:
                if im.size == expect:
                    return p
        except Exception:
            pass
    subprocess.run([sys.executable, str(HERE / "render_reference.py"), str(SCALE)],
                   check=True)
    (OUT / "cq_faithful.png").replace(p)
    return p


def main():
    p = ensure()
    im = Image.open(p).convert("RGB")
    W, H = im.size
    px = im.load()
    print(f"render {W}x{H} (reference units * {SCALE})")

    def unit(v): return v / SCALE

    # ---- 1. the plot's vertical extent: rows containing body or head colour ----
    BODY = (174, 193, 223)
    HEAD = (216, 153, 145)
    rows = []
    for y in range(H):
        c = sum(1 for x in range(0, W, 7) if px[x, y] in (BODY, HEAD))
        rows.append(c)
    filled = [y for y, c in enumerate(rows) if c > 3]
    if filled:
        print(f"\nenvelope occupies rows {filled[0]}..{filled[-1]}")
        print(f"  -> reference units {unit(filled[0]):.1f} .. {unit(filled[-1]):.1f}"
              f"   (height {unit(filled[-1] - filled[0]):.1f})")

    # ---- 2. the head/body boundary row (where the colour flips) ----------------
    flips = []
    for y in range(1, H):
        a = sum(1 for x in range(0, W, 7) if px[x, y - 1] == HEAD)
        b = sum(1 for x in range(0, W, 7) if px[x, y] == BODY)
        if a > 5 and b > 5:
            flips.append(y)
    if flips:
        print(f"\nhead->body colour boundary at rows {flips[:6]}"
              f"  -> reference units {[round(unit(f), 1) for f in flips[:6]]}")
        print(f"  (a horizontal boundary means the fill is a flat band there, which is")
        print(f"   what the gradient's hard stop produces)")

    # ---- 3. grid lines: faint horizontal rules --------------------------------
    print("\ngrid lines (rows where a long faint rule spans the plot):")
    found = []
    for y in range(H):
        n = sum(1 for x in range(int(0.03 * W), int(0.9 * W), 5)
                if px[x, y] not in ((255, 255, 255), BODY, HEAD))
        if n > 0.8 * len(range(int(0.03 * W), int(0.9 * W), 5)):
            found.append(y)
    # collapse runs
    runs = []
    for y in found:
        if runs and y - runs[-1][-1] <= 1:
            runs[-1].append(y)
        else:
            runs.append([y])
    for r in runs:
        yc = sum(r) / len(r)
        print(f"  rows {r[0]}..{r[-1]}  centre {yc:.1f}"
              f"  -> reference units {unit(yc):.1f}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
