"""Decode every text run in target/CQ.svg into real strings.

The SVG carries no <font>/unicode metadata, so the id -> character tables below were
established by reading the shapes via glyph_sheet.py. Three fonts are embedded:

  glyph-0-*  the "CQ" badge in the top-left corner (cap height ~236 units)
  glyph-1-*  axis ticks / time ticks / metric captions (cap height ~92 units)
  glyph-2-*  numeric readouts and the "LU" unit (cap height ~137 units)

Because the tables are the one thing here that cannot be re-derived mechanically,
glyph_sheet.py is kept next to this file so they can be re-checked.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SVG = ROOT / "target" / "CQ.svg"

# --- verified from glyph_sheet.py ------------------------------------------
GLYPH_0 = {"glyph-0-0": "C", "glyph-0-1": "Q"}

GLYPH_1 = {
    "glyph-1-0": "0", "glyph-1-1": "-", "glyph-1-2": "3", "glyph-1-3": "6",
    "glyph-1-4": "9", "glyph-1-5": "1", "glyph-1-6": "8", "glyph-1-7": "2",
    "glyph-1-8": "7", "glyph-1-9": "4", "glyph-1-10": "5", "glyph-1-11": "s",
    "glyph-1-12": "m", "glyph-1-13": ":", "glyph-1-14": "I", "glyph-1-15": "N",
    "glyph-1-16": "T", "glyph-1-17": "E", "glyph-1-18": "G", "glyph-1-19": "R",
    "glyph-1-20": "A", "glyph-1-21": "D", "glyph-1-22": "L", "glyph-1-23": "O",
    "glyph-1-24": "U", "glyph-1-25": "S", "glyph-1-26": "%", "glyph-1-27": "V",
    "glyph-1-28": "Y", "glyph-1-29": "M", "glyph-1-30": "C", "glyph-1-31": "(",
    "glyph-1-32": "P", "glyph-1-33": ")", "glyph-1-34": "X", "glyph-1-35": "H",
    "glyph-1-36": "K", "glyph-1-37": "a", "glyph-1-38": "d", "glyph-1-39": "e",
    "glyph-1-40": "b", "glyph-1-41": "v", "glyph-1-42": "o", "glyph-1-43": "u",
    "glyph-1-44": "l", "glyph-1-45": "n", "glyph-1-46": "t", "glyph-1-47": "r",
    "glyph-1-48": "v", "glyph-1-49": ".",
}

GLYPH_2 = {
    "glyph-2-0": "L", "glyph-2-1": "U", "glyph-2-2": "-", "glyph-2-3": "1",
    "glyph-2-4": "6", "glyph-2-5": ".", "glyph-2-6": "0", "glyph-2-7": "5",
    "glyph-2-8": "7", "glyph-2-9": "9", "glyph-2-10": "2",
}

MAP = {**GLYPH_0, **GLYPH_1, **GLYPH_2}


def runs(gap: float = 400.0):
    """`[(text, x, y, fill)]`, one entry per visual text run, in document order."""
    text = SVG.read_text(encoding="utf-8").replace("xlink:href", "href")
    body = text.split("<defs>", 1)[1]
    out = []
    for gm in re.finditer(r'<g fill="([^"]+)"[^>]*>(.*?)</g>', body, re.S):
        fill, inner = gm.group(1), gm.group(2)
        uses = re.findall(r'<use href="#([^"]+)" x="([\d.]+)" y="([\d.]+)"', inner)
        if not uses:
            continue
        chunk: list[tuple[str, float, float]] = []
        prev = None
        for gid, x, y in uses:
            x, y = float(x), float(y)
            if prev is not None and x - prev > gap:
                out.append((chunk, fill))
                chunk = []
            chunk.append((gid, x, y))
            prev = x
        if chunk:
            out.append((chunk, fill))
    return [("".join(MAP.get(g, "?") for g, _, _ in c), c[0][1], c[0][2], f)
            for c, f in out]


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for text, x, y, fill in runs():
        print(f"x={x:>9.1f}  y={y:>9.1f}  {text!r}")
