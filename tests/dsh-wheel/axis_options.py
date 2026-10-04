"""Candidate vertical axes for the reworked loudness chart -- numbers + a look at them.

WHY
---
`axis_spec.py` is frozen as a pure log map over -50 .. +1 LUFS. It is honest and it is
monotonic, but it spends almost the whole plot on the quiet tail. On a real master:

    uploads/POIZON SOUNDS,2088 RECORDS,Wooden - G R A V I T Y.flac   (227 s)
    short-term loudness  p5 -16.3   median -8.7   p95 -5.3

Under the frozen axis that entire band -- everything anyone would call "the music" --
is `frac(-16.3) - frac(-5.3)` = **7.4 % of the plot height**. The other 92.6 % is the
tail down to -50. No label fits either: the smallest labelled gap is 12.59 pt against a
14 pt font (see `layout_spec.axis_label_fit`).

So the axis has to change shape, and that is a judgement call, not a measurement. This
file defines six candidates spanning the design space, prints their numbers, and renders
one contact sheet so the choice can be made by looking.

    python axis_options.py                 # table + invariants + sheet
    python axis_options.py --file <audio>  # use a different recording
    python axis_options.py --sheet out.png

THE SIX
-------
  A  log        the frozen spec, kept as the baseline
  B  linear     equal LUFS per pixel -- what the reference chart (`target/CQ.svg`) does
  C  power      linear bent by one knob, `frac = u**gamma`; smooth, no kinks
  D  piecewise  hand-set segment budgets (mirrors the reference's control-point idea)
  E  ticks      snap every tick onto an even grid -- most readable, most distorted
  F  knee       linear above -30 LUFS, log below it: detail where the music is, the
                tail compressed into the last stretch

Every candidate maps +1 LUFS to frac 0 and -50 LUFS to frac 1, clamps outside, and is
strictly monotonic, so `axis_spec.frac` can be swapped for any of them without touching
a renderer.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for _p in (str(HERE), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import axis_spec                                                       # noqa: E402
import layout_spec                                                    # noqa: E402

#: ⚠ 这是**当初比较六个候选时**用的上界。老板后来把上界改成 +0.3（= `axis_spec.LUFS_TOP`），
#: 所以候选表保留 +1 的评估结果，**不要**改成当前值 —— 那会把"当初怎么选的"悄悄改写成
#: 另一次比较。已定的那条轴直接读 `axis_spec`，见下面的 `SELECTED`。
TOP = 1.0
BOTTOM = axis_spec.LUFS_BOTTOM    # -50

#: the segment the sketch's metadata card measured, reused here as "the music"
BAND = (-16.0, -5.0)

#: a label is 14 pt in the sketch's body font -- the bar a candidate has to clear
LABEL_PT = layout_spec.FONT_BODY

#: 曾经的定稿轴：**纯对数**，上界 +1。`frac = 1 - log10(v+51)/log10(52)`。
#:
#: 这段实现**故意写在这里**、而不是引用 `axis_spec.frac` —— `axis_spec` 已经换成 F 了，
#: 而这个候选是"当初被否掉的那条"，基线不能跟着换。有断言把它按老公式钉住。
_LEGACY_LOG_SHIFT = -BOTTOM + 1.0                 # 51
_LEGACY_LOG_BASE = TOP + _LEGACY_LOG_SHIFT        # 52


def _legacy_log(v: float) -> float:
    v = max(BOTTOM, min(TOP, v))
    return 1.0 - math.log10(v + _LEGACY_LOG_SHIFT) / math.log10(_LEGACY_LOG_BASE)


# --------------------------------------------------------------------- helpers
def _clamp(v: float) -> float:
    return max(BOTTOM, min(TOP, v))


def _u(v: float) -> float:
    """Normalised LUFS: 0 at +1 (top), 1 at -50 (bottom), **linear** in LUFS."""
    return (TOP - _clamp(v)) / (TOP - BOTTOM)


def _pw(points: tuple[tuple[float, float], ...]) -> Callable[[float], float]:
    """Piecewise-linear map from `((lufs, frac), ...)` (lufs descending)."""
    pts = tuple(sorted(points, key=lambda p: -p[0]))

    def f(v: float) -> float:
        v = _clamp(v)
        if v >= pts[0][0]:
            return pts[0][1]
        if v <= pts[-1][0]:
            return pts[-1][1]
        for (v0, f0), (v1, f1) in zip(pts, pts[1:]):
            if v0 >= v >= v1:
                t = (v0 - v) / (v0 - v1)
                return f0 + (f1 - f0) * t
        return 1.0

    return f


def _linear(v: float) -> float:
    return _u(v)


POWER_GAMMA = 0.7


def _power(v: float) -> float:
    # u < 1 and gamma < 1 -> u ** gamma > u, i.e. the LOUD end gets more of the plot.
    return _u(v) ** POWER_GAMMA


#: D -- explicit per-segment budgets. Segments: 4 / 12 / 22 / 22 / 14 / 26 %.
PIECEWISE = (
    (+1.0, 0.00),
    (0.0, 0.04),
    (-5.0, 0.16),
    (-14.0, 0.38),
    (-23.0, 0.60),
    (-30.0, 0.74),
    (-50.0, 1.00),
)

#: E -- every tick on an even grid, so no two labels can ever collide.
TICK_EVEN = tuple([(TOP, 0.0)]
                  + [(v, (k + 1) / (len(axis_spec.TICKS) + 1))
                     for k, v in enumerate(axis_spec.TICKS)]
                  + [(BOTTOM, 1.0)])

#: F -- the knee, and how much of the plot the linear half above it gets.
KNEE = -30.0
KNEE_SHARE = 0.70


def knee_map(top: float, knee: float, share: float):
    """F 的映射，`top` 可以换（老板最后把上界从 +1 改成了 +0.3）。

    上段 `top..knee` 线性占 `share`；下段 `knee..-50` 纯对数占剩下的。
    """
    def f(v: float) -> float:
        v = max(BOTTOM, min(top, v))
        if v >= knee:
            return (top - v) / (top - knee) * share
        # below the knee: pure log from KNEE (frac = share) to BOTTOM (frac = 1)
        sh = -BOTTOM + 1.0                                # 51, keeps log defined
        hi = math.log10(knee + sh)
        g = (hi - math.log10(v + sh)) / hi
        return share + (1.0 - share) * g

    return f


def _knee(v: float) -> float:
    return knee_map(TOP, KNEE, KNEE_SHARE)(v)


class Axis:
    """One candidate mapping."""

    def __init__(self, key: str, name: str, blurb: str,
                 frac: Callable[[float], float]) -> None:
        self.key = key
        self.name = name
        self.blurb = blurb
        self._f = frac

    def frac(self, v: float) -> float:
        return max(0.0, min(1.0, self._f(v)))

    def y(self, v: float, *, top: float | None = None,
          height: float | None = None) -> float:
        t = layout_spec.PLOT.y if top is None else top
        h = layout_spec.PLOT.h if height is None else height
        return t + self.frac(v) * h


CANDIDATES: tuple[Axis, ...] = (
    Axis("log", "A · 纯对数（已被 F 取代）",
         "frac = 1 - log10(v+51)/log10(52)。单调、平滑，但重压缩响端。",
         _legacy_log),
    Axis("linear", "B · 纯线性（参考图的做法）",
         "frac = (1-v)/51。每个 LUFS 等宽，响端立刻展开，尾部也等宽铺开。",
         _linear),
    Axis("power", "C · 幂律 γ=0.7（一个旋钮的平滑折中）",
         "frac = u**0.7。无折点，比线性更偏响端，调 γ 即可连续加/减展开。",
         _power),
    Axis("piecewise", "D · 分段控制点（每段显式配额）",
         "段占比 4/12/22/22/14/26 %，每个带标签的档都 >=12 % 高度。",
         _pw(PIECEWISE)),
    Axis("ticks", "E · 刻度等距（最易读，也最失真）",
         "把 11 个刻度均匀铺满：任何两个标签的间距都 = 10 % 高度。",
         _pw(TICK_EVEN)),
    Axis("knee", "F · 拐点 -30：上线性 + 下对数",
         f"+1..{KNEE:g} 线性占 {KNEE_SHARE:.0%}，{KNEE:g}..-50 对数占 "
         f"{1 - KNEE_SHARE:.0%}。",
         _knee),
)

BY_KEY = {a.key: a for a in CANDIDATES}

# ---------------------------------------------------------------- 已定参数（老板）
#: 这些**不是候选**，是决定。**已经落进 `axis_spec.py`** —— 所以这里不再自己维护一份，
#: 全部读 `axis_spec`，只有一处来源。
#:
#: ⚠ 更正：我之前写过"+1 是为超过 0 的真峰值留余量"，**那句是错的**。纵轴是 LUFS
#: （M / S），真峰值是 dBTP，**两个不同的量**，真峰值从来不上这条轴（它只进指标卡，
#: 以及横轴的爆音时段）。所以上界取多少，与 `truePeakMax` 实测 +4.3 无关。
SELECTED_KEY = "knee"
SELECTED_TOP = axis_spec.LUFS_TOP
SELECTED_KNEE = axis_spec.KNEE
SELECTED_KNEE_SHARE = axis_spec.KNEE_SHARE

#: 上界 +0.3 的意思是：**轴到 +0.3，但文字只到 0** —— 端点不出文字本来就是定稿结构
#: （`axis_spec` 里 +0.3 与 -50 都是端点、都不标）。所以"+0.3 但标尺只到 0"不但可以，
#: 而且正是原来的做法。
#:
#: `frac` **直接引用 `axis_spec.frac`** —— 有断言钉住两者一致，别再复制一份映射。
SELECTED = Axis(
    "knee_selected",
    f"F 拐点 {SELECTED_KNEE:g}，上界 {SELECTED_TOP:+g}",
    f"上段 {SELECTED_TOP:+g}..{SELECTED_KNEE:g} 线性占 {SELECTED_KNEE_SHARE:.0%}，"
    f"下段对数占 {1 - SELECTED_KNEE_SHARE:.0%}；端点 {SELECTED_TOP:+g} 不出文字。",
    axis_spec.frac,
)

#: 端点 +0.3 不标，最上面那个**出文字**的刻度是 `0`
TOP_LABELLED = axis_spec.LABELLED[0]

#: 红区上界：`axis_spec.RED_ABOVE`（老板从 -5 改到 **-3**）。
#:
#: ⚠ 这与参考图那片珊瑚红**不是一个语义**，别照搬：参考图只有一个硬渐变停靠点，落在
#: **-23**（那是 EBU R128 参考线，见 dsh-wheel README），覆盖绘图区上 1/3；
#: 这里的 -3 是"**过响区**"。两个数字差 20 LU，画的不是一件事。
SELECTED_RED_ABOVE = axis_spec.RED_ABOVE

#: the ticks every candidate has to place
TICKS = (TOP,) + tuple(axis_spec.TICKS) + (BOTTOM,)


# --------------------------------------------------------------------- labelling
def label_budget(ax: Axis, *, min_pt: float = LABEL_PT,
                 ticks: tuple[float, ...] | None = None) -> dict:
    """How many of the 9 ticks could carry TEXT under this axis.

    Greedy top -> bottom: keep a tick, drop the next one while it is closer than
    `min_pt` to the last kept one. This is what a renderer actually has to do; the
    frozen spec solved the same problem by hand, by cutting the set to five.
    """
    cand = tuple(axis_spec.TICKS) if ticks is None else ticks
    kept: list[float] = []
    dropped: list[float] = []
    for v in cand:
        if kept and (ax.frac(v) - ax.frac(kept[-1])) * layout_spec.PLOT.h < min_pt:
            dropped.append(v)
        else:
            kept.append(v)
    gaps = [round((ax.frac(b) - ax.frac(a)) * layout_spec.PLOT.h, 2)
            for a, b in zip(kept, kept[1:])]
    return {"kept": kept, "dropped": dropped, "gaps": gaps,
            "min_gap": min(gaps) if gaps else 0.0,
            "all": not dropped}


def top_clearance(ax: Axis, *, label_pt: float = LABEL_PT) -> dict:
    """最上面那个**出文字**的刻度（`0`）离绘图区顶边够不够放。

    标签是**居中**画在刻度线上的，所以需要 `label_pt / 2` 的余量。
    上界从 +1 收到 +0.3 会把 `0` 往上推，这个函数就是量这件事的代价。

    ⚠ 这只是"会不会翻出绘图区顶边"，**不是"会不会被裁掉"**：草图的画布在内容框
    上方还有 60 pt 空白，所以只要导出时不按内容框硬裁，翻出去也看得见。
    """
    have = ax.frac(TOP_LABELLED) * layout_spec.PLOT.h
    need = label_pt / 2.0
    return {"have": round(have, 2), "need": round(need, 2),
            "deficit": round(max(0.0, need - have), 2),
            "fits": have >= need}


# --------------------------------------------------------------------- numbers
def metrics(ax: Axis, *, height: float | None = None,
            band: tuple[float, float] = BAND) -> dict:
    """Everything needed to judge one candidate."""
    h = layout_spec.PLOT.h if height is None else height
    lab = list(axis_spec.LABELLED)
    gaps = [round((ax.frac(b) - ax.frac(a)) * h, 2)
            for a, b in zip(lab, lab[1:])]
    lo, hi = band                                       # lo = quieter, hi = louder
    return {
        "gaps": gaps,
        "min_gap": min(gaps),
        "fits": min(gaps) >= LABEL_PT,
        "band": round((ax.frac(lo) - ax.frac(hi)) * 100.0, 1),
        "loud": round((ax.frac(-16.0) - ax.frac(0.0)) * 100.0, 1),
        "tail": round((ax.frac(BOTTOM) - ax.frac(-30.0)) * 100.0, 1),
        # how far the `0` label sits below the plot's top edge -- under A it is 2.4 pt,
        # i.e. the topmost label is sitting on the border and has to be nudged down.
        "top0": round(ax.frac(0.0) * h, 2),
    }


def report(*, band: tuple[float, float] = BAND, band_label: str = "") -> str:
    h = layout_spec.PLOT.h
    sel_top = top_clearance(SELECTED)
    sel_lb = label_budget(SELECTED)
    sel_need = layout_spec.min_plot_height(SELECTED.frac)
    top_verdict = "够" if sel_top["fits"] else f"差 {sel_top['deficit']:.2f} pt"
    out = [
        f"已定：{SELECTED.name}   (SELECTED_KEY = {SELECTED_KEY!r})",
        f"  上界 {SELECTED_TOP:+g} LUFS（端点，不出文字） / 拐点 {SELECTED_KNEE:g}"
        f" / 上段占 {SELECTED_KNEE_SHARE:.0%}",
        f"  最上面出文字的刻度 = {TOP_LABELLED:g}，离顶边 {sel_top['have']:.2f} pt，"
        f"标签半高需要 {sel_top['need']:.2f} pt -> {top_verdict}",
        f"  9 个刻度全出文字：保留 {len(sel_lb['kept'])}/9，最紧 {sel_lb['min_gap']:.2f} pt，"
        f"需要绘图区 {sel_need:.0f} pt（草图给 {h:.1f}）",
        f"  红区上界 {SELECTED_RED_ABOVE:g} LUFS -> 顶边起 "
        f"{SELECTED.frac(SELECTED_RED_ABOVE) * h:.2f} pt"
        f"（占绘图区 {SELECTED.frac(SELECTED_RED_ABOVE) * 100:.1f}%，是一顶薄帽）；"
        f"参考图的颜色分界在 -23，不是一个语义",
        "",
        "以下是**当初怎么选的**，六个候选都按当时的上界 +1 评估：",
        "",
        f"plot area from the sketch: {layout_spec.PLOT.w:.0f} x {h:.2f} pt  "
        f"(layout_spec.PLOT)",
        f"a tick label is {LABEL_PT:.0f} pt tall -> every labelled gap must be"
        f" >= {LABEL_PT:.0f} pt",
        "",
        f"{'':<34} {'0..-16':>7} {'-30..-50':>9} {'band':>6} {'0@顶边':>7} "
        f"{'min gap':>8} {'14pt':>5}  labelled gaps (pt)",
    ]
    for ax in CANDIDATES:
        m = metrics(ax, band=band)
        out.append(f"{ax.name:<34} {m['loud']:>6.1f}% {m['tail']:>8.1f}% "
                   f"{m['band']:>5.1f}% {m['top0']:>6.1f} "
                   f"{m['min_gap']:>7.2f} "
                   f"{'ok' if m['fits'] else 'NO':>5}  {m['gaps']}")
    if band_label:
        out += ["", f"band column = share of the plot covered by {band_label}"
                    f"  ({band[0]:g} .. {band[1]:g} LUFS)"]
    out += ["",
            "range note: the axis top is +1 LUFS, so the `0` line is already some way"
            " down.",
            "            Pulling the top in to 0 would hand every candidate back the"
            " `0@顶边` column.",
            "",
            f"how many of the 9 ticks can carry text (greedy, >= {LABEL_PT:.0f} pt):",
            f"{'':<34} {'kept':>5} {'min gap':>8}  dropped"]
    for ax in CANDIDATES:
        lb = label_budget(ax)
        out.append(f"{ax.name:<34} {len(lb['kept']):>4}/9 {lb['min_gap']:>7.2f}  "
                   f"{lb['dropped'] if lb['dropped'] else '—'}")
    out += ["",
            "plot height each candidate would NEED to seat all 9 ticks"
            " (a 14 pt label / the tightest tick gap):",
            f"{'':<34} {'needed':>8} {'have':>8}  verdict"]
    for ax in CANDIDATES:
        need = layout_spec.min_plot_height(ax.frac)
        out.append(f"{ax.name:<34} {need:>7.0f} pt {layout_spec.PLOT.h:>7.1f} pt  "
                   f"{'ok' if need <= layout_spec.PLOT.h else 'no'}")
    out += ["",
            "tick positions (pt from the plot's top edge):",
            f"{'LUFS':>6} " + " ".join(f"{a.key:>10}" for a in CANDIDATES)]
    for v in TICKS:
        row = " ".join(f"{a.y(v) - layout_spec.PLOT.y:>10.1f}" for a in CANDIDATES)
        mark = "*" if axis_spec.is_labelled(v) else " "
        out.append(f"{v:>5g}{mark} {row}")
    out.append("  (* = labelled; unmarked are grid-line only)")
    return "\n".join(out)


# --------------------------------------------------------------------- data
def load_curve(path: Path | None = None) -> dict:
    """A real short-term loudness curve to draw the candidates with.

    Order of preference: `--file`, then an ASCII-named audio file in `uploads/`
    (ffmpeg chokes on the CJK names on this machine), then the widest cached timeline,
    then a synthetic two-level signal so the script still runs on a bare clone.
    """
    from backend import audio                                       # noqa: PLC0415

    if path is not None:
        d = audio.loudness_timeline(Path(path).resolve())
        return {"label": Path(path).name, "summary": d.get("summary") or {},
                "t": d.get("t") or [], "S": d.get("S") or [],
                "duration": d.get("duration") or 0.0, "synthetic": False}

    up = ROOT / "uploads"
    if up.is_dir():
        # Among the ASCII-named files, take the one with the WIDEST p5..p95 short-term
        # span: a flat master does not show what a different axis does.
        best_up = None
        for p in sorted(up.iterdir()):
            if not (p.is_file() and p.name.isascii()
                    and p.suffix.lower() in (".flac", ".wav", ".mp3", ".m4a")):
                continue
            try:
                d = audio.loudness_timeline(p.resolve())
            except Exception:                                      # noqa: BLE001
                continue
            s = [v for v in (d.get("S") or []) if v is not None and v > -70]
            if len(s) < 50:
                continue
            span = _pct(s, 95) - _pct(s, 5)
            if best_up is None or span > best_up[0]:
                best_up = (span, p, d)
        if best_up:
            _, p, d = best_up
            return {"label": p.name, "summary": d.get("summary") or {},
                    "t": d.get("t") or [], "S": d.get("S") or [],
                    "duration": d.get("duration") or 0.0, "synthetic": False}

    best = None
    for p in (ROOT / ".cache").glob("loudness-*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                          # noqa: BLE001
            continue
        s = [v for v in (d.get("S") or []) if v is not None and v > -70]
        if len(s) < 50:
            continue
        span = _pct(s, 95) - _pct(s, 5)
        if best is None or span > best[0]:
            best = (span, d)
    if best:
        d = best[1]
        return {"label": f"cached timeline ({d.get('duration', 0):.0f} s)",
                "summary": d.get("summary") or {}, "t": d.get("t") or [],
                "S": d.get("S") or [], "duration": d.get("duration") or 0.0,
                "synthetic": False}

    # synthetic: 20 s quiet, 20 s loud, 20 s quiet -- enough to show the difference
    t, s = [], []
    for k in range(600):
        t.append(k * 0.1)
        s.append(-30.0 if (k // 100) % 2 == 0 else -9.0)
    return {"label": "synthetic two-level signal", "summary": {"integrated": -12.0},
            "t": t, "S": s, "duration": 60.0, "synthetic": True}


def _pct(values: list[float], p: float) -> float:
    a = sorted(values)
    i = (len(a) - 1) * p / 100.0
    lo = int(i)
    hi = min(lo + 1, len(a) - 1)
    return a[lo] + (a[hi] - a[lo]) * (i - lo)


def band_of(curve: dict) -> tuple[float, float]:
    s = [v for v in curve["S"] if v is not None and v > -70]
    if len(s) < 20:
        return BAND
    return (round(_pct(s, 5), 1), round(_pct(s, 95), 1))


def envelope(curve: dict, cols: int) -> list[float | None]:
    """Per-column MAXIMUM of `S` (never the mean -- it would flatten the peaks)."""
    t, s = curve["t"], curve["S"]
    out: list[float | None] = [None] * cols
    dur = float(curve.get("duration") or (t[-1] if t else 1.0)) or 1.0
    for ti, vi in zip(t, s):
        if vi is None or vi <= axis_spec.LUFS_BOTTOM - 69.0:        # -120 silence floor
            continue
        i = int(ti / dur * cols)
        i = 0 if i < 0 else (cols - 1 if i >= cols else i)
        if out[i] is None or vi > out[i]:
            out[i] = vi
    return out


# --------------------------------------------------------------------- render
SHEET_COLS = 3
SCALE = 0.82
GAP = 26
PAD = 22
HEAD = 56


def _dashed(d, x0: float, x1: float, y: float, *, fill: str,
            on: int = 5, off: int = 4) -> None:
    """A horizontal dashed line (PIL has no dash support)."""
    x = x0
    while x < x1:
        d.line([(x, y), (min(x + on, x1), y)], fill=fill, width=1)
        x += on + off


def render_sheet(curve: dict, out_path: Path, *,
                 band: tuple[float, float] | None = None) -> Path:
    """Draw every candidate at the sketch's plot aspect, side by side."""
    from PIL import Image, ImageDraw                                # noqa: PLC0415
    from backend import audio                                       # noqa: PLC0415
    from backend import loudness_svg                                # noqa: PLC0415

    pw = int(round(layout_spec.PLOT.w * SCALE))
    ph = int(round(layout_spec.PLOT.h * SCALE))
    rail = int(round(layout_spec.AXIS_RAIL.w * SCALE))
    cell_w = rail + pw
    cell_h = HEAD + ph
    rows = (len(CANDIDATES) + SHEET_COLS - 1) // SHEET_COLS
    W = PAD * 2 + SHEET_COLS * cell_w + (SHEET_COLS - 1) * GAP
    H = PAD * 2 + 34 + rows * cell_h + (rows - 1) * GAP

    f_title = audio._pick_font(15, need_cjk=True) or audio._pick_font(15)
    f_sub = audio._pick_font(11, need_cjk=True) or audio._pick_font(11)
    f_tick = audio._pick_font(max(9, int(round(14 * SCALE))))
    f_note = audio._pick_font(12, need_cjk=True) or audio._pick_font(12)

    # 调色板取自**渲染器**（`loudness_svg.PALETTE`）—— `audio.LOUD_COLORS` 2026-10
    # 随旧 PNG 渲染器一起退役了，别再从这里引。
    C = loudness_svg.PALETTE
    img = Image.new("RGB", (W, H), C["bg"])
    d = ImageDraw.Draw(img)

    b_lo, b_hi = band if band else band_of(curve)
    band_note = (f"{b_lo:g} .. {b_hi:g} LUFS" if not curve["synthetic"]
                 else "synthetic")
    head = (f"纵轴候选方案：同一份真实响度曲线（最短时 S），只换 frac()"
            f"　—　源：{curve['label']}")
    d.text((PAD, PAD), head, font=f_title, fill=C["text"])
    d.text((PAD, PAD + 22),
           f"音乐实际活动区 {band_note} 用浅色带标出；每个面板的绘图区都是版式规格里的"
           f" {layout_spec.PLOT.w:.0f}×{layout_spec.PLOT.h:.0f} pt（按 {SCALE:.2f} 缩放）",
           font=f_note, fill=C["muted"])

    series = envelope(curve, pw)
    high = None
    integ = curve["summary"].get("integrated")
    lra = curve["summary"].get("lra")
    if isinstance(integ, (int, float)):
        high = float(integ) + float(lra or 0.0) / 2.0

    for k, ax in enumerate(CANDIDATES):
        col, row = k % SHEET_COLS, k // SHEET_COLS
        ox = PAD + col * (cell_w + GAP)
        oy = PAD + 34 + row * (cell_h + GAP)
        px, py = ox + rail, oy + HEAD
        m = metrics(ax, band=(b_lo, b_hi))

        d.text((ox, oy + 2), ax.name, font=f_title, fill=C["text"])
        d.text((ox, oy + 24),
               f"0..-16 占 {m['loud']:.1f}%　最小标签间距 {m['min_gap']:.1f} pt　"
               f"{'标签放得下' if m['fits'] else '标签放不下'}",
               font=f_sub, fill=C["text"] if m["fits"] else "#C0392B")
        d.text((ox, oy + 40), ax.blurb, font=f_sub, fill=C["muted"])

        # the rail, echoing the sketch's 80 pt column
        d.rectangle([ox, py, ox + rail - 1, py + ph - 1], fill="#FAFAFA",
                    outline="#E4E7EA")

        # "where the music lives" band -- filled first, then re-outlined ON TOP of the
        # envelope, otherwise the fill would hide it completely.
        y_hi = py + ax.frac(b_hi) * ph
        y_lo = py + ax.frac(b_lo) * ph
        d.rectangle([px, y_hi, px + pw - 1, y_lo], fill="#EFF4FA")

        # envelope: body below the red threshold, head above it
        if high is not None:
            body, headp = [], []
            for i, v in enumerate(series):
                if v is None:
                    continue
                x = px + i
                y = py + ax.frac(v) * ph
                body.append((x, y))
                headp.append((x, py + ax.frac(max(v, high)) * ph))
            if body:
                d.polygon(body + [(body[-1][0], py + ph), (body[0][0], py + ph)],
                          fill=C["body"])
                bandp = headp + list(reversed(body))
                if len(bandp) >= 3:
                    d.polygon(bandp, fill=C["head"])

        # Grid ON TOP of the fill. The envelope fills downward from the curve, so
        # drawing the grid underneath would hide the very spacing this sheet exists
        # to show. Labelled ticks get a slightly stronger line.
        for v in TICKS:
            y = py + ax.frac(v) * ph
            strong = axis_spec.is_labelled(v)
            d.line([(px, y), (px + pw, y)],
                   fill="#C3CAD2" if strong else C["grid"], width=1)

        # the music band's edges, dashed, so it reads as an overlay and not as a tick
        for y in (y_hi, y_lo):
            _dashed(d, px, px + pw - 1, y, fill="#5B7FA6")
        bb = d.textbbox((0, 0), "p5..p95", font=f_sub)
        d.text((px + pw - 6 - (bb[2] - bb[0]), max(py + 2, y_hi - 14)), "p5..p95",
               font=f_sub, fill="#3C5B7C")

        # -23 reference line
        ry = py + ax.frac(-23.0) * ph
        d.line([(px, ry), (px + pw, ry)], fill=C["refLine"], width=2)

        d.rectangle([px, py, px + pw - 1, py + ph - 1], outline="#B9BEC4")

        # tick labels in the rail
        for v in TICKS:
            if not axis_spec.is_labelled(v):
                continue
            y = py + ax.frac(v) * ph
            txt = f"{v:g}"
            bb = d.textbbox((0, 0), txt, font=f_tick)
            d.text((px - 6 - (bb[2] - bb[0]), y - (bb[3] - bb[1]) / 2 - bb[1]),
                   txt, font=f_tick,
                   fill="#C0392B" if axis_spec.is_red(v) else C["text"])

        # annotate the tightest labelled gap right on the axis
        y0 = py + ax.frac(0.0) * ph
        y1 = py + ax.frac(-5.0) * ph
        d.line([(px + 4, y0), (px + 4, y1)], fill="#C0392B", width=1)
        d.line([(px + 1, y0), (px + 7, y0)], fill="#C0392B", width=1)
        d.line([(px + 1, y1), (px + 7, y1)], fill="#C0392B", width=1)
        d.text((px + 10, (y0 + y1) / 2 - 6), f"{m['gaps'][0]:.0f}",
               font=f_sub, fill="#C0392B")

    img.save(out_path)
    return out_path


# --------------------------------------------------------------------- checks
def check() -> bool:
    ok = True

    def t(name: str, cond: bool, extra: object = "") -> None:
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + name
              + (f"   {extra}" if extra else ""))
        ok = ok and cond

    for ax in CANDIDATES:
        t(f"{ax.key}: +1 LUFS -> frac 0", abs(ax.frac(TOP)) < 1e-9, ax.frac(TOP))
        t(f"{ax.key}: -50 LUFS -> frac 1", abs(ax.frac(BOTTOM) - 1.0) < 1e-9,
          ax.frac(BOTTOM))
        t(f"{ax.key}: strictly increasing as LUFS falls",
          all(ax.frac(b) > ax.frac(a) for a, b in zip(TICKS, TICKS[1:])))
        t(f"{ax.key}: clamps above the top", abs(ax.frac(99.0)) < 1e-9)
        t(f"{ax.key}: clamps below the bottom",
          abs(ax.frac(-120.0) - 1.0) < 1e-9)

    t("candidate A reproduces the LEGACY pure-log map",
      all(abs(BY_KEY["log"].frac(v) - _legacy_log(v)) < 1e-12 for v in TICKS))
    t("the baseline did NOT silently follow axis_spec when it became F",
      any(abs(BY_KEY["log"].frac(v) - axis_spec.frac(v)) > 1e-6 for v in TICKS))
    # 按**老文档真正引用的数字**核对，而不是我手抄的控制点：
    # 交接文档 §1.2 的 5 个标签间距（726 单位绘图区）与 §1.3 的 9.5%。
    _lab5 = (0.0, -5.0, -14.0, -23.0, -30.0)
    _gaps5 = [round((_legacy_log(b) - _legacy_log(a)) * 726.0, 1)
              for a, b in zip(_lab5, _lab5[1:])]
    t("legacy log reproduces the doc's labelled gaps 19.0 / 40.0 / 51.2 / 52.9",
      _gaps5 == [19.0, 40.0, 51.2, 52.9], _gaps5)
    t("legacy log reproduces the doc's 9.5 % for the 0..-16 region",
      abs((_legacy_log(-16.0) - _legacy_log(0.0)) * 100 - 9.5) < 0.1,
      (_legacy_log(-16.0) - _legacy_log(0.0)) * 100)
    t("piecewise control points descend in LUFS and ascend in frac",
      all(a[0] > b[0] and a[1] < b[1] for a, b in zip(PIECEWISE, PIECEWISE[1:])))
    t("piecewise starts at +1/0 and ends at -50/1",
      PIECEWISE[0] == (TOP, 0.0) and PIECEWISE[-1] == (BOTTOM, 1.0))
    t("tick-even places every axis_spec tick on the even grid",
      [v for v, _ in TICK_EVEN][1:-1] == list(axis_spec.TICKS)
      and all(abs(f - (k + 1) / (len(axis_spec.TICKS) + 1)) < 1e-12
              for k, (_, f) in enumerate(TICK_EVEN[1:-1])))
    t("the knee is continuous",
      abs(_knee(KNEE) - KNEE_SHARE) < 1e-12)
    t("KNOWN: the frozen axis is the WORST candidate for the music band",
      metrics(BY_KEY["log"])["band"] == min(metrics(a)["band"]
                                            for a in CANDIDATES),
      [metrics(a)["band"] for a in CANDIDATES])
    t("every candidate except A seats a 14 pt label",
      all(metrics(a)["fits"] for a in CANDIDATES if a.key != "log"))

    # the owner's choice, and the payoff that comes with it
    t("SELECTED_KEY names a real candidate", SELECTED_KEY in BY_KEY, SELECTED_KEY)
    t("the selected axis keeps the music band well clear of the frozen one",
      metrics(SELECTED)["band"] > 3 * metrics(BY_KEY["log"])["band"],
      f"{metrics(SELECTED)['band']}% vs {metrics(BY_KEY['log'])['band']}%")
    t("KNOWN: the frozen axis forces labels to be cut, the selected one does not",
      not label_budget(BY_KEY["log"])["all"] and label_budget(SELECTED)["all"],
      f"A keeps {len(label_budget(BY_KEY['log'])['kept'])}/9,"
      f" F keeps {len(label_budget(SELECTED)['kept'])}/9")
    t("the frozen axis could not seat 9 labels even in a 1300 pt plot",
      layout_spec.min_plot_height(BY_KEY["log"].frac) > 1200.0,
      f"{layout_spec.min_plot_height(BY_KEY['log'].frac):.0f} pt")
    t("the selected axis seats 9 labels inside the sketch's 482.13 pt plot",
      layout_spec.min_plot_height(SELECTED.frac) <= layout_spec.PLOT.h,
      f"needs {layout_spec.min_plot_height(SELECTED.frac):.0f} pt,"
      f" has {layout_spec.PLOT.h:.1f} pt")

    # ---- the owner's final parameters: top +0.3, knee -30, share 0.70 ----
    t("sel: top is +0.3 and maps to frac 0", abs(SELECTED.frac(SELECTED_TOP)) < 1e-12)
    t("sel: -50 still maps to frac 1", abs(SELECTED.frac(BOTTOM) - 1.0) < 1e-12)
    t("sel: the knee is continuous at -30",
      abs(SELECTED.frac(SELECTED_KNEE) - SELECTED_KNEE_SHARE) < 1e-12)
    t("sel: 0 is a labelled tick AND is not an axis end point",
      TOP_LABELLED in axis_spec.LABELLED and TOP_LABELLED != SELECTED_TOP,
      f"top={SELECTED_TOP:+g}, topmost labelled={TOP_LABELLED:g}")
    t("sel: the segment above 0 is linear, so equal LU steps are equal pixels",
      _equal_steps(SELECTED, (0.3, 0.0, -5.0, -20.0, -30.0)))
    # KNOWN, asserted so it cannot quietly stop being true: pulling the top in to +0.3
    # squeezes the `0` label against the plot's top edge.
    t("KNOWN: +0.3 leaves the `0` label short of half its own height",
      not top_clearance(SELECTED)["fits"]
      and 0 < top_clearance(SELECTED)["deficit"] < 5.0,
      f"have {top_clearance(SELECTED)['have']:.2f} pt,"
      f" need {top_clearance(SELECTED)['need']:.2f} pt")
    t("+1 would have cleared it -- the deficit is a cost of +0.3, not of F",
      top_clearance(BY_KEY["knee"])["fits"])

    # ---- 红区：老板把上界从 -5 挪到 -3 ----
    t("sel: the red zone starts at -3 LUFS, and -3 is one of the ticks",
      SELECTED_RED_ABOVE == -3.0 and SELECTED_RED_ABOVE in axis_spec.TICKS)
    t("sel: under the new 9-label scheme -3 becomes a LABELLED tick",
      SELECTED_RED_ABOVE in label_budget(SELECTED)["kept"])
    t("sel: -3 sits in the LINEAR part, above the knee",
      SELECTED_RED_ABOVE > SELECTED_KNEE)
    t("KNOWN: -3 is a thin cap -- under a fifth of the plot",
      SELECTED.frac(SELECTED_RED_ABOVE) < 0.20,
      f"{(SELECTED.frac(SELECTED_RED_ABOVE)) * 100:.1f}% of the plot")
    t("KNOWN: the red zone is NOT the reference chart's -23 colour split",
      SELECTED_RED_ABOVE != -23.0 and abs(SELECTED_RED_ABOVE - (-23.0)) > 15.0,
      f"selected {SELECTED_RED_ABOVE:g} vs reference -23")
    return ok


def _equal_steps(ax: Axis, values: tuple[float, ...]) -> bool:
    """上面那几个值落在 F 的线性段里，所以相邻两档的像素间距应该相等。"""
    ys = [ax.frac(v) * layout_spec.PLOT.h for v in values]
    d = [b - a for a, b in zip(ys, ys[1:])]
    lu = [a - b for a, b in zip(values, values[1:])]
    per = [x / y for x, y in zip(d, lu)]
    return max(per) - min(per) < 1e-9


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="响度图纵轴候选方案")
    ap.add_argument("--file", help="用这个音频文件算时间线（缺省用 uploads / 缓存）")
    ap.add_argument("--sheet", default=str(ROOT / ".cache" / "_look"
                                           / "axis-options.png"),
                    help="对比图输出路径")
    ap.add_argument("--no-sheet", action="store_true", help="只出数字，不画图")
    a = ap.parse_args()

    curve = load_curve(Path(a.file) if a.file else None)
    band = band_of(curve)
    label = (f"实测 S 的 p5..p95（{curve['label']}）" if not curve["synthetic"]
             else "")
    print(report(band=band, band_label=label))
    print()
    print("invariants:")
    good = check()
    if not a.no_sheet:
        out = Path(a.sheet)
        out.parent.mkdir(parents=True, exist_ok=True)
        render_sheet(curve, out, band=band)
        print()
        print(f"对比图：{out}")
    print()
    print("ALL PASS" if good else "PROBLEMS")
    sys.exit(0 if good else 1)
