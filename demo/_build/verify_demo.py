"""跑一遍 UI 演示版的自检：把 verify_demo.js 注入页面，用无头浏览器真的点一遍。

    python demo/_build/verify_demo.py

自己起静态服务（不用先跑 serve.py），跑完关掉浏览器、删掉临时页，失败以非 0 退出。

**为什么用 CDP 而不是 `--dump-dom --virtual-time-budget`**：这套探针里有
`setInterval` 常驻轮询、有 `<audio>`、还有一段要等 3.2s 的"跨过自动刷新"的断言，
虚拟时间下浏览器**不会退出**（实测 300s 超时）。所以改成 CDP + 真实时间：
探针往页面里写 `#AE_RESULT`，这边轮询它。

依赖：websockets（uvicorn[standard] 会带）。
"""
import asyncio
import hashlib
import html
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
sys.path.insert(0, str(DEMO))

from serve import Handler, Server, pick_port          # noqa: E402


class CountingHandler(Handler):
    """记录**漏网**的 /api 请求。

    演示版的每个 /api 都应该被 mock.js 拦住；真打到静态服务器就是漏了一个接口
    （封面图那类 `<img>` 最容易漏：innerHTML 解析不走 JS 的 src setter）。
    这条断言比"页面上看着对"更强 —— 它检查 mock 的**完备性**。
    """

    unhandled: list[str] = []

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            CountingHandler.unhandled.append(self.path)
        super().log_message(fmt, *args)

try:
    import websockets
except ImportError:
    print("!! 需要 websockets（pip install websockets）")
    sys.exit(2)

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]
CDP_PORT = 9334


def find_browser():
    for b in BROWSERS:
        if Path(b).exists():
            return b
    for name in ("msedge", "chrome", "chromium", "google-chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def build_verify_page():
    src = (DEMO / "index.html").read_text(encoding="utf-8")
    tag = '<script src="_build/verify_demo.js"></script>'
    out = DEMO / "_verify.html"
    out.write_text(src.replace("</body>", tag + "\n</body>"), encoding="utf-8")
    return out


async def drive(url, timeout=180.0):
    """连上 CDP，轮询页面里的 #AE_RESULT。"""
    ws_url = None
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=1) as r:
                tabs = json.load(r)
            page = next((t for t in tabs if t["type"] == "page"), None)
            if page:
                ws_url = page["webSocketDebuggerUrl"]
                break
        except Exception:
            pass
        time.sleep(0.3)
    if not ws_url:
        raise RuntimeError("拿不到 CDP 页面")

    mid = 0
    # open_timeout + 每次 recv 都带 wait_for：CDP 少一个 --remote-allow-origins，
    # 握手会**不报错也不回复**，没有超时就会一直挂着（第一版就是这么卡死的）。
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024,
                                  open_timeout=15) as ws:
        events = []

        async def send(method, params=None):
            nonlocal mid
            mid += 1
            mine = mid
            await ws.send(json.dumps({"id": mine, "method": method, "params": params or {}}))
            while True:
                data = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
                if data.get("id") == mine:
                    if "error" in data:
                        raise RuntimeError(f"{method}: {data['error']}")
                    return data.get("result", {})
                events.append(data)

        async def evaluate(expr):
            r = await send("Runtime.evaluate",
                           {"expression": expr, "returnByValue": True, "awaitPromise": True})
            return r.get("result", {}).get("value")

        await send("Runtime.enable")
        await send("Page.enable")
        await send("Page.navigate", {"url": url})

        DIAG = ("(function(){try{return JSON.stringify({"
                "ready:document.readyState,"
                "API:typeof API,App:typeof App,demo:!!window.__AE_DEMO_API__,"
                "cards:document.querySelectorAll('#fileList .card').length,"
                "secs:document.querySelectorAll('#cardSections .cardsec').length,"
                "notice:(document.getElementById('noticeMsg')||{}).textContent||'',"
                "noticeTitle:(document.getElementById('noticeTitle')||{}).textContent||'',"
                "result:!!document.getElementById('AE_RESULT')});}catch(e){return 'DIAG-ERR '+e.message}})()")

        t0 = time.time()
        last_note = 0.0
        last_txt = None
        while time.time() - t0 < timeout:
            await asyncio.sleep(0.5)
            try:
                txt = await evaluate(
                    "(function(){var e=document.getElementById('AE_RESULT');"
                    "return e?e.textContent:null;})()")
            except Exception:
                txt = None
            if txt:
                last_txt = txt
                try:
                    if json.loads(txt.split("AE_RESULT", 1)[1]).get("finished"):
                        return txt, events
                except Exception:
                    pass
            if time.time() - last_note > 8:
                last_note = time.time()
                try:
                    print(f"  … {time.time() - t0:4.0f}s  {await evaluate(DIAG)}", flush=True)
                except Exception as e:
                    print(f"  … {time.time() - t0:4.0f}s  主线程无响应 ({type(e).__name__})",
                          flush=True)
                    if last_txt:                    # 卡住了：把已完成的部分交出去
                        return last_txt, events
                    raise
        if last_txt:
            return last_txt, events
        raise TimeoutError(f"{timeout:.0f}s 内没等到 AE_RESULT")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def staleness() -> list[str]:
    """demo/ 里的前端是**拷贝**，改了根目录的 app.js / css 之后必须重跑 make_demo.py。
    不查这一点的话，自检会对着旧副本一路绿灯（实测被骗过一次）。

    两类分开看：
      · app.js / api.js / theme.css / css/* 是逐字节拷贝 → 直接比 sha
      · index.html 是**有意改过**的（注入脚本 + 角标），不能直接比，
        改比 provenance.json 里记的"生成它时用的源文件 sha"
    """
    root = DEMO.parent
    stale = []
    for name in ("app.js", "api.js", "theme.css"):
        a, b = root / name, DEMO / name
        if a.exists() and b.exists() and sha(a) != sha(b):
            stale.append(name)
    for f in sorted((root / "css").glob("*.css")):
        b = DEMO / "css" / f.name
        if b.exists() and sha(f) != sha(b):
            stale.append(f"css/{f.name}")

    prov_path = HERE / "provenance.json"
    if not prov_path.exists():
        stale.append("_build/provenance.json 缺失")
        return stale
    prov = json.loads(prov_path.read_text(encoding="utf-8")).get("sources", {})
    rec = (prov.get("index.html") or {}).get("sha256_16")
    idx = root / "index.html"
    if rec and idx.exists() and sha(idx) != rec:
        stale.append("index.html（demo 页是用旧版生成的）")
    return stale


def main() -> int:
    exe = find_browser()
    if not exe:
        print("!! 没找到 Edge/Chrome")
        return 2

    stale = staleness()
    if stale:
        print("!! demo/ 里的前端副本已经过期：" + "、".join(stale))
        print("   先跑： python demo/_build/make_demo.py")
        return 2

    page = build_verify_page()
    port = pick_port(8810)
    CountingHandler.unhandled = []
    httpd = Server(("127.0.0.1", port), CountingHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/{page.name}"

    profile = Path(tempfile.gettempdir()) / "ae_demo_cdp_profile"
    proc = subprocess.Popen(
        [exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
         f"--remote-debugging-port={CDP_PORT}", "--remote-allow-origins=*",
         "--window-size=1406,927",
         "--autoplay-policy=no-user-gesture-required",
         f"--user-data-dir={profile}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"浏览器 = {Path(exe).name}")
    print(f"页面   = {url}\n")
    rc = 2
    events = []
    try:
        raw, events = asyncio.run(drive(url))
        data = json.loads(html.unescape(raw).split("AE_RESULT", 1)[1])
        if not data.get("finished"):
            print("=" * 76)
            print("!! 探针没跑完（主线程被卡住）。以下是卡住之前已经跑完的项：")
        else:
            print("=" * 76)
        for c in data["checks"]:
            print(f"  {'PASS' if c['ok'] else 'FAIL'}  {c['n']}"
                  + (f"\n          ↳ {c['d']}" if (not c["ok"] and c["d"]) else ""))
        print("=" * 76)
        leaked = CountingHandler.unhandled
        if leaked:
            print(f"  FAIL  没有未被 mock 接管的 /api 请求（漏了 {len(leaked)} 个）")
            for p in sorted(set(leaked))[:8]:
                print(f"          ↳ {p}")
        else:
            print("  PASS  没有未被 mock 接管的 /api 请求（mock 覆盖完整）")
        print("=" * 76)
        if not data.get("finished"):
            print(f"卡在第 {data['total'] + 1} 项（上面最后一项之后那一项）")
            rc = 2
        else:
            print(f"结果：{data['passed']} passed, {data['failed']} failed  (共 {data['total']} 项)"
                  + (f"，另有 {len(set(leaked))} 个漏网接口" if leaked else ""))
            rc = 1 if (data["failed"] or leaked) else 0
    except Exception as e:
        print(f"!! {type(e).__name__}: {e}")
        bad = [ev for ev in events
               if ev.get("method") in ("Runtime.exceptionThrown", "Runtime.consoleAPICalled")]
        for ev in bad[-25:]:
            p = ev.get("params", {})
            if ev["method"] == "Runtime.exceptionThrown":
                d = p.get("exceptionDetails", {})
                print("   JS 异常:", (d.get("exception") or {}).get("description")
                      or d.get("text"), flush=True)
            else:
                txt = " ".join(str(a.get("value")) for a in p.get("args", []))
                print(f"   console.{p.get('type')}: {txt[:200]}", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        httpd.shutdown()
        page.unlink(missing_ok=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
