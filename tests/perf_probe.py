"""界面渲染开销探针 —— 量「一次重绘到底花在哪」，不靠肉眼也不靠推测。

为什么走 CDP 而不是读源码估算：
  `renderFiles()` 每 2 秒被 `syncAndProbe` 触发一次，`drawWave()` 里
  `tok()` 又在**逐柱循环内**调 `getComputedStyle`。这两件事的代价
  只有真跑一遍才知道量级，静态读代码只能得出"看起来不对"。

  demo/ 跑的**就是仓库根目录这份前端**（app.js/api.js/css 逐字节拷贝，
  见 demo/README.md 与 _build/provenance.json），所以在这里量出来的数
  对正式版同样成立，而且不需要 ffmpeg / FastAPI。

用法：
    python tests/perf_probe.py                 # DPR 1
    python tests/perf_probe.py --dpr 2         # 模拟 HiDPI 屏
    python tests/perf_probe.py --json out.json # 同时落盘原始数据

会自己拉起 demo/serve.py 与一个无头 Edge，跑完自动清理。
"""
import argparse
import asyncio
import json
import shutil
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
HTTP_PORT = 8801
CDP_PORT = 9334


# --------------------------------------------------------------------- CDP

class Page:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0

    async def send(self, method, **params):
        self._id += 1
        mid = self._id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=120)
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def ev(self, expr, await_promise=False):
        r = await self.send("Runtime.evaluate", expression=expr,
                            returnByValue=True, awaitPromise=await_promise)
        if "exceptionDetails" in r:
            det = r["exceptionDetails"]
            desc = (det.get("exception") or {}).get("description") or det.get("text")
            raise RuntimeError(f"页面内异常：{desc}")
        return r["result"].get("value")


def wait_http(url, tries=80):
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=0.6) as r:
                r.read(1)
            return True
        except Exception:
            time.sleep(0.15)
    return False


def wait_page_ws(port, tries=100):
    for _ in range(tries):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=0.8) as r:
                for t in json.loads(r.read().decode("utf-8")):
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.2)
    return None


# ------------------------------------------------------- 页面内测量脚本
# 全部在页面里跑：这里刻意不用定时器去"模拟"轮询，而是直接调用
# renderFiles() / redrawAllWaves() —— 也就是轮询真正落到的那两个函数。

PAGE_JS = r"""
(async () => {
  const raf = () => new Promise(r => requestAnimationFrame(() => r()));
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const out = {};

  const nCards = () => document.querySelectorAll('#fileList .card').length;
  const nCanvas = () => document.querySelectorAll('[data-wave]').length;
  const nNodes = sel => document.querySelectorAll(sel).length;

  /* 只保留自己这一份 getComputedStyle 计数器；app.js 的 cssVar() 会在
     调用点按名字解析全局 getComputedStyle，所以打补丁能真的数到。 */
  if (!window.__gcsOrig) {
    window.__gcsOrig = window.getComputedStyle.bind(window);
    window.__gcs = 0;
    window.getComputedStyle = function () { window.__gcs++; return window.__gcsOrig.apply(window, arguments); };
  }
  const gcs = () => window.__gcs;

  function bench(fn, reps) {
    fn();                                  // 预热：第一遍含 JIT 与缓存冷启动
    const t0 = performance.now();
    for (let i = 0; i < reps; i++) fn();
    return (performance.now() - t0) / reps;
  }

  function frameStats(deltas) {
    const d = deltas.slice(1).sort((a, b) => a - b);
    if (!d.length) return null;
    const at = q => +d[Math.min(d.length - 1, Math.floor(d.length * q))].toFixed(2);
    return {
      frames: d.length,
      medianMs: at(0.5),
      p95Ms: at(0.95),
      maxMs: +d[d.length - 1].toFixed(2),
      over20ms: d.filter(x => x > 20).length,
      over33ms: d.filter(x => x > 33).length,
    };
  }

  /* 峰值数据：默认 demo/mock.js 会按需生成，这里直接塞进 peakCache，
     让 drawWave() 走"真波形"那条最贵的分支，且不受网络抖动影响。 */
  const PEAKS = new Array(1000);
  for (let i = 0; i < 1000; i++) PEAKS[i] = Math.abs(Math.sin(i / 9.7)) * 0.93;
  const seedPeaks = () => {
    for (const f of FILES) {
      if (!peakCache.has(f.id)) peakCache.set(f.id, { fileId: f.id, buckets: 1000, peaks: PEAKS });
    }
  };
  seedPeaks();
  await raf();

  out.env = {
    files: FILES.length,
    audio: FILES.filter(f => f.kind === 'audio').length,
    cards: nCards(),
    canvases: nCanvas(),
    fileListDomNodes: nNodes('#fileList *'),
    dpr: window.devicePixelRatio,
    theme: document.documentElement.dataset.theme,
  };

  /* A. 波形重绘：总耗时 + getComputedStyle 调用次数 -------------------- */
  window.__gcs = 0;
  const waveMs = bench(() => redrawAllWaves(), 3);
  out.wave = {
    canvases: nCanvas(),
    msPerRedraw: +waveMs.toFixed(2),
    msPerCanvas: +(waveMs / Math.max(1, nCanvas())).toFixed(3),
    gcsPerRedraw: Math.round(gcs() / 3),
  };

  /* A2. 同一件事，但把根元素的计算样式缓存起来（模拟"令牌缓存"这一项修复）
     用 A/B 把 getComputedStyle 的净代价从总时间里剥出来。 */
  const g0 = window.getComputedStyle;
  let rootDecl = null;
  window.getComputedStyle = function (el, ps) {
    if (el === document.documentElement && !ps) {
      if (!rootDecl) rootDecl = g0(el, ps);
      return rootDecl;
    }
    return g0(el, ps);
  };
  const waveMemoMs = bench(() => redrawAllWaves(), 3);
  window.getComputedStyle = g0;
  out.waveMemo = {
    msPerRedraw: +waveMemoMs.toFixed(2),
    speedup: +(waveMs / waveMemoMs).toFixed(2),
    savedMsPerRedraw: +(waveMs - waveMemoMs).toFixed(2),
  };

  /* B. renderFiles()：innerHTML 重建 + 顺带画的波形 ------------------- */
  const cvBefore = document.querySelector('[data-wave]');
  window.__gcs = 0;
  const filesMs = bench(() => renderFiles(), 3);
  const cvAfter = document.querySelector('[data-wave]');
  out.renderFiles = {
    msPerCall: +filesMs.toFixed(2),
    canvasRecreatedEachCall: cvBefore !== cvAfter,
    gcsPerCall: Math.round(gcs() / 3),
    /* renderFiles 末尾会自己把波形全画一遍，所以这一项 ≈ 模板+解析+布局 */
    nonWaveMs: +(filesMs - waveMs).toFixed(2),
  };

  /* C. 规模曲线：轮询每 2s 打一次，看它随文件数怎么长 ---------------- */
  const base = FILES.map(f => f._server).filter(Boolean);
  function payloadN(n) {
    const res = [];
    let k = 0;
    while (res.length < n && base.length) {
      for (const row of base) {
        if (res.length >= n) break;
        const c = JSON.parse(JSON.stringify(row));
        c.id = row.id + '_x' + k;
        c.name = 'x' + k + '_' + row.name;
        if (c.info) c.info = Object.assign({}, c.info, { hasCover: false });  // 隔离封面图请求
        res.push(c);
      }
      k++;
    }
    return res;
  }
  out.scaling = [];
  for (const n of [12, 48, 96, 192]) {
    window.applyServerFiles(payloadN(n));
    seedPeaks();
    await raf(); await raf();
    window.__gcs = 0;
    const ms = bench(() => renderFiles(), 5);
    out.scaling.push({
      files: FILES.length,
      cards: nCards(),
      msPerPoll: +ms.toFixed(2),
      msPerFile: +(ms / Math.max(1, FILES.length)).toFixed(3),
      gcsPerPoll: Math.round(gcs() / 5),
      domNodes: nNodes('#fileList *'),
      /* 每次 2s 轮询里，主线程被占用的比例 */
      dutyPct: +((ms / 2000) * 100).toFixed(2),
    });
  }

  /* D. 日志面板：applyServerLogs() 每轮都整表重建 -------------------- */
  const savedLogs = LOGS.slice();
  LOGS.length = 0;
  for (let i = 0; i < 500; i++) LOGS.push({ t: '12:00:00', m: '第 ' + i + ' 行日志内容，用于测量重建成本', k: '' });
  out.renderLogs500 = {
    msPerCall: +bench(() => renderLogs(), 3).toFixed(2),
    domNodes: nNodes('#logList *'),
  };
  LOGS.length = 0;
  savedLogs.forEach(l => LOGS.push(l));
  renderLogs();

  /* E. 任务队列面板 -------------------------------------------------- */
  const savedTasks = TASKS.slice();
  TASKS.length = 0;
  for (let i = 0; i < 12; i++) {
    TASKS.push({ id: 't' + i, name: 'convert · 文件' + i, pct: 40,
                 state: i < 2 ? 'running' : (i < 5 ? 'pending' : 'success'),
                 meta: '执行中', err: '', retryId: null, output: null });
  }
  out.renderQueue12 = { msPerCall: +bench(() => renderQueue(), 3).toFixed(2) };
  TASKS.length = 0;
  savedTasks.forEach(t => TASKS.push(t));
  renderQueue();

  /* F. 抽屉展开动画的帧间隔（backdrop-filter + top 过渡都在这条链上） */
  applyStop('closed');
  await sleep(450);
  const deltas = [];
  let last = performance.now(), stop = false;
  const rec = () => {
    const t = performance.now();
    deltas.push(t - last);
    last = t;
    if (!stop) requestAnimationFrame(rec);
  };
  requestAnimationFrame(rec);
  applyStop('full');
  await sleep(700);
  stop = true;
  await raf();
  out.drawerAnim = frameStats(deltas);

  /* G. .fcard 的 3D 跟随 + AM 网点：每帧一次 pointermove，量帧间隔 */
  const fc = document.querySelector('#cardSections .fcard');
  if (fc) {
    const g1 = window.getComputedStyle;
    window.getComputedStyle = window.__gcsOrig;      // 这一段不数调用次数，只量帧
    const fr = fc.getBoundingClientRect();
    const deltas2 = [];
    let last2 = performance.now(), stop2 = false;
    const rec2 = () => {
      const t = performance.now();
      deltas2.push(t - last2);
      last2 = t;
      if (!stop2) requestAnimationFrame(rec2);
    };
    requestAnimationFrame(rec2);
    const tStart = performance.now();
    let i = 0;
    while (performance.now() - tStart < 700) {
      fc.dispatchEvent(new PointerEvent('pointermove', {
        clientX: fr.left + 20 + (i % 100), clientY: fr.top + 20 + (i % 40), bubbles: true,
      }));
      i++;
      await raf();
    }
    stop2 = true;
    await raf();
    window.getComputedStyle = g1;

    /* 纯 JS 侧：连续派发 300 次，量 getBoundingClientRect + 4 个变量写入 */
    window.__gcs = 0;
    const t0 = performance.now();
    for (let k = 0; k < 300; k++) {
      fc.dispatchEvent(new PointerEvent('pointermove', {
        clientX: fr.left + 20 + (k % 100), clientY: fr.top + 20 + (k % 40), bubbles: true,
      }));
    }
    const jsMs = performance.now() - t0;
    out.fcardFollow = {
      moves: i,
      frames: frameStats(deltas2),
      jsPerEventMs: +(jsMs / 300).toFixed(4),
      gcsPerEvent: +(window.__gcs / 300).toFixed(3),
    };
  }

  /* I. 轮询空转：数据一个字都没变时 applyServerFiles() 的代价。
     这是 P1-1（数据指纹）那一项的直接度量 —— 修复前应当等于"整表重建"的量级，
     修复后应当接近 0。 */
  const samePayload = FILES.map(f => f._server).filter(Boolean);
  if (samePayload.length) {
    window.applyServerFiles(samePayload);   // 先让指纹落到当前这份数据上
    await raf(); await raf();
    out.pollNoop = {
      files: FILES.length,
      msPerCall: +bench(() => window.applyServerFiles(samePayload), 5).toFixed(3),
    };
  }

  /* H. 换主题：红绘全部波形（view-transition + mix-blend-mode 另算） */
  const tr = document.documentElement;
  const oldTheme = tr.dataset.theme;
  seedPeaks();
  tr.dataset.theme = oldTheme === 't1' ? 't2' : 't1';
  out.themeRedraw = { msPerRedraw: +bench(() => redrawAllWaves(), 3).toFixed(2) };
  tr.dataset.theme = oldTheme;
  redrawAllWaves();

  return out;
})()
"""


# ------------------------------------------------------------------- 主流程

async def run(dpr):
    demo = ROOT / "demo"
    server = subprocess.Popen(
        [sys.executable, str(demo / "serve.py"), "--port", str(HTTP_PORT), "--no-browser"],
        cwd=str(demo), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_http(f"http://127.0.0.1:{HTTP_PORT}/index.html"):
        server.kill()
        raise SystemExit(f"demo 静态服务没起来（: {HTTP_PORT}）")

    # user-data-dir 放在仓库内（被 .gitignore 的 _edge*/ 覆盖），
    # 与 snapshot_drag_real.py 用同一套启动参数 —— 那套是已经跑通的。
    profile = ROOT / "_edge_perf"
    shutil.rmtree(profile, ignore_errors=True)
    edge = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
        f"--remote-debugging-port={CDP_PORT}", "--window-size=1440,900",
        # 新版 Chromium 会拒绝带 Origin 头的 DevTools 握手 —— 症状是
        # 连上后第一条命令就 ConnectionClosed。这个开关是官方给的解法。
        "--remote-allow-origins=*",
        f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--disable-extensions", "--mute-audio", "--hide-crash-restore-bubble",
        f"--force-device-scale-factor={dpr}", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        ws_url = wait_page_ws(CDP_PORT)
        if not ws_url:
            raise SystemExit("无头 Edge 没开出可调试页面")
        async with websockets.connect(ws_url, max_size=32 * 1024 * 1024) as ws:
            page = Page(ws)
            await page.send("Page.enable")
            await page.send("Page.navigate",
                            url=f"http://127.0.0.1:{HTTP_PORT}/index.html")
            # 等首屏真的画出文件卡片，再开始量
            for _ in range(120):
                try:
                    got = await page.ev("document.querySelectorAll('#fileList .card').length")
                except Exception:
                    got = 0            # 页面还在导航，执行上下文尚未就绪
                if got:
                    break
                await asyncio.sleep(0.25)
            else:
                raise SystemExit("页面 30s 内没渲染出文件卡片")
            await asyncio.sleep(1.0)          # 让首轮的 peaks / 封面稳定下来
            return await page.ev(PAGE_JS, await_promise=True)
    finally:
        edge.terminate()
        server.terminate()
        try:
            edge.wait(timeout=5)
        except Exception:
            edge.kill()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()
        shutil.rmtree(profile, ignore_errors=True)


def report(d):
    e = d["env"]
    print(f"\n环境：{e['files']} 个文件（{e['audio']} 个音频）· {e['cards']} 张卡片 · "
          f"{e['canvases']} 个 canvas · #fileList 内 {e['fileListDomNodes']} 个节点 · "
          f"DPR {e['dpr']} · 主题 {e['theme']}")

    w = d["wave"]
    print(f"\n[A] 波形重绘（redrawAllWaves，{w['canvases']} 个 canvas）")
    print(f"    每次 {w['msPerRedraw']} ms · 每个 canvas {w['msPerCanvas']} ms · "
          f"getComputedStyle {w['gcsPerRedraw']} 次/次")
    m = d["waveMemo"]
    print(f"    缓存根元素计算样式后：{m['msPerRedraw']} ms "
          f"（快 {m['speedup']}×，每次省 {m['savedMsPerRedraw']} ms）")

    r = d["renderFiles"]
    print(f"\n[B] renderFiles()（innerHTML 整表重建 + 顺带画波形）")
    print(f"    每次 {r['msPerCall']} ms · 其中非波形部分 {r['nonWaveMs']} ms · "
          f"getComputedStyle {r['gcsPerCall']} 次/次")
    print(f"    每次重建都会换掉 canvas 元素：{r['canvasRecreatedEachCall']}")

    print(f"\n[C] 规模曲线（每 2s 一次轮询真正落到的成本）")
    print(f"    {'文件数':>6} {'DOM节点':>8} {'每次 ms':>9} {'每文件 ms':>10} "
          f"{'gCS 次数':>9} {'占用主线程':>10}")
    for s in d["scaling"]:
        print(f"    {s['files']:>6} {s['domNodes']:>8} {s['msPerPoll']:>9} "
              f"{s['msPerFile']:>10} {s['gcsPerPoll']:>9} {s['dutyPct']:>9}%")

    l = d["renderLogs500"]
    print(f"\n[D] 日志面板整表重建（500 行）：{l['msPerCall']} ms/次 · "
          f"{l['domNodes']} 个 DOM 节点")
    print(f"[E] 任务队列面板（12 条）：{d['renderQueue12']['msPerCall']} ms/次")

    p = d.get("pollNoop")
    if p:
        print(f"\n[I] 轮询空转（数据没变，{p['files']} 个文件）："
              f"{p['msPerCall']} ms/次 —— P1-1 数据指纹的收益就看这一项")

    a = d["drawerAnim"]
    if a:
        print(f"\n[F] 抽屉展开动画：{a['frames']} 帧 · 中位 {a['medianMs']} ms · "
              f"p95 {a['p95Ms']} ms · 最大 {a['maxMs']} ms · "
              f">20ms {a['over20ms']} 帧 · >33ms {a['over33ms']} 帧")

    f = d.get("fcardFollow")
    if f and f["frames"]:
        fr = f["frames"]
        print(f"\n[G] .fcard 3D 跟随 + AM 网点（每帧一次 pointermove，{f['moves']} 帧）")
        print(f"    帧间隔：中位 {fr['medianMs']} ms · p95 {fr['p95Ms']} ms · "
              f"最大 {fr['maxMs']} ms · >20ms {fr['over20ms']} 帧 · >33ms {fr['over33ms']} 帧")
        print(f"    纯 JS 侧：{f['jsPerEventMs']} ms/事件 · "
              f"getComputedStyle {f['gcsPerEvent']} 次/事件")

    print(f"\n[H] 换主题后整表重绘波形：{d['themeRedraw']['msPerRedraw']} ms/次")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dpr", type=int, default=1, help="强制设备像素比（默认 1）")
    ap.add_argument("--json", default="", help="把原始测量结果写到这个文件")
    args = ap.parse_args()

    data = asyncio.run(run(args.dpr))
    if not data:
        raise SystemExit("没拿到测量结果")
    report(data)
    if args.json:
        Path(args.json).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\n原始数据已写入 {args.json}")


if __name__ == "__main__":
    main()
