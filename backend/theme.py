"""主题色板的**解析器** —— `theme.css` 是唯一来源，这里不复制任何色值。

为什么需要它
------------
SVG 图有两种活法：嵌在页面里（那时它能继承页面的 CSS 变量）和被导出成独立文件
（那时**没有任何变量**）。老板 2026-10 要的是后者也正确：图按"**执行链那一刻用户选的
主题**"上色，而且颜色**硬编码进文件**。所以服务端必须能自己算出那 6 套色板。

为什么是"解析"而不是"抄一份常量"
--------------------------------
色值是设计师在 `theme.css` 里调的。抄一份出来**一定会脱节** —— t2 就漏跟过一次，
`tests/browser_palette_probe.js` 那 200 行就是为那次写的。这里读同一份文件，脱节
不可能发生。

6 套 = 3 主题（t1/t2/t3）× 2 模式（light/dark），取自
`:root[data-theme="tN"]` 与 `:root[data-theme="tN"][data-mode="dark"]`。

半透明令牌会被**压平成实色**
----------------------------
`theme.css` 里有 `rgb(36 68 94 / 0.62)` 这种带 alpha 的令牌（用于描边、次要文字、
投影）。HTML 里它叠在某个底色上；SVG 里如果直接写 `rgba(...)`，一部分查看器
（旧版 Inkscape、图片转换器）会把它当无效色丢掉，而老板要的正是"硬编码"。
所以按**它实际所在的那层底**压平成实色，视觉效果与页面一致。
"""
from __future__ import annotations

import re
from pathlib import Path

#: `theme.css` 的位置（组件库目录 `ui/`，见 `ui/README.md`）。**唯一来源**。
CSS_PATH = Path(__file__).resolve().parent.parent / "ui" / "theme.css"

#: 主题 × 模式
THEMES = ("t1", "t2", "t3")
MODES = ("light", "dark")
DEFAULT_THEME = "t1"
DEFAULT_MODE = "light"

#: WCAG AA 对正文的下限。红字那类"派生色"要按它校验（见 `ensure_contrast`）。
AA_RATIO = 4.5

_RGB = tuple[int, int, int]

#: `:root[data-theme="t1"]` / `:root[data-theme="t1"][data-mode="dark"]`
_BLOCK = re.compile(r':root\[data-theme="(?P<theme>t\d+)"\]'
                    r'(?:\[data-mode="(?P<mode>dark)"\])?\s*\{(?P<body>[^}]*)\}', re.S)
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_DECL = re.compile(r"--(?P<name>[\w-]+)\s*:\s*(?P<value>[^;]+);")
_HEX = re.compile(r"^#(?P<h>[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")
_FUNC = re.compile(r"^rgba?\((?P<body>[^)]*)\)$")

#: token → (r, g, b, a)。模块级缓存，`theme.css` 一进程只解析一次。
_CACHE: dict[tuple[str, str], dict[str, tuple[int, int, int, float]]] | None = None


# ------------------------------------------------------------------ 颜色小工具

def parse_color(value: str) -> tuple[int, int, int, float]:
    """`#RGB` / `#RRGGBB` / `rgb(r g b)` / `rgb(r g b / a)` / `rgba(r,g,b,a)` → RGBA。

    只认 `theme.css` 里实际出现过的写法。认不出来就抛 —— **不要静默返回黑色**：
    那会让"主题没生效"变成一个看不出原因的视觉问题。
    """
    v = str(value).strip()
    m = _HEX.match(v)
    if m:
        h = m.group("h")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0
    m = _FUNC.match(v)
    if m:
        body = m.group("body").replace(",", " ").replace("/", " ")
        parts = [x for x in body.split() if x]
        if len(parts) not in (3, 4):
            raise ValueError(f"颜色分量数不对：{value!r}")
        if parts[0].endswith("%"):
            r, g, b = (round(float(x.rstrip("%")) * 2.55) for x in parts[:3])
        else:
            r, g, b = (int(round(float(x))) for x in parts[:3])
        a = float(parts[3]) if len(parts) == 4 else 1.0
        return r, g, b, a
    raise ValueError(f"认不出的颜色写法：{value!r}")


def flat(color: tuple[int, int, int, float], bg: tuple[int, int, int, float]
         ) -> tuple[int, int, int, float]:
    """把带 alpha 的 `color` 压在 `bg` 上，得到实色。"""
    a = color[3]
    return (round(color[0] * a + bg[0] * (1 - a)),
            round(color[1] * a + bg[1] * (1 - a)),
            round(color[2] * a + bg[2] * (1 - a)), 1.0)


def to_hex(color: tuple[int, int, int, float]) -> str:
    """`#RRGGBB`。分量在这里**取整** —— `ensure_contrast` 的插值是浮点的。"""
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(c))) for c in color[:3])


def _lin(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color: tuple[int, int, int, float]) -> float:
    return (0.2126 * _lin(color[0]) + 0.7152 * _lin(color[1])
            + 0.0722 * _lin(color[2]))


def contrast(a: tuple[int, int, int, float], b: tuple[int, int, int, float]
             ) -> float:
    """WCAG 对比度。口径与 `tests/theme_check.py` 一致（AA 正文 4.5）。"""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def ensure_contrast(fg: tuple[int, int, int, float], bg: tuple[int, int, int, float],
                    min_ratio: float = AA_RATIO) -> tuple[int, int, int, float]:
    """把 `fg` 朝背离 `bg` 的方向**逐步调整**，直到对比度达标（最多 50 步 × 2%）。

    为什么需要：主题里那些"强色块"令牌（`--error-solid`）是给**白字压在上面**用的，
    当它反过来当**落在画布上的文字色**时，浅色模式够（#aa4141 对白 ≈ 6.0），
    深色模式就不够了（#9c3f3f 对 #1D252D ≈ 2.2）。这里只改亮度、不动色相 ——
    红还是红，只是换成那个底色上看得清的红。

    已经是实色（alpha = 1）时才调整；色相由"朝白/朝黑插值"保持。

    ⚠ 判据对着**取整后**的颜色（也就是真正写进文件的 `#RRGGBB`）。
    实测踩过：拿插值中的浮点色判"已达标"，`#……` 再一取整就掉回 4.498 < 4.5，
    于是断言红在一个只差 0.002 的地方。
    """
    def rounded(c) -> tuple[int, int, int, float]:
        return (int(round(c[0])), int(round(c[1])), int(round(c[2])), 1.0)

    if contrast(rounded(fg), bg) >= min_ratio:
        return rounded(fg)
    target = (255, 255, 255, 1.0) if luminance(bg) < luminance(fg) else (0, 0, 0, 1.0)
    cur = fg
    for _ in range(60):
        cur = tuple(cur[i] + (target[i] - cur[i]) * 0.02 for i in range(4))
        if contrast(rounded(cur), bg) >= min_ratio:
            return rounded(cur)
    return rounded(cur)


# ------------------------------------------------------------------ 解析 theme.css

def _load() -> dict[tuple[str, str], dict[str, tuple[int, int, int, float]]]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        text = _COMMENT.sub("", CSS_PATH.read_text(encoding="utf-8"))
    except OSError:
        _CACHE = {}
        return _CACHE
    palettes: dict[tuple[str, str], dict[str, tuple[int, int, int, float]]] = {}
    for m in _BLOCK.finditer(text):
        theme = m.group("theme")
        mode = "dark" if m.group("mode") else "light"
        toks: dict[str, tuple[int, int, int, float]] = {}
        for d in _DECL.finditer(m.group("body")):
            try:
                toks[d.group("name")] = parse_color(d.group("value"))
            except ValueError:
                continue            # 认不出的写法跳过，不让一条拖垮整套
        if toks:
            palettes[(theme, mode)] = toks
    _CACHE = palettes
    return _CACHE


def available() -> list[tuple[str, str]]:
    """实际解析出来的 (theme, mode)，已按固定顺序排好。"""
    p = _load()
    return [(t, m) for t in THEMES for m in MODES if (t, m) in p]


def tokens(theme: str, mode: str) -> dict[str, tuple[int, int, int, float]]:
    """某一套的原始令牌（**含 alpha**）。取不到时退回默认主题。"""
    p = _load()
    key = (str(theme or DEFAULT_THEME).strip().lower(),
           "dark" if str(mode or "").strip().lower() == "dark" else "light")
    if key in p:
        return p[key]
    if (DEFAULT_THEME, DEFAULT_MODE) in p:
        return p[(DEFAULT_THEME, DEFAULT_MODE)]
    return {}


def normalize(theme: str | None, mode: str | None) -> tuple[str, str]:
    """把客户端传来的值规范化成 `(theme, mode)`；认不出就用默认（**不报错**）。"""
    t = str(theme or "").strip().lower()
    m = str(mode or "").strip().lower()
    return (t if t in THEMES else DEFAULT_THEME,
            "dark" if m == "dark" else "light")


def solid(theme: str, mode: str, token: str, on: str = "bg-surface",
          fallback: str = "#808080") -> str:
    """取某个令牌的**实色** `#RRGGBB`，带 alpha 的按 `on` 令牌那层底压平。

    `on` 是它实际压着的那层底（默认卡片/绘图区底色 `--bg-surface`）——
    次要文字、描边、投影都是这么用的。
    """
    t = tokens(theme, mode)
    if token not in t:
        return fallback
    color = t[token]
    if color[3] >= 0.999:
        return to_hex(color)
    base = t.get(on) or (255, 255, 255, 1.0)
    return to_hex(flat(color, base))
