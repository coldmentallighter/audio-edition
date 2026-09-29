"""用 CDP 走**真实**拖拽管线验证快照替换。

为什么不能用合成 DragEvent：
  在页面里 `dispatchEvent(new DragEvent('drop'))` 会直接派发 drop，
  完全绕过浏览器的 effectAllowed / dropEffect 兼容性判定 ——
  而真实拖拽里，来源写 effectAllowed='copy'、落点写 dropEffect='move'
  会被判定为不允许，**根本不派发 drop**。这个 bug 就是合成事件漏掉的。

两步逼近真实：
  1) Input.setInterceptDrags(true) + 真实鼠标按下/移动 → 浏览器真的发起拖拽，
     从 Input.dragIntercepted 拿到 dragstart 真正写进去的载荷；
  2) 用那份载荷 Input.dispatchDragEvent 到落点，走浏览器自己的落点判定。
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
PORT = 9333
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


PAGE_JS = """
window.__R = { events: [] };
for (const t of ['dragstart','dragenter','dragover','drop','dragend','dragleave']) {
  document.addEventListener(t, (e) => {
    const el = e.target && e.target.closest
      ? (e.target.closest('[data-snap], [data-snap-add], [data-card]') || e.target) : null;
    window.__R.events.push({
      t,
      slot: el && el.dataset
        ? (el.dataset.snap ?? el.dataset.snapAdd ?? el.dataset.card ?? null) : null,
      dt: e.dataTransfer ? {
        effectAllowed: e.dataTransfer.effectAllowed,
        dropEffect: e.dataTransfer.dropEffect,
        types: [...(e.dataTransfer.types || [])],
      } : null,
    });
  }, true);
}
"""


def build_page():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    stub = ('<script>window.EventSource = class { constructor(){} addEventListener(){}'
            ' removeEventListener(){} close(){} };</script>')
    html = html.replace('<script src="app.js">', stub + '\n<script src="app.js">')
    html = html.replace("</body>", '<script src="tests/_snapdrag_page.js"></script>\n</body>')
    (ROOT / "_snapdrag.html").write_text(html, encoding="utf-8")
    (ROOT / "tests" / "_snapdrag_page.js").write_text(PAGE_JS, encoding="utf-8")


async def run_all():
    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
         f"--remote-debugging-port={PORT}", "--window-size=1406,927",
         f"--user-data-dir={ROOT / '_sdp'}", "about:blank"],
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
    pending = {}

    try:
        async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
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
                    ev = data.get("method")
                    if ev:
                        pending.setdefault(ev, []).append(data.get("params", {}))

            async def evaluate(expr):
                r = await send("Runtime.evaluate",
                               {"expression": expr, "returnByValue": True,
                                "awaitPromise": True})
                return r.get("result", {}).get("value")

            async def wait_for(method, timeout=8.0):
                t0 = time.time()
                while time.time() - t0 < timeout:
                    if pending.get(method):
                        return pending[method].pop(0)
                    await asyncio.sleep(0.05)
                return None

            async def rect_of(selector):
                raw = await evaluate(
                    "(() => { const el = document.querySelector(%s);"
                    " if (!el) return null; const r = el.getBoundingClientRect();"
                    " return JSON.stringify({x: Math.round(r.left+r.width/2),"
                    " y: Math.round(r.top+r.height/2)}); })()" % json.dumps(selector))
                return json.loads(raw) if raw else None

            async def real_drag(src_xy, dest_xy, moves=None):
                """真实发起一次拖拽，并用真实载荷落到 dest"""
                pending.pop("Input.dragIntercepted", None)
                await send("Input.setInterceptDrags", {"enabled": True})
                await send("Input.dispatchMouseEvent",
                           {"type": "mousePressed", "x": src_xy["x"], "y": src_xy["y"],
                            "button": "left", "buttons": 1, "clickCount": 1})
                for dx, dy in (moves or [(8, 0), (20, -4), (40, -10)]):
                    await send("Input.dispatchMouseEvent",
                               {"type": "mouseMoved", "x": src_xy["x"] + dx,
                                "y": src_xy["y"] + dy, "button": "left", "buttons": 1})
                    await asyncio.sleep(0.08)
                ic = await wait_for("Input.dragIntercepted", timeout=5)
                if ic is None:
                    return None
                items = ic["data"].get("items", [])
                # 用浏览器**自己算出来的**允许操作掩码，别写死：
                # 掩码来自 dragstart 的 effectAllowed（copy=1 move=2 link=4）。
                # 写死 1（只允许 copy）会让 dropEffect='move' 的落点永远收不到 drop，
                # 那是测试的错，不是页面的错。
                mask = ic["data"].get("dragOperationsMask", 1)
                print(f"  浏览器判定允许的操作掩码: {mask} "
                      f"(copy={'✓' if mask & 1 else '✗'} move={'✓' if mask & 2 else '✗'})")
                d = {"items": items, "dragOperationsMask": mask}
                for ty in ("dragEnter", "dragOver", "drop"):
                    await send("Input.dispatchDragEvent",
                               {"type": ty, "x": dest_xy["x"], "y": dest_xy["y"], "data": d})
                    await asyncio.sleep(0.15)
                await asyncio.sleep(1.0)
                return items

            await send("Page.enable")
            await send("Runtime.enable")
            await send("Page.navigate", {"url": f"{BASE}/_snapdrag.html"})
            await asyncio.sleep(4.0)

            for _ in range(40):
                if await evaluate("document.querySelectorAll('#fileList .card').length"):
                    break
                await asyncio.sleep(0.3)

            await evaluate("applyStop('full')")     # 卡片与快照条同时可见
            await asyncio.sleep(0.6)

            before = json.loads(await evaluate("JSON.stringify(SNAPS.map(s=>s.name))"))
            print(f"  起始快照: {before}")

            outsider = ("[...document.querySelectorAll('#cardSections [data-card]')]"
                        ".map(c=>c.dataset.card).find(n=>!SNAPS.some(s=>s.name===n))")
            card_name = await evaluate(outsider)
            check("能找到一个不在快照里的卡片", bool(card_name), card_name)
            if not card_name:
                return

            # ---------- 1. 卡片 → 快照第 2 格 ----------
            src = await rect_of('#cardSections [data-card="%s"]' % card_name)
            dst = await rect_of('#cardSnaps [data-snap="1"]')
            print(f"  拖动「{card_name}」({src['x']},{src['y']}) → 第2格 ({dst['x']},{dst['y']})")

            items = await real_drag(src, dst)
            check("浏览器真的发起了拖拽（dragIntercepted）", items is not None)
            if items is None:
                evs = json.loads(await evaluate("JSON.stringify(window.__R.events)"))
                print(f"  已捕获事件: {[e['t'] for e in evs]}")
                return
            print(f"  拖拽载荷: {json.dumps(items, ensure_ascii=False)[:150]}")
            custom = next((i for i in items
                           if i.get("mimeType") == "application/x-ae-snap"), None)
            check("dragstart 写入了自定义 MIME", custom is not None,
                  [i.get("mimeType") for i in items])
            if custom:
                check("载荷里有卡名",
                      json.loads(custom["data"]).get("name") == card_name, custom["data"])

            after = json.loads(await evaluate("JSON.stringify(SNAPS.map(s=>s.name))"))
            print(f"  拖放后快照: {after}")
            check("第 2 格被替换", after[1] == card_name, f"{after} vs {card_name}")
            check("格子数不变", len(after) == len(before), after)
            check("其它格未动", after[0] == before[0] and after[2] == before[2], after)

            srv = json.load(urllib.request.urlopen(f"{BASE}/api/snapshots"))["snapshots"]
            check("服务端已保存", srv == after, f"server={srv} ui={after}")

            evs = json.loads(await evaluate("JSON.stringify(window.__R.events)"))
            seen = [e["t"] for e in evs]
            check("真实拖拽派发了 drop 事件", "drop" in seen, seen)
            for want in ("dragstart", "dragover", "drop"):
                e0 = next((e for e in evs if e["t"] == want), None)
                if e0 and e0.get("dt"):
                    print(f"  {want:10} effectAllowed={e0['dt']['effectAllowed']:9} "
                          f"dropEffect={e0['dt']['dropEffect']}")

            # ---------- 2. 拖到 ＋ 追加 ----------
            await evaluate("window.__R.events.length = 0")
            another = await evaluate(outsider)
            if another:
                s2 = await rect_of('#cardSections [data-card="%s"]' % another)
                a2 = await rect_of('[data-snap-add]')
                n0 = len(json.loads(await evaluate("JSON.stringify(SNAPS)")))
                await real_drag(s2, a2)
                sn = json.loads(await evaluate("JSON.stringify(SNAPS.map(s=>s.name))"))
                check("拖到 ＋ 能追加", len(sn) == n0 + 1, sn)
                check("追加的是拖过去的卡片", bool(sn) and sn[-1] == another,
                      f"{sn} vs {another}")

            # ---------- 3. 快照互拖换位 ----------
            b3 = json.loads(await evaluate("JSON.stringify(SNAPS.map(s=>s.name))"))
            s0 = await rect_of('#cardSnaps [data-snap="0"]')
            t3 = await rect_of('#cardSnaps [data-snap="3"]')
            await real_drag(s0, t3, moves=[(6, 2), (14, 4), (26, 6)])
            a3 = json.loads(await evaluate("JSON.stringify(SNAPS.map(s=>s.name))"))
            check("快照互拖换位生效", a3[0] == b3[3] and a3[3] == b3[0], f"{b3} -> {a3}")

            # ---------- 4. 收尾 ----------
            await evaluate("(async()=>{const d=await API.resetSnapshots();"
                           "window.applyServerCards({snapshots:d.snapshots});})()")
            await asyncio.sleep(0.8)
    finally:
        try:
            proc.kill()
        except Exception:
            pass


build_page()
asyncio.run(run_all())
print(f"\n结果：{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
