"""Regression for the SVG chart renderer -- assertions on the markup, no browser.

⚠ 为什么不用截图做回归：本机的 `chrome-headless-shell.exe` 被沙箱的命名管道限制挡着
（`[FATAL:mojo/platform_channel.cc] 拒绝访问 (0x5)`），`python tests/...` 里栅格化不了。
但 **SVG 是文本**，所以坐标可以直接断言 —— 这比像素比对还严格（像素比对有抗锯齿噪声）。

    python tests/dsh-wheel/check_loudness_svg.py

覆盖：版式坐标、F 轴的 9 个刻度与红区高度、总响度虚线的位置、时间带三行文字 + 标记夹取 +
爆音标红 + PT_X 行、三张卡的字段、确定性（两次渲染逐字节相同）。
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from backend import chart_axis as A                                    # noqa: E402
from backend import chart_layout as L                                  # noqa: E402
from backend import loudness_svg as S                                  # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


# --------------------------------------------------------------- synthetic data
def _timeline(duration: float = 120.0, *, level: float = -18.0,
              clip_at: tuple[float, float] | None = None,
              flat_truepeak: float = -6.0) -> dict:
    """一段平直的时间线。

    `clip_at` 给出一个爆音时段 —— 它写进 **`summary["clips"]`**，因为渲染器只认那份
    （逐帧采样峰值算出来的，见 `loudness_svg._clip_runs`）。

    ⚠ `truePeak` 仍然会写，而且默认是**单调不减的累计最大值**语义 —— 实测 ebur128
    就是这个语义。默认给一条**递增**的假序列，就是为了让"拿 truePeak 定位爆音"这个
    曾经的 bug **必然**在测试里露头：那样会从第一次越线一路标到结尾。
    """
    n = int(duration * 10)
    t = [round(i / 10, 2) for i in range(n)]
    S_ = [level] * n
    # 累计最大值：起始 -6，若给了爆音时段就单调抬到 1.5 并**保持**（不回落的假数据也
    # 和实测一致：ebur128 的 truePeak 只在出现更大值时更新）
    tp = []
    cur = flat_truepeak
    for ti in t:
        if clip_at and clip_at[0] <= ti <= clip_at[1]:
            cur = max(cur, 1.5)
        tp.append(cur)
    clips = [[clip_at[0], clip_at[1]]] if clip_at else []
    return {"t": t, "M": list(S_), "S": S_, "truePeak": tp, "duration": duration,
            "hz": 10, "frames": n,
            "summary": {"integrated": level, "lra": 4.0, "dra": 4.5, "plr": 12.0,
                        "momentaryMax": level - 1, "shortTermMax": level - 2,
                        "truePeakMax": 1.5 if clip_at else -0.1,
                        "samplePeakMax": 0.0 if clip_at else -0.2,
                        "clips": clips, "clipCount": len(clips),
                        "clipSeconds": round(clip_at[1] - clip_at[0], 1) if clip_at else 0.0,
                        "drp": "2 模式 / 3 次", "pmax": "PT_2 · 3.10 LU",
                        "pmin": "PT_1 · 1.20 LU"}}


def _ys(markup: str) -> dict[str, float]:
    """水平网格线的 y（`class="grid"` 写在行首，别把顺序写反）。"""
    out: dict[str, float] = {}
    for m in re.finditer(r'<line class="grid" x1="([\d.]+)" y1="([\d.]+)" '
                         r'x2="([\d.]+)" y2="([\d.]+)"', markup):
        x1, y1, x2, y2 = (float(g) for g in m.groups())
        if abs(y1 - y2) < 0.01 and abs(x2 - x1) > 100:
            out[round(y1, 2)] = y1
    return out


def _band(markup: str) -> str:
    """时间带那一组（`PT_` 这种字样在动态卡里也有，断言必须限定范围）。"""
    return markup.split('id="time-band"')[1].split("</g>")[0]


def main() -> int:
    data = _timeline()
    svg = S.render_loudness_svg(data, title="示例.flac",
                                meta={"title": "T", "artist": "A", "album": "B",
                                      "track": "3", "channels": 2,
                                      "sampleRate": 44100, "bits": 24,
                                      "sizeText": "1.0 MB",
                                      "algorithm": "ebur128 9.0.2"})

    print("== 版式与根元素 ==")
    check("根元素带 viewBox 且是设计单位",
          f'viewBox="0 0 {L.CANVAS_W:.2f}' in svg, svg[:120])
    check("是响应式的（max-width:100%）", "max-width:100%" in svg)
    check("图表按 id 分了层，便于断言与后续改主题",
          all(f'id="{k}"' in svg for k in
              ("filename", "axis-rail", "plot", "time-band", "cards")))
    check("文件名画在页面左上角（不在元数据卡里）",
          f'x="{L.FILE_NAME_AT[0]:.2f}"' in svg and "示例.flac" in svg)

    print()
    print("== 纵轴：F 的 9 个刻度 ==")
    grid = _ys(svg)
    check("网格线正好 9 条（= 出文字的刻度数）", len(grid) == 9, sorted(grid))
    want = {round(A.y(v, L.PLOT.y, L.PLOT.h), 2) for v in A.TICKS}
    check("每条网格线都落在 axis_spec.frac 算出的位置上",
          set(grid) == want, (sorted(grid), sorted(want)))
    for v in A.TICKS:
        check(f"刻度 {v:g} 有文字", f'>{v:g}</text>' in svg)
    check("0 与 -3 的标签标红", svg.count('class="s-tick s-red s-bold"') == 2,
          svg.count('class="s-tick s-red s-bold"'))
    # 红区高度 = frac(-3) * plot_h
    red_h = A.y(A.RED_ABOVE, 0.0, L.PLOT.h)
    check(f"红区高度 = {red_h:.2f}pt（= frac(-3) × 绘图区高）",
          f'height="{red_h:.2f}" fill="{S.PALETTE["head"]}"' in svg
          or f'height="{red_h:.2f}"' in svg, red_h)
    check("点图边界的散点都在绘图区内",
          all(L.PLOT.y - 0.01 <= y <= L.PLOT.bottom + 0.01 for y in grid))

    print()
    print("== 曲线与总响度虚线 ==")
    # 老板 2026-10："RMS 删去，那条虚线改成 integrated 的 LUFS 值。"
    loud = float(data["summary"]["integrated"])
    loud_y = A.y(loud, L.PLOT.y, L.PLOT.h)
    check("虚线画在 integrated（总响度）那个值上",
          f'y1="{loud_y:.2f}"' in svg and 'stroke-dasharray' in svg, loud_y)
    check("虚线旁边的字是 总响度 + 值 + 单位",
          f'总响度 {loud:.1f} LUFS</text>' in svg)
    check("KNOWN: 整份图里不再出现 RMS（那个量删掉了）",
          "RMS" not in svg and "rms" not in svg)
    # 底边之外的值**不画** —— `A.frac()` 会把越界值夹到边界上，画出来就是假的
    far = _timeline(level=-80.0)
    check("KNOWN: integrated 在轴范围之外时**不画**那条线（不是夹到底边上）",
          "stroke-dasharray" not in S.render_loudness_svg(far, title="t"),
          A.LUFS_BOTTOM)
    check("S 有填充（蓝体）", f'fill="{S.PALETTE["body"]}"' in svg)
    check("−23 参考线在", f'stroke="{S.PALETTE["refLine"]}"' in svg)

    print()
    print("== 时间带：基础刻度**一行** ==")
    by_row = {}
    for m in re.finditer(r'<text x="([\d.]+)" y="([\d.]+)" class="s-tick" '
                         r'text-anchor="(\w+)">(\d+m\d+s)</text>', svg):
        by_row.setdefault(round(float(m.group(2)), 1), []).append(m.group(4))
    # 老板 2026-10："第二个和第三个基础时间标记莫名被换行，他们应当和其他基础时间标记
    # 位于同一行" —— 所以五个必须全在**一行**上。
    check("五个基础刻度全在同一行（没有换行）", len(by_row) == 1, by_row)
    rows = [sorted(v) for _, v in sorted(by_row.items())]
    check("一行里正好是 0 / 1-4 / 1-2 / 3-4 / 1（120s 素材）",
          rows == [["00m00s", "00m30s", "01m00s", "01m30s", "02m00s"]], rows)
    check("起点补零成 00m00s", "00m00s" in svg)

    print()
    print("== 标记：有则出、无则不出，且夹在绘图区内 ==")
    none_svg = S.render_loudness_svg(data, title="t")
    check("没有标记时不出标记行", "s-bold" in none_svg
          and "Marker" not in none_svg, "无标记也画了东西")
    m_svg = S.render_loudness_svg(data, title="t",
                                  markers=[{"time": 0.0, "name": "开头"},
                                           {"time": 60.0, "name": "副歌"},
                                           {"time": 120.0, "name": "结尾"}])
    check("有标记时写出 name", "副歌" in m_svg and "开头" in m_svg)
    check("标记的时间用 12px 更浅的一档", 'class="s-small s-muted"' in m_svg)
    xs = [float(m.group(1)) for m in
          re.finditer(r'<text x="([\d.]+)" y="[\d.]+" class="s-tick s-bold">', m_svg)]
    check("标记名夹在绘图区内（没伸出左右边界）",
          all(L.PLOT.x - 1 <= x <= L.PLOT.right for x in xs), xs)
    check("落在 0.000 的标记不会跑到标尺列里去", min(xs) >= L.PLOT.x - 1, min(xs))

    print()
    print("== 爆音：合并成时段、标红、不标时间 ==")
    c_svg = S.render_loudness_svg(_timeline(clip_at=(40.0, 50.0)), title="t")
    runs = re.findall(r'<rect x="([\d.]+)" y="[\d.]+" width="([\d.]+)" height="4"',
                      c_svg)
    check("爆音时段画成一条红色横条", len(runs) == 1, runs)
    if runs:
        x0, w = float(runs[0][0]), float(runs[0][1])
        want_x0 = L.PLOT.x + 40.0 / 120.0 * L.PLOT.w
        check("红色横条的起点就在 40s 处（按真实时间戳，不是按下标）",
              abs(x0 - want_x0) < 1.5, (x0, want_x0))

    # ⚠ 回归钉子：老板 2026-10 "爆音标红是个地图炮：一有就开始标，一标就从头标到尾"。
    # 那个 bug 的数据源是 `truePeak`，而它是**累计最大值**（单调不减）—— 上面这个
    # fixture 的 truePeak 就故意做成那样。这条断言的意思是：**渲染器完全不许看它**。
    # 判据：一份 `truePeak` 早早越线、`summary["clips"]` 却只有一小段的输入，
    # 画出来必须**只有那一段**，绝不能从越线处一路标到底。
    sneaky = _timeline(120.0, clip_at=(10.0, 12.0))
    sneaky["summary"]["clips"] = [[10.0, 12.0]]      # 只有 2s
    s_svg = S.render_loudness_svg(sneaky, title="t")
    s_runs = re.findall(r'<rect x="([\d.]+)" y="[\d.]+" width="([\d.]+)" height="4"',
                        s_svg)
    check("KNOWN: 渲染器**不看** truePeak —— 累计最大值越线后仍只标 2s",
          len(s_runs) == 1, s_runs)
    if s_runs:
        w_pt = float(s_runs[0][1])
        check("KNOWN: 那一段的宽度就是 2s（不是从 10s 到结尾的 110s）",
              abs(w_pt - 2.0 / 120.0 * L.PLOT.w) < 1.5,
              (w_pt, 2.0 / 120.0 * L.PLOT.w))
    # 没有 `clips`（旧缓存 / 手工构造的数据）时**什么都不画**，而不是退回 truePeak
    bare = _timeline(120.0, clip_at=(10.0, 12.0))
    bare["summary"].pop("clips")
    check("KNOWN: summary 里没有 clips 就不画爆音（**不许退回 truePeak**）",
          len(re.findall(r'height="4"', S.render_loudness_svg(bare, title="t"))) == 0)
    # `clip_runs=` 可以显式注入（任务路径与测试都用得上）
    check("`clip_runs=` 能直接给时段（不读 summary）",
          len(re.findall(r'height="4"', S.render_loudness_svg(
              bare, title="t", clip_runs=[(5.0, 6.0), (20.0, 21.0)]))) == 2)
    # 单帧爆音（起始 == 结束）**必须画出来**：`audio.py` 里 `ts[start], ts[i-1]`
    # 落在同一帧时就是这样。实测 Stellar 的 99 段里有 10 段是单帧，早先被过滤器丢掉了。
    one_frame = S.render_loudness_svg(bare, title="t", clip_runs=[(5.0, 5.0)])
    fm = re.findall(r'<rect x="([\d.]+)" y="[\d.]+" width="([\d.]+)" height="4"',
                    one_frame)
    check("KNOWN: 单帧爆音（起始==结束）也画成一条可见的红条（最小 2pt 宽）",
          len(fm) == 1 and abs(float(fm[0][1]) - 2.0) < 0.01, fm)
    # 密集时报"红帘子"：引导线穿过绘图区、段一多就把包络盖住（实测 99 段 ⇒ 198 条线
    # 铺在 660pt 上）。段少时引导线要留着（它回答"这条红标指的是哪一刻"）。
    def _guides(svg_: str) -> int:
        return len(re.findall(r'y1="[\d.]+" x2="[\d.]+" y2="[\d.]+" stroke="#[0-9A-F]{6}" '
                              r'stroke-width="1" opacity="0.35"', svg_))

    sparse = S.render_loudness_svg(bare, title="t", clip_runs=[(5.0, 6.0), (20.0, 21.0)])
    check("爆音段少时保留穿过绘图区的引导线", _guides(sparse) == 4, _guides(sparse))
    # 造 60 段挤在一起：间距远小于 CLIP_GUIDE_MIN_GAP_PT
    many = [(30.0 + i * 0.2, 30.1 + i * 0.2) for i in range(60)]
    dense_svg = S.render_loudness_svg(bare, title="t", clip_runs=many)
    check("KNOWN: 爆音段密集成帘子时引导线整批退场（只留时间带里的红条）",
          _guides(dense_svg) == 0, _guides(dense_svg))
    check("但密集时那 60 条红条一条都不能少",
          len(re.findall(r'height="4"', dense_svg)) == 60,
          len(re.findall(r'height="4"', dense_svg)))
    check("爆音不写任何时间文字（时间带里只该有那 5 个基础刻度）",
          len(re.findall(r'>\d\dm\d\ds</text>', _band(c_svg))) == 5,
          re.findall(r'>\d\dm\d\ds</text>', _band(c_svg)))

    print()
    print("== PT_X：每一次出现都标 ==")
    pats = [{"start": 10.0, "end": 20.0, "pattern": 1},
            {"start": 30.0, "end": 40.0, "pattern": 2},
            {"start": 80.0, "end": 90.0, "pattern": 1}]
    p_svg = S.render_loudness_svg(data, title="t", patterns=pats)
    band = _band(p_svg)
    # 规格是"标出不同动态模式的**起止**处"，所以 3 次出现 = 6 个边界：
    # PT_1 ×4（两次出现的首尾）+ PT_2 ×2
    # 老板 2026-10："响度模式标注写在模式对应的刻度开头，模式结束的刻度不需要写。"
    # 3 次出现（PT_1 ×2 + PT_2 ×1）-> **3 个标签**，但边界刻度线是 6 条。
    check("只在每一次出现的**起点**写标签（3 段 -> 3 个标签）",
          band.count(">PT_1</text>") == 2 and band.count(">PT_2</text>") == 1,
          (band.count(">PT_1</text>"), band.count(">PT_2</text>")))
    check("没有模式时时间带里不出现 PT_", ">PT_" not in _band(none_svg))

    print()
    print("== 时间刻度线的锚法（老板 2026-10：PT 指代不清）==")
    import re as _re
    vlines: dict[float, tuple[float, float]] = {}
    for a, b, c, d in _re.findall(
            r'<line x1="([\d.]+)" y1="([\d.]+)" x2="([\d.]+)" y2="([\d.]+)"', band):
        if abs(float(a) - float(c)) < 0.01:
            vlines[round(float(a), 2)] = (float(b), float(d))
    ptexts = [round(float(m.group(1)), 2) for m in _re.finditer(
        r'<text x="([\d.]+)" y="[\d.]+" class="s-small" text-anchor="start"', band)]
    check("PT 标签都是左对齐（不再是压在线上居中）", len(ptexts) == 3, ptexts)
    check("每个 PT 标签的**左侧**都有它自己那条延伸线",
          all(any(abs(lx - (px - S.PATTERN_LABEL_GAP)) < 0.02 for lx in vlines)
              for px in ptexts), (ptexts, sorted(vlines)[:8]))
    _owned = [vlines[lx] for lx in vlines
              if any(abs(lx - (px - S.PATTERN_LABEL_GAP)) < 0.02 for px in ptexts)]
    check("那条线从**绘图区顶边**开始（刻度线延伸进图内）",
          all(y1 == L.PLOT.y for y1, _ in _owned), _owned[:3])
    check("并且一直向下伸到标签那一行（伸到绘图区之外）",
          all(y2 > L.PLOT.bottom for _, y2 in _owned), _owned[:3])
    # 基础刻度与标记同样要在绘图区里画到底
    _base = [vlines[round(L.PLOT.x + L.PLOT.w * f, 2)] for f, _ in L.TIME_BASE_TICKS
             if round(L.PLOT.x + L.PLOT.w * f, 2) in vlines]
    check("基础刻度的竖线也都从绘图区顶边起", len(_base) == 5 and all(
        y1 == L.PLOT.y for y1, _ in _base), _base)

    print()
    print("== 网格线要压在填充之上（老板：横线被挡住了）==")
    plot = svg.split('id="plot"')[1].split("</g>")[0]
    check("网格线出现在包络填充**之后**",
          plot.find('class="grid"') > plot.find('fill="' + S.PALETTE["body"] + '"'),
          (plot.find('class="grid"'),
           plot.find('fill="' + S.PALETTE["body"] + '"')))
    check("网格线又排在总响度虚线**之前**（不压住它）",
          plot.find('class="grid"') < plot.find("stroke-dasharray"),
          (plot.find('class="grid"'), plot.find("stroke-dasharray")))

    print()
    print("== 三张卡：标签 + 值 ==")
    for label, _k, _u in L.LOUD_CARD_FIELDS:
        check(f"响度卡有「{label}」", label in svg)
    for label, _k, _u in L.DYN_CARD_FIELDS:
        check(f"动态卡有「{label}」", label in svg)
    check("动态卡显示的是 DRP/PMAX/PMIN 的字符串值，不是 —",
          "PT_2 · 3.10 LU" in svg and "PT_1 · 1.20 LU" in svg)
    for label in ("文件时长", "声道数", "采样率", "位深", "文件大小", "测量算法"):
        check(f"元数据卡有「{label}」", label in svg)
    check("旧的 8 张 footer 卡里的 PLR / DIAL 不再出现",
          "DIAL" not in svg and ">PLR<" not in svg)
    check("测量算法用的是短写法", "ebur128 9.0.2" in svg)

    print()
    print("== 时间带是动态框 ==")
    tall = S.render_loudness_svg(
        data, title="t",
        markers=[{"time": i * 5.0, "name": f"标记{i}"} for i in range(8)],
        patterns=pats)
    h_none = float(re.search(r'viewBox="0 0 [\d.]+ ([\d.]+)"', none_svg).group(1))
    h_tall = float(re.search(r'viewBox="0 0 [\d.]+ ([\d.]+)"', tall).group(1))
    check("标记多了画布会长高（时间带是动态的）", h_tall > h_none,
          (h_none, h_tall))
    check("绘图区高度不受影响（grow=canvas：轴映射不用重算）",
          _ys(svg) == _ys(tall))

    print()
    print("== 确定性 / 健壮性 ==")
    a1 = S.render_loudness_svg(data, title="t", markers=[{"time": 1.0, "name": "x"}])
    a2 = S.render_loudness_svg(data, title="t", markers=[{"time": 1.0, "name": "x"}])
    check("同一份输入两次渲染逐字节相同", a1 == a2)
    # 旧 PNG 渲染器的测试退役时，把这两条仍然有价值的覆盖搬了过来
    quiet = S.render_loudness_svg(_timeline(level=-40.0), title="t")
    check("数据变了 -> 图也变（不是画死的模板）", quiet != svg)
    for name, bad in (("时长为 0", {"duration": 0, "t": [], "S": [], "truePeak": []}),
                      ("只有一帧", {"duration": 1.0, "t": [0.0], "S": [-20.0],
                                   "truePeak": [-6.0]}),
                      ("序列里全是 None", {"duration": 5.0, "t": [0.0, 1.0, 2.0],
                                          "S": [None] * 3, "truePeak": [None] * 3})):
        try:
            out = S.render_loudness_svg(bad, title="t")
            check(f"退化输入（{name}）不崩，且仍是完整 SVG",
                  out.startswith("<svg") and out.rstrip().endswith("</svg>"))
        except Exception as e:                                     # noqa: BLE001
            check(f"退化输入（{name}）不崩", False, f"{type(e).__name__}: {e}")

    body = svg.split("</style>", 1)[1]        # CSS 里有 `{}`，只查正文
    check("正文里没有留下 Python 的 repr 或 None",
          "None" not in body and "{" not in body and "}" not in body
          and "[" not in body)
    check("中文没有被替换成问号（SVG 用看的人的字体）",
          "?" not in body.replace("&#", ""))

    # ---------------------------------------------------------------- 主题
    #
    # 老板 2026-10："svg 生成的主题颜色改成用户执行链时主题的，并在导出时将颜色硬编码
    # 入文件。" 所以这里钉两件事：**颜色全部是实色字面量**（没有任何 `var()`），
    # 以及**六套主题确实出六张不同的图**。
    print()
    print("== 主题（t1/t2/t3 × light/dark，颜色硬编码）==")
    from backend import theme as T                                     # noqa: E402
    check("theme.css 解析出 6 套（3 主题 × 2 模式）",
          T.available() == [(t, m) for t in T.THEMES for m in T.MODES],
          T.available())
    check("theme.css 是唯一来源（模块里只有一处兜底色值）",
          T.CSS_PATH.name == "theme.css" and T.CSS_PATH.exists())

    lit = {}
    for th, md in T.available():
        out = S.render_loudness_svg(data, title="测试.wav", theme=th, mode=md)
        pal = S.chart_palette(th, md)
        lit[(th, md)] = out
        check(f"{th}/{md}：整份文件没有 `var(`（颜色已硬编码）", "var(" not in out)
        check(f"{th}/{md}：各处颜色都等于 `chart_palette()` 算出来的实色",
              f'fill="{pal["bg"]}"' in out and f'fill:{pal["text"]}' in out
              and f'fill:{pal["card_fill"]}' in out and f'fill:{pal["redText"]}' in out)
        check(f"{th}/{md}：没有 :root 块（内联进页面时不会污染页面的变量）",
              ":root" not in out)
        check(f"{th}/{md}：颜色只有 #RRGGBB（没有 rgba/alpha 残留）",
              "rgba(" not in out)

    check("六套主题两两不同（不是同一张图换个名字）",
          len(set(lit.values())) == 6, len(set(lit.values())))
    check("KNOWN: 深色模式的画布就是深色（不是只改了文字）",
          T.luminance(T.parse_color(S.chart_palette("t1", "dark")["bg"]))
          < T.luminance(T.parse_color(S.chart_palette("t1", "light")["bg"])))
    check("KNOWN: 主题给不出时落回 t1 浅色，且不报错（一张图不值得让链失败）",
          S.render_loudness_svg(data, title="t", theme="不存在的主题")
          == S.render_loudness_svg(data, title="t", theme="t1", mode="light"))
    # 红区文字是**派生**的：主题里的 `--error-solid` 是"给白字当底"用的，直接当文字色
    # 在深色模式下对比度只有 ~2.2，所以按 AA 4.5 推过亮度。这条断言就是钉那个推导。
    print()
    for th, md in T.available():
        toks = T.tokens(th, md)
        red = T.parse_color(S.chart_palette(th, md)["redText"])
        ratio = T.contrast(red, toks["bg-app"])
        check(f"{th}/{md}：红区文字对画布的对比度 ≥ AA 4.5（实算 {ratio:.2f}）",
              ratio >= T.AA_RATIO, ratio)
    check("KNOWN: 直接拿 --error-solid 当深色模式的文字**会**不达标"
          "（所以才有上面那条派生）",
          T.contrast(T.tokens("t1", "dark")["error-solid"],
                     T.tokens("t1", "dark")["bg-app"]) < T.AA_RATIO)
    # 总响度虚线 vs −23 参考线：**必须分得开**。实测踩过：`--ink` 与 `--ink-accent`
    # 在 t2/t3 里同值，两条水平线会撞成一个颜色。
    print()
    for th, md in T.available():
        pal = S.chart_palette(th, md)
        plot = T.parse_color(pal["plot_fill"])
        check(f"{th}/{md}：总响度虚线与参考线不同色",
              pal["loudLine"] != pal["refLine"], (pal["loudLine"], pal["refLine"]))
        check(f"{th}/{md}：总响度虚线在绘图区上看得见（对底色 ≥ 2.5）",
              T.contrast(T.parse_color(pal["loudLine"]), plot) >= 2.5,
              T.contrast(T.parse_color(pal["loudLine"]), plot))
        check(f"{th}/{md}：PT_X 标注与总响度虚线同色（都是次要墨色）",
              pal["pattern"] == pal["loudLine"], (pal["pattern"], pal["loudLine"]))
    # "面淡、标记浓"这个层级：主题里只有**一支**警戒色，所以层级靠透明度表达。
    # 实测踩过：红冠漏了 opacity，变成纯浓红、比红区带跳一大截。
    # ⚠ 用一段**真的越过 −3** 的素材，否则红冠那条路径根本不会被画出来（漏测）。
    loud = _timeline(120.0, level=-1.0)
    for th, md in T.available():
        out = S.render_loudness_svg(loud, title="t", theme=th, mode=md)
        pal = S.chart_palette(th, md)
        check(f"{th}/{md}：红区带与红冠都按面透明度画（`red` 才是不透明的标记色）",
              out.count(f'fill="{pal["head"]}" opacity="{S.AREA_TINT}"') == 2,
              out.count(f'fill="{pal["head"]}" opacity="{S.AREA_TINT}"'))

    print()
    print(f"结果：{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
