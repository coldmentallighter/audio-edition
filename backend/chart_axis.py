"""The loudness chart's vertical axis (FROZEN spec) -- the F / knee map.

Single source of truth for the mapping, so renderer, tests and docs all read it here.

DECISION (project owner, final)
-------------------------------
  * unit LUFS; **top = +0.3**, **bottom = -50** -- both are axis END POINTS and
    **neither is labelled**
  * the map is a **KNEE**: linear from +0.3 down to **-30** (using 70 % of the plot
    height), then logarithmic from -30 down to -50 (the remaining 30 %)
  * LABELLED ticks (text drawn): **0 -3 -5 -7 -10 -14 -16 -23 -30** -- all nine
  * there are no grid-line-only ticks any more (UNLABELLED is empty)
  * red at or above **-3 LUFS**

WHY THIS SHAPE
--------------
The pure log map that came before this one spent 77 % of the plot on `-30..-50` and
squeezed `0..-16` -- where music actually lives -- into **9.5 %**, so the loud end was
a sliver and no two labels could be seated (smallest labelled gap 12.59 pt against a
14 pt label; seating all nine would have needed a 1300 pt plot). `axis_options.py` holds
the six candidates that were compared. Under F the nine ticks need **303 pt** of plot
and the page's plot is **482.13 pt** (see `layout_spec.PLOT`).

The upper segment being *linear* is what buys that room, and it has a second payoff:
equal LUFS steps above the knee are equal pixels, so the loud end reads evenly.

FORMULA
-------
    top = +0.3   knee = -30   share = 0.70   bottom = -50

    v >= knee:   frac(v) = (top - v) / (top - knee) * share
    v <  knee:   frac(v) = share + (1 - share) * g(v)
                 g(v)    = (log10(knee + S) - log10(v + S)) / log10(knee + S)
                 S       = -bottom + 1 = 51     (keeps v + S >= 1, so log10 is defined)

`frac` is the fraction of plot height measured DOWN from the top, so it feeds straight
into pixel layout. `invert` is the analytic inverse of each branch.

KNOWN LIMITATION (accepted, not a bug)
--------------------------------------
Pulling the top in from +1 to +0.3 means the `0` line sits only
`frac(0) * 482.13` = **3.34 pt** below the plot's top edge, but a 14 pt label is centred
on its line and needs **7.00 pt**. So the `0` label **overhangs the plot by ~3.66 pt**.

That is deliberate and harmless: the page canvas has **60 pt of empty margin** above the
content box, so the label is fully visible. The only rule it imposes is that an export
must **not hard-crop to the content box** (`layout_spec` records this; PNG's
`PNG_PAD_T` = 34 px already covers it). `axis_options.top_clearance()` measures it.
"""
from __future__ import annotations

import math

#: axis end points -- NEITHER gets text
LUFS_TOP = 0.3
LUFS_BOTTOM = -50.0

#: where the map changes shape, and how much of the plot the linear part above it takes
KNEE = -30.0
KNEE_SHARE = 0.70

#: log needs a shift because LUFS spans zero. 51 keeps (v + S) >= 1 across the whole
#: range; at the knee that is 21, which normalises the logarithmic branch.
LOG_SHIFT = -LUFS_BOTTOM + 1.0            # 51.0
LOG_KNEE = KNEE + LOG_SHIFT              # 21.0
LOG_KNEE_BASE = math.log10(LOG_KNEE)

#: ticks that get BOTH a grid line and text -- all nine of them
LABELLED: tuple[float, ...] = (0.0, -3.0, -5.0, -7.0, -10.0, -14.0, -16.0, -23.0,
                               -30.0)

#: ticks that get a grid line but NO text. Empty: F made room for all nine.
UNLABELLED: tuple[float, ...] = ()

#: every tick, top to bottom
TICKS: tuple[float, ...] = tuple(sorted(LABELLED + UNLABELLED, reverse=True))

#: ticks at or above this value are tinted red
RED_ABOVE = -3.0

#: height of one label's text, in **pt** (the design's body font, `layout_spec.FONT_BODY`)
LABEL_HEIGHT = 14.0

#: the plot height the KNOWN-limitation arithmetic below is quoted against.
#: `layout_spec` asserts this equals `layout_spec.PLOT.h` so the two cannot drift.
PLOT_H_REF = 482.13


def _clamp(v: float) -> float:
    return max(LUFS_BOTTOM, min(LUFS_TOP, v))


def frac(v: float) -> float:
    """LUFS -> fraction of plot height. 0.0 = top, 1.0 = bottom.

    Out-of-range values are clamped, so the -120 LUFS silence floor lands on the bottom
    edge instead of at infinity, and anything above +0.3 lands on the top edge.
    """
    v = _clamp(v)
    if v >= KNEE:
        return (LUFS_TOP - v) / (LUFS_TOP - KNEE) * KNEE_SHARE
    g = (LOG_KNEE_BASE - math.log10(v + LOG_SHIFT)) / LOG_KNEE_BASE
    return KNEE_SHARE + (1.0 - KNEE_SHARE) * g


def invert(f: float) -> float:
    """Fraction of plot height -> LUFS (analytic inverse of `frac`)."""
    f = max(0.0, min(1.0, f))
    if f <= KNEE_SHARE:
        return LUFS_TOP - (f / KNEE_SHARE) * (LUFS_TOP - KNEE)
    g = (f - KNEE_SHARE) / (1.0 - KNEE_SHARE)
    return (10.0 ** (LOG_KNEE_BASE * (1.0 - g))) - LOG_SHIFT


def y(v: float, top: float, height: float) -> float:
    """LUFS -> pixel y, given the plot's top edge and height."""
    return top + frac(v) * height


def is_labelled(v: float) -> bool:
    return v in LABELLED


def is_red(v: float) -> bool:
    """Whether this tick's text is drawn in the red zone (at or above -3 LUFS)."""
    return v >= RED_ABOVE


def ticks() -> list[tuple[float, bool, bool]]:
    """`[(lufs, labelled, red)]`, top to bottom -- everything a renderer needs."""
    return [(v, is_labelled(v), is_red(v)) for v in TICKS]


def min_plot_height(height: float | None = None,
                    label_pt: float = LABEL_HEIGHT) -> float:
    """Plot height needed to seat EVERY labelled tick without overlap.

    = label height / the tightest labelled gap as a fraction. `layout_spec` has a
    generic version of this that can take another mapping.
    """
    lab = list(LABELLED)
    gaps = [frac(b) - frac(a) for a, b in zip(lab, lab[1:])]
    return label_pt / min(gaps)


def top_clearance(height: float | None = None,
                  label_pt: float = LABEL_HEIGHT) -> dict:
    """How much room the topmost LABELLED tick has before it overhangs the plot."""
    h = PLOT_H_REF if height is None else height
    have = frac(LABELLED[0]) * h
    need = label_pt / 2.0
    return {"have": round(have, 2), "need": round(need, 2),
            "deficit": round(max(0.0, need - have), 2), "fits": have >= need}


def layout_report(height: float | None = None,
                  label_h: float = LABEL_HEIGHT) -> str:
    h = PLOT_H_REF if height is None else height
    out = [f"axis: {LUFS_BOTTOM:+.1f} .. {LUFS_TOP:+.1f} LUFS, knee at {KNEE:g} "
           f"({KNEE_SHARE:.0%} linear above it)",
           f"      frac = (top-v)/(top-knee)*share            for v >= {KNEE:g}",
           f"      frac = share + (1-share)*log ratio         for v <  {KNEE:g}",
           f"      labelled: {' '.join(f'{v:g}' for v in LABELLED)}  ({len(LABELLED)})",
           f"      unlabelled: {'(none)' if not UNLABELLED else UNLABELLED}",
           f"      end points: {LUFS_TOP:+.1f} (top) and {LUFS_BOTTOM:+.0f} (bottom),"
           f" neither labelled",
           f"      red at or above {RED_ABOVE:g} LUFS",
           "",
           f"plot height {h:.2f} pt; a label is {label_h:.0f} pt tall",
           "",
           f"{'LUFS':>6} {'frac':>8} {'y':>8} {'gap':>8}  {'text':>6}  colour"]
    prev = None
    for v in TICKS:
        f = frac(v)
        gap = "" if prev is None else f"{(f - prev) * h:>8.2f}"
        out.append(f"{v:>6g} {f:>8.4f} {f * h:>8.2f} {gap:>8}  "
                   f"{'yes':>6}  {'red' if is_red(v) else 'normal'}")
        prev = f
    f_bot = frac(LUFS_BOTTOM)
    out.append(f"{LUFS_BOTTOM:>6g} {f_bot:>8.4f} {f_bot * h:>8.2f} "
               f"{(f_bot - prev) * h:>8.2f}  {'(end)':>6}  -")
    tc = top_clearance(h, label_h)
    out += ["",
            f"  tightest labelled gap: "
            f"{min((frac(b) - frac(a)) * h for a, b in zip(TICKS, TICKS[1:])):.2f} pt"
            f"  -> all {len(LABELLED)} labels need {min_plot_height(h, label_h):.0f} pt"
            f" of plot",
            f"  0 .. -16 (where music lives) gets "
            f"{(frac(-16) - frac(0)) * 100:.1f}% of the plot; -30 .. -50 gets"
            f" {(f_bot - frac(-30)) * 100:.1f}%",
            f"  the `{LABELLED[0]:g}` label sits {tc['have']:.2f} pt below the top edge"
            f" and needs {tc['need']:.2f} pt -> overhangs by {tc['deficit']:.2f} pt"
            f" (accepted; the canvas has 60 pt of margin above the content box)"]
    return "\n".join(out)


def check() -> bool:
    """Invariants. Cheap enough to call from any test."""
    ok = True

    def t(name: str, cond: bool, extra: object = "") -> None:
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + name
              + (f"   {extra}" if extra else ""))
        ok = ok and cond

    t("top (+0.3) maps to frac 0", abs(frac(LUFS_TOP)) < 1e-12, frac(LUFS_TOP))
    t("bottom (-50) maps to frac 1", abs(frac(LUFS_BOTTOM) - 1.0) < 1e-12,
      frac(LUFS_BOTTOM))
    t("the knee lands exactly on KNEE_SHARE",
      abs(frac(KNEE) - KNEE_SHARE) < 1e-12, frac(KNEE))
    t("the map is continuous at the knee",
      abs(frac(KNEE + 1e-6) - frac(KNEE - 1e-6)) < 1e-6)
    t("ticks run strictly top -> bottom",
      all(frac(a) < frac(b) for a, b in zip(TICKS, TICKS[1:])))
    t("invert round-trips on every tick, the knee and both end points",
      all(abs(invert(frac(v)) - v) < 1e-9
          for v in list(TICKS) + [KNEE, LUFS_TOP, LUFS_BOTTOM]))
    t("invert is strictly DECREASING in frac (further down = quieter)",
      all(invert(a) > invert(b)
          for a, b in ((0.0, 0.1), (0.69, 0.71), (0.9, 1.0))))
    t("above the knee equal LUFS steps are equal frac steps (it is linear)",
      abs((frac(-10) - frac(-20)) / 10 - (frac(0) - frac(-5)) / 5) < 1e-12)
    t("below the knee steps SHRINK as LUFS falls (it is logarithmic)",
      (frac(-40) - frac(-50)) < (frac(-30) - frac(-40)))
    t("no end point is labelled",
      LUFS_BOTTOM not in LABELLED and LUFS_TOP not in LABELLED)
    t("every tick is labelled -- UNLABELLED is empty",
      UNLABELLED == () and all(is_labelled(v) for v in TICKS), UNLABELLED)
    t("there are exactly 9 labelled ticks", len(LABELLED) == 9, LABELLED)
    t("TICKS == LABELLED (sorted top to bottom)",
      TICKS == tuple(sorted(LABELLED, reverse=True)))
    t("the red ticks are exactly 0 and -3",
      [v for v in TICKS if is_red(v)] == [0.0, -3.0])
    t("the red zone starts at -3 and covers everything above",
      all(is_red(v) for v in TICKS if v >= RED_ABOVE)
      and not any(is_red(v) for v in TICKS if v < RED_ABOVE))
    t("silence (-120) clamps to the bottom", abs(frac(-120.0) - 1.0) < 1e-12)
    t("above the top clamps to the top", abs(frac(99.0)) < 1e-12)
    t("ticks() covers every tick exactly once",
      [v for v, _, _ in ticks()] == list(TICKS))
    t("KNEE_SHARE is a real fraction", 0.0 < KNEE_SHARE < 1.0, KNEE_SHARE)

    # KNOWN, asserted so it cannot regress silently.
    need = min_plot_height()
    t("KNOWN: all 9 labels need ~303 pt of plot -- less than the page's 482.13",
      abs(need - 303.0) < 2.0 and need < PLOT_H_REF, f"{need:.1f} pt")
    tc = top_clearance()
    t("KNOWN: the `0` label overhangs the plot's top edge by ~3.66 pt",
      not tc["fits"] and abs(tc["deficit"] - 3.66) < 0.1, tc)
    return ok


if __name__ == "__main__":
    print(layout_report())
    print()
    print("invariants:")
    print("ALL PASS" if check() else "PROBLEMS")
