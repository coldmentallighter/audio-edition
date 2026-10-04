"""Render each glyph of a named font enlarged, with the baseline marked.

The earlier contact sheet clipped descenders and used the wrong y direction, which
is how glyph-2-2 ("-") got recorded as "L". This version normalises each outline
into its own cell and draws the baseline, so the character can be read off directly.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from svg_raster import parse_path          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
SVG = ROOT / "target" / "CQ.svg"
OUT = ROOT / ".cache" / "_look"
# a missing output dir must not crash a verification run
OUT.mkdir(parents=True, exist_ok=True)

CELL = 150
COLS = 10


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else "glyph-2-"
    text = SVG.read_text(encoding="utf-8")
    ids = re.findall(r'<g id="(' + re.escape(prefix) + r'\d+)">', text)
    ids.sort(key=lambda s: int(s.rsplit("-", 1)[1]))

    rows = (len(ids) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * CELL, rows * CELL), "white")
    d = ImageDraw.Draw(sheet)

    for i, gid in enumerate(ids):
        m = re.search(r'<g id="' + re.escape(gid) + r'">\s*<path d="([^"]*)"', text)
        subs = parse_path(m.group(1))
        pts = [p for s in subs for p in s]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        print(f"{gid:12s} x {min(xs):>7.1f}..{max(xs):>7.1f}"
              f"   y {min(ys):>7.1f}..{max(ys):>7.1f}"
              f"   w={max(xs) - min(xs):>6.1f} h={max(ys) - min(ys):>6.1f}")

        cx, cy = (i % COLS) * CELL, (i // COLS) * CELL
        # fit the outline into the cell, keeping the glyph's own baseline (y=0)
        scale = 0.62 * CELL / max(max(ys) - min(ys), 1)
        ox = cx + (CELL - (max(xs) - min(xs)) * scale) / 2 - min(xs) * scale
        oy = cy + CELL * 0.80
        d.line([(cx + 4, oy), (cx + CELL - 4, oy)], fill=(255, 120, 120))
        for s in subs:
            d.polygon([(ox + x * scale, oy + y * scale) for x, y in s], fill=(20, 20, 25))
        d.rectangle([cx, cy, cx + CELL - 1, cy + CELL - 1], outline=(210, 210, 210))
        d.text((cx + 5, cy + CELL - 16), gid, fill=(190, 0, 0))

    p = OUT / f"glyphs_{prefix.rstrip('-')}.png"
    sheet.save(p)
    print(f"\n{p}  {sheet.size}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
