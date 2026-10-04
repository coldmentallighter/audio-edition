"""Exact geometry of the reference chart in `target/CQ.svg`.

READ THIS FIRST -- the file uses TWO coordinate spaces
------------------------------------------------------
The stroke path and the fill path carry **the same 1945 data points**, but they are
authored in different spaces::

    stroke (raw)  x   52 .. 3948      y  270.8 .. 1142.5
    stroke * 8    x  416 .. 31584     y 2166.8 .. 9139.7   <-- byte-identical
    fill          x  416 .. 31584     y 2166.8 .. 9139.7       to the fill path

Only the *fill* path is in device coordinates. Everything else -- clipPath rects,
the ten grid strokes, the gradient stops and the glyph `<use>` elements -- lives in
a space **8x smaller**, which is the space the `gradientTransform="matrix(8,0,0,8,
0,0)"` maps up to the canvas. So::

    device = raw * 8

Getting this wrong is how you end up "measuring" a non-linear axis, or a chart that
starts at -13 LUFS. See `axis_table()` at the bottom for the corrected result.
"""
from __future__ import annotations

import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from svg_raster import parse_path                      # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
SVG = ROOT / "target" / "CQ.svg"

#: the two spaces are related by this factor:  device = raw * SPACE
SPACE = 8.0

#: canvas size, in device units
CANVAS = (32000.0, 8640.0)

#: Everything below reports in the **raw** space (the space the fill path is NOT
#: in, but the space the fill path *would* be in after dividing by 8). Raw is the
#: convenient common denominator: grid strokes, clip rects, gradient stops and the
#: axis labels are all authored raw, so raw is what you measure against.
#:   device = raw * 8        raw = device / 8


def body() -> str:
    return SVG.read_text(encoding="utf-8").split("<defs>", 1)[1]


def to_device(v: float) -> float:
    return v * SPACE


# ------------------------------------------------------------------ clip rects

def clip_rects() -> dict[str, tuple[float, float, float, float]]:
    """`{clip id: (x0, y0, x1, y1)}` in **raw** units."""
    out: dict[str, tuple[float, float, float, float]] = {}
    for m in re.finditer(r'<clipPath id="(clip-\d+)">\s*<path[^>]*d="([^"]*)"', body()):
        pts = [p for s in parse_path(m.group(2)) for p in s]
        if not pts:
            continue
        out[m.group(1)] = (min(p[0] for p in pts), min(p[1] for p in pts),
                           max(p[0] for p in pts), max(p[1] for p in pts))
    return out


# ------------------------------------------------------------------ grid + axis

def grid_lines() -> list[float]:
    """y of the ten labelled grid lines, in **raw** units, top to bottom."""
    ys = []
    for m in re.finditer(r'<g clip-path="url\(#(clip-\d+)\)">(.*?)</g>', body(), re.S):
        lm = re.search(r'<path fill="none" stroke-width="1"[^>]*d="M ([\d.]+) ([\d.]+)',
                       m.group(2))
        if lm:
            ys.append(float(lm.group(2)))
    return sorted(ys)


#: value of each grid line, in document order (top to bottom). Decoded from the
#: glyph outlines by `decode_text.py`; the ten labels are these strings verbatim:
#:   0, -3, -6, -9, -18, -23, -27, -36, -45, -54
GRID_LUFS = (0.0, -3.0, -6.0, -9.0, -18.0, -23.0, -27.0, -36.0, -45.0, -54.0)


def axis_table() -> list[tuple[float, float]]:
    """`[(lufs, raw y)]` -- the axis really is these ten breakpoints.

    Consecutive gaps are 36.27 / 36.27 / 36.27 / 108.81 / 60.45 / 48.36 / 108.81 /
    108.81 / 108.81 raw units. Those are **proportional to the LUFS deltas**
    (3/3/3/9/5/4/9/9/9), so the axis is in fact **linear**, at 12.09 raw units per
    LUFS, and the tick *values* are simply not evenly spaced.

    The obvious reading of the label positions -- "290 then 870, so the axis is
    compressed" -- comes from mixing device units (labels) with raw units (grid
    lines). Once both are in the same space the compression disappears.
    """
    lines = grid_lines()
    assert len(lines) == len(GRID_LUFS), (len(lines), len(GRID_LUFS))
    return list(zip(GRID_LUFS, lines))


def axis_segments() -> list[tuple[float, float, float, float, float]]:
    """`[(lufs_hi, lufs_lo, y_hi, y_lo, units_per_LU)]` for each axis segment."""
    t = axis_table()
    out = []
    for (v0, y0), (v1, y1) in zip(t, t[1:]):
        out.append((v0, v1, y0, y1, (y1 - y0) / (v0 - v1)))
    return out


# ------------------------------------------------------------------ data paths

def envelope() -> list[tuple[float, float]]:
    """The loudness envelope in **raw** units, without the closing baseline run.

    The path is authored in device units, so it is divided by SPACE here to land in
    the same space as the axis. 1943 points spanning 291 s (one point per 2 raw
    units of x == 16 device units).
    """
    m = re.search(r'fill="url\(#linear-pattern-0\)" d="([^"]*)"',
                  SVG.read_text(encoding="utf-8"))
    pts = parse_path(m.group(1))[0]
    return [(x / SPACE, y / SPACE) for x, y in pts[:-2]]   # drop closing baseline


def gradients() -> dict[str, dict]:
    """`{id: {p1, p2, stops}}` in **raw** units (gradientTransform divided out)."""
    out = {}
    text = SVG.read_text(encoding="utf-8")
    for m in re.finditer(r'<linearGradient id="([^"]+)"([^>]*)>(.*?)</linearGradient>',
                         text, re.S):
        a = dict(re.findall(r'([\w:-]+)="([^"]*)"', m.group(2)))
        stops = [(float(s.group(1)), s.group(2)) for s in
                 re.finditer(r'<stop offset="([^"]+)" stop-color="([^"]+)"', m.group(3))]
        out[m.group(1)] = {
            "p1": (float(a.get("x1", 0)), float(a.get("y1", 0))),
            "p2": (float(a.get("x2", 0)), float(a.get("y2", 0))),
            "stops": stops,
        }
    return out


def lufs_of(raw_y: float) -> float:
    """Invert the axis (raw y -> LUFS)."""
    t = axis_table()
    if raw_y <= t[0][1]:
        return t[0][0]
    if raw_y >= t[-1][1]:
        return t[-1][0]
    for (v0, y0), (v1, y1) in zip(t, t[1:]):
        if y0 <= raw_y <= y1:
            return v0 + (raw_y - y0) / (y1 - y0) * (v1 - v0)
    return t[-1][0]


def describe() -> str:
    out = ["== canvas ==", f"  {CANVAS[0]:.0f} x {CANVAS[1]:.0f} device units"
           f"  (= {CANVAS[0] / SPACE:.0f} x {CANVAS[1] / SPACE:.0f} raw)",
           f"  overall ratio {CANVAS[0] / CANVAS[1]:.2f} : 1", ""]
    out.append("== clip rects (raw) ==")
    for cid, (x0, y0, x1, y1) in sorted(clip_rects().items(),
                                        key=lambda kv: int(kv[0].split("-")[1])):
        out.append(f"  {cid:9s} x {x0:>8.1f}..{x1:>8.1f}  y {y0:>7.2f}..{y1:>7.2f}")
    out.append("")
    out.append("== axis: value -> raw y, units per LU ==")
    for v0, v1, y0, y1, k in axis_segments():
        out.append(f"  {v0:>5.0f} -> {v1:>5.0f}   y {y0:>7.2f} -> {y1:>7.2f}   "
                   f"{k:>6.3f} units/LU   (gap {y1 - y0:>6.2f})")
    out.append("")
    out.append("== gradients (raw) ==")
    for gid, g in gradients().items():
        out.append(f"  {gid}: y {g['p1'][1]:.1f} -> {g['p2'][1]:.1f} (raw)")
        for off, col in g["stops"]:
            y = g["p1"][1] + off * (g["p2"][1] - g["p1"][1])
            out.append(f"      offset {off:<10} {col:<42} raw y {y:>7.2f}"
                       f"  = {lufs_of(y):>6.2f} LUFS")
    out.append("")
    e = envelope()
    ys = [p[1] for p in e]
    out.append("== envelope (raw) ==")
    out.append(f"  {len(e)} points   x {e[0][0]:.1f}..{e[-1][0]:.1f}"
               f"   (= {e[0][0] * SPACE:.0f}..{e[-1][0] * SPACE:.0f} device)")
    out.append(f"  y {min(ys):.2f}..{max(ys):.2f} "
               f"= {lufs_of(max(ys)):.2f}..{lufs_of(min(ys)):.2f} LUFS")
    return "\n".join(out)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(describe())
