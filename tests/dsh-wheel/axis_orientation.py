"""Why the OLD pure-log axis forced "label only 5 of the 9 ticks".

⚠ **This documents a superseded axis.** `axis_spec.py` is now the F / knee map
(linear +0.3..-30 over 70 % of the plot, log below), under which all nine labels fit.
This file is kept because the argument below is *why the pure log had to be abandoned*,
and it is worth being able to re-run.

The maths:

    |y(v2) - y(v1)|  depends ONLY on  raw(v) = log10(v+51)/log10(52)

so flipping the orientation (`1-raw` instead of `raw`) merely reverses the ORDER of the
same segment heights. There is no orientation of a pure log axis that spreads the loud
end: `0..-16` always got ~9.5 % of the plot height and `-30..-50` always ~77 %, no matter
which way round it was drawn. Spreading the loud end requires leaving the pure-log form --
which is exactly what F does.

The map is re-implemented here rather than imported, so this evidence does not change
whenever `axis_spec` does.

Run: python axis_orientation.py
"""
from __future__ import annotations

import math

# ---- the legacy axis, frozen as it was (top +1, bottom -50, pure log) ----
LUFS_TOP = 1.0
LUFS_BOTTOM = -50.0
SHIFT = -LUFS_BOTTOM + 1.0                  # 51
BASE = LUFS_TOP + SHIFT                     # 52
LABEL_HEIGHT = 92.0                         # reference-chart units, not pt
TICKS = (0.0, -3.0, -5.0, -7.0, -10.0, -14.0, -16.0, -23.0, -30.0)

PLOT = 726.0          # reference plot height, same units as the label text


def raw(v: float) -> float:
    return math.log10(max(LUFS_BOTTOM, min(LUFS_TOP, v)) + SHIFT) / math.log10(BASE)


def frac(v: float) -> float:
    return 1.0 - raw(v)


def segment_heights() -> list[tuple[str, float]]:
    """Heights of the segments between consecutive ticks, top to bottom."""
    vals = list(TICKS) + [LUFS_BOTTOM]
    ys = [frac(v) for v in vals]
    out = []
    for a, b, ya, yb in zip(vals, vals[1:], ys, ys[1:]):
        out.append((f"{a:g} -> {b:g}", (yb - ya) * PLOT))
    return out


def main():
    print(f"plot height {PLOT:.0f}, one label is {LABEL_HEIGHT:.0f} units tall\n")
    print(f"{'segment':>14} {'height':>9} {'share':>8}  {'label fits':>10}")
    segs = segment_heights()
    for name, h in segs:
        fits = "yes" if h >= LABEL_HEIGHT else "NO"
        print(f"{name:>14} {h:>9.1f} {h / PLOT * 100:>7.1f}%  {fits:>10}")
    print()
    active = abs(raw(-16) - raw(0)) * 100
    tail = abs(raw(LUFS_BOTTOM) - raw(-30)) * 100
    print(f"0 .. -16 (where music lives) : {active:>5.1f}% of the plot")
    print(f"-30 .. -50 (the tail)        : {tail:>5.1f}% of the plot")
    print()
    print("These two numbers are orientation-independent: |y(b)-y(a)| == |raw(b)-raw(a)|")
    print("for both frac=raw and frac=1-raw. Flipping only reverses the ORDER of the")
    print("segments, so whichever way it is drawn, one end is compressed and the loud")
    print("end is the candidate -- labelling all 9 ticks was never possible.")
    print()
    print("What the reference chart does instead: a LINEAR axis, whose segments are")
    print("43/29/29/43/57/29/100/100/285 units -- no overlap problem. A piecewise")
    print("control table is the way to get an arbitrary shape.")
    print()
    print("What we do instead (since 2026-10): the F / knee map in `axis_spec.py` --")
    print("linear from +0.3 to -30 over 70 % of the plot, log below. All nine labels")
    print("then need 303 pt of plot and the page has 482.13. See `axis_options.py`.")


if __name__ == "__main__":
    main()
