"""卡片 hover 的彩色半调（网点）验证。

分两段：

  A. 结构断言（走 CDP 的 Runtime.evaluate）
     两层网点是否真的落地、格距/半格错位对不对、遮罩是否还在跟 --mx/--my、
     六套主题下两种 ink 是否跟着 token 走。

  B. 像素证伪（这是决定性的那一条）
     光靠 computed style 只能证明"CSS 写对了"，**证明不了它看起来是网点** ——
     点太小、对比太低、或者被内容盖住，computed style 一样是全绿的。
     所以这里把卡片内容隐掉，逼浏览器**真的画一张**，截图后用 ffmpeg 解成
     RGBA 原始像素，对亮度做自相关，断言画面里存在周期 = 格距的明暗起伏。

     顺带两个对照，防"随便什么图案都算过"：
       · 负控：鼠标不在卡片上时截同一块，**不应该**有周期（证明它确实只挂在 hover）；
       · 双色：换主题后网点颜色必须跟着变（证明它真的是"彩色"半调，不是灰点）。

为什么要用 CDP 而不是页面内探针：
  · 需要真实鼠标移动才会进 :hover，并且只有真实 pointermove 才会经 bindFollow
    写进 --mx/--my；
  · 需要 Page.captureScreenshot 拿到真正的渲染结果。

前置：服务在 8765 跑着，且库里至少有一张内置卡片（69 张是内置的，恒成立）。
"""
import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import websockets  # uvicorn[standard] 会带上

ROOT = Path(__file__).resolve().parent.parent
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9334                    # 避开 snapshot_drag_real.py 的 9333
BASE = "http://127.0.0.1:8765"

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label}  {detail}")


# ---------------------------------------------------------------- PNG → 像素
def decode_png(png: bytes):
    """用 ffmpeg 解成 RGBA 原始像素（waveform_check.py 同款做法，不引新依赖）。"""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0",
                          "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                         input=png, capture_output=True).stdout
    return raw


def png_size(png: bytes):
    import struct
    w, h = struct.unpack(">II", png[16:24])
    return w, h


def band_luma(raw, w, h, x0, x1, y0, y1):
    """把 y0..y1 几行的亮度沿 x 平均成一条一维信号，压掉抗锯齿的抖动。"""
    out = []
    for x in range(x0, x1):
        s = 0.0
        for y in range(y0, y1):
            i = (y * w + x) * 4
            s += 0.2126 * raw[i] + 0.7152 * raw[i + 1] + 0.0722 * raw[i + 2]
        out.append(s / max(1, y1 - y0))
    return out


def dot_runs(vals, frac=0.5):
    """一条带子上"连续点像素"的游程长度 —— 也就是**点的直径**（像素）。

    用**本区域自己的** min/max 定阈值，不用全局阈值：远处那些又小又淡的点
    会被全局阈值判成底色，"近大远小"就测不出来了。

    这是区分 AM 与"只调透明度"的关键：只调透明度时点径处处一样，
    游程长度不会随离光标的距离变化。
    """
    if not vals:
        return []
    lo, hi = min(vals), max(vals)
    if hi - lo < 2:
        return []
    thr = lo + (hi - lo) * frac
    runs, cur = [], 0
    for v in vals:
        if v <= thr:          # 点比底色暗
            cur += 1
        else:
            if cur:
                runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    return runs


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0


def acf(vals, lag):
    """归一化自相关：+1 = 完全同相，0 = 无关，-1 = 反相。

    **必须先挡掉"几乎是常数"的输入**：平坦的一条带理论上 den=0，
    但 `sum(vals)/n` 的浮点误差会留下 ~1e-13 的残差，于是 num/den 变成
    极小数除极小数，比值可以是任何值 —— 实测在一条 min==max==215.4 的
    平坦带上报出 +0.949，差点当成"负控里也出现了网点"。
    """
    n = len(vals)
    if n <= lag:
        return 0.0
    if max(vals) - min(vals) < 1.0:      # 峰谷差不到 1 个亮度单位 = 没有信号
        return 0.0
    m = sum(vals) / n
    num = sum((vals[i] - m) * (vals[i + lag] - m) for i in range(n - lag))
    den = sum((v - m) ** 2 for v in vals)
    return num / den if den else 0.0


def mean_rgb(raw, w, h, x0, x1, y0, y1):
    n = 0
    acc = [0.0, 0.0, 0.0]
    for y in range(y0, y1):
        for x in range(x0, x1):
            i = (y * w + x) * 4
            acc[0] += raw[i]
            acc[1] += raw[i + 1]
            acc[2] += raw[i + 2]
            n += 1
    return tuple(round(c / max(1, n), 1) for c in acc)


def parse_rgb(s):
    """吃 `#RRGGBB` 和 `rgb(r, g, b)`（浏览器算完的 background-color 长这样）。"""
    s = s.strip()
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    inner = s[s.find("(") + 1:s.find(")")]
    parts = [p for p in inner.replace(",", " ").split() if p]
    return tuple(int(float(p)) for p in parts[:3])


def dist2(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b))


def dot_core_color(raw, w, h, x0, x1, y0, y1, bg, frac=0.05):
    """取"离底色最远"的那一小撮像素的平均色 = 网点的点芯颜色。

    为什么不直接对整块求平均：卡片 hover 底色本身就随主题变，
    整块平均量的其实是"底色差"，会把 ink 的颜色差稀释掉（实测只有 9~10，
    分不清是 ink 变了还是底色变了）。点芯才是 ink 本身。
    """
    px = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            i = (y * w + x) * 4
            px.append((raw[i], raw[i + 1], raw[i + 2]))
    px.sort(key=lambda c: dist2(c, bg), reverse=True)
    n = max(1, int(len(px) * frac))
    top = px[:n]
    return (round(sum(c[0] for c in top) / n, 1),
            round(sum(c[1] for c in top) / n, 1),
            round(sum(c[2] for c in top) / n, 1))


# ---------------------------------------------------------------- 页面内小工具
JS_STRUCT = r"""
(() => {
  const freeze = document.createElement('style');
  freeze.textContent = '*{transition:none!important;animation:none!important}';
  document.head.appendChild(freeze);
  const el = document.querySelector('#cardSections .fcard:not(.fcard--new)');
  if (!el) return JSON.stringify({ found: false });
  const root = getComputedStyle(document.documentElement);
  const tok = (n) => root.getPropertyValue(n).trim();
  // 四层：::after、.fcard__dots 自己、以及它的两个伪元素。
  // 每层只声明一个点径 + 一个 reach，串起来就是 AM 网点（近大远小）。
  const layers = [
    ['fcard::after', getComputedStyle(el, '::after')],
    ['fcard__dots', getComputedStyle(el.querySelector('.fcard__dots'))],
    ['fcard__dots::before', getComputedStyle(el.querySelector('.fcard__dots'), '::before')],
    ['fcard__dots::after', getComputedStyle(el.querySelector('.fcard__dots'), '::after')],
  ];
  return JSON.stringify({
    found: true,
    hoverMedia: matchMedia('(hover: hover)').matches,
    supportsMask: CSS.supports('mask-image', 'none') || CSS.supports('-webkit-mask-image', 'none'),
    dotSpanFound: !!el.querySelector('.fcard__dots'),
    layers: layers.map(([name, cs]) => ({
      name,
      backgroundImage: cs.backgroundImage,
      backgroundSize: cs.backgroundSize,
      backgroundPosition: cs.backgroundPosition,
      maskImage: cs.maskImage || cs.webkitMaskImage || '',
      opacity: cs.opacity,
      zIndex: cs.zIndex,
    })),
    cell: tok('--spot-cell'),
    dot1: tok('--spot-dot-1'), dot2: tok('--spot-dot-2'),
    dot3: tok('--spot-dot-3'), dot4: tok('--spot-dot-4'),
    reach1: tok('--spot-reach-1'), reach2: tok('--spot-reach-2'),
    reach3: tok('--spot-reach-3'), reach4: tok('--spot-reach-4'),
    altA: tok('--spot-alt-a'),
    inkBase: tok('--spot-mix-base'),
    inkAlt: tok('--spot-mix-alt'),
  });
})()
"""

JS_ISOLATE = r"""
(() => {
  let s = document.getElementById('__halftone_iso');
  if (!s) {
    s = document.createElement('style');
    s.id = '__halftone_iso';
    // 只隐掉**内容**与对勾，留下卡片底色 + 全部网点层，像素分析才不被文字干扰。
    // 注意必须 `:not(.fcard__dots)` —— .fcard__dots 是承载 3 层网点的子元素，
    // 顺手把它一起隐掉的话，画面里就只剩 `.fcard::after` 那一层最大的点，
    // 远处（reach 更小的层够不到）自然一片空白。这个坑真的踩过：
    // 量出来"远处没有点"，其实是探针自己把远处那几层藏了。
    s.textContent = '#cardSections .fcard > *:not(.fcard__dots),' +
                    '#cardSections .fcard::before' +
                    '{ visibility: hidden !important }';
    document.head.appendChild(s);
  }
  return true;
})()
"""

JS_CARD_RECT = r"""
(() => {
  const el = document.querySelector('#cardSections .fcard:not(.fcard--new)');
  if (!el) return null;
  el.scrollIntoView({ block: 'center' });
  const r = el.getBoundingClientRect();
  // Page.captureScreenshot 的 clip 用的是**页面坐标**，getBoundingClientRect 给的是
  // **视口坐标**，页面一旦滚动两者就不相等 —— 这里把滚动量带上。
  return JSON.stringify({ x: r.left + scrollX, y: r.top + scrollY,
                          w: r.width, h: r.height,
                          vx: r.left, vy: r.top,
                          vw: innerWidth, vh: innerHeight,
                          scrollX: scrollX, scrollY: scrollY });
})()
"""


async def main():
    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
         # DPR 2：网点点径只有 3.8 CSS px，DPR 1 下几乎没有一个"完全覆盖"的像素，
         # 点芯颜色会被抗锯齿冲淡到分不清 ink 与底色。DPR 2 才有实心的点芯。
         "--force-device-scale-factor=2",
         f"--remote-debugging-port={PORT}", "--window-size=1406,927",
         f"--user-data-dir={ROOT / '_hdp'}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ws_url = None
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=1) as r:
                tabs = json.load(r)
            page = next((t for t in tabs if t["type"] == "page"), None)
            if page:
                ws_url = page["webSocketDebuggerUrl"]
                break
        except Exception:
            pass
        time.sleep(0.4)
    if not ws_url:
        proc.kill()
        raise RuntimeError("拿不到 CDP 页面")

    msg_id = 0

    try:
        async with websockets.connect(ws_url, max_size=128 * 1024 * 1024) as ws:
            async def send(method, params=None):
                nonlocal msg_id
                msg_id += 1
                mid = msg_id
                await ws.send(json.dumps({"id": mid, "method": method,
                                          "params": params or {}}))
                while True:
                    data = json.loads(await ws.recv())
                    if data.get("id") == mid:
                        if "error" in data:
                            raise RuntimeError(f"{method}: {data['error']}")
                        return data.get("result", {})

            async def evaluate(expr):
                r = await send("Runtime.evaluate",
                               {"expression": expr, "returnByValue": True,
                                "awaitPromise": True})
                return r.get("result", {}).get("value")

            async def shot(clip):
                r = await send("Page.captureScreenshot",
                               {"format": "png", "clip": dict(clip, scale=1)})
                import base64
                return base64.b64decode(r["data"])

            # ---- 打开卡片库（默认抽屉是收起的，卡片在视口之外）----
            await send("Page.enable")
            await send("Page.navigate", {"url": BASE + "/"})
            for _ in range(80):
                n = await evaluate(
                    "document.querySelectorAll('#cardSections .fcard').length")
                if n and n > 0:
                    break
                await asyncio.sleep(0.25)
            await asyncio.sleep(1.5)
            await evaluate("typeof applyStop === 'function' && applyStop('mid')")
            await asyncio.sleep(0.8)

            print("== A. 结构断言 ==")
            st = json.loads(await evaluate(JS_STRUCT))
            check("卡片存在", st.get("found"), st)
            if not st.get("found"):
                raise RuntimeError("找不到 .fcard，无法继续")

            check("headless 报告 hover: hover（否则指针链路测不到）",
                  st["hoverMedia"] is True, st["hoverMedia"])
            check("浏览器支持 mask-image", st["supportsMask"] is True)
            check("卡片里有 .fcard__dots 层（四层网点靠它撑出 3 层）",
                  st["dotSpanFound"] is True)

            layers = st["layers"]
            check("一共 4 层网点", len(layers) == 4, [x["name"] for x in layers])
            check("每层都是一层 closest-side 网点",
                  all(x["backgroundImage"].count("radial-gradient(") == 1
                      and "closest-side" in x["backgroundImage"] for x in layers),
                  [x["backgroundImage"][:60] for x in layers])

            cell = st["cell"]
            check(f"四层共用同一格网（{cell}）",
                  all(x["backgroundSize"] == f"{cell} {cell}" for x in layers),
                  [x["backgroundSize"] for x in layers])
            check("四层格网原点一致（0 0）→ 点同心，才谈得上「近大远小」",
                  all(x["backgroundPosition"] in ("0px 0px", "0% 0%") for x in layers),
                  [x["backgroundPosition"] for x in layers])

            # 点径递增、reach 递减 —— 这就是 AM 网点本身
            dots = [float(st[f"dot{i}"].rstrip("%")) for i in (1, 2, 3, 4)]
            reach = [float(st[f"reach{i}"].rstrip("%")) for i in (1, 2, 3, 4)]
            print(f"  点径由外到内: {dots}   遮罩 reach: {reach}")
            check("点径逐层递增（外层最小、里层最大）",
                  all(a < b for a, b in zip(dots, dots[1:])), dots)
            check("遮罩 reach 逐层递减（大点只出现在光标附近）",
                  all(a > b for a, b in zip(reach, reach[1:])), reach)

            check("每层的遮罩都是径向渐变（跟着光标走）",
                  all("radial-gradient" in x["maskImage"] for x in layers),
                  [x["maskImage"][:50] for x in layers])
            check("四层未 hover 时都不显示",
                  all(x["opacity"] == "0" for x in layers),
                  [x["opacity"] for x in layers])
            check("网点层在内容之下（z-index 0）",
                  layers[1]["zIndex"] == "0", layers[1]["zIndex"])

            print()
            print("== B. 像素证伪 ==")
            await evaluate(JS_ISOLATE)
            await asyncio.sleep(0.3)

            rect = json.loads(await evaluate(JS_CARD_RECT))
            inside = (rect["vx"] >= 0 and rect["vy"] >= 0
                      and rect["vx"] + rect["w"] <= rect["vw"] + 1
                      and rect["vy"] + rect["h"] <= rect["vh"] + 1)
            check("卡片在视口内（抽屉已升到 mid）", inside, rect)
            if not inside:
                raise RuntimeError(f"卡片不在视口内: {rect}")

            cx = int(rect["vx"] + rect["w"] / 2)
            cy = int(rect["vy"] + rect["h"] / 2)
            clip = {"x": int(rect["x"]), "y": int(rect["y"]),
                    "width": int(rect["w"]), "height": int(rect["h"])}

            # --- 负控：鼠标移开卡片 ---
            # 移到左侧栏中部（远离卡片库），而不是视口左上角 ——
            # 并且**断言此时 ::after 确实关着**：否则"负控里测到周期"到底是
            # 页面没退出 hover、还是别的图案，就分不清了（踩过一次，报 acf=0.949）。
            await send("Input.dispatchMouseEvent",
                       {"type": "mouseMoved", "x": 20, "y": 500, "button": "none"})
            await asyncio.sleep(0.5)
            off_op = await evaluate(
                "getComputedStyle(document.querySelector('#cardSections .fcard'),'::after')"
                ".opacity")
            off_hover = await evaluate(
                "document.querySelector('#cardSections .fcard').matches(':hover')")
            check("负控前提：鼠标已移开，::after 处于关闭态",
                  off_op == "0" and off_hover is False,
                  f"opacity={off_op} hover={off_hover}")
            png_off = await shot(clip)
            raw_off = decode_png(png_off)
            w, h = png_size(png_off)
            dpr = float(await evaluate("devicePixelRatio") or 1)
            check(f"截图按 DPR 放大（{clip['width']}x{clip['height']} CSS → {w}x{h} 设备像素）",
                  abs(w - clip["width"] * dpr) <= 1 and abs(h - clip["height"] * dpr) <= 1,
                  f"dpr={dpr}")

            mid_y = h // 2

            # --- 真实鼠标移到卡片中心：进 :hover，并经 bindFollow 写 --mx/--my ---
            # 移到正中还有个好处：--nx/--ny ≈ 0，卡片不做 3D 倾斜，截图区域稳定。
            await send("Input.dispatchMouseEvent",
                       {"type": "mouseMoved", "x": cx, "y": cy, "button": "none"})
            await asyncio.sleep(0.6)
            mx = await evaluate(
                "getComputedStyle(document.querySelector('#cardSections .fcard'))."
                "getPropertyValue('--mx').trim()")
            check("真实 pointermove 写进了 --mx（跟随光标的链路是通的）",
                  mx.endswith("px"), repr(mx))

            png_on = await shot(clip)
            raw_on = decode_png(png_on)

            pitch_half = int(round(float(cell.replace("px", "")) * dpr / 2))  # 主/副 ink 交替
            pitch_full = int(round(float(cell.replace("px", "")) * dpr))      # 同色 ink 间距

            # 采样带必须**压在点行上**：点比行距小得多，离了半格就只扫到点的尾巴，
            # 颜色被底色冲淡、半格自相关甚至翻号（DPR 2 下实测过 -0.158）。
            # 所以不猜相位，直接在竖直方向搜一条方差最大的窄带 —— 那就是点行。
            def find_dot_band(raw):
                best = None
                for o in range(-pitch_full, pitch_full + 1):
                    yc = mid_y + o
                    y0, y1 = max(1, yc - 3), min(h - 1, yc + 3)
                    if y1 - y0 < 3:
                        continue
                    b = band_luma(raw, w, h, 4, w - 4, y0, y1)
                    m = sum(b) / len(b)
                    v = sum((x - m) ** 2 for x in b) / len(b)
                    if best is None or v > best[0]:
                        best = (v, b, y0, y1)
                return best[1], best[2], best[3]

            on_band, band_y0, band_y1 = find_dot_band(raw_on)
            off_band = band_luma(raw_off, w, h, 4, w - 4, band_y0, band_y1)
            print(f"  点行搜索：y[{band_y0},{band_y1})（卡片中线 {mid_y}）")
            print(f"  负控那条带：min={min(off_band):.1f} max={max(off_band):.1f}"
                  f" 极差={max(off_band) - min(off_band):.1f}"
                  f"（hover 那条带极差={max(on_band) - min(on_band):.1f}）")

            a_full = acf(on_band, pitch_full)
            a_double = acf(on_band, 2 * pitch_full)
            # 非格距处应当明显更弱。注意**不能**拿 pitch_full-2 当"非周期"：
            # 那个滞后离整格只差 2px，天然就很相关（实测 0.716，是自己挖的坑）。
            lo, hi = int(pitch_full * 0.3), int(pitch_full * 0.7)
            a_offperiod = max(acf(on_band, lag) for lag in range(lo, hi + 1))
            a_off = abs(acf(off_band, pitch_full))

            # 关于"半格"：两层 ink 是**对角**错开半格（0 0 与 5px 5px），
            # 所以同一行里只有一种 ink、间距就是整格；半格滞后落在两点之间，
            # 自相关是负的才对。别把"半格应当正相关"写成断言 —— 那是错的预期。
            a_half = acf(on_band, pitch_half)

            print(f"  格距 {cell}: 自相关 整格({pitch_full}px)={a_full:+.3f}"
                  f"  两倍格({2*pitch_full}px)={a_double:+.3f}"
                  f"  半格({pitch_half}px)={a_half:+.3f}"
                  f"  非格距({lo}~{hi}px 最大)={a_offperiod:+.3f}")
            print(f"  负控（鼠标不在卡片上）: 半格={acf(off_band, pitch_half):+.3f}"
                  f"  整格={acf(off_band, pitch_full):+.3f}")

            check(f"hover 时存在 {pitch_full}px 周期的明暗起伏（真的画出了网点）",
                  a_full > 0.5, f"acf({pitch_full})={a_full:.3f}")
            check("这个周期是重复出现的，不是单次起伏",
                  a_double > 0.4, f"acf({2 * pitch_full})={a_double:.3f}")
            check("非格距处明显更弱（不是随机纹理冒充网点）",
                  a_offperiod < a_full * 0.4, f"{a_offperiod:.3f} vs {a_full:.3f}")
            check("半格处不正相关（对角错位：同一行只有一种 ink）",
                  a_half < 0.2, f"acf({pitch_half})={a_half:.3f}")
            check("负控：不 hover 时没有周期（证明只挂在 hover 上）",
                  a_off < 0.05, f"{a_off:.3f}")

            # --- AM 的核心：点径要随离光标的距离变 ---
            # 只调透明度时点径处处相同、游程不变；所以量"点有多宽"才是这一条的判据。
            cxd = int(rect["w"] * dpr / 2)
            span = int(25 * dpr)
            far0, far1 = int(55 * dpr), int(95 * dpr)

            def band(x0, x1):
                return band_luma(raw_on, w, h, max(4, x0), min(w - 4, x1),
                                 band_y0, band_y1)

            runs_in = dot_runs(band(cxd - span, cxd + span))
            runs_out = (dot_runs(band(cxd - far1, cxd - far0))
                        + dot_runs(band(cxd + far0, cxd + far1)))
            med_in, med_out = median(runs_in), median(runs_out)
            b_in = band(cxd - span, cxd + span)
            b_out = band(cxd - far1, cxd - far0) + band(cxd + far0, cxd + far1)
            print(f"  近处带 [{cxd - span},{cxd + span}) 亮度 {min(b_in):.1f}~{max(b_in):.1f}"
                  f" 极差 {max(b_in) - min(b_in):.1f}")
            print(f"  远处带 亮度 {min(b_out):.1f}~{max(b_out):.1f}"
                  f" 极差 {max(b_out) - min(b_out):.1f}")
            print(f"  点径（游程长度，设备像素）: 光标附近 median={med_in}"
                  f"（{len(runs_in)} 个点）  远处置 median={med_out}"
                  f"（{len(runs_out)} 个点）")

            check("光标附近与远处都取到了点（否则这条断言没有意义）",
                  bool(runs_in) and bool(runs_out),
                  f"in={len(runs_in)} out={len(runs_out)}")
            check("近处点明显比远处大（近大远小，不是只改透明度）",
                  med_in >= med_out * 1.4, f"近 {med_in} vs 远 {med_out}")

            # --- 双色：点芯颜色必须跟着主题的 ink token 走 ---
            # 用**刚才搜出来的点行**，并且横向也取遮罩中心附近：
            # 鼠标在卡片正中，--mx≈w/2，这里网点最强、点芯最实。
            lcx = int(rect["w"] * dpr / 2)
            bx0, bx1 = max(4, lcx - 40), min(w - 4, lcx + 40)
            by0, by1 = band_y0, band_y1

            hover_bg = parse_rgb(await evaluate(
                "getComputedStyle(document.querySelector('#cardSections .fcard'))"
                ".backgroundColor"))
            core_t1 = dot_core_color(raw_on, w, h, bx0, bx1, by0, by1, hover_bg)
            ink_t1 = parse_rgb(st["inkBase"])

            # 换主题：**不动鼠标**，这样两次截图的遮罩相位完全一致，
            # 逐像素差异只可能来自主题，不会混进"遮罩挪了 2px"的干扰。
            await evaluate("document.documentElement.dataset.theme='t3';"
                           "delete document.documentElement.dataset.mode;")
            await asyncio.sleep(0.6)
            png_t3 = await shot(clip)
            raw_t3 = decode_png(png_t3)
            hover_bg3 = parse_rgb(await evaluate(
                "getComputedStyle(document.querySelector('#cardSections .fcard'))"
                ".backgroundColor"))
            ink_t3 = parse_rgb(await evaluate(
                "getComputedStyle(document.documentElement)"
                ".getPropertyValue('--spot-mix-base').trim()"))
            core_t3 = dot_core_color(raw_t3, w, h, bx0, bx1, by0, by1, hover_bg3)

            d_t1_own = dist2(core_t1, ink_t1) ** 0.5
            d_t1_other = dist2(core_t1, ink_t3) ** 0.5
            d_t3_own = dist2(core_t3, ink_t3) ** 0.5
            d_t3_other = dist2(core_t3, ink_t1) ** 0.5
            cross = dist2(core_t1, core_t3) ** 0.5

            print(f"  点芯色 t1={core_t1}  ←本主题 ink {ink_t1} 距离 {d_t1_own:.1f}"
                  f" / 另一主题 ink {ink_t3} 距离 {d_t1_other:.1f}")
            print(f"  点芯色 t3={core_t3}  ←本主题 ink {ink_t3} 距离 {d_t3_own:.1f}"
                  f" / 另一主题 ink {ink_t1} 距离 {d_t3_other:.1f}")
            print(f"  两个主题的点芯色距离 = {cross:.1f}")

            # 为什么不拿"点芯 vs 卡片底色"做断言：点芯是半透明的 ink 叠在 hover 底上，
            # 离 ink 和离底色都有一段距离，两者谁更近会随点径/透明度摇摆，判不稳。
            # "离本主题 ink 比离另一个主题的 ink 更近"才是稳的，而且直接就是我们要的结论。
            check("t1 的点芯更接近 t1 的 ink（而不是 t3 的）",
                  d_t1_own + 4 < d_t1_other, f"{d_t1_own:.1f} vs {d_t1_other:.1f}")
            check("t3 的点芯更接近 t3 的 ink（而不是 t1 的）",
                  d_t3_own + 4 < d_t3_other, f"{d_t3_own:.1f} vs {d_t3_other:.1f}")
            check("换主题后点芯颜色确实变了（是彩色半调，不是灰点）",
                  cross > 15.0, f"distance={cross:.1f}")

            await evaluate("document.documentElement.dataset.theme='t1'")
    finally:
        proc.kill()
        proc.wait(timeout=10)
        # Windows 上刚 kill 完浏览器还攥着 profile 里的文件，rmtree 会静默失败并留下
        # _hdp/ 这个几十 MB 的垃圾 —— 等一下再删，并重试几次。
        import shutil
        for _ in range(10):
            shutil.rmtree(ROOT / "_hdp", ignore_errors=True)
            if not (ROOT / "_hdp").exists():
                break
            time.sleep(0.4)

    print()
    print(f"结果：{ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
