"""Render target/CQ.svg faithfully to a PNG, for measurement and eyeballing.

The reference uses TWO coordinate spaces (see reference_geometry.py):
  * device space  (x 448..31584)  -- canvas units, = raw * 8
  * raw space     (x 56..3948)    -- everything else: the data path, the grid
                                     strokes, the clipPath rects, the gradient
                                     stops, the glyph outlines and <use> x/y
This renderer draws entirely in raw space and only uses SPACE for the canvas size,
which sidesteps every space-mixing bug.

Honest limits -- read these before trusting a render:
  * `Rend` is a purpose-built parser, not a browser. It handles M/L/H/V/C/Z, solid
    fills, vertical linear gradients, 1-unit strokes and glyph outlines. That is
    everything this particular file uses, and nothing more.
  * Clipping composites a masked layer, so it is approximate at the clip boundary.
  * The reference draws the data curve twice (a gradient fill plus a stroked
    outline). `skip_stroke` drops the second pass, which only re-inks the same edge.
  * **`render_text` defaults to False and should stay there.** The glyph outlines and
    the `<use>` x/y do not agree on a scale that can be derived from the document:
    the axis font's "0" is 92 units tall while its baseline is at device y=1173, so
    outlines behave as 1/8-scale relative to placement -- but scaling them that way
    makes the text land in the wrong place anyway, which means a third factor is
    involved that only a real renderer would resolve. Text *content and position* are
    known exactly from `decode_text.py`; text *rasterisation* is not modelled. Leave
    text out of this render, or verify against a browser before using it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from svg_raster import Rend, parse_path, parse_color, attrs, apply   # noqa: E402
from reference_geometry import clip_rects                             # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
SVG = ROOT / "target" / "CQ.svg"
OUT = ROOT / ".cache" / "_look"
# a missing output dir must not crash a verification run
OUT.mkdir(parents=True, exist_ok=True)

SPACE = 8.0
DEV = (1, 0, 0, 1, 0, 0)
skip_stroke = True          # the stroke path re-inks the fill's own edge
render_text = False         # see the note below


def main():
    s = float(sys.argv[1]) if len(sys.argv) > 1 else 0.06
    text = SVG.read_text(encoding="utf-8").replace("xlink:href", "href")
    body = text.split("</defs>", 1)[1].rsplit("</svg>", 1)[0]

    r = Rend(s, int(32000 * s), int(8640 * s))
    r.load(text)
    clips = dict(clip_rects())          # already raw

    TAG = re.compile(r"<(/?)(g|rect|path|use)\b([^>]*?)(/?)>", re.S)
    stack: list[dict] = []

    def inherited() -> dict:
        st = {"fill": None, "stroke": None, "sw": 1.0, "op": 1.0, "clip": None}
        for f in stack:
            for k, v in f.items():
                if v is not None:
                    st[k] = v
        return st

    for m in TAG.finditer(body):
        close, name, astr = m.group(1), m.group(2), m.group(3)
        a = attrs("<x " + astr + ">")

        if name == "g":
            if close:
                if stack:
                    stack.pop()
            else:
                cur: dict = {}
                for k in ("fill", "stroke"):
                    if k in a:
                        cur[k] = a[k]
                if "stroke-width" in a:
                    cur["sw"] = float(a["stroke-width"])
                if "fill-opacity" in a:
                    cur["op"] = float(a["fill-opacity"])
                cm = re.search(r'clip-path="url\(#(clip-\d+)\)"', astr)
                if cm:
                    cur["clip"] = clips.get(cm.group(1))
                stack.append(cur)
            continue

        st = inherited()
        clip = st.get("clip")
        cm = re.search(r'clip-path="url\(#(clip-\d+)\)"', astr)
        if cm:
            clip = clips.get(cm.group(1))

        if name == "rect":
            x, y = float(a.get("x", 0)), float(a.get("y", 0))
            w, h = float(a.get("width", 0)), float(a.get("height", 0))
            c = parse_color(a.get("fill", ""))
            if c:
                r.fill_poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], c, clip)

        elif name == "path":
            subs = parse_path(a.get("d", ""))
            fill = a.get("fill") or st["fill"] or "none"
            stroke = a.get("stroke") or st["stroke"] or "none"
            sw = float(a.get("stroke-width") or st["sw"] or 1)
            op = float(a.get("fill-opacity") or st["op"] or 1)
            if fill != "none":
                if fill.startswith("url("):
                    gid = re.sub(r"url\(#([^)]+)\)", r"\1", fill)
                    for sub in subs:
                        r.fill_grad([apply(DEV, x, y) for x, y in sub], gid, clip)
                else:
                    c = parse_color(fill)
                    if c:
                        if op < 1.0 and a.get("fill-opacity"):
                            c = tuple(int(round(255 + (c[j] - 255) * op)) for j in range(3))
                        for sub in subs:
                            r.fill_poly([apply(DEV, x, y) for x, y in sub], c, clip)
            if stroke != "none" and not skip_stroke:
                c = parse_color(stroke)
                if c:
                    for sub in subs:
                        r.stroke_poly([apply(DEV, x, y) for x, y in sub], c, sw)

        elif name == "use":
            if not render_text:
                continue
            gid = a.get("href", "").lstrip("#")
            d = r.glyphs.get(gid)
            if not d:
                continue
            c = parse_color(st["fill"] or "")
            op = float(st["op"] or 1)
            if c and op < 1.0:
                c = tuple(int(round(255 + (c[j] - 255) * op)) for j in range(3))
            if c:
                r.glyph(d, float(a.get("x", 0)), float(a.get("y", 0)), c)

    p = OUT / "cq_faithful.png"
    r.img.save(p)
    print(f"{p}  {r.img.size}")


if __name__ == "__main__":
    main()
