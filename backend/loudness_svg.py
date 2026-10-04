"""响度总览图的 SVG 渲染器。

版式唯一来源 `backend/chart_layout.py`，纵轴唯一来源 `backend/chart_axis.py` ——
这个模块**不自己发明坐标**，只把数据放进那张版面。

为什么是 SVG（而不是继续用 PNG）
--------------------------------
* **中文不会退化成 `?`**：PNG 那条路靠 `_pick_font(need_cjk=True)` 找系统字体，找不到
  就把非 ASCII 换成问号（`audio._ascii_title`）。SVG 用**看的人的字体**，标记名（实测
  可能是 `标记 0` 这种中文）永远正常。
* **主题跟着用户走，而且烘进文件**（2026-10）：颜色从 `theme.css` 解析（`backend/theme.py`），
  按**执行链那一刻**的 `t1/t2/t3 × light/dark` 算成实色写进每个元素和 `<style>` ——
  全文没有一处 `var()`，所以导出件在任何查看器里都长一样，也不依赖、不影响任何页面。
  见 `ROLE_TOKENS` / `chart_palette()`。
* **可断言**：SVG 是文本，测试可以直接断言坐标**和颜色**，不需要浏览器（本机
  `chrome-headless-shell` 被沙箱的命名管道限制挡住，`python tests/...` 里栅格化不了）。
* 矢量、可嵌入、可打印、几 KB。

量不了文字宽度这件事
--------------------
服务端没有字体引擎，所以"标记字会不会重叠 → 要不要再起一行"只能用**估计宽度**判断：
`_text_w()` 把全角算 1.0 em、半角算 0.55 em。保守高估，宁可多起一行，也不要两段文字压在一起。
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from backend import chart_axis as A
from backend import chart_layout as L
from backend import theme as T

#: ---------------------------------------------------------------- 颜色：全部来自主题
#:
#: 老板 2026-10："接下来将 svg 生成的主题颜色改成用户执行链时主题的，并在导出时
#: 将颜色硬编码入文件。"
#:
#: 所以：**色值一律从 `theme.css` 解析**（`backend/theme.py`），渲染时算成实色写进
#: 每个元素和 `<style>` 里。这里只声明"图的哪个角色用主题的哪个令牌"。
#:
#: 配对契约照 `theme.css` 顶部那三行（深色模式最容易搞错的地方）：
#:   `--bg-*` 配 `--ink`；`--tint-*` 配 `--ink`；`--fill-*` 配 `--on-fill`。
#:
#: | 图的角色 | 令牌 | 为什么是它 |
#: |---|---|---|
#: | `bg`（整张画布） | `--bg-app` | 页底色 |
#: | `panel`（卡片/绘图区/时间带） | `--bg-surface` | 面底色，靠它和画布分层 |
#: | `body`（S 在红区以下） | `--fill-primary` | 主题的主色块，深色模式会自动反转成浅色 |
#: | `head`（S 在红区以上 ≥ −3） | `--error-solid` | 红区是"警戒"语义，走错误色族 |
#: | `red`（红区带、爆音段、红刻度线） | `--error-solid` | 同上；它本来也定义了"白字可用" |
#: | `refLine`（−23 参考线） | `--ink-accent` | 第二色，且是对比度受检的墨色 |
#: | `grid` / `border` | `--border` / `--border-strong` | 描边族，带 alpha，按底色压平 |
#: | `text` / `muted` | `--ink` / `--text-muted` | 正文与次要文字 |
#: | `loudLine`（总响度虚线） | `--text-muted` | 数据线要**比参考线淡**，见下面那条 |
#: | `pattern`（`PT_X` 的线与字） | `--text-muted` | 结构性标注，不该抢包络的视线 |
#:
#: ⚠ `loudLine`/`pattern` 一开始都叫 `rms`、取的是 `--ink`，实测**撞色**：
#: `t2`/`t3` 的 `--ink-accent` 就等于 `--ink`（同一套色板里第二色和墨色同值），于是
#: 那条虚线和 −23 参考线在 4/6 套主题里变成**同一个颜色**，图上两条水平线分不出来。
#: 改用主题自己那支"淡化的墨色"（`--text-muted`）：它本来就是"墨色朝底色让一步"的结果，
#: 语义对，六套都分得开。`check_loudness_svg.py` 钉着"两者不同色"这条。
#:
#: 2026-10 拆成两个角色（原来是同一个 `rms`）：那条虚线现在画的是**总响度**，
#: 而 `PT_X` 是结构标注 —— 共用名字会让"改一个颜色"顺手改到另一个。
#:
#: `redText`（红区那两个字）是**派生**的，不在表里：见 `_red_text()`。
ROLE_TOKENS: dict[str, tuple[str, str | None]] = {
    "bg":        ("bg-app", None),
    "panel":     ("bg-surface", None),
    "body":      ("fill-primary", None),
    "head":      ("error-solid", None),
    "red":       ("error-solid", None),
    "refLine":   ("ink-accent", None),
    "grid":      ("border", "bg-surface"),
    "border":    ("border-strong", "bg-surface"),
    "text":      ("ink", None),
    "muted":     ("text-muted", "bg-surface"),
    "loudLine":  ("text-muted", "bg-surface"),
    "pattern":   ("text-muted", "bg-surface"),
}

#: 框线里**不是颜色**的旋钮（几何），不随主题变。
#: 老板 2026-10 说过"那些框的描边好难看"，所以框线仍然集中在这里一处可调：
#:   `frame_w` 线宽（0 = 不要框）、`radius` 圆角（绘图区保持 0）。
FRAME_GEOM = {"frame_w": 0.0, "radius": 8.0}

#: "**面**要淡、**标记**要浓"的那个面透明度：红区带与红冠都乘它，爆音段/红刻度线
#: 用不透明的 `red`。
#:
#: 旧硬编码调色板用**两支红**（软 `head` #D89890 / 强 `red` #C0392B）表达这个层级；
#: 主题里只有一支警戒色（`--error-solid`），所以层级改由透明度承担。
#: 0.55 是从旧值继承下来的（红区带一直是 0.55），红冠以前是纯色、现在跟着一起乘。
AREA_TINT = 0.55

#: 爆音段**穿过绘图区的淡引导线**之间的最小平均间距（pt）。比这更密就整批不画，
#: 只留时间带里的红条 + 短刻度。
#:
#: 为什么：段少时引导线有用（"这条红标指的是哪一刻"），段一多就变成一道红帘子把包络
#: 盖住。实测 `INFinite - Stellar.flac` 修好数据源后有 **99 段 ⇒ 198 条线**铺在 660pt
#: 上（平均 3.3pt 一条），中间那段的包络根本读不出来。
#: 取 12pt 的理由：低于一个正文行高时这些线只是一层**纹理**，不再传达"位置"信息。
CLIP_GUIDE_MIN_GAP_PT = 12.0

#: `theme.css` 读不到时的兜底（应用本身这时也活不成了，这里只是不让渲染炸掉）。
#: ⚠ 这是**全仓库唯一**一份抄下来的色值，且只在解析失败时用。
FALLBACK_PALETTE = {
    "bg": "#FFFFFF", "panel": "#FAFAFA", "body": "#A8C0D8", "head": "#D89890",
    "red": "#C0392B", "redText": "#C0392B", "refLine": "#F2B84B",
    "grid": "#E4E7EA", "border": "#C9CFD6", "text": "#1E1F23",
    "muted": "#8A9099", "loudLine": "#5B7FA6", "pattern": "#5B7FA6",
    "card_fill": "#FBFCFD", "rail_fill": "#FFFFFF", "band_fill": "#FFFFFF",
    "plot_fill": "#FFFFFF", "frame": "#E4E8ED", "frame_soft": "#EDF1F4",
}


def _red_text(theme: str, mode: str) -> str:
    """红区文字的实色 —— **派生**，不是直接取令牌。

    为什么不能直接用 `--error-solid`：那个令牌的契约是"**白字压在上面**可用"
    （`theme.css` 原话），也就是它注定偏暗。当它反过来当**落在画布上的文字色**时：
    浅色模式够（t1 `#AA4141` 对白 ≈ 6.0），深色模式不够
    （t1 深色 `#9C3F3F` 对 `#1D171C` 那类底 ≈ 2.2，红字基本看不清）。
    所以按 WCAG AA（4.5）把它**只调亮度、不动色相**地推到可读 —— 红还是红。
    """
    toks = T.tokens(theme, mode)
    fg = toks.get("error-solid")
    bg = toks.get("bg-app")
    if not fg or not bg:
        return FALLBACK_PALETTE["redText"]
    return T.to_hex(T.ensure_contrast(fg, bg, T.AA_RATIO))


def chart_palette(theme: str = T.DEFAULT_THEME, mode: str = T.DEFAULT_MODE) -> dict:
    """算出这一套主题下、这张图要用的**全部实色**。

    返回的键就是渲染器里那些角色名（外加 `card_fill`/`rail_fill`/`band_fill`/
    `plot_fill`/`frame`/`frame_soft`/`frame_w`/`radius`），全是 `#RRGGBB` 或数字 ——
    **没有一个 `var()`**，因为老板要的是"导出时颜色硬编码入文件"。
    """
    theme, mode = T.normalize(theme, mode)
    if (theme, mode) not in T.available():
        return dict(FALLBACK_PALETTE) | dict(FRAME_GEOM)
    pal: dict[str, Any] = {}
    for role, (token, on) in ROLE_TOKENS.items():
        pal[role] = T.solid(theme, mode, token,
                            on=on or "bg-surface",
                            fallback=FALLBACK_PALETTE.get(role, "#808080"))
    pal["redText"] = _red_text(theme, mode)
    # 框与三块区域：都用面底色 + 描边族，靠底色分层而不是靠描边抢视线
    surface = pal["panel"]
    pal |= {"card_fill": surface, "rail_fill": pal["bg"], "band_fill": pal["bg"],
            "plot_fill": surface, "frame": pal["grid"], "frame_soft": pal["grid"]}
    pal |= dict(FRAME_GEOM)
    return pal


#: 默认那套（t1 浅色）。渲染器不再读它 —— 它只是给"没有主题信息"的调用方一个
#: 确定性的起点（比如测试、脚本），真实渲染一律走 `chart_palette()`。
PALETTE = chart_palette()


#: 字体栈：先中文，再通用，最后兜底
FONT_STACK = ('"Microsoft YaHei","PingFang SC","Noto Sans CJK SC","Hiragino Sans GB",'
              'system-ui,-apple-system,"Segoe UI",sans-serif')

#: ---------------------------------------------------------------- 框线的样式旋钮
#: 老板 2026-10："那些框的描边好难看，我想改"。
#:
#:   `frame`    —— 框线颜色。**越浅越"退后"**：原来用的是硬编码的 #C9CFD6（偏深），
#:                 每个矩形都在抢视线，所以看着"难看"。现在取主题的 `--border`。
#:   `frame_w`  —— 线宽。1 就够；0.75 更轻，0 就是不要框。见 `FRAME_GEOM`。
#:   `radius`   —— 圆角。卡片给 8 会明显柔和；绘图区保持 0（数据区要方正的边界）。
#:   `card_fill`—— 卡片底色 = 主题的 `--bg-surface`（靠底色分层，不靠描边）。
#:   `rail`/`plot`/`band` —— 三块区域各自的底色，同上。
#:
#: 想彻底不要框：把 `FRAME_GEOM["frame_w"]` 设成 0（代码里会自动省掉 stroke）。
#: 颜色部分**不在这里** —— 那是主题的事，见上面的 `ROLE_TOKENS`/`chart_palette()`。

#: 各处的字号（pt）。时间带里的"小一号"= 12，`PT_X` 也是 12。
FS_BODY = L.FONT_BODY          # 14
FS_SMALL = 12.0
FS_TINY = L.FONT_SMALL         # 10
FS_VALUE = 16.0
FS_TITLE = 16.0

ROW_ASCENT = 0.72              # 行内基线位置（× 行距）

#: 模式标注与它那条刻度延伸线之间的横向间隙（pt）。
#: 标签落在线的**右侧** —— 老板要的锚法，见 `_layout_pattern_rows`。
PATTERN_LABEL_GAP = 4.0


# --------------------------------------------------------------------- helpers
def _esc(s: Any) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _text_w(s: str, size: float) -> float:
    """粗略的文本宽度估计。全角 1.0 em、半角 0.55 em，**故意高估**。"""
    return sum(1.0 if ord(c) > 0x2E80 else 0.55 for c in s) * size
#: 值那一列（拉丁为主）的宽度**安全系数**。
#:
#: `_text_w()` 把半角一律按 0.55 em 估，对**大写**偏乐观 —— 实际字体里
#: `F` `N` `O` `C` `R` 这类字身普遍 0.6–0.7 em，混排下来整串常比估计宽 ~13%。
#: 标签那一列是 CJK（1 em = 一个汉字），没有这个误差，所以**只给值乘**。
#:
#: 不乘的后果（两条都是实测的）：
#:   · 右对齐的值越过估计边界，压上左对齐的标签；
#:   · 截断点偏晚 —— `From Now On (Camellia Remix)` 该截成 `From Now On (Cam…`，
#:     不乘会截成 `From Now On (Camell…`。
VALUE_W_SLACK = 1.13

def _num(v: Any, *, unit: str = "", dash: str = "—") -> str:
    """卡片值。**字符串原样透传** —— DRP 那几项在 `audio.py` 里就已经格式化成
    给人看的样子了（`PT_3 · 3.63 LU`），渲染器不再加工一遍。"""
    if isinstance(v, str):
        return v or dash
    if not isinstance(v, (int, float)):
        return dash
    return f"{v:.1f} {unit}".strip()


def _clip(s: str, max_w: float, size: float) -> str:
    """把文字截到给定宽度（按 `_text_w` 的估计），尾巴加省略号。

    ⚠ `max_w` 窄到连省略号都放不下时返回**空串**。旧版无条件下
    `out + "…"`，于是"截断"后仍可能比 `max_w` 宽 —— 右对齐的值越界压上
    左对齐的标签。返回空串的意思是"这个值在当前版面里没地方放"，
    而不是"硬塞一个省略号让它看起来没被截"。
    """
    if _text_w(s, size) <= max_w:
        return s
    ell = "…"
    if _text_w(ell, size) > max_w:
        return ""
    out = ""
    for ch in s:
        if _text_w(out + ch + ell, size) > max_w:
            break
        out += ch
    return out + ell


def _clamp_box(left: float, right: float, x0: float,
               x1: float) -> tuple[float, float]:
    """把一个标签框夹进 `[x0, x1]`，保持宽度。

    标记落在 0.000 时，居中标签会有一半伸到绘图区外面去 —— 必须夹回来。
    """
    w = right - left
    if w >= (x1 - x0):
        return x0, x1
    if left < x0:
        return x0, x0 + w
    if right > x1:
        return x1 - w, x1
    return left, right


#: 「标签 + 值」的尺寸级联：先试大字，放不下就逐级降（标签比值更耐降），
#: 五级都不行才截值。卡只有 200pt 宽，动态卡那几行不降级一定会撞上。
_SIZE_CASCADE = (
    (FS_BODY, "s-tick", FS_VALUE, "s-val"),
    (FS_BODY, "s-tick", FS_SMALL, "s-small"),
    (FS_BODY, "s-tick", FS_TINY, "s-tiny"),
    (FS_SMALL, "s-small", FS_TINY, "s-tiny"),
    (FS_TINY, "s-tiny", FS_TINY, "s-tiny"),
)


def _fit_pair(label: str, value: str,
              avail: float) -> tuple[str, str, str, str]:
    """在 `avail` 宽度里给「标签 + 值」挑一组放得下的字号。

    返回 `(label_class, value_class, label_text, value_text)`。都放不下时截**值**
    （标签是字段名，截了就认不出是哪一项）。

    ⚠ 兜底那一步必须真的保证 `label + value <= avail`：早先写的是
    `room = max(20.0, avail - _text_w(label, lsz))` —— 一旦
    `avail - 标签宽 < 20` 就强行给值留 20pt，于是 `label + value > avail`，
    右对齐的值压上左对齐的标签（长的标题/专辑名 + 窄卡时实测踩过）。
    现在 `room = avail - 标签宽`；连一个省略号都放不下就干脆不画值。
    """
    for lsz, lcls, vsz, vcls in _SIZE_CASCADE:
        # ↓ 值乘 slack：级联的"放得下"判断
        if _text_w(label, lsz) + _text_w(value, vsz) * VALUE_W_SLACK <= avail:
            return lcls, vcls, label, value
    lsz, lcls, vsz, vcls = _SIZE_CASCADE[-1]
    room = avail - _text_w(label, lsz)
    if room <= 0.0:
        return lcls, vcls, label, ""
    # ↓ 预算除 slack：兜底的截断，与上面同一个口径
    return lcls, vcls, label, _clip(value, room / VALUE_W_SLACK, vsz)


def _columns(ts: Sequence[float], series: Sequence[float | None], n: int,
             duration: float) -> list[float | None]:
    """按列取**段内最大值**（平均会把瞬时峰值削平）。"""
    out: list[float | None] = [None] * n
    dur = float(duration) or (ts[-1] if ts else 1.0) or 1.0
    for ti, vi in zip(ts, series):
        if vi is None or vi <= A.LUFS_BOTTOM - 69.0:        # −120 静音底
            continue
        i = int(ti / dur * n)
        i = 0 if i < 0 else (n - 1 if i >= n else i)
        if out[i] is None or vi > out[i]:
            out[i] = vi
    return out


def _clip_runs(data: dict) -> list[tuple[float, float]]:
    """爆音时段 —— **从 `summary["clips"]` 取**，不在这里重算。

    老板 2026-10 报的那个 bug：

    > 现在的爆音标红是个地图炮：一有就开始标，一标就从头标到尾。

    根因有两层，都在这一处：

    1. **数据源用错了**。原来读的是 `data["truePeak"]`，而 ebur128 的 `truePeak` 是
       **到当前为止的累计最大值**（实测序列单调不减）⇒ 第一次越线之后永远越线。
       实测：`INFinite - Stellar.flac` 画出**一段 35.9→159.5s（123.6s）**、
       `ariiol - REK421.flac` 画出**一段 2.9→215.7s（212.8s，整首）**。
       逐帧的削波信息只有 `astats` 的 `Peak_level` 有，`audio.py` 早就算好了
       （`summary["clips"]`），只是这张图没用它。
    2. 就算换成逐帧数据，**`_fill_runs` 用的是严格 `>`**，而 `Peak_level` 顶到的是
       **恰好 0.000 dBFS** ⇒ 一段都画不出来（静默失灵，比画错更难发现）。
       权威判据在 `audio.py` 里是 `>= 0.0`，并且做过间隔合并（`CLIP_MERGE_GAP`）。

    所以这里**只消费**已算好的结果：图和"剪贴段"那几项汇总值、以及报告里的时段列表
    用的是同一份数据 —— 两边不一致本身就是一种 bug。

    ⚠ 别想着自己拿 `data["t"]` 去索引 `data["peak"]`：**那是两条不同的网格**
    （实测 `t` 是 1567 帧、`peakT`/`peak` 是 1597 帧，且 `t[0]=2.9` 而 `peakT[0]=0.0`，
    `t` 少掉的正是 S 窗预热那几秒），照着索引会错位、越界。

    ⚠ 起始等于结束的段**必须留着**：那是"只有一帧顶满"的爆音（`audio.py` 里
    `ts[start], ts[i-1]` 同一帧就是这样）。早先这里写了 `if b > a`，把这种段全丢了 ——
    实测 `INFinite - Stellar.flac` 的 99 段里有 **10 段**是单帧，图上就少了 10 条红标，
    而下面画的时候本来专门有一条 `xb = max(xb, xa + 2.0)` 让单帧也看得见。
    """
    s = data.get("summary") or {}
    out: list[tuple[float, float]] = []
    for item in s.get("clips") or []:
        try:
            a, b = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if b >= a >= 0.0:
            out.append((a, b))
    return out


def _wrap_rows(items: list[tuple[float, float, str]], *, x0: float, w: float,
               gap: float) -> list[list[tuple[float, float, str]]]:
    """把标签按"起点排序 + 不重叠"铺成若干行。

    `items` 是 `(label_left, label_right, text)`；返回按行分组的列表。
    `label_left` 是**标签框左边界**（不是刻度位置）—— 居中的标签会向左伸出半个宽度。
    """
    rows: list[list[tuple[float, float, str]]] = []
    for item in sorted(items, key=lambda r: r[0]):
        left, right, _ = item
        placed = False
        for row in rows:
            if not row or left >= row[-1][1] + gap:
                row.append(item)
                placed = True
                break
        if not placed:
            rows.append([item])
    return rows


# --------------------------------------------------------------------- renderer
def render_loudness_svg(data: dict, *, title: str = "",
                        meta: dict[str, Any] | None = None,
                        cover_href: str | None = None,
                        markers: Iterable[dict[str, Any]] = (),
                        patterns: Iterable[dict[str, Any]] = (),
                        cover: str | None = None,
                        width: float | None = None,
                        ref_lufs: float = -23.0,
                        theme: str = T.DEFAULT_THEME,
                        mode: str = T.DEFAULT_MODE,
                        palette: dict | None = None,
                        clip_runs: Sequence[tuple[float, float]] | None = None,
                        ) -> str:
    """把一份 `loudness_timeline()` 的结果画成**整页 SVG**。

    * `title`  —— 文件名，画在**页面左上角**（不进元数据卡）
    * `meta`   —— 元数据卡的四行拉丁字段 + 六项文件字段
    * `markers`—— `[{"time": 12.3, "name": "副歌"}]`，来自音频章节；空则不出标记行
    * `patterns`—— `[{"start":…, "end":…, "pattern":1}]`，DRP 的每一次出现
    * `cover`  —— 封面图（data URI）；`cover_href` 是它的别名
    * `width`  —— 显示宽度。`viewBox` 永远是设计单位（1031.81 宽），所以给不给
      都**无损缩放**：这是矢量图，不存在"按宽度重排"那回事（PNG 那条路才需要）。
    * `theme`/`mode` —— 上色用的主题（`t1/t2/t3` × `light/dark`），**执行链那一刻
      用户选的**那个；由客户端随任务参数传进来。颜色会被**算成实色写进文件**，
      所以导出件在任何查看器里都一样、也不会反过来影响页面。见 `chart_palette()`。
    * `palette`—— 已经算好的色板（给了就直接用，`theme`/`mode` 忽略）。
    * `clip_runs`—— 爆音时段。不给就从 `data["summary"]["clips"]` 取。
    """
    meta = dict(meta or {})
    markers = [m for m in markers if m.get("time") is not None]
    patterns = [p for p in patterns if p.get("start") is not None]
    cover_uri = cover_href or cover
    pal = palette or chart_palette(theme, mode)

    duration = float(data.get("duration") or 0.0)
    ts = list(data.get("t") or [])
    series_s = list(data.get("S") or [])
    summary = dict(data.get("summary") or {})
    if duration <= 0 and ts:
        duration = float(ts[-1]) or 1.0

    # ---- 时间带要先知道行数，因为它是动态框 ----
    marker_rows = _layout_time_band(markers, duration, n_rows_hint=True)
    pattern_rows = _layout_pattern_rows(patterns, duration, n_rows_hint=True)
    band_h = L.time_band_height(marker_rows=marker_rows, pattern_rows=pattern_rows)
    canvas_h = L.canvas_height(marker_rows=marker_rows, pattern_rows=pattern_rows)
    # 爆音时段：**只认 `audio.py` 用逐帧采样峰值算好的那份**（见 `_clip_runs`）
    clip = clip_runs if clip_runs is not None else _clip_runs(data)

    W, H = L.CANVAS_W, canvas_h
    band_y = L.TIME_BAND.y
    # 底右卡跟时间带同高，底边对齐（草图里它们是同一行）
    dyn_h = max(L.DYN_CARD.h, band_h)

    dw = W if width is None else float(width)
    dh = dw * H / W
    out: list[str] = []
    add = out.append

    add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.2f} {H:.2f}" '
        f'width="{dw:.0f}" height="{dh:.0f}" style="max-width:100%;height:auto" '
        f'role="img" aria-label="{_esc(title or "响度总览图")}">')
    add(_style(pal))
    add(f'<rect width="{W:.2f}" height="{H:.2f}" fill="{pal["bg"]}"/>')

    add(f'<g id="filename"><text x="{L.FILE_NAME_AT[0]:.2f}" '
        f'y="{L.CONTENT_TOP - 20:.2f}" class="s-title">{_esc(title or "—")}</text></g>')

    add(_render_axis_rail(pal))
    add(_render_plot(pal, ts, series_s, duration, summary, ref_lufs))
    add(_render_time_band(pal, markers, patterns, duration, band_h, clip))
    add(_render_cards(pal, meta, summary, cover_uri, dyn_h))
    add("</svg>")
    return "\n".join(out) + "\n"


def _style(pal: dict) -> str:
    """全部样式集中在这里，改这一处就能换皮。

    ⚠ **颜色全部是实色字面量，没有一处 `var()`** —— 老板 2026-10 要的是
    "导出时将颜色硬编码入文件"。这样导出件在任何查看器里都长一样，也不依赖
    任何 CSS 变量。

    顺带修掉一个隐患：原来这里写的是 `:root{--text:…}`，而**内联进页面时
    `:root` 指向的是 HTML 的 `<html>`** —— 也就是说图的样式会反过来污染页面的
    变量（当时只是恰好没和 `theme.css` 的令牌重名才没出事）。现在没有变量了。
    """
    f = pal
    stroke = (f'stroke="{f["frame"]}" stroke-width="{f["frame_w"]:g}"'
              if f["frame_w"] else 'stroke="none"')
    return (
        "<style>\n"
        f"  text{{font-family:{FONT_STACK};fill:{f['text']}}}\n"
        f"  .s-tick{{font-size:{FS_BODY}px}}\n"
        f"  .s-red{{fill:{f['redText']}}}\n"
        f"  .s-small{{font-size:{FS_SMALL}px}}\n"
        f"  .s-tiny{{font-size:{FS_TINY}px}}\n"
        f"  .s-muted{{fill:{f['muted']}}}\n"
        f"  .s-title{{font-size:{FS_TITLE}px;font-weight:600}}\n"
        f"  .s-val{{font-size:{FS_VALUE}px;font-weight:600}}\n"
        f"  .s-bold{{font-weight:700}}\n"
        f"  .s-tabular{{font-variant-numeric:tabular-nums}}\n"
        # 卡片：圆角 + 面底色 + 浅描边。**不靠描边分隔**，靠底色。
        f"  .card{{fill:{f['card_fill']};{stroke};rx:{f['radius']:g}}}\n"
        # 数据区：底色 `.frame-plot`（**最先画**）与边线 `.outline-plot`（**最后画**）
        # ⚠ 必须分成两个元素。早先把"填白 + 描边"合成一个矩形放在**收尾**，结果它把
        # 包络整个盖住了，图上只剩坐标轴 —— 实测踩过。
        f"  .frame-plot{{fill:{f['plot_fill']}}}\n"
        f"  .outline-plot{{fill:none;stroke:{f['frame_soft']};"
        f"stroke-width:{f['frame_w']:g}}}\n"
        f"  .frame-band{{fill:{f['band_fill']};{stroke}}}\n"
        f"  .frame-rail{{fill:{f['rail_fill']};stroke:none}}\n"
        f"  .grid{{stroke:{f['grid']};stroke-width:1}}\n"
        f"  .grid-strong{{stroke:{f['grid']};stroke-width:1;opacity:1}}\n"
        "</style>")


def _render_axis_rail(pal: dict) -> str:
    r = L.AXIS_RAIL
    o = [f'<g id="axis-rail">',
         f'<rect class="frame-rail" x="{r.x:.2f}" y="{r.y:.2f}" '
         f'width="{r.w:.2f}" height="{r.h:.2f}"/>']
    for v in A.TICKS:
        y = A.y(v, L.PLOT.y, L.PLOT.h)
        cls = "s-tick s-red s-bold" if A.is_red(v) else "s-tick"
        o.append(f'<text x="{r.right - 8:.2f}" y="{y + FS_BODY * 0.35:.2f}" '
                 f'class="{cls}" text-anchor="end">{v:g}</text>')
    # `0` 的标签会向上溢出绘图区顶边（≥ +0.3 的代价，已接受）；画布上方有 60pt 空白。
    # 单位放在标尺列**底部** —— 放顶部会和 −0.3 线的标签抢位置（实测撞上过）。
    o.append(f'<text x="{r.x + 50:.2f}" y="{r.bottom - 8:.2f}" class="s-tiny s-muted">'
             f'LUFS</text>')
    o.append("</g>")
    return "\n".join(o)


def _render_plot(pal: dict, ts: Sequence[float], series: Sequence[float | None],
                 duration: float, summary: dict, ref_lufs: float = -23.0) -> str:
    pl = L.PLOT
    y = lambda v: A.y(v, pl.y, pl.h)                                  # noqa: E731
    o = ['<g id="plot">']
    o.append(f'<rect class="frame-plot" x="{pl.x:.2f}" y="{pl.y:.2f}" '
             f'width="{pl.w:.2f}" height="{pl.h:.2f}"/>')

    # 红区：≥ −3 LUFS
    o.append(f'<rect x="{pl.x:.2f}" y="{pl.y:.2f}" width="{pl.w:.2f}" '
             f'height="{y(A.RED_ABOVE) - pl.y:.2f}" fill="{pal["head"]}" '
             f'opacity="{AREA_TINT}"/>')

    # S 的填充：蓝体（曲线向下到底）+ 红冠（曲线高出 −3 的那一段）
    cols = int(pl.w)
    vals = _columns(ts, series, cols, duration)
    body: list[tuple[float, float]] = []
    caps: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    for i, v in enumerate(vals):
        if v is None:
            continue
        x = pl.x + i + 0.5
        body.append((x, y(v) if v <= A.RED_ABOVE else y(A.RED_ABOVE)))
        if v > A.RED_ABOVE:
            cur.append((x, y(v)))
        elif cur:
            caps.append(cur)
            cur = []
    if cur:
        caps.append(cur)

    if body:
        d = (f'M{body[0][0]:.1f},{body[0][1]:.1f}'
             + "".join(f'L{x:.1f},{yy:.1f}' for x, yy in body[1:])
             + f'L{body[-1][0]:.1f},{pl.bottom:.2f}'
             + f'L{body[0][0]:.1f},{pl.bottom:.2f}Z')
        o.append(f'<path d="{d}" fill="{pal["body"]}"/>')
    cap_y = y(A.RED_ABOVE)
    for run in caps:
        # 红冠是**曲线与 −3 线之间**那一条带，不是从 −3 一路填到底
        d = (f'M{run[0][0]:.1f},{run[0][1]:.1f}'
             + "".join(f'L{x:.1f},{yy:.1f}' for x, yy in run[1:])
             + f'L{run[-1][0]:.1f},{cap_y:.2f}'
             + f'L{run[0][0]:.1f},{cap_y:.2f}Z')
        # 和红区带同一个透明度。为什么：旧硬编码调色板里有**两支红**
        # （软的 `head` 画面、强的 `red` 画标记），换到主题后只有一支警戒色
        # （`--error-solid`），"面淡、标记浓"这个层级就只能靠透明度表达。
        # 漏了这个 opacity 时红冠会是纯浓红，比红区带跳一大截。
        o.append(f'<path d="{d}" fill="{pal["head"]}" opacity="{AREA_TINT}"/>')

    # 网格线画在**填充之后** —— 否则包络会把刻度线整个盖住
    # （老板 2026-10："坐标图上的横线被挡住了"）。放在填充之上、别的元素之下：
    # 既看得见刻度，又不会压过总响度虚线与参考线。
    for v in A.TICKS:
        yy = y(v)
        o.append(f'<line class="grid" x1="{pl.x:.2f}" y1="{yy:.2f}" '
                 f'x2="{pl.right:.2f}" y2="{yy:.2f}"/>')

    # 总响度（integrated）：一条水平虚线
    #
    # 老板 2026-10："RMS 删去，那条虚线改成 integrated 的 LUFS 值。"
    # 原来画的是 `S` 的能量域 RMS（`L.energy_rms_lufs()`）—— 那个量在图上**没有对应
    # 指标**（卡片里没有它），而 integrated 就是响度卡第一项「总响度」：画在图上，
    # "这个文件整体到没到目标"一眼可见。RMS 的口径留档在 `chart_layout` 里，不再画。
    loud = summary.get("integrated")
    if isinstance(loud, (int, float)) and not isinstance(loud, bool) \
            and A.LUFS_BOTTOM <= float(loud) <= A.LUFS_TOP:
        yy = y(float(loud))
        o.append(f'<line x1="{pl.x:.2f}" y1="{yy:.2f}" x2="{pl.right:.2f}" '
                 f'y2="{yy:.2f}" stroke="{pal["loudLine"]}" stroke-width="1.6" '
                 f'stroke-dasharray="7 4"/>')
        o.append(f'<text x="{pl.right - 6:.2f}" y="{yy - 5:.2f}" class="s-small" '
                 f'text-anchor="end" fill="{pal["loudLine"]}">'
                 f'总响度 {float(loud):.1f} LUFS</text>')
    # ⚠ 上面那个范围判断是必须的，不是防御性代码：`A.frac()` 会把越界值**夹取**到
    # 边界上，于是一个 −70 LUFS 的文件会把这条线画在绘图区**底边**上 —— 那是假的。
    # 底边之外的值不画（卡片里仍然有它的数字）。

    # 参考线（默认 −23，即 EBU R128 参考值）
    ry = y(ref_lufs)
    o.append(f'<line x1="{pl.x:.2f}" y1="{ry:.2f}" x2="{pl.right:.2f}" y2="{ry:.2f}" '
             f'stroke="{pal["refLine"]}" stroke-width="2"/>')

    o.append(f'<rect class="outline-plot" x="{pl.x:.2f}" y="{pl.y:.2f}" '
             f'width="{pl.w:.2f}" height="{pl.h:.2f}"/>')
    o.append("</g>")
    return "\n".join(o)


def _layout_time_band(markers: Sequence[dict], duration: float, *,
                      n_rows_hint: bool = False):
    """返回标记**要几行**（`n_rows_hint=True`）或逐行的标签框。"""
    pl = L.PLOT
    items: list[tuple[float, float, str]] = []
    for m in markers:
        t = float(m.get("time") or 0.0)
        x = pl.x + (t / duration * pl.w if duration else 0.0)
        name = str(m.get("name") or L.MARKER_DEFAULT_NAME)
        label = f"{name} / {L.format_time(t)}"
        w = _text_w(label, FS_BODY)
        left, right = _clamp_box(x - w / 2, x + w / 2, pl.x, pl.right)
        items.append((left, right, label))
    rows = _wrap_rows(items, x0=pl.x, w=pl.w, gap=10.0) if items else []
    return len(rows) if n_rows_hint else rows


def _layout_pattern_rows(patterns: Sequence[dict], duration: float, *,
                         n_rows_hint: bool = False):
    """动态模式：**只在每一次出现的「起点」写 `PT_n`**，终点只画刻度线。

    老板 2026-10："响度模式标注写在模式对应的刻度开头，模式结束的刻度不需要写。"

    返回 `(rows, edges)`：`rows` 是标签行（`(left, right, 文本)`），`edges` 是
    **全部**边界刻度线的 x（起点 + 终点）。`n_rows_hint=True` 时只返回行数。

    标签框是**左对齐到刻度线右侧**（`x + PATTERN_LABEL_GAP`）而不是居中 ——
    老板要的是"模式标注落在模式起始点刻度延伸线的右侧"。
    """
    pl = L.PLOT
    items: list[tuple[float, float, str]] = []
    edges: list[float] = []
    for p in patterns:
        n = int(p.get("pattern") or 0)
        for which, edge in (("start", p.get("start")), ("end", p.get("end"))):
            if edge is None:
                continue
            x = pl.x + (float(edge) / duration * pl.w if duration else 0.0)
            edges.append(x)
            if which != "start":
                continue                     # 终点不写文字
            label = f"PT_{n}"
            w = _text_w(label, FS_SMALL)
            left, right = _clamp_box(x + PATTERN_LABEL_GAP, x + PATTERN_LABEL_GAP + w,
                                     pl.x, pl.right)
            items.append((left, right, label))
    rows = _wrap_rows(items, x0=pl.x, w=pl.w, gap=8.0) if items else []
    if n_rows_hint:
        return len(rows)
    return rows, edges


def _render_time_band(pal: dict, markers: Sequence[dict], patterns: Sequence[dict],
                      duration: float, band_h: float,
                      clip: Sequence[tuple[float, float]] = ()) -> str:
    pl = L.PLOT
    tb = L.TIME_BAND
    o = ['<g id="time-band">',
         f'<rect class="frame-band" x="{tb.x:.2f}" y="{tb.y:.2f}" '
         f'width="{tb.w:.2f}" height="{band_h:.2f}"/>']

    x_of = lambda t: pl.x + (t / duration * pl.w if duration else 0.0)   # noqa: E731

    # 爆音时段：该段横轴**标红**、刻度标出、**不标时间**
    #
    # 每个爆音段有两条"边"：一条**穿过绘图区**的淡引导线（让人看出红条指的是哪一刻），
    # 一条从绘图区底边扎进时间带的短刻度。段少时引导线很有用；段一多它就变成一道红帘子、
    # 把包络整个盖住 —— 实测 `INFinite - Stellar.flac` 有 99 段 ⇒ 198 条线铺在 660pt 上
    # （平均每 3.3pt 一条）。所以**密集时只留短刻度**，引导线退场（判据见下）。
    edges = sorted({x for t0, t1 in clip for x in (x_of(t0), x_of(t1))})
    dense = (len(edges) >= 2
             and (edges[-1] - edges[0]) / max(1, len(edges) - 1) < CLIP_GUIDE_MIN_GAP_PT)
    for t0, t1 in clip:
        xa, xb = x_of(t0), x_of(t1)
        xb = max(xb, xa + 2.0)                      # 单帧的爆音也要看得见
        o.append(f'<rect x="{xa:.2f}" y="{tb.y:.2f}" width="{xb - xa:.2f}" '
                 f'height="4" fill="{pal["red"]}"/>')
        for x in (xa, xb):
            if not dense:
                o.append(f'<line x1="{x:.2f}" y1="{pl.y:.2f}" x2="{x:.2f}" '
                         f'y2="{pl.bottom:.2f}" stroke="{pal["red"]}" '
                         f'stroke-width="1" opacity="0.35"/>')
            o.append(f'<line x1="{x:.2f}" y1="{pl.bottom:.2f}" x2="{x:.2f}" '
                     f'y2="{tb.y + 4:.2f}" stroke="{pal["red"]}" '
                     f'stroke-width="1.5"/>')

    # 基础刻度：**刻度线**（全部引出网格线）
    for frac, _row in L.TIME_BASE_TICKS:
        x = x_of(frac * duration)
        o.append(f'<line x1="{x:.2f}" y1="{pl.y:.2f}" x2="{x:.2f}" '
                 f'y2="{pl.bottom:.2f}" stroke="{pal["grid"]}" '
                 f'stroke-width="1"/>')

    # 标记行（重叠就再起一行）
    mrows = _layout_time_band(markers, duration)
    for i, row in enumerate(mrows):
        by = (tb.y + (L.TIME_ROW_MARKERS + i) * L.TIME_BAND_LINE_STEP
              + L.TIME_BAND_LINE_STEP * ROW_ASCENT)
        for left, right, label in row:
            x = (left + right) / 2
            start = x - _text_w(label, FS_BODY) / 2
            # 刻度线：**在绘图区里画到底**，再向下延伸到这个标签自己那一行 ——
            # 这样"这条标签指的是哪个时刻"一眼看得出来（老板 2026-10 的要求）。
            o.append(f'<line x1="{x:.2f}" y1="{pl.y:.2f}" x2="{x:.2f}" '
                     f'y2="{by - FS_BODY * 0.9:.2f}" stroke="{pal["grid"]}" '
                     f'stroke-width="1"/>')
            # 名称与基础刻度同字号但 **BOLD**；时间**小一号且更浅**
            name, _, stamp = label.partition(" / ")
            o.append(f'<text x="{start:.2f}" y="{by:.2f}" class="s-tick s-bold">'
                     f'{_esc(name)}</text>')
            o.append(f'<text x="{start + _text_w(name, FS_BODY) + 2:.2f}" '
                     f'y="{by:.2f}" class="s-small s-muted">/{_esc(stamp)}</text>')

    # 动态模式行
    #
    # ⚠ 2026-10 改过两处锚法：
    #   · **只在起点写 `PT_n`**，终点只画刻度线（老板："模式结束的刻度不需要写"）；
    #   · 标签落在**起点那条延伸线的右侧**（`text-anchor="start"`），
    #     所以"PT_n 从这条线开始"是读得出来的。原来标签居中压在线上，指代不清。
    # 边界线一律**从绘图区顶边**画起（"所有时间的纵向刻度线也要延伸到图内"），
    # 再向下延伸出绘图区、伸到标签自己那一行。
    prows, pedges = _layout_pattern_rows(patterns, duration)
    base_row = L.TIME_ROW_MARKERS + len(mrows)
    row_of: dict[float, float] = {}
    for i, row in enumerate(prows):
        by = (tb.y + (base_row + i) * L.TIME_BAND_LINE_STEP
              + L.TIME_BAND_LINE_STEP * ROW_ASCENT)
        for left, _right, _label in row:
            row_of[round(left - PATTERN_LABEL_GAP, 2)] = by
    for ex in pedges:
        by = row_of.get(round(ex, 2))
        # 有标签的那条线伸到标签行；终点那条没有标签，就只伸到时间带顶边
        y2 = (by - FS_SMALL * 0.9) if by is not None else tb.y
        o.append(f'<line x1="{ex:.2f}" y1="{pl.y:.2f}" x2="{ex:.2f}" '
                 f'y2="{y2:.2f}" stroke="{pal["pattern"]}" '
                 f'stroke-width="1" opacity="0.65"/>')
    for row in prows:
        by = row_of.get(round(row[0][0] - PATTERN_LABEL_GAP, 2), tb.y)
        for left, _right, label in row:
            o.append(f'<text x="{left:.2f}" y="{by:.2f}" class="s-small" '
                     f'text-anchor="start" fill="{pal["pattern"]}">'
                     f'{_esc(label)}</text>')

    # 基础刻度的**文字** —— ⚠ 画在**最末尾**（老板 2026-10）。
    #
    # 这些文字落在时间带的最上面一行，而标记 / 爆音 / 动态模式的**延伸线**都要
    # 从绘图区顶边一路向下穿过整个时间带。早先文字画在那些线**之前**，每条竖线
    # 就都从 `00m00s` 这些数字上压过去 —— 看着像被划掉的字。挪到最后：线先画、
    # 文字后画，文字永远在最顶层。
    #
    # 只挪**文字**，不挪刻度线：刻度线要引出网格，必须留在填充之上、别的元素之下
    # （见上面"网格线画在填充之后"那条）。
    for frac, row in L.TIME_BASE_TICKS:
        x = x_of(frac * duration)
        anchor = "start" if frac == 0.0 else ("end" if frac == 1.0 else "middle")
        by = tb.y + row * L.TIME_BAND_LINE_STEP + L.TIME_BAND_LINE_STEP * ROW_ASCENT
        o.append(f'<text x="{x:.2f}" y="{by:.2f}" class="s-tick" '
                 f'text-anchor="{anchor}">{L.format_time(frac * duration)}</text>')
    o.append("</g>")
    return "\n".join(o)


def _card(box, rows: Sequence[tuple[str, str]], *, value_col: float | None = None,
          height: float | None = None) -> str:
    """一张「标签 + 值」的卡：标签左对齐、值右对齐同一列。

    值先按 `s-val`（16px）量；放不下就退 `s-small`（12px），再放不下退 `s-tiny`
    （10px）并截断。卡只有 200pt 宽，动态卡那几行（`DRP` 的值是
    `4 个模式 / 10 次出现`）不缩就一定会压到标签上 —— 实测撞过。
    """
    h = box.h if height is None else height
    o = [f'<rect class="card" x="{box.x:.2f}" y="{box.y:.2f}" width="{box.w:.2f}" '
         f'height="{h:.2f}"/>']
    left_x = box.x + 12.0
    right = box.right - 12.0
    avail = right - left_x
    for i, (label, value) in enumerate(rows):
        by = box.y + L.LINE_STEP * (i + 0.5) + L.LINE_STEP * 0.32
        lcls, vcls, ltxt, vtxt = _fit_pair(label, value, avail)
        o.append(f'<text x="{left_x:.2f}" y="{by:.2f}" class="{lcls}">'
                 f'{_esc(ltxt)}</text>')
        o.append(f'<text x="{right:.2f}" y="{by:.2f}" class="{vcls} s-tabular" '
                 f'text-anchor="end">{_esc(vtxt)}</text>')
    return "\n".join(o)


def _render_cards(pal: dict, meta: dict, summary: dict, cover_uri: str | None,
                  dyn_h: float) -> str:
    o = ['<g id="cards">']
    mc = L.META_CARD

    # 元数据卡：封面 + 四行标题信息 + 六项文件字段
    o.append(f'<rect class="card" x="{mc.x:.2f}" y="{mc.y:.2f}" '
             f'width="{mc.w:.2f}" height="{mc.h:.2f}"/>')
    cv = L.COVER
    if cover_uri:
        o.append(f'<image x="{cv.x:.2f}" y="{cv.y:.2f}" width="{cv.w:.2f}" '
                 f'height="{cv.h:.2f}" preserveAspectRatio="xMidYMid slice" '
                 f'href="{_esc(cover_uri)}"/>')
    else:
        # 封面占位：颜色也要跟主题 —— 原来硬编码 #DDD，深色模式下会是一块刺眼的亮灰
        o.append(f'<rect x="{cv.x:.2f}" y="{cv.y:.2f}" width="{cv.w:.2f}" '
                 f'height="{cv.h:.2f}" fill="{pal["grid"]}" '
                 f'stroke="{pal["border"]}"/>')
        o.append(f'<text x="{cv.x + cv.w / 2:.2f}" y="{cv.y + cv.h / 2:.2f}" '
                 f'class="s-tiny s-muted" text-anchor="middle" '
                 f'dominant-baseline="middle">封面</text>')

    info = [("标题", meta.get("title")), ("作者", meta.get("artist")),
            ("专辑名", meta.get("album")), ("TrackNumber", meta.get("track"))]
    for i, (label, value) in enumerate(info):
        x    = L.META_TEXT_LATIN[0][1]     # 共用左起点
        ytop = L.META_INFO_Y[i]            # ← 唯一实质改动
        lx = x - 6
        # 值右对齐到封面左边；可用宽度就是"标签起点 → 封面左边"
        lcls, vcls, ltxt, vtxt = _fit_pair(
            label, str(value or "—"), (cv.x - 4) - lx)
        o.append(f'<text x="{lx:.2f}" y="{ytop + 11:.2f}" class="{lcls}">'
                 f'{_esc(ltxt)}</text>')
        o.append(f'<text x="{cv.x - 4:.2f}" y="{ytop + 11:.2f}" '
                 f'class="{vcls} s-muted" text-anchor="end">{_esc(vtxt)}</text>')

    files = [("文件时长", L.format_time(meta.get("duration") or 0)),
             ("声道数", str(meta.get("channels") or "—")),
             ("采样率", f"{meta.get('sampleRate') or 0} Hz"
                      if meta.get("sampleRate") else "—"),
             ("位深", f"{meta.get('bits')} bit" if meta.get("bits") else "—"),
             ("文件大小", meta.get("sizeText") or "—"),
             ("测量算法", meta.get("algorithm") or "—")]
    # 这 6 行在封面**下面**，所以值可以吃到整卡宽度。
    for i, (label, value) in enumerate(files):
        _, x, ytop, _size = L.META_TEXT_CJK[i]
        lx = x - 6
        lcls, vcls, ltxt, vtxt = _fit_pair(label, str(value),
                                           (mc.right - 10) - lx)
        o.append(f'<text x="{lx:.2f}" y="{ytop + 11:.2f}" class="{lcls}">'
                 f'{_esc(ltxt)}</text>')
        o.append(f'<text x="{mc.right - 10:.2f}" y="{ytop + 11:.2f}" '
                 f'class="{vcls} s-tabular" text-anchor="end">{_esc(vtxt)}</text>')

    # 响度卡 6 项
    loud = [(label, _num(summary.get(key), unit=unit))
            for label, key, unit in L.LOUD_CARD_FIELDS]
    o.append(_card(L.LOUD_CARD, loud))
    # 动态卡 4 项（跟时间带同高，底边对齐）
    dyn = [(label, _num(summary.get(key), unit=unit))
           for label, key, unit in L.DYN_CARD_FIELDS]
    o.append(_card(L.DYN_CARD, dyn, height=dyn_h))
    o.append("</g>")
    return "\n".join(o)
