"""响度总览图 PNG 自检（`响度总览图（LoudnessAnalysis）实现构想.md` §3）。

纯函数 + 真文件两层：

  · **纵轴分段映射**：`axis_y` 控制点必须逐点命中（§1.6 —— 用线性公式
    画出来的形状跟原图对不上，而这是整张图最容易悄悄退化的地方）
  · **版面**：整图约 6.6:1、绘图区约 1:1.07；`width` 参数按比例重排
  · **红带口径**：留空 = `Integrated + LRA/2`；给了值就用给的
  · **静音底**：`-120.x` 不算数据（§5.4），否则曲线开头掉到谷底
  · **确定性**：同一份数据两次渲染逐字节相同（否则缓存/回归都没法用）
  · **真文件**：拿 uploads 里的 flac 跑一趟，断言 PNG 头与尺寸

不需要服务；真文件那节在 uploads/ 为空时自动跳过。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend import audio                                            # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


def _fake(duration=100.0, *, m=-30.0, integrated=-14.0, lra=8.0):
    n = int(duration * 10)
    ts = [i / 10.0 for i in range(n)]
    mv = [-120.691] * 3 + [m + (i % 7) for i in range(n - 3)]
    return {
        "version": audio.CACHE_VERSION, "key": "fake", "duration": duration,
        "hz": 10, "frames": n, "t": ts, "M": mv,
        "S": [-120.691] * n, "I": [-70.0] * n, "truePeak": [0.001] * n,
        "summary": {"integrated": integrated, "lra": lra, "plr": 11.4,
                    "momentaryMax": m + 6, "shortTermMax": m + 5,
                    "truePeakMax": 1.5},
    }


try:
    # ---------------------------------------------------------------- 1
    print("== 1. 纵轴是分段映射，不是线性公式 ==")
    pts = audio.AXIS_Y
    check("控制点就是文档 §3.2 那一组",
          pts == ((-13, 90), (-18, 253), (-23, 342), (-27, 597),
                  (-36, 784), (-45, 1141), (-54, 1489)), pts)
    ok_pts = True
    for lufs, y in pts:
        top, bot = pts[0][1], pts[-1][1]
        want = (y - top) / (bot - top)
        got = audio._axis_frac(lufs)
        if abs(got - want) > 1e-9:
            ok_pts = False
            print(f"        {lufs} LUFS 期望 {want:.4f}，实际 {got:.4f}")
    check("每个控制点都精确命中（分段线性插值）", ok_pts)
    check("最响端 (−13) 在顶部", abs(audio._axis_frac(-13) - 0.0) < 1e-9)
    check("最轻端 (−54) 在底部", abs(audio._axis_frac(-54) - 1.0) < 1e-9)
    # 有效响度区被**放大**：−23→−27 这一档（只 4 LU）的屏幕跨度
    # 应当**大于** −13→−18（5 LU）—— 这正是"非线性"的可观测后果。
    span_lo = abs(audio._axis_frac(-23) - audio._axis_frac(-27))
    span_hi = abs(audio._axis_frac(-13) - audio._axis_frac(-18))
    check("−23~−27 这档被放大（4 LU 的跨度 > 5 LU 的跨度）",
          span_lo > span_hi, (span_lo, span_hi))
    # 对照原图实测（文档 §1.6 的表，单位是 viewBox）：
    #   −13→−18 = 163、−18→−23 = 89、**−23→−27 = 255**、−36→−45 = 357
    # 所以"有效响度区被放大"指的是 **255 : 163 ≈ 1.56**（4 LU 的跨度比 5 LU 还大）。
    # 1536/1489 是整轴跨度，两个值相除就是同一把尺子量出来的比例。
    axis_span = audio.AXIS_Y[0][1] - audio.AXIS_Y[-1][1]      # 1489 - 90 = 1399
    got = span_lo * axis_span / (span_hi * axis_span) if span_hi else 0
    want = 255 / 163
    check("放大倍数与原图实测一致（255:163 ≈ 1.56）",
          abs(got - want) < 0.02, (got, want))
    check("单调递增（响度越低越靠下）",
          all(audio._axis_frac(a) <= audio._axis_frac(b) + 1e-9
              for a, b in zip([-13, -18, -23, -27, -36, -45],
                              [-18, -23, -27, -36, -45, -54])))
    check("超出控制点范围**夹住**不外推（静音底 −120 也落在底部）",
          abs(audio._axis_frac(-120.0) - 1.0) < 1e-9)
    check("比 −13 还响也夹在顶部", abs(audio._axis_frac(-5.0) - 0.0) < 1e-9)

    # ---------------------------------------------------------------- 2
    print()
    print("== 2. 版面比例与 width 参数 ==")
    from PIL import Image
    b0 = audio.render_loudness_png(_fake(), title="t")
    im0 = Image.open(io.BytesIO(b0))
    check("默认尺寸 2400 宽", im0.size == (2400, 430), im0.size)
    # ⚠ 文档 §3.3 里"整图 6.6:1"与"绘图区 1:1.07"**互相推不出来**
    # （2400 宽若按绘图区 1:1.07 算，绘图区就高 2243px，整图会接近 1:1）。
    # 那两个数是各自从原图不同坐标系量的。这里取**实用的中间值**：
    # 宽高比落在 5~7 之间（够扁、footer 文字又还看得清），并把它钉住 ——
    # 真正的回归锚点是"control 点映射"和"确定性"，不是这个比例。
    ratio0 = im0.size[0] / im0.size[1]
    check("整图宽高比落在 5~7:1（够扁，footer 还看得清）",
          5.0 <= ratio0 <= 7.0, ratio0)
    check("输出是真的 PNG（头 8 字节）",
          b0[:8] == b"\x89PNG\r\n\x1a\n", b0[:8])
    for w in (800, 1600, 4800):
        im = Image.open(io.BytesIO(audio.render_loudness_png(_fake(), width=w)))
        ratio = im.size[0] / im.size[1]
        check(f"width={w} 时比例与默认一致（{im.size[0]}×{im.size[1]}）",
              abs(ratio - ratio0) < 0.15, (im.size, ratio, ratio0))
    im_big = Image.open(io.BytesIO(audio.render_loudness_png(_fake(), width=4800)))
    check("放大是**重新排版**不是拉伸（4800 比 2400 的字节数更多）",
          len(audio.render_loudness_png(_fake(), width=4800)) > len(b0))

    # ---------------------------------------------------------------- 3
    print()
    print("== 3. 静音底与红带口径 ==")
    # 静音底（-120.691）不进重采样
    cols = audio._resample_columns([0.0, 0.1, 0.2], [-120.691, -30.0, -31.0], 10, 1.0)
    check("静音底被剔除，不当成数据点",
          all(v is None or v > -100 for v in cols), cols)
    check("有效点进得来", any(v is not None for v in cols), cols)
    # 段内取**最大值**（不是平均）—— §2.3
    cols2 = audio._resample_columns([0.0, 0.05, 0.1], [-40.0, -10.0, -40.0], 1, 1.0)
    check("重采样取段内最大值（平均会把峰值削平）",
          cols2 == [-10.0], cols2)
    check("时长 <= 0 不炸，返回全 None",
          audio._resample_columns([], [], 5, 0.0) == [None] * 5)
    # 红带默认阈值 = Integrated + LRA/2：两个 LRA 不同的输入应画出不同的图
    a = audio.render_loudness_png(_fake(integrated=-20.0, lra=2.0), title="t")
    b = audio.render_loudness_png(_fake(integrated=-20.0, lra=20.0), title="t")
    check("红带阈值随 LRA 变（留空 = Integrated + LRA/2）", a != b)
    # 显式给 highLufs 时不该再看 LRA。
    # ⚠ 不能直接比整图：footer 的 LRA 卡会显示不同数字，图本来就不一样。
    # 只比**绘图区**（footer 以上那一块）才对得上"红带口径"这一件事。
    def _plot_only(png_bytes: bytes):
        im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        return im.crop((0, 0, im.size[0], im.size[1] - int(round(88 * im.size[0] / 2400))))

    a2 = _plot_only(audio.render_loudness_png(_fake(integrated=-20.0, lra=2.0),
                                              title="t", high_lufs=-15.0))
    b2 = _plot_only(audio.render_loudness_png(_fake(integrated=-20.0, lra=20.0),
                                              title="t", high_lufs=-15.0))
    check("显式给 highLufs 时**绘图区**不再受 LRA 影响",
          a2.tobytes() == b2.tobytes())

    # ---------------------------------------------------------------- 4
    print()
    print("== 4. 确定性与边界 ==")
    d = _fake()
    check("同一份数据两次渲染**逐字节相同**",
          audio.render_loudness_png(d, title="t")
          == audio.render_loudness_png(d, title="t"))
    check("全静音（没有有效点）也能出图，不抛异常",
          audio.render_loudness_png(
              _fake(m=-120.5), title="t")[:8] == b"\x89PNG\r\n\x1a\n")
    check("空数据也能出图（不抛异常）",
          audio.render_loudness_png({"duration": 0, "t": [], "M": [],
                                     "summary": {}}, title="")[:8]
          == b"\x89PNG\r\n\x1a\n")
    check("没给 summary 也能出图",
          audio.render_loudness_png({"duration": 10, "t": [0, 5],
                                     "M": [-30, -20]},
                                    title="t")[:8] == b"\x89PNG\r\n\x1a\n")
    check("`—` 用于缺失指标（不是 `None` 字样）", audio._fmt_lufs(None) == "—")
    check("指标保留一位小数", audio._fmt_lufs(-14.25) == "-14.2")
    check("标题里的非 ASCII 有兜底函数（拿不到 CJK 字体时用）",
          audio._ascii_title("かめりあ-x") == "????-x")

    # ---------------------------------------------------------------- 5
    print()
    print("== 5. 真文件（uploads/ 里有 flac 才跑）==")
    src = next((p for p in (audio.config.UPLOADS).rglob("*")
                if p.suffix.lower() in (".flac", ".wav", ".mp3")), None)
    if not src:
        print("        （uploads/ 里没有音频，跳过）")
    else:
        data = audio.loudness_timeline(src, force=False)
        s = data.get("summary") or {}
        check("真文件拿到完整时间线",
              bool(data.get("t")) and data.get("duration", 0) > 0,
              (len(data.get("t") or []), data.get("duration")))
        check("6 项实测指标都在（footer 8 格里另两格是未启用的 DIAL 占位）",
              set(s) >= {"integrated", "lra", "plr", "momentaryMax",
                         "shortTermMax", "truePeakMax"}, sorted(s))
        png = audio.render_loudness_png(data, title=src.stem)
        im = Image.open(io.BytesIO(png))
        check("真文件出图尺寸正确", im.size == (2400, 430), im.size)
        check("真文件出图不是空白（字节数 > 8KB）", len(png) > 8000, len(png))
        # 图里应当同时出现蓝体与红带两种颜色（说明两条曲线都画上了）
        colors = {c for _, c in (im.convert("RGB").getcolors(maxcolors=1 << 20) or [])}
        check("图里有蓝体色", tuple(int(audio.LOUD_COLORS["body"][i:i + 2], 16)
                                    for i in (1, 3, 5)) in colors)
        check("图里有红色（说明有段落超过红带阈值）",
              tuple(int(audio.LOUD_COLORS["head"][i:i + 2], 16)
                    for i in (1, 3, 5)) in colors)
        check("真文件渲染也是确定性的",
              audio.render_loudness_png(data, title=src.stem) == png)

finally:
    pass

print()
print(f"结果：{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
