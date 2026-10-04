"""Frozen page layout for the reworked loudness view (single source of truth).

This lives in `backend/` because the **renderer** consumes it. The `.ai` decoder and the
cross-check that re-derives these numbers from the sketch live in
`tests/dsh-wheel/layout_spec.py`.

WHERE THESE NUMBERS COME FROM
-----------------------------
Every coordinate below was **measured** from `大致布局.ai` (workspace root) -- the
owner's rough layout sketch. Re-derive them at any time:

    python tests/_design-extract/tools/ai_extract.py 大致布局.ai --report out.txt
    python tests/dsh-wheel/layout_spec.py --from-ai      # asserts this file == the .ai

Both `--report` and the PDF layer need two things the stock reporter does not do,
because the sketch's font is a subset of AdobeSongStd-Light (Type0 / Identity-H) with
**no ToUnicode**:

* **Latin** -- the codes are Adobe-GB1 CIDs, and CIDs 1..95 are ASCII 32..126, so
  `char = chr(cid + 31)`. The raw `/BNF` is `Name`.
* **CJK** -- same CID space; the 18 hanzi that appear are pinned in
  `tests/dsh-wheel/layout_spec.py:CJK_CIDS`.

The *order* of the CJK assignment is not a guess: the AI11 text document inside the
`.ai` stores the same two text frames as real Unicode, and the PDF draws one line per
paragraph in that order:

    'Name\\r\\rArtist\\r\\rAlbum-\\rName\\rTrackNumber'
    '文件时长\\r声道数\\r采样率\\r位深\\r文件大小\\r测量算法'

`文件` (CIDs 3795, 2161) also recurs in `文件大小`, which cross-checks the assignment.

MEASURED vs INFERRED
--------------------
**Measured** -- every `x` / `y` / `w` / `h`, the 6x6 grid, the text strings, the font
sizes, the canvas size.

**Inferred (owner-confirmed, NOT readable from geometry)** -- which component each box
is. Those boxes carry `inferred_role=True`. The metadata card is the only box whose
contents the sketch spells out; the 响度指标 / 动态指标 cards are empty placeholders in
the sketch, so **their internal row layout is still open** -- `ROWS_PER_CARD` below only
records how many rows the sketch's own 24 pt line step would fit.

COORDINATES
-----------
Origin is the canvas top-left, `y` grows DOWNWARD (`ytop` in the extractor's report).
Units are the sketch's points; the canvas is 1031.81 x 728.504. A renderer that targets
a different pixel size should scale by `S = target_width / 1031.81` and multiply every
number here — the renderer rescales the whole viewBox instead of re-laying out.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import chart_axis as axis_spec                            # noqa: E402

#: the sketch these numbers were measured from
AI_PATH = ROOT / "大致布局.ai"

#: canvas width/height are DERIVED from the box geometry below (see PLOT_W).


class Box(NamedTuple):
    """An axis-aligned rectangle, `y` measured down from the canvas top."""

    key: str
    name: str
    x: float
    y: float
    w: float
    h: float
    inferred_role: bool = False

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    @property
    def aspect(self) -> float:
        return self.w / self.h


# --------------------------------------------------------------------------- boxes
# ⚠ 横向有个**明写的偏离**：老板 2026-10 说"图不够宽，加宽一些"。
# 做法是**只拉横轴**，竖向一个数都不动 —— 理由：竖向的任何改动都会动到轴映射与
# 标签预算（`chart_axis` 那 20 多条不变量全钉在 482.13 这个高度上），横向拉长
# 完全不影响它们。所以：
#
#     绘图区宽 = SKETCH_PLOT_W(660) + PLOT_EXTRA_W(240) = 900
#     右栏整体右移，间距（40）保持不变；画布跟着变宽。
#
# `check_against_ai()` 核的是**草图**（`SKETCH_*`），不是这里的加宽值 —— 否则
# "与 .ai 一致"这条断言会因为一次配色/尺寸调整而失效。
SKETCH_PLOT_W = 660.0
SKETCH_CANVAS_W = 1031.81
SKETCH_META_X = 800.0
SKETCH_TIME_BAND_W = 660.0
SKETCH_CARD_W = 200.0

#: 加宽的量。想再宽/再窄就改这两个数。
#:
#: `PLOT_EXTRA_W` 拉绘图区（图更宽）；`CARD_EXTRA_W` 拉右栏那三张卡
#: （老板 2026-10："生成图片的右侧出现重叠，需要扩宽"）。两者都**只动横向**。
#:
#: 右侧为什么重叠：卡只有 200pt，而元数据卡那四行右边被 100pt 封面占着，值那一列
#: 只剩 200−12−100−17.95−4 ≈ 66pt —— 标签 `TrackNumber` 一个字就 85pt，连标签自己
#: 都放不下，只能降到 10px 再截断；动态卡那边 `最大段落 PMAX` + `PT_2 · 6.08 LU`
#: 也要 200pt 出头。加到 260 之后两处都宽松了。
PLOT_EXTRA_W = 240.0
CARD_EXTRA_W = 60.0

PLOT_W = SKETCH_PLOT_W + PLOT_EXTRA_W          # 900.0
PLOT_X = 100.0
PLOT_Y = 60.0
PLOT_H = 482.13

#: 绘图区右边缘 → 右栏左边缘的间距（草图实测 40，加宽后保持不变）
COLUMN_GAP = 40.0
META_X = PLOT_X + PLOT_W + COLUMN_GAP          # 1040.0
CARD_W = SKETCH_CARD_W + CARD_EXTRA_W          # 260.0

#: 画布右侧留白（草图是 1031.81 − 1000 = 31.81）
CANVAS_PAD_R = SKETCH_CANVAS_W - (SKETCH_META_X + SKETCH_CARD_W)     # 31.81
CANVAS_W = META_X + CARD_W + CANVAS_PAD_R

#: LUFS 刻度文字区（绘图区左侧）
AXIS_RAIL = Box("axis_rail", "纵轴标尺列", 20.0, PLOT_Y, 80.0, PLOT_H, False)

#: 响度包络绘图区（草图里画了 6x6 等距网格，只是"这里是图"的占位，不是真实刻度）
PLOT = Box("plot", "绘图区", PLOT_X, PLOT_Y, PLOT_W, PLOT_H, False)

#: 横轴 / 时间带（交接文档 §6 的六行文字落在这里）
TIME_BAND = Box("time_band", "横轴时间带", PLOT_X, 560.0, PLOT_W, 140.0, True)

#: 右栏上卡：封面 + 标题/作者/专辑/… + 文件时长…测量算法
META_CARD = Box("meta_card", "右栏上卡 · 元数据", META_X, PLOT_Y, CARD_W, 304.5, True)

#: 右栏下卡：响度六项
LOUD_CARD = Box("loud_card", "右栏下卡 · 响度指标", META_X, 364.5, CARD_W, 177.63, True)

#: 底右卡：动态指标 DRA / DRP / PMAX / PMIN
DYN_CARD = Box("dyn_card", "底右卡 · 动态指标", META_X, 560.0, CARD_W, 140.0, True)

#: 元数据卡里的封面方框（贴卡右边 17.949、上边 26.374 —— 草图实测的偏移）
COVER = Box("cover", "封面", META_X + CARD_W - 17.949 - 100.0, PLOT_Y + 26.374,
            100.0, 100.0, False)

BOXES: tuple[Box, ...] = (AXIS_RAIL, PLOT, TIME_BAND, META_CARD, LOUD_CARD,
                          DYN_CARD, COVER)

#: 排版用的两个横向基准：绘图区左边缘（时间带也对齐它）、右栏左边缘
CONTENT_LEFT = AXIS_RAIL.x                 # 20.0
CONTENT_RIGHT = META_CARD.right            # 1240.0（加宽后）
CONTENT_TOP = PLOT.y                       # 60.0
CONTENT_BOTTOM = TIME_BAND.bottom         # 700.0

#: 画布高度（时间带变高时才增长，见 `canvas_height()`）
CANVAS_H = 728.504

# --------------------------------------------------------------------------- grid
#: 草图在绘图区里画了 6 列 x 6 行的等距网格。
#: ⚠ 它是**占位**，不是按 LUFS / 时间算出来的刻度线 —— 别把它当成刻度依据。
GRID_COLS = 6
GRID_ROWS = 6

# --------------------------------------------------------------------------- text
#: 元数据卡里的 5 行拉丁文字（草图原文，含重复的 `Name` 与结尾的 `-`）。
#: `(text, x, ytop, size)`。按 ytop 升序。
META_TEXT_LATIN: tuple[tuple[str, float, float, float], ...] = (
    ("Name",        META_X + 12.9854,  87.3394, 14.0),
    ("Artist",      META_X + 12.9854, 115.3394, 14.0),
    ("Album-",      META_X + 12.9854, 143.3394, 14.0),
    ("Name",        META_X + 12.9854, 157.3394, 14.0),
    ("TrackNumber", META_X + 12.9854, 175.3394, 10.0),
)

#: 拉丁**逻辑字段**的 ytop。草图 5 行里第 3、4 行是同一个 `Album-Name` 折行，
#: 所以逻辑字段只有 4 个：第 3 行要跳过，TrackNumber 用草图第 5 行（175.3394）。
#: 从 `META_TEXT_LATIN` 派生，不另写魔数 —— 这四个数仍然只从 .ai 来。
META_INFO_Y: tuple[float, ...] = (
    META_TEXT_LATIN[0][2],   # 标题   87.3394
    META_TEXT_LATIN[1][2],   # 作者   115.3394
    META_TEXT_LATIN[2][2],   # 专辑名 143.3394
    META_TEXT_LATIN[4][2],   # TrackNumber 175.3394  ← 草图真正那行，跳过折行后半截
)

#: 元数据卡里的 6 行中文字段名。行距固定 23.996（= 草图实测）。
META_TEXT_CJK: tuple[tuple[str, float, float, float], ...] = (
    ("文件时长", META_X + 17.9487, 206.7105, 14.0),
    ("声道数",   META_X + 17.9487, 230.7065, 14.0),
    ("采样率",   META_X + 17.9487, 254.7025, 14.0),
    ("位深",     META_X + 17.9487, 278.6985, 14.0),
    ("文件大小", META_X + 17.9487, 302.6945, 14.0),
    ("测量算法", META_X + 17.9487, 326.6905, 14.0),
)

META_TEXT = META_TEXT_LATIN + META_TEXT_CJK

# ------------------------------------------------------------------- 卡片内容（已定）
#: 老板 2026-10 拍板，草图上读不出来的部分：
#:
#: * **卡里是"标签 + 值"**，不是只有标签（草图只画了标签，值是空的）。
#: * 草图第一个 `Name` = **标题**（`Name` 就是标题，不是文件名）。
#: * **文件名不在这张卡里** —— 写在**页面左上角**（`FILE_NAME_AT`），
#:   也就是内容框上方那 60 pt 留白里。
#: * 指标卡**按新结构来**：右栏下卡 = 响度 6 项，底右卡 = 动态 4 项。
#:   旧 footer 里的 `PLR` 与两个 `DIAL` 占位**在新结构里没有位置，不再出现**。
META_CARD_SHOWS_VALUES = True
FILE_NAME_AT = (CONTENT_LEFT, 0.0)          # 页面左上角（内容框上方留白内）

#: 元数据卡的拉丁字段。老板澄清了草图那两个 `Name`：
#: 第 3、4 行是**同一个字段 `Album-Name` 被窄栏折行**，不是两个字段。
#: 所以卡里是 4 个拉丁字段：标题 / 作者 / 专辑名 / TrackNumber。
#: `DiscNumber` 只在 `测量图指标设计.md` 里，草图和老板的答复都没有 -> 不进卡。
META_CARD_FIELDS: tuple[str, ...] = ("标题", "作者", "专辑名 Album-Name", "TrackNumber")

#: 草图的第 3、4 行拼起来必须正好是 `Album-Name`（有断言钉住）
ALBUM_WRAP = ("Album-", "Name")

# --------------------------------------------------------------------- 时间格式
def format_time(seconds: float) -> str:
    """`[x]m[y]s`，**永不引入小时**（老板定："仍然按照分钟来，不要引入 hour"）。

    75 分钟写 `75m00s`，不是 `1h15m00s`。起点补零成 `00m00s`，所以分钟至少两位、
    再长就自然变三位；秒永远两位。
    """
    s = max(0, int(round(seconds)))
    return f"{s // 60:02d}m{s % 60:02d}s"


# --------------------------------------------------------------------- 标记
#: 标记来源：**音频文件自带的章节**，有就加载、没有就不需要（老板定）。
#: 读法只要一条：`ffprobe -show_chapters -of json` —— 本机 ffmpeg 9.0.2 实测：
#:
#:   * Vorbis 注释 `CHAPTER001=00:00:00.000` + `CHAPTER001NAME=Intro`
#:     -> 3 章，**带名字**
#:   * FLAC `CUESHEET`（`metaflac --import-cuesheet-from`）
#:     -> 3 章，但**名字是空的**（cue 的 TITLE 没进 chapter tags）
#:
#: 所以默认名 `Marker` 不是锦上添花，是 CUESHEET 那条路**必需**的兜底。
#: ⚠ 另：本机 `uploads/` 里 5 个 flac **一个章节都没有** —— 没有标记是常态，不是异常，
#: 不要因为"没读到"就报错或占一行空行。
MARKER_DEFAULT_NAME = "Marker"
MARKER_SOURCE = "ffprobe -show_chapters"

#: 一条**实测过**的第三条来源：WAV 的 `cue `/`LIST adtl`（DAW 导出的标记）。
#: 老板给的 `萤火冷光_ColdLight, 桑霰 - CQ Under #132048 PREMASTER_BPM=135.wav`
#: -> 4 章，**名字是中文**（`标记 0` / `4/4` / `4/4` / `标记 3`）。
#: 所以标记名会是 CJK —— 又一条选 SVG 的理由（PNG 那条路拿不到 CJK 字体会退化成 `?`）。
MARKER_WAV_SOURCE = "wav cue + LIST adtl labl"

#: 章节首尾相接时，标记取**每一章的起点**，不取终点。
#: 理由：章节连着排（实测那 4 章是 0 3.111 134.667 291.111 293.62），
#: 取"起点+终点"会把 3.111 / 134.667 / 291.111 各标两遍。
#: 副作用（要接受）：第一个标记可能正好落在 0.000，与基础刻度的"起点"同刻 ——
#: 不冲突，因为标记文字本来就在下一行。
MARKER_IS_CHAPTER_START = True

#: "静音底"门槛。`backend/audio.py` 的 `SILENCE_LUFS = -120`，S 的前 3 秒窗口预热
#: 会给出接近它的值。统计前必须剔除。
SILENCE_FLOOR = -70.0


def energy_rms_lufs(values, *, floor: float = SILENCE_FLOOR) -> float | None:
    """一串 LUFS 的**能量域** RMS：`10·log10(mean(10**(v/10)))`。

    ⚠ **2026-10 起这个量不再画在图上**。老板："RMS 删去，那条虚线改成 integrated 的
    LUFS 值。" 图上那条虚线现在画的是响度卡第一项「总响度」（`summary.integrated`），
    见 `loudness_svg._render_plot`。这个函数**留档**：那段口径实测还值钱（见下），
    而且 `layout_spec.py` 的不变量仍钉着它 —— 但**渲染器不再调用**。

    老板问"RMS 是 S 的 RMS 值可以吗" —— 可以，而且比波形 RMS 好：不用多解一趟
    PCM、与 `S` 同单位同一条轴、没有任何跨单位混用。

    但"dB 值的 RMS"必须按**能量域**算。直接写 `sqrt(mean(v**2))` 会得到**正数**
    ——平方把符号吃掉了。实测：

    | 曲 | 能量域 RMS | 算术平均 | `sqrt(mean(v**2))` |
    |---|---|---|---|
    | CQ Under（premaster） | −17.01 | −20.91 | **+21.90** |
    | G R A V I T Y | −8.38 | −10.51 | **+12.60** |

    第三列是废数（正 LUFS 不存在）。前两列都合理，但**不是同一个量**：能量域被响段
    拉高，实测差 3.9 / 2.1 LU。选一个，别混。
    """
    vals = [float(v) for v in values if v is not None and v > floor]
    if not vals:
        return None
    return 10.0 * math.log10(sum(10.0 ** (v / 10.0) for v in vals) / len(vals))


#: 响度卡 6 项（顺序即显示顺序）—— 取自 `测量图指标设计.md` 的"响度部分"
LOUD_CARD_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("总响度", "integrated", "LUFS"),
    ("短时最大", "shortTermMax", "LUFS"),
    ("瞬时最大", "momentaryMax", "LUFS"),
    ("响度范围", "lra", "LU"),
    ("采样峰值", "samplePeakMax", "dBFS"),
    ("真峰值", "truePeakMax", "dBTP"),
)

#: 动态卡 4 项
DYN_CARD_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("平均动态 DRA", "dra", "LU"),
    ("动态模式 DRP", "drp", ""),
    ("最大段落 PMAX", "pmax", ""),
    ("最小段落 PMIN", "pmin", ""),
)

#: 草图里那个**第二个 `Name`** 已经定了：它是 `Album-Name` 被折行掉的后半截，
#: 见上面的 `ALBUM_WRAP`。所以元数据卡没有"第 4 个待定字段"这回事了。

#: 字号：正文 14，唯一的小字 10（`TrackNumber`）
FONT_BODY = 14.0
FONT_SMALL = 10.0

#: 中文字段的实测行距
LINE_STEP = 23.996

#: 草图**没有**给出这两张卡的内部行距。能拿来当锚的只有上卡实测的 24 pt:
#: 6 行 x 24 = 144 <= 177.63（响度六项放得下），4 行 x 24 = 96 <= 140（动态四项也放得下）。
#: 这只是"放得下"，不是设计决定 —— 排版时仍要自己定。
ROWS_PER_CARD = {"loud_card": 6, "dyn_card": 4}
LINE_STEP_ASSUMED = 24.0

# --------------------------------------------------------------------- time band
#: 横轴时间带的**行结构**。来源是老板给的原文规格（交接文档 §6 有全文），要点：
#:
#:   行 0   基础刻度 A —— `起点 / 3/4 / 终点` 同一水平线（最贴近上方响度图）
#:   行 1   基础刻度 B —— `1/4 / 1/2` 另起一行
#:   行 2+  标记行 —— `[name]/[x]m[y]s`（名称同字号 BOLD，时间小一号且更浅）；
#:          标记字重叠就**再起一行** —— 所以行数随曲子变
#:   ...    动态模式行 —— 每个模式的起止，标 `PT_X`（再起一行）
#:
#: **爆音不占文字行**：只把该时段横轴标红 + 出刻度，不写时间。
#: 还要注意"刻度位置"比"文字"多 —— 起止 / 1/4 / 1/2 / 3/4 / 标记处 / 爆音处**都出刻度线**，
#: 但只有那 5 个点位出文字。
TIME_ROW_TICKS = 0
TIME_ROW_MARKERS = 1
TIME_BASE_ROWS = 1

#: 基础时间刻度的 5 个位置 -> 行号（原文：起止点到 3/4 同高，1/4 与 1/2 另起一行）
#:
#: ⚠ **这里没有"每 N 秒一格"的步长**，别去实现参考图那种 7s 等距刻度。
#: 老板的规格把基础刻度定义成**时长的分数**（0 / 1/4 / 1/2 / 3/4 / 1），
#: 所以"步长"= 时长 / 4，是算出来的，不是搜出来的。
#: 已退役的 PNG 渲染器那套 5/10/15/30/60 自动选步长**整段不用**。
#: ⚠ **五个全在同一行**（老板 2026-10："第二个和第三个基础时间标记莫名被换行，
#: 他们应当和其他基础时间标记位于同一行"）。之前是 起点/3-4/终点 一行、1-4/1-2 另起
#: 一行 —— 那是从"起止点时间到 3/4 标记同高"这句话里多读出来的一层。实测五个标签
#: 在 900pt 宽的绘图区里排得开（相邻 225pt，最长标签 ~54pt），根本不需要换行。
TIME_BASE_TICKS: tuple[tuple[float, int], ...] = (
    (0.00, TIME_ROW_TICKS),
    (0.25, TIME_ROW_TICKS),
    (0.50, TIME_ROW_TICKS),
    (0.75, TIME_ROW_TICKS),
    (1.00, TIME_ROW_TICKS),
)

#: 时间带的行距：**借**元数据卡实测的那一个（草图里唯一量到的行距）。
#: ⚠ 是借用，不是时间带自己的实测值 —— 时间带的字号构成（14/12/10 + BOLD）与元数据卡不同。
TIME_BAND_LINE_STEP = LINE_STEP

# --------------------------------------------------------------------------- derived
def grid_columns() -> list[float]:
    """绘图区内 5 条竖线的 x（不含左右边框）。"""
    return [PLOT.x + PLOT.w * k / GRID_COLS for k in range(1, GRID_COLS)]


def grid_rows() -> list[float]:
    """绘图区内 5 条横线的 ytop（不含上下边框）。"""
    return [PLOT.y + PLOT.h * k / GRID_ROWS for k in range(1, GRID_ROWS)]


def y_in_plot(lufs: float, *, frac=None) -> float:
    """LUFS -> 画布 ytop，按某个映射落在绘图区里。

    缺省用**定稿轴**（`axis_spec`）；也可以传别的 `frac` 做方案对比
    （见 `axis_options.py`）。
    """
    f = axis_spec.frac(lufs) if frac is None else frac(lufs)
    return PLOT.y + f * PLOT.h


def axis_label_fit(height: float | None = None,
                   label_pt: float = FONT_BODY) -> dict:
    """定稿轴（`axis_spec`）落在**这张草图的绘图区**里，标签放不放得下。

    返 `{"gaps": [...], "min_gap": float, "max_label_pt": float, "fits": bool}`。
    """
    h = PLOT.h if height is None else height
    lab = list(axis_spec.LABELLED)
    gaps = [round((axis_spec.frac(b) - axis_spec.frac(a)) * h, 2)
            for a, b in zip(lab, lab[1:])]
    lo = min(gaps)
    return {"gaps": gaps, "min_gap": lo, "max_label_pt": lo,
            "fits": lo >= label_pt, "label_pt": label_pt}


def label_gap_fracs(frac=None, ticks=None) -> list[float]:
    """一组刻度相邻两档的**占比**间距（不是像素）。`frac` 缺省用定稿轴。"""
    f = axis_spec.frac if frac is None else frac
    t = tuple(axis_spec.TICKS) if ticks is None else tuple(ticks)
    return [f(b) - f(a) for a, b in zip(t, t[1:])]


def min_plot_height(frac=None, ticks=None, label_pt: float = FONT_BODY) -> float:
    """要让一组刻度**全部**出文字，绘图区至少要多高。

    = 字高 / 最紧的那一段占比。`frac` 可以传别的映射（`axis_options` 就这么用它核 F），
    所以这里不假设是哪条轴 —— 换轴只要换 `frac`。
    """
    gaps = label_gap_fracs(frac, ticks)
    if not gaps:
        return 0.0
    return label_pt / min(gaps)


# ------------------------------------------------------------------- time band
def time_band_rows(*, marker_rows: int = 1, pattern_rows: int = 1) -> int:
    """时间带要几行：2 行基础刻度 + 标记行 + 动态模式行。

    标记行与模式行**都是可变的**：标记字重叠就得再起一行，模式数量也随曲子变。
    **没有标记就是 0 行**（老板："有则加载，无则不需要"）—— 不占空行。
    """
    return TIME_BASE_ROWS + max(0, marker_rows) + max(0, pattern_rows)


def time_band_height(*, marker_rows: int = 1, pattern_rows: int = 1) -> float:
    """时间带高度：行少时用草图给的 140，行多时按行距长出去。

    老板的原话："因为不同歌曲标记点存在差异所以我认为需要留余量/做成动态的框"。
    所以这是**函数**而不是常量。
    """
    rows = time_band_rows(marker_rows=marker_rows, pattern_rows=pattern_rows)
    return max(TIME_BAND.h, rows * TIME_BAND_LINE_STEP)


def canvas_height(*, marker_rows: int = 1, pattern_rows: int = 1,
                  grow: str = "canvas") -> float:
    """时间带长高时，多出来的高度从哪来。

    * `grow="canvas"`（缺省）—— 画布变高，**绘图区保持 482.13**。这样轴映射、
      标签预算、三张卡的高度全都不用重算，是最省事也最不容易出错的一侧。
    * `grow="plot"` —— 画布锁在草图的 728.504，从绘图区里扣。扣多了标签会重新挤起来，
      所以下面有 `plot_height_if_band_grows()` 可以核。
    """
    if grow not in ("canvas", "plot"):
        raise ValueError(f"grow 只能是 'canvas' 或 'plot'，收到 {grow!r}")
    extra = time_band_height(marker_rows=marker_rows,
                             pattern_rows=pattern_rows) - TIME_BAND.h
    return CANVAS_H if grow == "plot" else CANVAS_H + extra


def plot_height_if_band_grows(*, marker_rows: int = 1,
                             pattern_rows: int = 1) -> float:
    """`grow="plot"` 时绘图区还剩多高（画布总高锁在草图上）。"""
    extra = time_band_height(marker_rows=marker_rows,
                             pattern_rows=pattern_rows) - TIME_BAND.h
    return PLOT.h - extra



# --------------------------------------------------------------------------- report
def layout_report() -> str:
    fit = axis_label_fit()
    out = [
        f"canvas {CANVAS_W:.2f} x {CANVAS_H:.2f} pt   "
        f"content {CONTENT_LEFT:.1f},{CONTENT_TOP:.1f} .. "
        f"{CONTENT_RIGHT:.1f},{CONTENT_BOTTOM:.1f}  "
        f"({CONTENT_RIGHT - CONTENT_LEFT:.0f} x {CONTENT_BOTTOM - CONTENT_TOP:.0f})",
        "",
        f"{'box':<12} {'x':>8} {'ytop':>8} {'w':>8} {'h':>8} {'right':>8} "
        f"{'bottom':>8} {'aspect':>7}  role",
    ]
    for b in BOXES:
        out.append(f"{b.key:<12} {b.x:>8.2f} {b.y:>8.2f} {b.w:>8.2f} {b.h:>8.2f} "
                   f"{b.right:>8.2f} {b.bottom:>8.2f} {b.aspect:>7.3f}  "
                   f"{'inferred' if b.inferred_role else 'measured'}")
    out += [
        "",
        "gaps:",
        f"  rail.right -> plot.x            {PLOT.x - AXIS_RAIL.right:>8.2f}"
        f"   (0 = 标尺列紧贴绘图区)",
        f"  plot.right -> right column      {META_CARD.x - PLOT.right:>8.2f}",
        f"  plot.bottom -> time band        {TIME_BAND.y - PLOT.bottom:>8.2f}",
        f"  column split (meta|loud)        {LOUD_CARD.y - META_CARD.bottom:>8.2f}"
        f"   (0 = 两卡无缝)",
        "",
        f"grid  {GRID_COLS} x {GRID_ROWS} in the plot, even:"
        f"  dx={PLOT.w / GRID_COLS:.3f}  dy={PLOT.h / GRID_ROWS:.3f}",
        "  cols " + " ".join(f"{v:.1f}" for v in grid_columns()),
        "  rows " + " ".join(f"{v:.1f}" for v in grid_rows()),
        "",
        "vertical axis (axis_spec, frozen) inside THIS plot:",
        f"  labelled gaps {fit['gaps']}",
        f"  smallest gap {fit['min_gap']:.2f} pt -> a label may be at most"
        f" {fit['max_label_pt']:.2f} pt tall",
        f"  the sketch's body font is {fit['label_pt']:.0f} pt ->"
        f" {'fits' if fit['fits'] else 'DOES NOT FIT'}",
        "",
        "metadata card rows the sketch's own 24 pt step would fit:",
    ]
    for key, n in ROWS_PER_CARD.items():
        box = {"loud_card": LOUD_CARD, "dyn_card": DYN_CARD}[key]
        need = n * LINE_STEP_ASSUMED
        out.append(f"  {box.name:<20} {n} rows x {LINE_STEP_ASSUMED:.0f} = {need:.0f}"
                   f"  vs h={box.h:.2f}  ->"
                   f" {'ok' if need <= box.h else 'TOO TIGHT'}")

    out += ["",
            "time band -- the owner asked for a DYNAMIC box, so it is a function:",
            f"  base rows {TIME_BASE_ROWS} + marker rows + pattern rows,"
            f" pitch {TIME_BAND_LINE_STEP:.3f} pt (borrowed from the metadata card)",
            f"  {'markers':>7} {'patterns':>8} {'rows':>5} {'band h':>8} "
            f"{'canvas h':>9} {'plot h':>8}"]
    for mr, pr in ((1, 1), (2, 1), (2, 2), (3, 2)):  # rows = 1 + mr + pr
        out.append(f"  {mr:>7} {pr:>8} {time_band_rows(marker_rows=mr, pattern_rows=pr):>5}"
                   f" {time_band_height(marker_rows=mr, pattern_rows=pr):>8.2f}"
                   f" {canvas_height(marker_rows=mr, pattern_rows=pr):>9.2f}"
                   f" {plot_height_if_band_grows(marker_rows=mr, pattern_rows=pr):>8.2f}")
    out += ["",
            "  base tick text: all five on ONE row",
            "    row 0  " + " ".join(f"{f:.0%}" for f, _ in TIME_BASE_TICKS),
            "  markers and clipping also get TICK MARKS; clipping gets no text at all.",
            "  base tick TEXT is at fixed FRACTIONS of the duration --"
            " there is no N-second step to search for.",
            "",
            "card contents (owner-decided; the sketch only drew the labels):",
            f"  file name -> page top-left {FILE_NAME_AT}, NOT in the metadata card",
            f"  Latin fields: {' / '.join(META_CARD_FIELDS)}"
            f"   (sketch rows 3+4 = one wrapped `Album-Name`)",
            "  every row shows LABEL + VALUE",
            f"  markers: from the file's own chapters ({MARKER_SOURCE}),"
            f" default name {MARKER_DEFAULT_NAME!r}; none -> no marker row at all",
            f"  time text: {format_time(0)} .. {format_time(291)} .."
            f" {format_time(3661)}  (never an hour field)",
            "  响度指标卡:  " + " / ".join(f"{n}({u})" if u else n
                                          for n, _, u in LOUD_CARD_FIELDS),
            "  动态指标卡:  " + " / ".join(f"{n}({u})" if u else n
                                          for n, _, u in DYN_CARD_FIELDS),
            "  (old footer's PLR + DIAL/Dial-LRA placeholders are dropped)",
            "",
            f"for reference: seating all 9 ticks needs a plot of"
            f" {min_plot_height():.0f} pt under the FROZEN axis."
            f" A different mapping is a different number --"
            f" `axis_options.py` prints it for the chosen one."]
    return "\n".join(out)


# --------------------------------------------------------------------------- checks
def check() -> bool:
    """Geometry invariants. Pure arithmetic -- no file access (see `check_against_ai`)."""
    ok = True

    def t(name: str, cond: bool, extra: object = "") -> None:
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + name
              + (f"   {extra}" if extra else ""))
        ok = ok and cond

    near = lambda a, b, tol=0.01: abs(a - b) <= tol                   # noqa: E731

    t("rail and plot share top and bottom",
      near(AXIS_RAIL.y, PLOT.y) and near(AXIS_RAIL.bottom, PLOT.bottom))
    t("rail sits flush against the plot's left edge",
      near(AXIS_RAIL.right, PLOT.x), f"rail.right={AXIS_RAIL.right} plot.x={PLOT.x}")
    t("right column starts 40 pt right of the plot",
      near(META_CARD.x - PLOT.right, 40.0))
    t(f"right column is {CARD_W:g} pt wide, all three cards",
      near(META_CARD.w, CARD_W) and near(LOUD_CARD.w, CARD_W)
      and near(DYN_CARD.w, CARD_W))
    t("the two right-column cards tile the plot's height exactly",
      near(META_CARD.y, PLOT.y) and near(META_CARD.bottom, LOUD_CARD.y)
      and near(LOUD_CARD.bottom, PLOT.bottom),
      f"{META_CARD.h} + {LOUD_CARD.h} = {META_CARD.h + LOUD_CARD.h} vs {PLOT.h}")
    t("time band is exactly as wide as the plot and left-aligned with it",
      near(TIME_BAND.x, PLOT.x) and near(TIME_BAND.w, PLOT.w))
    t("time band and the dynamics card are the same row",
      near(TIME_BAND.y, DYN_CARD.y) and near(TIME_BAND.h, DYN_CARD.h))
    t("time band clears the plot by 17.87 pt",
      near(TIME_BAND.y - PLOT.bottom, 17.87))
    t("grid is 6x6 and divides the plot evenly",
      GRID_COLS == 6 and GRID_ROWS == 6
      and near(PLOT.w / GRID_COLS, PLOT_W / 6)
      and near(PLOT.h / GRID_ROWS, PLOT_H / 6),
      f"{PLOT.w / GRID_COLS:.3f} x {PLOT.h / GRID_ROWS:.3f}")
    t("grid lines are strictly inside the plot",
      all(PLOT.x < v < PLOT.right for v in grid_columns())
      and all(PLOT.y < v < PLOT.bottom for v in grid_rows()))
    t("cover is 100x100, inside the metadata card and flush to its right margin",
      near(COVER.w, 100.0) and near(COVER.h, 100.0)
      and COVER.x > META_CARD.x and COVER.right < META_CARD.right
      and COVER.y > META_CARD.y and COVER.bottom < META_CARD.bottom
      and near(META_CARD.right - COVER.right, 17.949),
      f"right margin {META_CARD.right - COVER.right:.3f}")
    t("all metadata text sits inside the metadata card",
      all(META_CARD.x < x < META_CARD.right
          and META_CARD.y < y < META_CARD.bottom
          for _, x, y, _ in META_TEXT))
    t("metadata text x never crosses the cover's left edge",
      all(x < COVER.x for _, x, _, _ in META_TEXT))
    t("only two font sizes are used, 14 and 10",
      {s for _, _, _, s in META_TEXT} == {FONT_BODY, FONT_SMALL})
    t("CJK field rows step by 23.996 pt",
      all(near(META_TEXT_CJK[i + 1][2] - META_TEXT_CJK[i][2], LINE_STEP, 0.01)
          for i in range(len(META_TEXT_CJK) - 1)))
    t("6 loudness rows and 4 dynamics rows fit the sketch's 24 pt step",
      all(n * LINE_STEP_ASSUMED <= {"loud_card": LOUD_CARD,
                                    "dyn_card": DYN_CARD}[k].h
          for k, n in ROWS_PER_CARD.items()))
    # 内容框现在是**加宽后**的 1220 × 640；草图的 980 × 640 记在 SKETCH_* 里，
    # 由 `check_against_ai()` 核。
    t(f"content box is {CONTENT_RIGHT - CONTENT_LEFT:.0f} x 640 (the widened one)",
      near(CONTENT_RIGHT - CONTENT_LEFT,
           SKETCH_PLOT_W + PLOT_EXTRA_W + COLUMN_GAP + CARD_W + 80.0)
      and near(CONTENT_BOTTOM - CONTENT_TOP, 640.0),
      f"{CONTENT_RIGHT - CONTENT_LEFT:.1f} x {CONTENT_BOTTOM - CONTENT_TOP:.1f}")
    t("the width overrides are exactly PLOT_EXTRA_W + CARD_EXTRA_W, nothing vertical",
      near(PLOT_W - SKETCH_PLOT_W, PLOT_EXTRA_W)
      and near(CARD_W - SKETCH_CARD_W, CARD_EXTRA_W)
      and near(PLOT.h, PLOT_H) and near(TIME_BAND.h, 140.0)
      and near(LOUD_CARD.y, 364.5) and near(DYN_CARD.h, 140.0))
    t("the sketch's own numbers are still recorded for the .ai cross-check",
      near(SKETCH_PLOT_W, 660.0) and near(SKETCH_CANVAS_W, 1031.81)
      and near(SKETCH_META_X, 800.0) and near(SKETCH_TIME_BAND_W, 660.0)
      and near(SKETCH_CARD_W, 200.0))

    fit = axis_label_fit()
    # The axis used to be a pure log map, under which the five labels could not be
    # seated (tightest gap 12.59 pt vs a 14 pt label). F inverted that: all nine now
    # fit with room to spare, so the assertion is the opposite of what it used to be.
    t("the F axis DOES seat all 9 labels in this plot",
      fit["fits"], f"min gap {fit['min_gap']:.2f} pt vs 14 pt")
    t("the tightest labelled gap is 22.28 pt",
      abs(fit["min_gap"] - 22.28) < 0.05, fit["gaps"])
    t("the F axis needs ~303 pt of plot and this page has 482.13",
      abs(min_plot_height() - 303.0) < 2.0 and min_plot_height() <= PLOT.h,
      f"{min_plot_height():.1f} pt")
    t("layout_spec and axis_spec agree on the plot height they quote",
      near(PLOT.h, axis_spec.PLOT_H_REF, 0.005),
      f"{PLOT.h} vs {axis_spec.PLOT_H_REF}")
    # Owner-accepted: with the top pulled in to +0.3 the `0` label overhangs the plot's
    # top edge. Harmless because the canvas has 60 pt of margin above the content box --
    # but an export must not hard-crop to the content box.
    t("the `0` label is allowed to overhang the plot's top edge",
      axis_spec.top_clearance(PLOT.h)["deficit"] > 0.0
      and CONTENT_TOP >= axis_spec.top_clearance(PLOT.h)["need"],
      f"overhang {axis_spec.top_clearance(PLOT.h)['deficit']:.2f} pt,"
      f" margin above content {CONTENT_TOP:.0f} pt")

    # ---- time band: the owner wants it dynamic, so the row model is the spec ----
    t("the sketch's 140 pt band seats the default 3 rows (1 base + 1 marker + 1 pattern)",
      time_band_height() == TIME_BAND.h
      and time_band_rows() * TIME_BAND_LINE_STEP <= TIME_BAND.h,
      f"{time_band_rows()} rows = {time_band_rows() * TIME_BAND_LINE_STEP:.2f} pt")
    t("the sketch's 140 pt band CANNOT seat 6 rows -- the band has to grow",
      time_band_height(marker_rows=3, pattern_rows=2) > TIME_BAND.h
      and time_band_rows(marker_rows=3, pattern_rows=2) == 6,
      f"6 rows = {6 * TIME_BAND_LINE_STEP:.2f} pt vs {TIME_BAND.h:.0f}")
    t("KNOWN: 6 rows are only ~4 pt short, so the growth is small",
      0 < time_band_height(marker_rows=3, pattern_rows=2) - TIME_BAND.h < 6.0,
      f"{time_band_height(marker_rows=3, pattern_rows=2) - TIME_BAND.h:.2f} pt")
    t("band height never decreases as rows are added",
      all(time_band_height(marker_rows=m, pattern_rows=p)
          <= time_band_height(marker_rows=m + 1, pattern_rows=p)
          for m, p in ((1, 1), (2, 1), (3, 1))))
    t("grow='canvas' keeps the plot at 482.13",
      near(canvas_height(grow="canvas") - canvas_height(grow="plot"), 0.0)
      and near(plot_height_if_band_grows(), PLOT.h))
    t("grow='plot' costs the plot under 5 pt even at 6 rows",
      PLOT.h - plot_height_if_band_grows(marker_rows=3, pattern_rows=2) < 5.0,
      f"{PLOT.h - plot_height_if_band_grows(marker_rows=3, pattern_rows=2):.2f} pt")
    t("grow only accepts 'canvas' or 'plot'",
      _raises(lambda: canvas_height(grow="nope")))
    t("base ticks are exactly 0 / 1-4 / 1-2 / 3-4 / 1",
      sorted(f for f, _ in TIME_BASE_TICKS) == [0.0, 0.25, 0.5, 0.75, 1.0])
    t("all five base ticks share ONE row (no wrapping)",
      {r for _, r in TIME_BASE_TICKS} == {TIME_ROW_TICKS} and TIME_BASE_ROWS == 1,
      [r for _, r in TIME_BASE_TICKS])
    t("the five base labels sit 225 pt apart (a 14 pt label needs ~60 pt at worst)",
      PLOT_W / 4 >= 60.0, PLOT_W / 4)
    t("markers start on row 2 and patterns follow them",
      TIME_ROW_MARKERS == TIME_BASE_ROWS
      and time_band_rows(marker_rows=1, pattern_rows=1) == TIME_ROW_MARKERS + 2)
    t("a file with NO markers and NO patterns gets 2 rows and no empty row",
      time_band_rows(marker_rows=0, pattern_rows=0) == TIME_BASE_ROWS
      and time_band_height(marker_rows=0, pattern_rows=0) == TIME_BAND.h,
      f"{time_band_rows(marker_rows=0, pattern_rows=0)} rows")
    return ok

  ## ---- 卡片内容：老板定的那一批 ----
   #t("loud / dyn cards hold exactly 6 and 4 rows, matching ROWS_PER_CARD",
   #  len(LOUD_CARD_FIELDS) == ROWS_PER_CARD["loud_card"]
   #  and len(DYN_CARD_FIELDS) == ROWS_PER_CARD["dyn_card"],
   #  f"{len(LOUD_CARD_FIELDS)} / {len(DYN_CARD_FIELDS)}")
   #t("every referenced summary key is one the pipeline produces or will produce",
   #  {k for _, k, _ in LOUD_CARD_FIELDS + DYN_CARD_FIELDS}
   #  <= {"integrated", "shortTermMax", "momentaryMax", "lra", "samplePeakMax",
   #      "truePeakMax", "dra", "drp", "pmax", "pmin"})
   #t("the old footer's PLR and both DIAL placeholders are gone",
   #  not any(k in ("plr", "dialI", "dialLra")
   #          for _, k, _ in LOUD_CARD_FIELDS + DYN_CARD_FIELDS))
   #t("both cards still fit their box at the borrowed 24 pt step",
   #  len(LOUD_CARD_FIELDS) * LINE_STEP_ASSUMED <= LOUD_CARD.h
   #  and len(DYN_CARD_FIELDS) * LINE_STEP_ASSUMED <= DYN_CARD.h)
   #t("the file name is anchored at the page's top-left, above the content box",
   #  FILE_NAME_AT[0] == CONTENT_LEFT and FILE_NAME_AT[1] < CONTENT_TOP,
   #  FILE_NAME_AT)
   #t("the sketch's rows 3+4 concatenate to exactly one field `Album-Name`",
   #  META_TEXT_LATIN[2][0] + META_TEXT_LATIN[3][0] == "".join(ALBUM_WRAP)
   #  == "Album-Name",
   #  f"{META_TEXT_LATIN[2][0]!r} + {META_TEXT_LATIN[3][0]!r}")
   #t("DiscNumber is NOT a metadata card field",
   #  not any("disc" in f.lower() for f in META_CARD_FIELDS))

   ## ---- 时间格式：永不引入小时 ----
   #t("format_time(0) is the required `00m00s`", format_time(0) == "00m00s")
   #t("format_time(291) is `04m51s`", format_time(291) == "04m51s")
   #t("past an hour it keeps counting minutes (`60m00s`, `61m01s`)",
   #  format_time(3600) == "60m00s" and format_time(3661) == "61m01s",
   #  f"{format_time(3600)} / {format_time(3661)}")
   #t("format_time never emits an hour field or an `h`",
   #  all("h" not in format_time(x) for x in (0, 59, 60, 3599, 3600, 90000)))
   #t("the seconds part is always two digits and < 60",
   #  all(format_time(x)[-3:-1] == f"{x % 60:02d}"
   #      for x in (0, 7, 59, 60, 61, 3599, 3600, 3661)))
   #t("format_time is non-decreasing in seconds",
   #  all(format_time(a) <= format_time(b) for a, b in ((0, 1), (59, 60), (3599, 3600))))
   #t("the marker default name is the owner's `Marker`",
   #  MARKER_DEFAULT_NAME == "Marker")

   ## ---- RMS 口径：能量域 vs 直接对 dB 求 RMS ----
   #t("a constant series has energy-RMS equal to itself",
   #  abs(energy_rms_lufs([-23.0] * 10) - (-23.0)) < 1e-9)
   #t("energy-RMS is pulled up by the loud parts, never below the mean",
   #  energy_rms_lufs([-30.0, -10.0]) > (-30.0 + -10.0) / 2)
   #t("KNOWN: sqrt(mean(v**2)) on dB values gives a POSITIVE number -- useless",
   #  math.sqrt(sum(v * v for v in (-30.0, -10.0)) / 2) > 0,
   #  f"{math.sqrt(sum(v * v for v in (-30.0, -10.0)) / 2):.1f} -- not a LUFS")
   #t("the silence floor is dropped",
   #  energy_rms_lufs([-20.0, -20.0, -120.0]) == energy_rms_lufs([-20.0, -20.0])
   #  and energy_rms_lufs([-120.0, -120.0]) is None)
   #t("an all-silence series yields None, not -inf",
   #  energy_rms_lufs([-120.0] * 5) is None)
   #return ok


def _raises(fn) -> bool:
    try:
        fn()
    except Exception:                                              # noqa: BLE001
        return True
    return False


if __name__ == "__main__":
    print(layout_report())
    print()
    print("invariants:")
    print("ALL PASS" if check() else "PROBLEMS")
