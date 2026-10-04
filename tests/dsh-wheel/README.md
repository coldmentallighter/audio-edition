# dsh-wheel — reference-chart decoding toolkit

## Why this exists

`target/CQ.svg` is a Youlean Loudness Meter "Custom Quality" export. It has no
`<font>`/`unicode` metadata — its text is 63 sets of bezier outlines — and it mixes
coordinate spaces, so it cannot be read with a normal SVG tool. It also cannot be
rasterised on this machine (ffmpeg here has no SVG decoder, and a headless browser is
blocked by the sandbox's named-pipe restriction). Everything below exists to get exact
numbers out of it anyway.

## What the reference file actually is

All of this was measured, not transcribed. `python reference_geometry.py` prints it.

| | |
|---|---|
| canvas | 32000 × 8640 device units (= 4000 × 1080 raw) → **3.70 : 1** |
| two spaces | the envelope path, grid strokes, axis labels and gradient stops agree with **each other** in *raw* units; only the **clipPath rects** are authored in *device* units (8× larger). Using a clip rect as the plot box scales the plot 8× — see `check_axis.py`. |
| plot box | raw x 56 … 3948, raw y 142.10 … 794.96 |
| axis | **0 … −54 LUFS, LINEAR**, 12.090 raw units per LU |
| labelled lines | `0  −3  −6  −9  −18  −23  −27  −36  −45  −54` |
| time axis | 0s, 7s, … 4m:51s → **291 s**, one tick every 7 s |
| footer height | 134 raw units |

**The reference axis is linear.** Its tick *values* are unevenly spaced
(3/3/3/9/5/4/9/9/9 LU), which is why its labels look bunched. Measured across all nine
segments the ratio is 12.0900 / 12.0899 units per LU — a spread of 0.001 %.

### Gradient and colour split

`linear-pattern-0`, raw y 868 → 141.6:

| offset | colour | lands on |
|---|---|---|
| 0.0 | `#969E9F` | raw y 868 (below the plot — never visible) |
| 0.616667 | `#AEC1DF` pale blue | raw y 420.05 |
| 0.616667 | `#D89991` coral | raw y 420.05 |
| 1.0 | `#D89991` | raw y 141.60 |

The hard stop lands on raw y **420.05**; the −23 LUFS grid line is at **420.17** — 0.12
units apart, and confirmed in pixels (rendering the reference puts the head→body colour
boundary on device row 3360 = 420.0 raw). So below −23 is the blue body, above −23 the
coral head, and −23 is also the line whose label is drawn in `#FBC648`.

Grid strokes are `#1D1F1F` at 7 % (→ `#EFEFEF` on white); text `#1D1F23` at 84.7 %
(→ `#404145`).

### Footer metric cards (decoded)

| # | caption | value |
|---|---|---|
| 1 | INTEGRATED | −16.0 |
| 2 | LOUDNESS RANGE | 15.9 |
| 3 | INTEGRATED DIAL 0% | – |
| 4 | LOUDNESS RANGE DIAL | – |
| 5 | AVERAGE DYNAMICS (PLR) | 15.9 |
| 6 | MOMENTARY MAX | −5.9 |
| 7 | SHORT-TERM MAX | −10.9 |
| 8 | TRUE PEAK MAX | −0.2 |

## Reworked axis (owner-approved)

`axis_spec.py` is the frozen spec for the reworked chart. Run it to print the table and
14 invariants.

| | |
|---|---|
| range | **−50 LUFS (bottom) … +1 LUFS (top)**, logarithmic |
| formula | `frac(v) = 1 − log10(v+51)/log10(52)`; `0` = top, `1` = bottom |
| labelled ticks | **0, −5, −14, −23, −30** |
| grid-line-only ticks | −3, −7, −10, −16 |
| end points | +1 and −50 (neither labelled) |
| red zone | at or above **−5 LUFS**, tinting the 0 and −5 labels |

Labels were cut from 9 to 5 because the axis cannot fit them: `|y(v2) − y(v1)|` depends
only on `raw(v)`, so flipping the orientation reverses the *order* of the segment heights
but not their sizes. The loud end is compressed either way, and the top segments are
8…53 units against a ~92-unit label. See `axis_orientation.py`.

Accepted consequence: `0…−16` gets **9.5 %** of the plot height, `−30…−50` gets **77.1 %**.

## Page layout (frozen from the owner's sketch)

`layout_spec.py` is the frozen layout, measured from `大致布局.ai` (workspace root). Canvas
1031.81 × 728.504 pt, one layer, 45 elements. Nothing in it is estimated:

| box | x | ytop | size |
|---|---|---|---|
| axis rail (LUFS labels) | 20 – 100 | 60 – 542.13 | 80 × 482.13 |
| plot area (6×6 even grid = placeholder) | 100 – 760 | 60 – 542.13 | 660 × 482.13 |
| metadata card | 800 – 1000 | 60 – 364.5 | 200 × 304.5 |
| loudness-metrics card | 800 – 1000 | 364.5 – 542.13 | 200 × 177.63 |
| time band | 100 – 760 | 560 – 700 | 660 × 140 |
| dynamics card | 800 – 1000 | 560 – 700 | 200 × 140 |

The two right-column cards tile the plot's height exactly (304.5 + 177.63 = 482.13). Content
box 980 × 640; plot → time band 17.87 pt; plot → right column 40 pt.

Reading the sketch's text needed work the stock reporter does not do: the font is a subset of
AdobeSongStd-Light (Type0 / Identity-H) with **no ToUnicode**. Latin codes are Adobe-GB1 CIDs,
and CIDs 1..95 are ASCII 32..126, so `char = chr(cid + 31)`; the 18 CJK CIDs are pinned in
`layout_spec.CJK_CIDS`. The assignment's order is *proven*, not guessed — the `.ai`'s AI11 text
document stores the same two frames as real Unicode
(`'Name\r\rArtist\r\rAlbum-\rName\rTrackNumber'`, `'文件时长\r声道数\r采样率\r位深\r文件大小\r测量算法'`)
and the PDF draws one line per paragraph, in order. `文件` (CIDs 3795, 2161) recurs in
`文件大小`, which cross-checks it.

**What the sketch settles:** `python layout_spec.py` reports that the frozen log axis in this
482.13 pt plot gives labelled gaps `12.59 / 26.57 / 34.01 / 35.10` pt, so a label may be at
most **12.59 pt** tall. The sketch's body font is 14 pt — the `0` and `−5` labels do not fit,
and the `0` line sits 2.4 pt below the top edge. Both are asserted as `KNOWN:` so they cannot
silently drift.

**What the sketch does not settle:** the internal row layout of the 响度指标 / 动态指标 cards.
The only pitch the sketch measures is the metadata card's 24 pt line step, which would fit 6
rows in 177.63 and 4 rows in 140 — that is "it fits", not a design decision.

**The time band is a function, not a constant.** The owner's spec makes its row count depend on
the song (marker labels wrap when they would collide, and the number of DRP patterns varies), so
`time_band_height(marker_rows=, pattern_rows=)` returns `2 + markers + patterns` rows at the
metadata card's borrowed 23.996 pt pitch, floored at the sketch's 140 pt:

| markers | patterns | rows | band | canvas (`grow="canvas"`) | plot (`grow="plot"`) |
|---|---|---|---|---|---|
| 1 | 1 | 4 | 140.00 | 728.50 | 482.13 |
| 2 | 2 | 6 | 143.98 | 732.48 | 478.15 |
| 3 | 2 | 7 | 167.97 | 756.48 | 454.16 |

So the "six text rows" worry is **3.98 pt**, not the 690-vs-142 the older summary quoted — that
number was computed in the *reference chart's* scale and does not transfer. `grow="canvas"` is
the default, so a taller band never re-squeezes the axis.

## Candidate vertical axes

Because the frozen log axis buries the music: on `uploads/POIZON SOUNDS,2088 RECORDS,Wooden -
G R A V I T Y.flac` (227 s) the short-term loudness runs p5 −16.3 / median −8.7 / p95 −5.3, and
that whole band is **7.0 % of the plot height** under the log map.

`axis_options.py` defines six candidate mappings and renders one contact sheet so the choice is
made by looking. All six map +1 LUFS → frac 0 and −50 → frac 1, clamp outside, and are strictly
monotonic, so `axis_spec.frac` swaps out for any of them without touching a renderer.

| | 0..−16 | −30..−50 | music band | `0` below top | min labelled gap | 14 pt |
|---|---|---|---|---|---|---|
| A log (frozen) | 9.5 % | 77.1 % | 7.0 % | 2.4 | 12.59 | no |
| B linear (what the reference does) | 31.4 % | 39.2 % | 21.6 % | 9.4 | 47.27 | ok |
| C power γ=0.7 | 40.0 % | 29.4 % | 23.8 % | 30.8 | 55.81 | ok |
| D piecewise budgets | 38.9 % | 26.0 % | 26.9 % | 19.3 | 57.86 | ok |
| E ticks on an even grid | 60.0 % | 10.0 % | 38.9 % | 48.2 | 48.21 | ok |
| **F knee at −30 (lin above, log below)** | 36.1 % | 30.0 % | 24.8 % | 10.9 | 54.43 | ok |

**F is the owner's choice** (`axis_options.SELECTED_KEY = "knee"`), and so are its final
parameters: **top +0.3 LUFS** (the candidate table above was evaluated at +1), knee −30, upper
segment 70 %. Nothing downstream reads them yet — `axis_spec.py` still holds the pure-log +1 map
until the wiring lands.

Top +0.3 with the ruler labelled only up to `0` is exactly the frozen structure: end points are
never labelled. It does have a measured cost — `axis_options.top_clearance()`:

| top | `0` below the plot's top edge | a 14 pt label needs | verdict |
|---|---|---|---|
| +1 (candidate table) | 10.89 pt | 7.00 pt | clears |
| **+0.3 (chosen)** | **3.34 pt** | 7.00 pt | 3.66 pt short |

That is not a clipping risk: the sketch's canvas has **60 pt of empty margin above the content
box**, so the label simply overhangs — as long as an export does not hard-crop to the content box.

**F's payoff is bigger than the plot shares suggest.** The frozen axis forced the label set
down from nine ticks to five; under F, all nine fit. `label_budget()` runs the greedy
top→bottom placement a renderer actually has to do:

| | ticks that can carry text | tightest gap | plot height needed |
|---|---|---|---|
| A log (frozen) | 5/9 — drops −3, −5, −10, −16 | 18.01 pt | 1300 pt |
| **F knee, top +0.3** | **9/9** | **22.28 pt** | **303 pt** (has 482.13) |

At the frozen axis's own scale a label was 92 units against a 22.5-unit gap; here a 14 pt
label has 22.28 pt of room, so §1.1's five-label concession can be **reversed**.

Candidate A is *literally* `axis_spec.frac`, asserted equal, so the baseline cannot drift from
the frozen spec. Wiring F into the renderer is still open.

### Correction

An earlier revision of this file said the axis top of +1 was "for true peaks above 0 dBTP".
**That was wrong.** The y axis is LUFS (`M`/`S`); true peak is dBTP — a different quantity that
never appears on this axis (it goes in a metric card and in the time axis's clipping band). The
top has nothing to do with the measured `truePeakMax = +4.3`.

## Measurement fixes

Three real bugs in `backend/audio.py`, all found by auditing **units** rather than by
reading the docs. `check_loudness_metrics.py` (22 assertions) pins all three.

| bug | what it did | fix |
|---|---|---|
| **truePeak / samplePeak were linear amplitude, used as dB** | a −21 dBFS master reported `truePeakMax = 0.1 "dBTP"` and `PLR = 21.2` (true PLR 0.0). It also meant the planned *"mark clipping where TruePeak > 0 dB"* rule could **never fire** | `20·log10(value)` via `_to_db()` |
| **LRA read a spurious value** | the `lavfi.r128.LRA` series carries paired bogus `20.000` entries; the last non-zero gave 20.0 LU for a constant sine (true 0.0) | derive from the last frame's `LRA.high − LRA.low` |
| **`sample_peak` was never present** | `ebur128=peak=true` emits only `true_peak`; `peak=sample` emits only `sample_peak` | `peak=sample+true` — both in one pass, so no extra `astats` run |

Also added `summary.dra` = **P95 − P10 of the short-term loudness series** (LU), the
owner-chosen definition of average dynamics. It is ungated, so it is *not* the same
quantity as `lra` (which libebur128 gates per EBU Tech 3342) — both are kept.

`CACHE_VERSION` was bumped to **2** for those fixes. It has **not** been bumped again for
the `S` precision change, because that changes no reported number; bump it when DRP ships.

### Also fixed: `toolchain` probe crashed in restricted environments

`_probe_metaflac()` used `tempfile.TemporaryDirectory(..., ignore_cleanup_errors=True)`.
On Python 3.14 that flag is not enough — cleanup calls `tempfile._resetperms()`, whose
`os.chmod` raises `PermissionError [WinError 5]` *outside* the ignored handler and
propagates out of `__exit__`. Since the probe runs on first toolchain access, one
un-removable temp directory took down everything that measures audio. Now uses `mkdtemp`
with a best-effort `rmtree`.

## DRP (dynamic-range pattern) — current state

Owner's definition: a pattern is a **≥3–5 s** stretch whose `(level, |dS/dt|)` behaviour
repeats elsewhere in the file. Owner's choices: base curve `S`, criterion 1 (both mean
level and mean slope within tolerance), outliers **discarded**, matching by **complete
linkage**.

The detector lives in `backend/drp.py` and is **two-stage**, which is what made it work:

1. **Segment** — walk 5 s windows at a 1 s step and extend the current segment while the
   next window stays within tolerance of the window *immediately before it*. Segments are
   mutually exclusive by construction, and matching locally allows the slow drift a real
   passage has.
2. **Match** — agglomerative complete-linkage clustering of the segments, then keep
   clusters appearing in ≥2 places.

### What the measurements forced

| finding | evidence |
|---|---|
| `S` had to be un-rounded to 2 decimals | at 1 dp a centred difference divides by 0.2 s, making the 0.1 LU step exactly 0.5 LU/s, so `\|dS/dt\|` had only **13 distinct values**. At 2 dp it has 73; a 2 s span has 292. Cache cost **+2.9 %**. |
| derivative needs a **2 s span** | an instantaneous difference is dominated by quantisation even un-rounded |
| **single linkage is unusable** | it chained 15 of 16 windows into one "pattern" spanning 6 LU |
| **clustering windows directly causes overlap** | windows of one passage landed in different groups, so several patterns each claimed the same seconds: **44 % of the file multiply covered, total pattern time 145 % of file length** |
| **two-stage segmentation fixes most of it** | overlap drops to **18.6 %**, coverage to 97.7 %, patterns become 5–17 s long instead of 5 s fragments |

### Current output on `uploads/Cloudier - Set Free.flac` (235.5 s)

10 patterns, coverage 97.7 %, overlap 18.6 %; PMAX `PT_3` (DR 3.57 LU), PMIN `PT_6`
(DR 0.47 LU).

## Files

| file | what it does |
|---|---|
| `axis_spec.py` | **thin shim** over `backend/chart_axis.py` — keeps `python axis_spec.py` → ALL PASS |
| `layout_spec.py` | **thin shim** over `backend/chart_layout.py` + the `.ai` decoder and `--from-ai` cross-check |
| `check_loudness_svg.py` | **SVG renderer regression (59 assertions, no browser needed)** |
| `axis_options.py` | six candidate vertical axes + the contact sheet to choose one by eye |
| `axis_orientation.py` | evidence for why only 5 ticks were labelled |
| `check_loudness_metrics.py` | **regression test for the measurement fix** (truePeak units, DRA, sample peak) |
| `verify_units.py` | audits the UNITS of every ebur128 number; how the truePeak bug was found |
| `verify_measure_doc.py` | checks the AI-written measurement doc's claims against this machine |
| `check_handover_doc.py` | verifies every concrete claim in `响度图重构-交接.md` against the code |
| `probe_drp.py` | DRP feasibility: outlier removal, derivative size, naive segmentation |
| `probe_drp_span.py` | shows how the derivative SPAN is forced by the rounding of S |
| `probe_drp_step.py` | sweeps sliding step/window and reports coverage + overlap + granularity |
| `drp_truth.py` | **DRP 唯一基准**：老板手标素材（`target/`）+ 可分性证据 + 算法对齐（方案 C） |
| `check_loudness_svg.py` | SVG 回归（124 条）：坐标 + 六套主题（颜色硬编码、对比度实算） |
| `cards_store_check.py` | **卡片持久化回归**（21 条）：真的落盘 / 快照改一项不许带走卡片 / 坏文件时必须抛错而不是写空 |
| `svg_raster.py` | minimal SVG path/gradient/glyph rasteriser (importable) |
| `reference_geometry.py` | geometry of the *reference* file, axis table, LUFS ↔ y |
| `check_axis.py` | the reference file's decisive check: labels vs spacing vs colour split |
| `decode_text.py` | the three glyph id→char tables, and every decoded text run |
| `glyph_sheet.py` | renders a font's glyphs enlarged, to establish those tables |
| `render_reference.py` | geometry-only PNG render of the reference (for eyeballing) |
| `measure_reference.py` | measures the rendered reference: envelope extent, colour boundary |
| `find_ink.py` | locates ink blobs in a render (coordinate-space debugging) |

## Usage

```
python check_loudness_metrics.py # regression: units + DRA + sample peak (real ffmpeg)
python verify_units.py           # audit what units ebur128 actually reports
python axis_spec.py              # shim -> backend/chart_axis.py  (ALL PASS)
python layout_spec.py --from-ai  # sketch cross-check + the widening rule
python check_loudness_svg.py     # SVG renderer: 59 coordinate assertions, no browser
python render_svg_preview.py     # render a real file to .cache/_look/loudness.{svg,html}
python axis_options.py           # six candidate axes: table + invariants + contact sheet
python axis_options.py --file uploads/X.flac --sheet .cache/_look/axes.png
python check_axis.py             # is the REFERENCE axis linear, where is its split
python reference_geometry.py     # the reference file's full geometry
python decode_text.py            # every decoded text run in the reference
python glyph_sheet.py glyph-2-   # contact sheet for the numeric font
python render_reference.py 0.075 # writes .cache/_look/cq_faithful.png
python measure_reference.py      # pixel positions from that render
python probe_drp_step.py         # DRP step/window sweep with overlap table
python drp_truth.py              # DRP vs 老板手标素材（基准，方案 C）
python render_svg_preview.py --theme t1 --mode dark   # 主题：六套都看一眼
python cards_store_check.py      # 卡片持久化（cards.json）—— 会丢用户数据的那几条路径
```

## Measured vs assumed

**Measured — safe to rely on.** Canvas size; which space each element lives in; the plot
box; the ten grid-line y positions; the linear 12.090 units/LU mapping; gradient stops
and where each lands in LUFS (cross-checked against a render); the envelope (1943 points,
x 56…3948 raw); the eight footer values; the time-axis step and total duration; the
3.70 : 1 aspect ratio.

**Assumed or not verified.**

1. That the top label is `0` rather than some other value on the same line. The glyph
   reads `0`, but the axis never prints a sign there.
2. That the reference's data is a *synthetic test signal*, not a real song. Its envelope
   only moves between about −10.6 and −51 LUFS with a flat baseline, and no source audio
   ships with the repo. So the chart's **structure** transfers to our renderer, but the
   curve's **shape** cannot be validated against a real measurement.
3. Text *rasterisation*. Content and position are exact (see `decode_text.py`), but
   `render_reference.py` does not draw text: the glyph outlines and their `<use>`
   placement disagree on a scale that cannot be derived from the document, so leaving it
   out is more honest than approximating it. `render_text = False` is deliberate.
4. DRP thresholds. The window/step/tolerance values in `backend/drp.py` were chosen from
   sweeps on **one** file. They are a starting point, not a calibrated standard.
5. The page layout's **component roles**. Every coordinate in `layout_spec.py` is measured,
   but which component each box is comes from the owner, not from the file — those boxes are
   flagged `inferred_role=True`. The 6×6 grid inside the plot is a placeholder, not ticks.
6. **F is chosen but not wired.** `axis_spec.py` (and therefore every renderer, which reads
   `axis_spec`) still returns the pure-log map. `axis_options.SELECTED_KEY` records the
   decision; replacing the spec is a separate, deliberate step.

## What this corrected

The design doc `响度总览图（LoudnessAnalysis）实现构想.md` was written from rasterising
this same file, and three of its key numbers do not survive re-measurement:

* §1.6's axis table (`-13 → -54`, with a 2.9× compression at `-23 ~ -27`) is wrong in
  both range and shape. The real axis is **0 → −54, linear**.
* §1.3's metric cards record LOUDNESS RANGE as 15.7 and MOMENTARY MAX as −5.7; the actual
  values are **15.9** and **−5.9**.
* §1.5 describes the colours as a blue body with a narrow red band on top; the split is a
  single hard gradient stop landing exactly on the **−23 LUFS** line, so the coral head
  covers everything above −23.

`backend/audio.py`'s `AXIS_Y` and `tests/loudness_png_check.py`'s assertions were both
derived from that doc, so both inherited the error. **Both are now retired** (2026-10):
the renderer is `backend/loudness_svg.py`, the axis is `backend/chart_axis.py` (F/knee),
and the layout is `backend/chart_layout.py`.
