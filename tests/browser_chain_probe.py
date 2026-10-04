"""执行链前端自检（真浏览器，走 CDP）。

对应《执行链并发方案.md》§3.7 / §3.2.4 / §9.1 —— 这些必须在**真页面**上验，
因为要证的是"接线接对了"：

  1. 顶栏那个 串行/并行 开关真的存在、真的持久化、真的进了提交体
  2. 链上的"接不上"提示按档位出现/消失，并且「改成串行」真的切档
  3. **卡片可用性置灰**：`打包 ZIP` 之后所有卡片变灰、点了不加入链、给得出原因；
     删掉 ZIP 之后**全部恢复**（证明重算走的是同一条刷新路径，没有残留态）
  4. 一次提交：提交体里是**一条链**（steps 数组），而不是 N 次单点提交
  5. `保存预设` 是 disabled 且不撒谎；`导出脚本` 按钮**已从 DOM 消失**

真跑一次链会动到真实音频（转码要几十秒），所以这一版**只验到"提交体正确"**
就够 —— 真正的执行语义由 tests/chain_build_check.py 与 chain_queue_check.py 覆盖，
后端的端到端由 tests/smoke_api.py 覆盖。这样这个探针跑得快、可以常跑。

前置：服务在 8765 跑着，且库里至少有 1 个音频文件。
用法：python tests/browser_chain_probe.py
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

import websockets  # uvicorn[standard] 会带上

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 期望的 op 个数**从后端算**，不写死：写死的话每加一个 op 这条断言就红一次，
# 而它真正想证明的是"后端的 op 都送到了前端"（个数一致），不是"恰好 14 个"。
from backend.cards.specs import OPS as _OPS                            # noqa: E402

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9337
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


# 注入页面的探针：全部用页面自己的全局（chain / chainMode / CARDS / addToChain…）
#
# ⚠ 顶层 `const FILES = []` **不是 `window.FILES`**：经典脚本的顶层 const/let
# 落在"全局词法环境"里，`Runtime.evaluate` 能按裸标识符读到，但读 `window.FILES`
# 得到 undefined。第一版探针写的就是 `window.FILES`，于是报"页面没数据"，
# 而实际上 DOM 里已经有 34 个文件卡了 —— 白排查一轮。
PROBE = r"""
(async () => {
  const out = { errors: [] };
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const q = (s) => document.querySelector(s);
  const qa = (s) => [...document.querySelectorAll(s)];
  try {
    // 等首屏数据：用裸标识符读，不用 window.*
    for (let i = 0; i < 60 && !(typeof FILES !== 'undefined' && FILES.length); i++) await wait(250);
    await wait(600);
    out.files = (typeof FILES !== 'undefined' ? FILES.length : 0);
    out.cards = (typeof CARDS !== 'undefined' ? CARDS.length : 0);
    out.ops = (typeof OPS_CATALOG !== 'undefined' && OPS_CATALOG)
              ? Object.keys(OPS_CATALOG).length : 0;
    // 页面**实际执行**的是哪一版 app.js。浏览器缓存过一次就够把人坑惨：
    // 症状是"改完了、后端测试全绿，页面还是老行为"。
    // 用 `String(fn)` 读函数体，比看文件时间可靠。
    out.liveFnHasNoHandoff = String(availabilityOf).includes('noHandoff');

    // ---------- 0) 两个按钮：一个删掉、一个**真的能用** ----------
    // 历史：`保存预设` 曾是 P0-0 的诚实占位（disabled + title"未实现"）。
    // 现在它是真功能了，所以这两条断言**反过来**：必须是可点的，
    // 且 title 要说得清"存的是什么"（含档位与参数），不能再提"未实现"。
    out.exportBtnExists = !!q('#chainExport');
    const save = q('#chainSave');
    out.saveDisabled = !!save && save.disabled === true;
    out.saveTitle = save ? (save.title || '') : null;
    out.saveModalExists = !!q('#presetModal');
    out.drawerTabExists = !!q('#drawerTab');
    out.presetSectionsExists = !!q('#presetSections');

    // ---------- 1) 档位开关 ----------
    const modeBox = q('#chainMode');
    out.modeBoxExists = !!modeBox;
    out.modeHiddenWhenEmpty = modeBox ? modeBox.hidden : null;
    // 先清空链与档位，保证起点确定
    chain.length = 0; renderChain(); await wait(60);
    setChainMode('parallel'); await wait(60);
    out.modeAfterReset = chainMode;
    out.modeHiddenWhenHasChainBefore = modeBox.hidden;

    // 选一个文件 + 造一条链
    if (FILES.length) { FILES.forEach(f => f.checked = false); FILES[0].checked = true; syncBulkbar(); }
    const cardByName = (n) => CARDS.find(c => c.name === n);
    const cConvert = CARDS.find(c => c.op === 'convert');
    const cNormalize = CARDS.find(c => c.op === 'normalize');
    const cZip = cardByName('打包 ZIP') || CARDS.find(c => c.op === 'zip');
    addToChain(cConvert.name); await wait(80);
    addToChain(cNormalize.name); await wait(80);
    out.chainLen = chain.length;
    out.chainHasCardId = chain.every(s => typeof s.cardId === 'string' && s.cardId.length > 0);
    out.chainHasOpSnapshot = chain.every(s => !!s.op);
    out.modeHiddenWithChain = modeBox.hidden;

    // ---------- 2) 链上提示：并行档出现、串行档消失 ----------
    setChainMode('parallel'); await wait(120);
    const notes = q('#chainNotes');
    out.notesVisibleParallel = notes && !notes.hidden;
    out.notesTextParallel = notes ? notes.textContent.trim().slice(0, 120) : null;
    const fixBtn = q('#chainFixSerial');
    out.hasFixSerialBtn = !!fixBtn;
    if (fixBtn) { fixBtn.click(); await wait(120); }
    out.modeAfterFixClick = chainMode;
    out.notesVisibleSerial = notes && !notes.hidden;
    setChainMode('parallel'); await wait(100);

    // ---------- 3) 卡片可用性：**压缩包之后不再置灰**（P1 起） ----------
    // 回归钉子（反向）：原来这里钉的是"打包 ZIP 之后所有卡片变灰、点了不加入链"。
    // 但 `zip` 是 `mode=read` + `consumes=false`：它什么都没改动，后面的步骤照旧
    // 作用于当前文件；而且一条链可以有多个打包步骤（每个收集"上一个打包步骤之后"
    // 的产物，见 `执行链打包与串行交接方案.md` §4）。判据改动必须**前后端同步**，
    // 所以这条与后端 `boundary.availability` 的对账是本节的重点。
    const zipCardEl = qa('#cardSections .fcard[data-card]')
        .find(el => el.dataset.card === cZip.name);
    out.zipCardExists = !!zipCardEl;
    out.zipBlockedBefore = zipCardEl ? zipCardEl.classList.contains('fcard--blocked') : null;

    addToChain(cZip.name); await wait(150);
    const blockedEls = qa('#cardSections .fcard--blocked');
    out.blockedCountAfterZip = blockedEls.length;
    out.totalCards = qa('#cardSections .fcard[data-card]').length;
    const anyCard = qa('#cardSections .fcard[data-card]')
        .find(el => !el.classList.contains('fcard--blocked'));
    out.anySelectableAfterZip = !!anyCard;
    // 压缩包之后**每张卡都还该可选**（不置灰）
    out.selectableAfterZip = qa('#cardSections .fcard[data-card]').length - blockedEls.length;
    out.zipTailHint = (typeof availabilityOf === 'function')
        ? (availabilityOf('tags') || {}).why : null;

    // 「压缩包不会被递下去」这句提示要真的出现在链上提示区
    out.notesTextAfterZip = notes ? notes.textContent.trim().slice(0, 160) : null;

    // 删掉 zip 那一步 → 状态与加之前一致
    chain.pop(); renderChain(); await wait(150);
    out.blockedCountAfterUndo = qa('#cardSections .fcard--blocked').length;
    out.chainLenAfterUndo = chain.length;

    // ---------- 3b) gives='none' 的只读分析之后**不能**置灰 ----------
    // 回归钉子：`gives='none'`（响度报告 / 响度总览图 / 完整性校验 / 重新探测）
    // 的字面意思是"没交出产物"，真正的意思是"**没动那个音频文件**"——
    // 后面的卡照旧读原文件就行。前端 `availabilityOf` 的终态判定差一点把
    // `none` 当成"没有任何卡接得住"（只有 `zip` 显式收 `none`），
    // 现象是「响度分析报告」之后除打包以外**全部置灰**，正常用法被前端堵死。
    chain.length = 0; renderChain(); await wait(120);
    const noneCards = (typeof CARDS !== 'undefined' ? CARDS : [])
        .filter(c => c.op === 'loudness-report' || c.op === 'loudness'
                  || c.op === 'verify' || c.op === 'probe');
    out.noneCardsFound = noneCards.length;
    out.noneTailResults = [];
    for (const nc of noneCards) {
      chain.length = 0; renderChain(); await wait(60);
      addToChain(nc.name); await wait(120);
      const tailOp = chainTailOp();
      const blocked = qa('#cardSections .fcard--blocked').length;
      const total = qa('#cardSections .fcard[data-card]').length;
      out.noneTailResults.push({
        card: nc.name, op: nc.op, tail: tailOp,
        blocked: blocked, total: total,
        convertOk: (typeof availabilityOf === 'function')
          ? availabilityOf('convert').ok : null,
        zipOk: (typeof availabilityOf === 'function')
          ? availabilityOf('zip').ok : null,
      });
    }
    chain.length = 0; renderChain(); await wait(60);
    // 诊断：把 availabilityOf 里那几个量按 **OPS_CATALOG 的真实字段** 抄一遍
    out.noneDiag = (function () {
      chain.length = 0;
      if (typeof CARDS === 'undefined') return { why: 'no CARDS' };
      const nc = CARDS.find(c => c.op === 'loudness-report') || CARDS[0];
      addToChain(nc.name);
      const tail = chainTailOp();
      const ts = OPS_CATALOG[tail];
      const src = String(availabilityOf);
      return {
        tail: tail,
        tailKeys: Object.keys(ts || {}),
        produce: ts ? ts.produce : null,
        produceType: ts ? typeof ts.produce : null,
        produceIsNone: ts ? ts.produce === 'none' : null,
        produceIsSidecar: ts ? ts.produce === 'sidecar' : null,
        gives: ts ? ts.gives : null,
        catalogProbe: OPS_CATALOG['probe'] ? OPS_CATALOG['probe'].produce : 'MISSING',
        catalogReport: OPS_CATALOG['loudness-report']
          ? OPS_CATALOG['loudness-report'].produce : 'MISSING',
        avail: availabilityOf('convert'),
        // 现场复算：用**函数体内那几个真实变量**的等价表达式
        liveAnyAccepts: (function () {
          const give = ts.gives;
          const noHandoff = ts.produce === 'none' || ts.produce === 'sidecar';
          return { noHandoff: noHandoff,
                   some: Object.values(OPS_CATALOG).some(
                     s => (s.needs || []).includes(give) || (s.needs || []).includes('any')),
                   combined: noHandoff || Object.values(OPS_CATALOG).some(
                     s => (s.needs || []).includes(give) || (s.needs || []).includes('any')) };
        })(),
        srcHasNoHandoff: src.includes('noHandoff'),
        srcAnyAcceptsLine: src.split('\n')
          .filter(l => l.includes('anyAccepts') || l.includes('noHandoff')).join(' | '),
        srcLen: src.length,
        srcHash: src.split('').reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 7),
      };
    })();
    chain.length = 0; renderChain(); await wait(60);

    // ---------- 4) 一次提交：抓提交体 ----------
    // ⚠ 上面 pop 掉的是 zip，链里还剩 convert/normalize 两步 —— 但发现
    // `chain.length` 若为 0，`runChain` 会**提前 return**（"执行链为空"），
    // 于是什么都抓不到。这里显式把链重建到确定的两步，别依赖前面几节的残留。
    chain.length = 0;
    addToChain(cConvert.name); await wait(60);
    addToChain(cNormalize.name); await wait(60);
    setChainMode('parallel'); await wait(60);
    out.chainLenBeforeSubmit = chain.length;

    let captured = null;
    out.diag = {};
    if ((typeof API !== 'undefined') && API.chain) {
      const realChain = API.chain, realOp = API.op;
      API.chain = async (payload) => { captured = payload;
        return { chainId: 'ch_probe', steps: [], total: 0, notes: [] }; };
      API.op = async () => { throw new Error('整链执行不该走单点提交'); };
      out.diag.chainLen = chain.length;
      out.diag.selected = FILES.filter(f => f.checked).length;
      out.diag.chainMode = chainMode;
      out.diag.apiIsWindow = (typeof API !== 'undefined');
      // 直接看一眼"解析后的步骤"能不能算出来 —— 这是 runChain 的第一道关
      try {
        const resolved = await resolveChainSteps(null);
        out.diag.resolvedSteps = resolved ? resolved.length : null;
        out.diag.resolvedOps = resolved ? resolved.map(s => s.op) : null;
      } catch (e) { out.diag.resolveError = String(e); }
      await runChain();
      await wait(200);
      out.diag.capturedAfter = !!captured;
      API.chain = realChain; API.op = realOp;
    } else {
      out.diag.noApi = { api: typeof API, chain: (typeof API !== 'undefined') ? typeof API.chain : 'n/a' };
    }
    out.submitted = captured ? JSON.parse(JSON.stringify(captured)) : null;
    out.submitMode = captured ? captured.mode : null;
    out.submitStepCount = captured ? (captured.steps || []).length : null;
    out.submitStepsHaveOp = captured ? (captured.steps || []).every(s => !!s.op) : null;
    out.submitFileIds = captured ? (captured.fileIds || []).length : null;

    // 串行档下的提交体
    captured = null;
    setChainMode('serial'); await wait(80);
    if ((typeof API !== 'undefined') && API.chain) {
      const realChain = API.chain, realOp = API.op;
      API.chain = async (payload) => { captured = payload;
        return { chainId: 'ch_probe2', steps: [], total: 0, notes: [] }; };
      API.op = async () => { throw new Error('不该走单点'); };
      await runChain();
      await wait(150);
      API.chain = realChain; API.op = realOp;
    }
    out.serialSubmitMode = captured ? captured.mode : null;

    // ---------- 5) 单步执行不走链 ----------
    let opCalled = null, chainCalled = false;
    if (typeof API !== 'undefined') {
      const rc = API.chain, ro = API.op;
      API.chain = async () => { chainCalled = true; return {}; };
      API.op = async (name) => { opCalled = name; return { total: 1 }; };
      await runChain(0);
      await wait(150);
      API.chain = rc; API.op = ro;
    }
    out.singleStepOp = opCalled;
    out.singleStepUsedChain = chainCalled;

    // ---------- 6) 前端可用性 vs 后端判据：**逐组对账** ----------
    // 后端 `boundary.availability` 是边界的权威，前端 `availabilityOf` 是体验层，
    // 两者判据必须同源。分叉过一次，表现是"后端说能选、前端把卡置灰了"，
    // 而当时的断言只覆盖后端、只覆盖"zip 之后全灰"，于是十几组不一致却全绿。
    out.opsList = Object.keys(OPS_CATALOG || {});
    out.parityChecked = 0;
    out.parityBad = [];
    {
      const reportTail = (typeof CARDS !== 'undefined' ? CARDS : [])
        .find(c => c.op === 'loudness-report');
      for (const op of out.opsList) {
        for (const tailSpec of out.opsList) {
          // 用页面自己的链来驱动：清空 → 加入能产生该 op 的卡
          const tc = (typeof CARDS !== 'undefined' ? CARDS : [])
            .find(c => c.op === tailSpec);
          if (!tc) continue;
          chain.length = 0;
          chain.push({ cardId: tc.id || '', name: tc.name, op: tailSpec,
                       params: JSON.parse(JSON.stringify(tc.params || {})),
                       ico: tc.ico || '', custom: !!tc.custom });
          const fe = availabilityOf(op);
          const ts = OPS_CATALOG[tailSpec];
          const spec = OPS_CATALOG[op];
          const give = ts.gives;
          const needs = spec.needs || [];
          let beOk;
          // ⚠ 探针里这份是**后端判据的镜像**（`boundary.availability`）：
          // 压缩包那条 P1 起从"硬禁"降级成"可选 + 提示"，所以这里也必须是
          // `true` —— 否则对账会把"前后端其实一致"报成不一致。
          if (give === 'archive') beOk = true;
          else if (ts.produce === 'none' || ts.produce === 'sidecar') beOk = true;
          else if (spec.consumes === false) beOk = true;
          else beOk = (needs.includes('any') || needs.includes(give));
          out.parityChecked += 1;
          if (fe.ok !== beOk) {
            out.parityBad.push({ tail: tailSpec, op: op, fe: fe.ok, be: beOk,
                                 why: fe.why, give: give,
                                 produce: ts.produce, consumes: spec.consumes });
          }
        }
      }
      out.parityReportTail = reportTail ? reportTail.name : null;
    }

    // ---------- 7) 串行档的格式流：有损→无损要在**点之前**就置灰 ----------
    // 用户报的现象："串行模式下仍然可以先转 MP3 再转 WAV"。
    // 后端一直是拦的（400 + stepIdx），但**提交前看不出来** —— 两张卡都能加进链，
    // 点执行才报错。前端必须自己算一遍格式流（`formatFlowBlocked`）。
    out.ffCases = [];
    if (typeof CARDS !== 'undefined' && typeof formatFlowBlocked === 'function') {
      const findByName = (n) => CARDS.find(c => c.name === n);
      const mp3 = findByName('转 MP3 320');
      const wav = findByName('转 WAV 24bit');
      const flac = findByName('转 FLAC');
      const ff = (name) => {
        const c = findByName(name);
        return c ? formatFlowBlocked(c) : '卡不存在';
      };
      // (1) 串行 + 链里已有「转 MP3」→ 「转 WAV」必须被挡
      chain.length = 0; setChainMode('serial'); renderChain();
      if (mp3) addToChain(mp3.name);
      out.ffCases.push({ case: '串行: MP3→WAV 被挡', got: !!ff('转 WAV 24bit'),
                         expect: true });
      out.ffCases.push({ case: '串行: MP3→FLAC 被挡', got: !!ff('转 FLAC'),
                         expect: true });
      // (2) 并行档同一条链不该挡
      setChainMode('parallel'); renderChain();
      out.ffCases.push({ case: '并行: MP3→WAV 不挡', got: !!ff('转 WAV 24bit'),
                         expect: false });
      // (3) 无损→有损永远放行
      chain.length = 0; setChainMode('serial'); renderChain();
      if (flac) addToChain(flac.name);
      out.ffCases.push({ case: '串行: FLAC→MP3 放行', got: !!ff('转 MP3 320'),
                         expect: false });
      // (4) 第 0 步不判：空链时"转 WAV"能当第一张
      chain.length = 0; renderChain();
      out.ffCases.push({ case: '空链: 转 WAV 可当第一张（第 0 步不判）',
                         got: !!ff('转 WAV 24bit'), expect: false });
      // (5) 点一张本该被挡的卡 → 真的加不进链
      chain.length = 0; setChainMode('serial'); renderChain();
      if (mp3) addToChain(mp3.name);
      const before = chain.length;
      const wavEl = [...document.querySelectorAll('#cardSections .fcard[data-card]')]
        .find(el => (el.dataset.card || '') === '转 WAV 24bit');
      out.ffWavGreyed = wavEl ? wavEl.classList.contains('fcard--blocked') : null;
      if (wavEl) wavEl.click();
      out.ffClickAdded = chain.length - before;
      chain.length = 0; setChainMode('parallel'); renderChain();

      // (6) 码率规则：**有损源**才适用。样例库里是 FLAC（无损），
      //     所以临时把那一行改造成"MP3 128k"再问一遍，然后还原 ——
      //     比"上传一个真 MP3"省事，测的还是同一条前端代码路径。
      const f0 = FILES[0];
      if (f0) {
        const save = { relPath: f0.relPath, info: f0.info };
        f0.relPath = 'src128.mp3';
        f0.info = Object.assign({}, f0.info, { bitRate: 128000, sampleRate: 44100 });
        chain.length = 0; setChainMode('serial'); renderChain();
        out.br = {
          src320: !!ff('转 MP3 320'),        // 128 → 320：拦
          src128: !!ff('转 MP3 128k'),       // 128 → 128：放
          srcOpus96: !!ff('转 Opus 96k'),    // 128 → 96：放
          srcFlac: !!ff('转 FLAC'),          // 单步转无损：放（第 0 步不判格式）
          srcWav: !!ff('转 WAV 24bit'),      // 无损容器：放
        };
        // 链内升码率：先转 WAV（无损），再转 MP3 → 放行
        const wavCard = findByName('转 WAV 24bit');
        if (wavCard) addToChain(wavCard.name);
        out.br.afterWavToMp3 = !!ff('转 MP3 320');   // WAV → MP3 320：放
        chain.length = 0;
        const mp3128 = findByName('转 MP3 128k');
        if (mp3128) addToChain(mp3128.name);
        out.br.inChainUp = !!ff('转 MP3 320');       // MP3 128 → MP3 320：拦
        out.br.inChainDown = !!ff('转 Opus 96k');    // MP3 128 → Opus 96：放
        chain.length = 0;
        const mp3320 = findByName('转 MP3 320');
        if (mp3320) addToChain(mp3320.name);
        out.br.inChainDown2 = !!ff('转 MP3 128k');   // MP3 320 → MP3 128：放
        // 并行档：同一条链不拦
        setChainMode('parallel'); renderChain();
        out.br.parallelUp = !!ff('转 MP3 320');
        chain.length = 0; setChainMode('serial'); renderChain();
        out.br.unitSource = !!ff('转 MP3 320');
        // 还原
        f0.relPath = save.relPath; f0.info = save.info;
        chain.length = 0; setChainMode('parallel'); renderChain();
      }
    }

    // 复位
    chain.length = 0; setChainMode('parallel'); renderChain();
  } catch (e) {
    out.errors.push(String(e && e.stack || e));
  }
  return out;
})()
"""


async def run_all() -> None:
    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
         f"--remote-debugging-port={PORT}", "--window-size=1406,927",
         f"--user-data-dir={ROOT / '_cp'}", BASE],
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

            await send("Page.enable")
            await send("Runtime.enable")
            await asyncio.sleep(2.5)
            res = await send("Runtime.evaluate", {
                "expression": PROBE, "awaitPromise": True, "returnByValue": True,
                "timeout": 60000})
            if "exceptionDetails" in res:
                raise RuntimeError(json.dumps(res["exceptionDetails"],
                                              ensure_ascii=False)[:800])
            out = res["result"]["value"]

        print("== 前端执行链（真页面）==")
        check("探针没有抛异常", not out.get("errors"), out.get("errors"))
        check("页面拿到了文件与卡片", out["files"] > 0 and out["cards"] > 0,
              f"files={out['files']} cards={out['cards']}")
        check(f"/api/ops 的字段到了前端（OPS_CATALOG 有 {len(_OPS)} 个 op）",
              out["ops"] == len(_OPS), out["ops"])

        print()
        print("--- 两个按钮 ---")
        check("`导出脚本` 按钮已从 DOM 消失", out["exportBtnExists"] is False,
              out["exportBtnExists"])
        # P0-0 的占位断言在这里**反转**：功能实现了，按钮就必须真能点。
        check("`保存预设` 现在是**可点的**（功能已实现，不再是诚实占位）",
              out["saveDisabled"] is False, out["saveDisabled"])
        check("title 说得清存的是什么（不再提「未实现」）",
              "未实现" not in (out["saveTitle"] or "")
              and "预设" in (out["saveTitle"] or ""), out["saveTitle"])
        check("保存预设对话框与预设容器都在 DOM 里",
              out["saveModalExists"] and out["drawerTabExists"]
              and out["presetSectionsExists"],
              (out["saveModalExists"], out["drawerTabExists"],
               out["presetSectionsExists"]))

        print()
        print("--- 档位开关 ---")
        check("顶栏有档位开关", out["modeBoxExists"] is True)
        check("链为空时不占地方（hidden）", out["modeHiddenWhenEmpty"] is True,
              out["modeHiddenWhenEmpty"])
        check("有链时显示出来", out["modeHiddenWithChain"] is False,
              out["modeHiddenWithChain"])
        check("默认是并行（不动存量行为）", out["modeAfterReset"] == "parallel",
              out["modeAfterReset"])

        print()
        print("--- 链上提示与「改成串行」 ---")
        check("并行档：链上出现「接不上」提示", out["notesVisibleParallel"] is True,
              out["notesTextParallel"])
        check("提示文案说明了会回到原文件",
              "原文件" in (out["notesTextParallel"] or ""), out["notesTextParallel"])
        check("提示里带「改成串行」按钮", out["hasFixSerialBtn"] is True)
        check("点它真的切到串行", out["modeAfterFixClick"] == "serial",
              out["modeAfterFixClick"])
        check("串行档：提示消失（产物接上了）", out["notesVisibleSerial"] is False,
              out["notesVisibleSerial"])

        print()
        print("--- 卡片可用性（链尾决定） ---")
        check("ZIP 卡片在加入前是可选的", out["zipBlockedBefore"] is False,
              out["zipBlockedBefore"])
        # ⚠ P1 起这里**反过来**钉：压缩包之后不再整片置灰。
        # 原来钉的是"打包 ZIP 之后所有卡片变灰、点了不加入链"，而现在
        # `打包 → 任何卡` 都是合法链（多打包步骤 + 压缩包不会被递下去）。
        check("`打包 ZIP` 加入链后**没有卡片被置灰**",
              out["blockedCountAfterZip"] == 0,
              f"blocked={out['blockedCountAfterZip']} total={out['totalCards']}")
        check("每一张都还能选", out["selectableAfterZip"] == out["totalCards"],
              (out["selectableAfterZip"], out["totalCards"]))
        check("链尾是压缩包时，提示里说清它不会被递下去",
              "压缩包" in (out["zipTailHint"] or ""), out["zipTailHint"])
        check("链上提示区真的显示了这条提示",
              "压缩包" in (out["notesTextAfterZip"] or ""), out["notesTextAfterZip"])
        check("删掉 ZIP 之后状态与加之前一致（都不置灰）",
              out["blockedCountAfterUndo"] == 0, out["blockedCountAfterUndo"])

        print()
        print("--- 只读分析（gives=none）之后不许整片置灰 ---")
        # 先自证跑的是新代码：否则下面几条失败会把"缓存"误报成"逻辑坏了"
        check("页面执行的是含本修复的 app.js（不是缓存里的旧版）",
              out["liveFnHasNoHandoff"] is True, out["liveFnHasNoHandoff"])
        print("        [诊断] noneDiag =", json.dumps(out.get("noneDiag"),
                                                     ensure_ascii=False))
        check("页面上找得到 gives='none' 的代表卡",
              out["noneCardsFound"] >= 4, out["noneCardsFound"])
        for r in out["noneTailResults"]:
            check(f"「{r['card']}」之后没有卡被置灰",
                  r["blocked"] == 0,
                  f"blocked={r['blocked']}/{r['total']} tail={r['tail']}")
            check(f"「{r['card']}」之后格式转换仍可选", r["convertOk"] is True,
                  r["convertOk"])
            check(f"「{r['card']}」之后打包也仍可选", r["zipOk"] is True, r["zipOk"])

        print()
        print("--- 前端 availabilityOf 与后端判据全组合对账 ---")
        # 这次真 bug 的**通用解**：后端 `boundary.availability` 是边界的权威，
        # 前端 `availabilityOf` 是体验层，两者判据必须同源。分叉过一次的表现是
        # "后端说能选、前端把卡置灰了"，而当时的断言只覆盖后端 + 只覆盖
        # "zip 之后全灰"，于是 240 组里十几组不一致却全绿。
        check("对账覆盖了全部 op×op 组合",
              out["parityChecked"] >= len(out["opsList"]) ** 2,
              f"checked={out['parityChecked']}")
        check("前端可用性与后端判据**逐组一致**",
              not out["parityBad"], (out["parityBad"] or [])[:6])

        print()
        print("--- 串行档格式流：有损→无损必须在**点之前**就置灰 ---")
        for c in (out.get("ffCases") or []):
            check(c["case"], c["got"] is c["expect"],
                  f"got={c['got']} expect={c['expect']}")
        check("「转 WAV 24bit」在「转 MP3 320」之后被置灰", out["ffWavGreyed"] is True,
              out["ffWavGreyed"])
        check("点那张置灰的卡**加不进链**", out["ffClickAdded"] == 0,
              out["ffClickAdded"])

        print()
        print("--- 码率规则：有损源升码率也要在点之前置灰 ---")
        br = out.get("br") or {}
        check("源 MP3 128k → 转 MP3 320 **被挡**（不会白丢一层信息）",
              br.get("src320") is True, br)
        check("源 MP3 128k → 转 MP3 128k 放行", br.get("src128") is False, br)
        check("源 MP3 128k → 转 Opus 96k 放行（降码率）",
              br.get("srcOpus96") is False, br)
        check("源 MP3 128k → 转 FLAC 放行（第 0 步不判格式流）",
              br.get("srcFlac") is False, br)
        check("**用户给的用例**：转 WAV → 转 MP3 320 放行（先到无损再编码）",
              br.get("afterWavToMp3") is False, br)
        check("链内 MP3 128k → 转 MP3 320 **被挡**", br.get("inChainUp") is True, br)
        check("链内 MP3 128k → 转 Opus 96k 放行", br.get("inChainDown") is False, br)
        check("链内 MP3 320 → 转 MP3 128k 放行（降码率）",
              br.get("inChainDown2") is False, br)
        check("并行档不判码率", br.get("parallelUp") is False, br)

        print()
        print("--- 一次提交（不再逐步提交） ---")
        check("提交体里有 steps 数组（一条链）", out["submitStepCount"] == 2,
              out["submitStepCount"])
        check("每一步都带 op（卡片删了也能跑）", out["submitStepsHaveOp"] is True)
        check("提交体带 mode=parallel", out["submitMode"] == "parallel", out["submitMode"])
        check("提交体带 fileIds", (out["submitFileIds"] or 0) >= 1, out["submitFileIds"])
        check("切到串行后提交体的 mode=serial", out["serialSubmitMode"] == "serial",
              out["serialSubmitMode"])
        check("链上的步骤存了 cardId（身份不用名字）", out["chainHasCardId"] is True)
        check("链上的步骤存了 op 快照", out["chainHasOpSnapshot"] is True)

        print()
        print("--- 单步执行不构成链 ---")
        check("点某一步走的是单点提交", out["singleStepOp"] is not None,
              out["singleStepOp"])
        check("单步**没有**走链提交（它不受档位约束）",
              out["singleStepUsedChain"] is False)

    finally:
        proc.kill()

    print()
    print(f"结果：{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(run_all())
