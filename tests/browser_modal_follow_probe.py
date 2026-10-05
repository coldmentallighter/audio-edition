"""模态 3D 跟随抖动探针（用户实测报的：鼠标停在窗口边缘时模态在抖）。

**根因**（这个探针把它钉住）：`.modal__box` 的倾斜是"指针位置 → `--nx/--ny` →
`transform`"，而**浏览器命中测试**打在变换**之后**的几何上。于是"指针还在不在盒子
上"也变成了倾斜的函数：

    指针停在盒子边缘
      → 静止：命中盒子 → 写变量 → 倾斜
      → 那一侧轮廓缩进约 5px → 不再命中 → `pointerout` 清掉变量
      → 回正 → 又命中 → ……        （按帧率跑的极限环）

实测（宽盒 918px、tilt 1.4°、`.modal` 的 perspective 1000px、视口 966×703）：
盒子边框离**窗口边**只有 24px（`min(940px, 100vw - 48px)`），所以用户看到的是
"鼠标到窗口边缘时模态在抖"。

**修法**（`app.js` 的 `bindFollow(..., { stableHit: true })`）：这类目标的命中
判定不再用浏览器的命中测试，改用**布局矩形**（`offset*`，与 transform 无关）——
判定边界固定在屏幕空间里，不随倾斜移动，环就不存在了。

⚠ 顺带钉住一个**试过但不行**的修法：给 `.modal__box` 加 `::after { inset: -8px }`
内扩命中区。内侧的翻转带确实消失，但伪元素跟着盒子一起被变换，**内外一起扫**会
发现多出 6 条翻转带落在边缘**外** 2–7px —— 只是把不稳定搬了个地方。
所以本探针的翻转扫描**必须同时覆盖边缘内侧与外侧**（`-12..14px`）。

**释放动画**（"指针离开时从倾斜瞬变回平整"）：模态的 `transform` 被故意移出过渡
列表以省连续跟随期间的开销，代价就是离开时**一帧跳回**（实测逐帧采样 computed
transform 只有 1 个不同值 = SNAP；fcard/pcard 是 12 个 = 本来就有动画）。
修法是 `clearFollow()` 在摘变量**之前**挂一个 `.is-returning`，CSS 借此把这一次
回正走成过渡；指针再进来时立刻摘掉。所以这里还要钉住两条**性能前提**不能被带回来：

  · 跟随态**不挂** `.is-returning`，且 `transform` **不在** `transition-property` 里
    （否则 --nx/--ny 每帧都变 → 过渡每帧重启 → 每帧一次 Layout，实测 +15 → +88）；
  · 回正窗口（380ms）过后必须摘掉，不留脏类。

另外两个坑写在这里免得下次再踩：`.is-returning` 只有 380ms 寿命，**必须在触发器里
立刻读**，等采样完 26 帧（~0.43s）再读必然是 false；`flushFollow` 有 `MIN_MOVE=3px`
阈值，检查之间手工改过变量时 `lastXY` 仍记着旧点，所以要**换一个明显不同的位置**
才能保证真的写入。

用法：服务在 8765（`run.bat` 或 `python -m backend.app`）。
"""
from __future__ import annotations

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

import websockets  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9361
PROFILE = ROOT / "tests" / "_modal_profile"
BASE = "http://127.0.0.1:8765"

PASS = FAIL = 0


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}   {detail}")


PROBE = r"""
(async () => {
  const out = {};
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const raf2 = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  try {
    // ---- 四个模态都要走 stableHit 那条路（漏一个就还会抖）----
    out.bound = ['metaModal', 'cardModal', 'presetModal', 'chainEditModal']
      .map(id => {
        const m = document.getElementById(id);
        return m ? m.dataset.followBound === '1' : 'missing';
      });

    const modal = document.getElementById('cardModal');
    const box = modal.querySelector('.modal__box');
    modal.hidden = false;
    modal.classList.add('is-open', 'is-settled');
    await raf2();

    // 与 app.js 的 layoutRectOf 同一算法（offset* 与 transform 无关）
    const mr = modal.getBoundingClientRect();
    const L = { left: mr.left + box.offsetLeft, top: mr.top + box.offsetTop,
                w: box.offsetWidth, h: box.offsetHeight };
    out.layout = { l: +L.left.toFixed(1), t: +L.top.toFixed(1), w: L.w, h: L.h };
    out.boxIsWide = box.classList.contains('modal__box--wide');

    // ---- 关键测试点：右边缘**内侧 3px**（修复前翻转带的中心）----
    const P = { x: L.left + L.w - 3, y: L.top + L.h / 2 };
    out.testPoint = { x: +P.x.toFixed(1), y: +P.y.toFixed(1) };

    const inBox = (el) => !!el && (el === box || box.contains(el));

    // ① 根因：静止命中 → 倾斜后不命中
    box.style.setProperty('--nx', '0'); box.style.setProperty('--ny', '0');
    const restHit = inBox(document.elementFromPoint(P.x, P.y));
    box.style.setProperty('--nx', '1');
    const tiltHit = inBox(document.elementFromPoint(P.x, P.y));
    box.style.setProperty('--nx', '-1');
    const tiltHitL = inBox(document.elementFromPoint(P.x, P.y));
    box.style.removeProperty('--nx'); box.style.removeProperty('--ny');
    out.rootCause = { rest: restHit, tiltRight: tiltHit, tiltLeft: tiltHitL };

    // ② 两种倾斜下：AABB 会位移（旧算法的病根），布局矩形**完全相同**（修法的立足点）
    box.style.setProperty('--nx', '1');
    const a1 = box.getBoundingClientRect();
    const m1 = modal.getBoundingClientRect();
    const l1 = { l: +(m1.left + box.offsetLeft).toFixed(3), w: box.offsetWidth };
    box.style.setProperty('--nx', '-1');
    const a2 = box.getBoundingClientRect();
    const m2 = modal.getBoundingClientRect();
    const l2 = { l: +(m2.left + box.offsetLeft).toFixed(3), w: box.offsetWidth };
    box.style.removeProperty('--nx');
    out.aabbShifts = +Math.abs(a1.left - a2.left).toFixed(2);
    out.layoutTiltInvariant = (l1.l === l2.l && l1.w === l2.w);

    // ---- 交互断言 ----
    const nx = () => box.style.getPropertyValue('--nx');
    const scrim = modal.querySelector('.modal__scrim');
    const pmoveOn = (t, x, y) => t.dispatchEvent(new PointerEvent('pointermove',
      { clientX: x, clientY: y, bubbles: true, composed: true }));
    const poutOn = (t, to) => t.dispatchEvent(new PointerEvent('pointerout',
      { bubbles: true, relatedTarget: to || null }));

    // ③ 盒内 pointermove → 写入
    pmoveOn(box, P.x, P.y); await raf2(); await wait(40);
    out.afterMoveInside = nx();

    // ④ **假**离开信号（倾斜后浏览器会发的那个）→ 必须保留
    poutOn(box, scrim); await raf2(); await wait(40);
    out.afterFalsePointerout = nx();

    // ⑤ 指针真移出盒子 60px → 必须清除
    pmoveOn(modal, L.left + L.w + 60, P.y); await raf2(); await wait(40);
    out.afterMoveOutside = nx();

    // ⑥ 回到盒内 → 重新写入
    pmoveOn(box, P.x, P.y); await raf2(); await wait(40);
    out.afterMoveBack = nx();

    // ⑦ 对照：卡片走**老**路径，pointerout 仍应立即清除（没被改坏）
    const card = document.querySelector('#cardSections .fcard');
    if (card) {
      const cr = card.getBoundingClientRect();
      pmoveOn(card, cr.left + cr.width / 2, cr.top + cr.height / 2);
      await raf2(); await wait(40);
      out.cardAfterMove = card.style.getPropertyValue('--nx');
      poutOn(card, document.body);
      await raf2(); await wait(40);
      out.cardAfterPointerout = card.style.getPropertyValue('--nx');
    } else {
      out.card = '(no .fcard in DOM)';
    }
    // ---- ⑧ 释放动画：指针离开时必须是"缓回"而不是瞬变 ----
    //     用 app.js 的真实路径触发（模态：指针移出布局矩形 → clearFollow）。
    const tf = (el) => getComputedStyle(el).transform;
    const nf = () => new Promise(r => requestAnimationFrame(r));

    /* 让目标带上**真实**的倾斜（走 flushFollow，而不是手工设变量）。
       ⚠ 必须换成一个与上次明显不同的点：flushFollow 有 MIN_MOVE=3px 的阈值，
         而上面几条检查手工摘过变量、lastXY 却还记着那个点 ——
         在同一位置再 pointermove 会被判成"没动够"，干脆不写变量，
         于是"释放"时无倾斜可缓回，检查会假失败。 */
    async function tiltIt(el, x, y, container) {
      pmoveOn(container || el, x - 45, y); await raf2(); await wait(40);
      pmoveOn(el, x, y);                  await raf2(); await wait(60);
      return el.style.getPropertyValue('--nx') !== '';
    }

    /* 触发释放并逐帧采样。**在触发器里立刻读 .is-returning** ——
       它 380ms 后就被摘掉，而采样 26 帧约 0.43s，等采样完再读必然是 false。 */
    async function sampleRelease(el, trigger, frames) {
      trigger();
      const hadClass = el.classList.contains('is-returning');
      const s = [];
      for (let i = 0; i < frames; i++) { await nf(); s.push(tf(el)); }
      let stableAt = s.length - 1;
      for (let i = 1; i < s.length; i++) {
        if (s[i] === s[s.length - 1]) { stableAt = i; break; }
      }
      return { distinct: new Set(s).size, stableAt, hadClass };
    }

    // 跟随态：**不该**挂着 .is-returning，且 transform **不在**过渡列表里
    // （那正是"落定后不过渡 transform"省下的每帧 Layout —— 不能被这次改动带回来）
    out.followTiltSet = await tiltIt(box, P.x, P.y, modal);
    out.followTransitionProperty = getComputedStyle(box).transitionProperty;
    out.followHasReturning = box.classList.contains('is-returning');

    out.modalRelease = await sampleRelease(
      box, () => pmoveOn(modal, L.left + L.w + 300, P.y), 26);
    await wait(520);
    out.modalReturningExpired = box.classList.contains('is-returning');

    // 卡片（老路径）也要缓回
    const crd = document.querySelector('#cardSections .fcard');
    if (crd) {
      const cr = crd.getBoundingClientRect();
      out.cardTiltSet = await tiltIt(crd, cr.left + cr.width - 4,
                                     cr.top + cr.height / 2);
      out.cardRelease = await sampleRelease(crd, () => poutOn(crd, document.body), 26);
    } else {
      out.card = '(no .fcard in DOM)';
    }

    // 预设卡片：工作区没有预设时 #presetSections 是空的，这里注入一张最小的
    const ps = document.getElementById('presetSections');
    if (ps) {
      const hadHidden = ps.hidden;
      ps.hidden = false;
      const pc = document.createElement('button');
      pc.className = 'pcard';
      pc.innerHTML = '<span class="pcard__dots" aria-hidden="true"></span>' +
                     '<span class="pcard__name">探针预设</span>' +
                     '<span class="pcard__desc">tilt release probe</span>';
      ps.appendChild(pc);
      await raf2(); await wait(50);
      const pr = pc.getBoundingClientRect();
      out.pcardTiltSet = await tiltIt(pc, pr.left + pr.width - 4,
                                      pr.top + pr.height / 2);
      out.pcardRelease = await sampleRelease(pc, () => poutOn(pc, document.body), 26);
      pc.remove();
      ps.hidden = hadHidden;
    } else {
      out.pcard = '(no #presetSections)';
    }

    // ---- ⑨ 决定性检查（放在最后：它会手工动 --nx，别去影响上面的释放检查）----
    //    证明命中判定用的是布局矩形，而不是退回 AABB：
    //    把盒子置于倾斜态，此时 AABB 的右沿缩进约 5px，
    //    于是"边缘内侧 3px"已经落在**倾斜后的 AABB 之外**。
    //    · 用布局矩形判 → 仍在盒内 → 保留倾斜；
    //    · 若退回 AABB 判 → 判为离开 → clearFollow 清空。
    pmoveOn(modal, L.left + L.w + 60, P.y); await raf2(); await wait(40);
    box.style.setProperty('--nx', '1');
    // ⚠ 等它**真的走到位**再量 AABB：此刻元素可能还在 .is-returning 窗口里，
    //   手工改 --nx 也一样走过渡，只等两帧量到的是中间值。
    await wait(400);
    const aabbRight = box.getBoundingClientRect().right;
    out.bandPointIsOutsideTiltedAABB = (P.x - aabbRight) > 0;
    pmoveOn(box, P.x - 45, P.y); await raf2(); await wait(40);   // 换个点，保证会真写
    pmoveOn(box, P.x, P.y);      await raf2(); await wait(40);
    out.tiltedMoveAtBand = nx();
    box.style.removeProperty('--nx');

    out.ok = true;
  } catch (e) {
    out.err = String((e && e.stack) || e);
  }
  return out;
})()
"""


def main() -> int:
    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}", BASE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws_url = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{PORT}/json/list", timeout=3) as r:
                    tabs = json.loads(r.read().decode("utf-8"))
                page = [t for t in tabs if t.get("type") == "page"
                        and "127.0.0.1:8765" in t.get("url", "")]
                if page:
                    ws_url = page[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not ws_url:
            check("拿到 CDP 页面", False, "服务在 8765？")
            return 1

        async def run() -> dict | None:
            async with websockets.connect(ws_url, max_size=None) as ws:
                mid = [0]

                async def send(method, params=None, timeout=60):
                    mid[0] += 1
                    await ws.send(json.dumps({"id": mid[0], "method": method,
                                              "params": params or {}}))
                    want = mid[0]
                    while True:
                        m = json.loads(await asyncio.wait_for(ws.recv(),
                                                             timeout=timeout))
                        if m.get("id") == want:
                            return m

                async def evaluate(expr, await_promise=False):
                    m = await send("Runtime.evaluate", {
                        "expression": expr, "returnByValue": True,
                        "awaitPromise": await_promise})
                    res = (m.get("result") or {})
                    if res.get("exceptionDetails"):        # 别把它吞成 None
                        exc = res["exceptionDetails"]
                        return {"__exception":
                                ((exc.get("exception") or {}).get("description")
                                 or json.dumps(exc, ensure_ascii=False)[:900])}
                    return ((res.get("result") or {}).get("value"))

                await send("Runtime.enable")
                # 等卡片库渲染出来（⑦ 要用到一张 .fcard）
                for _ in range(60):
                    n = await evaluate("document.querySelectorAll("
                                       "'#cardSections .fcard[data-card]').length")
                    if (n or 0) >= 60:
                        break
                    await asyncio.sleep(0.5)
                return await evaluate(PROBE, await_promise=True)

        out = asyncio.run(run())
        if isinstance(out, dict) and out.get("__exception"):
            check("探针求值无 JS 异常", False, out["__exception"])
            print()
            print(f"结果：{PASS} passed, {FAIL} failed")
            return 1
        if not isinstance(out, dict):
            check("探针求值成功", False, out)
            return 1
        if out.get("err"):
            check("探针内部无异常", False, out["err"][:400])
            return 1

        # ---- ① 根因确实存在（否则这个探针在测空气）----
        rc = out["rootCause"]
        check("**边缘内侧 3px**：静止时命中模态盒子", rc["rest"] is True, rc)
        check("倾斜后**不再**命中（这就是抖动的引擎）", rc["tiltRight"] is False, rc)

        # ---- ② 修法的立足点：布局矩形与 transform 无关 ----
        check("AABB 确实随倾斜位移（>2px）", out["aabbShifts"] > 2, out["aabbShifts"])
        check("布局矩形在 ±tilt 下完全相同", out["layoutTiltInvariant"] is True,
              out["layoutTiltInvariant"])

        # ---- ③④⑤⑥ 交互 ----
        def near1(v):
            try:
                return abs(abs(float(v)) - 1) < 0.05
            except Exception:
                return False

        check("盒内 pointermove 写入 --nx≈1", near1(out["afterMoveInside"]),
              out["afterMoveInside"])
        check("**假的 pointerout 不再清掉 --nx**（核心修复）",
              out["afterFalsePointerout"] not in ("", None), out["afterFalsePointerout"])
        check("真移出 60px 后清空 --nx", out["afterMoveOutside"] in ("", None),
              out["afterMoveOutside"])
        check("回到盒内重新写入 --nx≈1", near1(out["afterMoveBack"]),
              out["afterMoveBack"])

        # ---- ⑧ 决定性：判定用的是布局矩形，不是 AABB ----
        check("该点在倾斜后的 AABB 之外（前提成立）",
              out["bandPointIsOutsideTiltedAABB"] is True,
              out["bandPointIsOutsideTiltedAABB"])
        check("**倾斜态下边缘内侧仍算命中**（退回 AABB 就会在这里判成离开）",
              near1(out["tiltedMoveAtBand"]), out["tiltedMoveAtBand"])

        # ---- ⑦ 卡片的老路径没被改坏 ----
        check("卡片 pointermove 写入 --nx", out.get("cardAfterMove") not in (None, ""),
              out.get("cardAfterMove", out.get("card")))
        check("卡片 pointerout 仍立即清除（未改动老路径）",
              out.get("cardAfterPointerout") in ("", None),
              out.get("cardAfterPointerout"))

        # ---- 四个模态都登记了（漏一个就还会抖）----
        check("四个模态都登记了 bindFollow",
              out["bound"] == [True, True, True, True], out["bound"])

        # ---- ⑧ 释放动画 ----
        check("跟随态确实带着倾斜（前置条件：flushFollow 真的写了变量）",
              out["followTiltSet"] is True, out["followTiltSet"])
        check("跟随态**不**挂 .is-returning（否则每帧重启过渡 → 每帧 Layout）",
              out["followHasReturning"] is False, out["followHasReturning"])
        check("跟随态 transform **不在**过渡列表里（保住了当初的优化）",
              "transform" not in (out["followTransitionProperty"] or ""),
              out["followTransitionProperty"])

        def animated(d):
            return isinstance(d, dict) and d.get("distinct", 0) > 3 and d.get("stableAt", 0) >= 4

        check("模态释放是**缓回**而不是瞬变（修复前 distinct=1 / SNAP）",
              animated(out["modalRelease"]), out["modalRelease"])
        check("模态释放时挂上了 .is-returning",
              (out["modalRelease"] or {}).get("hadClass") is True, out["modalRelease"])
        check("回正窗口过后摘掉 .is-returning（不留脏类）",
              out["modalReturningExpired"] is False, out["modalReturningExpired"])
        check("功能卡片释放也是缓回", animated(out["cardRelease"]),
              out["cardRelease"])
        check("功能卡片释放时挂上了 .is-returning",
              (out["cardRelease"] or {}).get("hadClass") is True, out["cardRelease"])
        check("预设卡片释放也是缓回", animated(out["pcardRelease"]),
              out["pcardRelease"])
        check("预设卡片释放时挂上了 .is-returning",
              (out["pcardRelease"] or {}).get("hadClass") is True, out["pcardRelease"])
    finally:
        proc.kill()

    print()
    print(f"结果：{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
