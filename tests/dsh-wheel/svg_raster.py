"""Minimal SVG rasterizer for target/CQ.svg (Youlean LoudnessAnalysis export).

Handles: <clipPath><path>, <linearGradient> (userSpaceOnUse + matrix), <rect>,
<path> (fill/stroke, M/L/C/H/V/Z), <g id="glyph-*">, <use x y>.
No CSS classes and no <text> in this file -- pure geometry, so this is tractable.

Usage:  python tools/_svg_raster.py <scale> [crop:x0,y0,x1,y1:outname] ...
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent.parent
SVG = ROOT / "target" / "CQ.svg"
OUT = ROOT / ".cache" / "_look"

NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
TOK = re.compile(NUM + r"|[A-Za-z]")
IDENT = (1, 0, 0, 1, 0, 0)


def attrs(tag: str) -> dict[str, str]:
    return dict(re.findall(r'([\w:-]+)\s*=\s*"([^"]*)"', tag))


NAMED = {
    "white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0),
    "green": (0, 128, 0), "blue": (0, 0, 255), "gray": (128, 128, 128),
    "grey": (128, 128, 128), "none": None,
}


def parse_color(s: str):
    s = (s or "").strip()
    if not s:
        return None
    low = s.lower()
    if low in NAMED:
        return NAMED[low]
    m = re.match(r"rgb\(\s*([\d.]+)%\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%\s*\)", s)
    if m:
        return tuple(int(round(float(g) / 100 * 255)) for g in m.groups())
    m = re.match(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", s)
    if m:
        return tuple(int(g) for g in m.groups())
    if s.startswith("#") and len(s) == 7:
        return (int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16))
    return None


def apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def parse_path(d: str):
    """-> list of subpaths; each subpath is a closed point list (cubics flattened)."""
    toks = TOK.findall(d)
    i = 0
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    subs: list[list[tuple[float, float]]] = []
    pts: list[tuple[float, float]] = []

    def n() -> float:
        nonlocal i
        v = float(toks[i]); i += 1
        return v

    def flush():
        if len(pts) > 2:
            subs.append(list(pts))
        pts.clear()

    while i < len(toks):
        t = toks[i]
        if not re.match(r"[A-Za-z]", t):
            i += 1
            continue
        i += 1
        if t == "M":
            flush(); cur = (n(), n()); start = cur; pts.append(cur)
        elif t == "L":
            cur = (n(), n()); pts.append(cur)
        elif t == "H":
            cur = (n(), cur[1]); pts.append(cur)
        elif t == "V":
            cur = (cur[0], n()); pts.append(cur)
        elif t == "C":
            c1 = (n(), n()); c2 = (n(), n()); p = (n(), n())
            x0, y0 = cur
            for s in range(1, 10):
                u = s / 9; v = 1 - u
                pts.append((v ** 3 * x0 + 3 * v * v * u * c1[0] + 3 * v * u * u * c2[0] + u ** 3 * p[0],
                            v ** 3 * y0 + 3 * v * v * u * c1[1] + 3 * v * u * u * c2[1] + u ** 3 * p[1]))
            cur = p
        elif t == "Z":
            if pts:
                pts.append(start); flush()
            cur = start
        else:
            break
    flush()
    return subs


class Rend:
    def __init__(self, scale: float, w: int, h: int):
        self.s = scale
        self.img = Image.new("RGB", (w, h), "white")
        self.clips: dict[str, list] = {}
        self.grads: dict[str, tuple] = {}
        self.glyphs: dict[str, str] = {}

    def load(self, text: str):
        for cm in re.finditer(r'<clipPath id="([^"]+)">(.*?)</clipPath>', text, re.S):
            dm = re.search(r'\bd="([^"]*)"', cm.group(2))
            self.clips[cm.group(1)] = parse_path(dm.group(1)) if dm else []
        for gm in re.finditer(r'<linearGradient id="([^"]+)"[^>]*>(.*?)</linearGradient>',
                              text, re.S):
            a = attrs(gm.group(0).split(">")[0] + ">")
            m = IDENT
            tm = re.search(r"matrix\(([^)]*)\)", a.get("gradientTransform", ""))
            if tm:
                v = [float(z) for z in re.split(r"[,\s]+", tm.group(1).strip())]
                m = tuple(v)
            p1 = apply(m, float(a.get("x1", 0)), float(a.get("y1", 0)))
            p2 = apply(m, float(a.get("x2", 0)), float(a.get("y2", 0)))
            stops = []
            for sm in re.finditer(r'<stop offset="([^"]+)" stop-color="([^"]+)"', gm.group(2)):
                stops.append((float(sm.group(1)), parse_color(sm.group(2)) or (0, 0, 0)))
            self.grads[gm.group(1)] = (p1, p2, stops)
        for dm in re.finditer(r'<g id="(glyph-[^"]+)">\s*(.*?)</g>', text, re.S):
            pm = re.search(r'\bd="([^"]*)"', dm.group(2))
            if pm:
                self.glyphs[dm.group(1)] = pm.group(1)

    def grad_at(self, gid: str, y: float):
        g = self.grads.get(gid)
        if not g:
            return (128, 128, 128)
        (x1, y1), (x2, y2), stops = g
        span = y2 - y1
        f = 0.0 if span == 0 else (y - y1) / span
        f = max(0.0, min(1.0, f))
        prev = stops[0]
        for off, col in stops:
            if f <= off:
                o0, c0 = prev
                if off <= o0:
                    return col
                k = (f - o0) / (off - o0)
                return tuple(int(round(c0[j] + (col[j] - c0[j]) * k)) for j in range(3))
            prev = (off, col)
        return stops[-1][1]

    def fill_poly(self, pts, color, clip=None):
        if len(pts) < 3:
            return
        xy = [(x * self.s, y * self.s) for x, y in pts]
        if clip is None:
            ImageDraw.Draw(self.img).polygon(xy, fill=color)
            return
        self._clipped(lambda d: d.polygon(xy, fill=color), clip)

    def fill_grad(self, pts, gid, clip=None):
        """Vertical gradient via per-row spans (the image is a height-field)."""
        if len(pts) < 3:
            return
        im = self.img
        d = ImageDraw.Draw(im)
        W, H = im.size
        ys = [p[1] * self.s for p in pts]
        y0 = max(0, int(min(ys)) - 1)
        y1 = min(H - 1, int(max(ys)) + 1)
        if clip is not None:
            y0 = max(y0, int(clip[1] * self.s))
            y1 = min(y1, int(clip[3] * self.s))
        for yy in range(y0, y1 + 1):
            fy = (yy + 0.5) / self.s
            xs = []
            n = len(pts)
            for i in range(n):
                ax, ay = pts[i]
                bx, by = pts[(i + 1) % n]
                if (ay <= fy < by) or (by <= fy < ay):
                    t = (fy - ay) / (by - ay)
                    xs.append((ax + (bx - ax) * t) * self.s)
            if len(xs) < 2:
                continue
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                col = self.grad_at(gid, fy)
                x0 = xs[k]
                x1 = xs[k + 1]
                if clip is not None:
                    x0 = max(x0, clip[0] * self.s)
                    x1 = min(x1, clip[2] * self.s)
                if x1 > x0:
                    d.line([(x0, yy), (x1, yy)], fill=col)

    def _clipped(self, draw_fn, clip):
        """Run draw_fn on a transparent layer, keep only the clip rect, composite."""
        W, H = self.img.size
        layer = Image.new("RGB", (W, H))
        # copy the current image so antialiasing/edges blend like a normal draw
        layer.paste(self.img)
        draw_fn(ImageDraw.Draw(layer))
        mask = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mask)
        md.rectangle([clip[0] * self.s, clip[1] * self.s,
                      clip[2] * self.s, clip[3] * self.s], fill=255)
        self.img.paste(layer, (0, 0), mask)

    def stroke_poly(self, pts, color, width):
        if len(pts) < 2:
            return
        ImageDraw.Draw(self.img).line(
            [(x * self.s, y * self.s) for x, y in pts], fill=color,
            width=max(1, int(round(width * self.s))), joint="curve")

    def glyph(self, d: str, x: float, y: float, color, scale: float = 1.0):
        """Draw a glyph outline. `scale` scales the OUTLINE only (not the anchor),
        for fonts whose outlines are authored at a different size than their use."""
        m = (scale, 0, 0, scale, x, y)
        for sub in parse_path(d):
            self.fill_poly([apply(m, px, py) for px, py in sub], color)


def main():
    scale = float(sys.argv[1]) if len(sys.argv) > 1 else 0.05
    text = SVG.read_text(encoding="utf-8").replace("xlink:href", "href")
    body = text.split("</defs>", 1)[1].rsplit("</svg>", 1)[0]

    W, H = int(32000 * scale), int(8640 * scale)
    r = Rend(scale, W, H)
    r.load(text)

    tag_re = re.compile(r"<(/?)(g|rect|path|use)\b([^>]*?)(/?)>", re.S)
    stack: list[dict] = []

    def inherited():
        st = {"fill": None, "stroke": None, "sw": 1.0, "opacity": 1.0}
        for f in stack:
            for k, v in f.items():
                if v is not None:
                    st[k] = v
        return st

    for m in tag_re.finditer(body):
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
                if k_op := a.get("fill-opacity"):
                    cur["opacity"] = float(k_op)
                stack.append(cur)
            continue

        st = inherited()
        if name == "rect":
            x, y = float(a.get("x", 0)), float(a.get("y", 0))
            w, h = float(a.get("width", 0)), float(a.get("height", 0))
            c = parse_color(a.get("fill", ""))
            if c:
                r.fill_poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], c)
        elif name == "path":
            subs = parse_path(a.get("d", ""))
            fill = a.get("fill") or st["fill"] or "none"
            stroke = a.get("stroke") or st["stroke"] or "none"
            sw = float(a.get("stroke-width") or st["sw"] or 1)
            op = float(a.get("fill-opacity") or st["opacity"] or 1)
            if fill != "none":
                if fill.startswith("url("):
                    gid = re.sub(r"url\(#([^)]+)\)", r"\1", fill)
                    for sub in subs:
                        r.fill_grad(sub, gid)
                else:
                    c = parse_color(fill)
                    if c:
                        if op < 1.0:
                            c = tuple(int(round(255 + (c[j] - 255) * op)) for j in range(3))
                        for sub in subs:
                            r.fill_poly(sub, c)
            if stroke != "none":
                c = parse_color(stroke)
                if c:
                    for sub in subs:
                        r.stroke_poly(sub, c, sw)
        elif name == "use":
            gid = a.get("href", "").lstrip("#")
            d = r.glyphs.get(gid)
            if not d:
                continue
            c = parse_color(st["fill"] or "")
            op = float(st["opacity"] or 1)
            if c and op < 1.0:
                c = tuple(int(round(255 + (c[j] - 255) * op)) for j in range(3))
            if c:
                r.glyph(d, float(a.get("x", 0)), float(a.get("y", 0)), c)

    OUT.mkdir(parents=True, exist_ok=True)
    full = OUT / "cq_render.png"
    r.img.save(full)
    print(f"{full}  {r.img.size}")

    for arg in sys.argv[2:]:
        _, coords, name = arg.split(":")
        x0, y0, x1, y1 = (float(v) for v in coords.split(","))
        crop = r.img.crop((int(x0 * scale), int(y0 * scale),
                           int(x1 * scale), int(y1 * scale)))
        k = min(4, max(1, int(1400 / max(1, crop.size[0]))))
        if k > 1:
            crop = crop.resize((crop.size[0] * k, crop.size[1] * k), Image.LANCZOS)
        p = OUT / f"{name}.png"
        crop.save(p)
        print(f"{p}  {crop.size}")


if __name__ == "__main__":
    main()
