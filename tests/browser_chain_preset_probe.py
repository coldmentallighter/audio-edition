"""`保存预设` 全流程探针（方案 §7.2 / §3.8.2）。

在真页面上点一遍，验的是**接线**而不是"函数返回值对"：

  1. 存 `批量改标签 → 转 FLAC` → 对话框里名称默认 `预设_01`、描述非空、显示 `串行`
  2. 保存后 `GET /api/presets` 读得到，且 `mode='serial'`、`steps` 存的是 `{op, params}`
     **不是卡片名**
  3. **刷新页面**后预设仍在
  4. 再存一条，名称默认变成 `预设_02`（**max+1，不复用空洞**）
  5. 手工塞一条非法预设进 `cards.json` → 点它**逐条报原因并跳过该项**
  6. `op` 不在 `OPS` 里 → 整条标灰（`is-unknown`）+ notice 警告，但**照样能加载**
     （快照齐全时它真的能跑，所以不是禁用）

用法：服务在 8765，`python tests/browser_chain_probe.py` 那套 CDP 环境。
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
PORT = 9351
PROFILE = ROOT / "tests" / "_preset_profile"
BASE = "http://127.0.0.1:8765"
CARDS_JSON = ROOT / "cards.json"

PASS = FAIL = 0


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}   {detail}")


def http(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            return e.code, {}


# 页面里要跑的那一段（IIFE，返回值会被 returnByValue 收走）
PROBE = r"""
(async () => {
  const out = { errors: [] };
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const card = (n) => CARDS.find(c => c.name === n);
  try {
    // ---------- 1) 排一条链：批量改标签 → 转 FLAC ----------
    chain.length = 0;
    setChainMode('serial');
    addToChain('批量改标签');
    await wait(120);
    addToChain('转 FLAC');
    await wait(120);
    out.chainNames = chain.map(s => s.name);
    out.modeBefore = chainMode;

    // 点「保存预设」→ 对话框
    document.querySelector('#chainSave').click();
    await wait(260);
    const modal = document.querySelector('#presetModal');
    out.modalOpen = !!(modal && !modal.hidden
                       && modal.classList.contains('is-open'));
    out.dlgName = (document.querySelector('#presetName') || {}).value;
    out.dlgDesc = (document.querySelector('#presetDesc') || {}).value;
    out.dlgMeta = (document.querySelector('#presetMeta') || {}).textContent;
    out.dlgTitle = (document.querySelector('#presetModalTitle') || {}).textContent;
    // 图标选择器：默认候选应是**链上用到的**那几个。
    // ⚠ 选择器与状态属性在 §10.6.7 对齐成卡片编辑器那套：
    //   `.iconpick__item` + `aria-pressed` → `.iconpick__btn` + `aria-checked`。
    out.iconPool = [...document.querySelectorAll('#presetIcons .iconpick__btn')]
      .map(b => b.dataset.icon);
    out.iconShapes = [...document.querySelectorAll('#presetIcons .iconpick__btn')]
      .map(b => b.querySelectorAll('svg path').length);
    out.iconText = [...document.querySelectorAll('#presetIcons .iconpick__btn')]
      .map(b => (b.textContent || '').trim());
    out.autoAttr = document.querySelector('#presetIcons').dataset.auto;
    out.autoChecked = [...document.querySelectorAll(
      '#presetIcons .iconpick__btn[aria-checked="true"]')].map(b => b.dataset.icon);
    out.autoCheckedStyle = (() => {
      const on = document.querySelector('#presetIcons .iconpick__btn[aria-checked="true"]');
      if (!on) return null;
      const cs = getComputedStyle(on);
      return [cs.borderStyle, cs.backgroundColor];
    })();
    out.iconHint = (document.getElementById('presetIconsState') || {}).textContent || null;
    out.autoIcons = presetIcons(chain).filter(x => x !== '…');
    // 点一下「全部图标」应展开候选
    document.querySelector('#presetIconsAll').click();
    await wait(80);
    out.iconPoolAll = [...document.querySelectorAll('#presetIcons .iconpick__btn')]
      .map(b => b.dataset.icon);
    document.querySelector('#presetIconsAll').click();
    await wait(80);

    // ---------- 2) 保存 ----------
    document.querySelector('#presetSave').click();
    await wait(700);
    out.modalClosed = document.querySelector('#presetModal').hidden
                   || !document.querySelector('#presetModal').classList.contains('is-open');
    out.presetsInMemory = PRESETS.map(p => ({ name: p.name, mode: p.mode,
                                              steps: p.steps.length }));

    // ---------- 4) 再存一条：默认名必须变成 预设_02 ----------
    chain.length = 0; renderChain();
    addToChain('标准化 -16 LUFS');
    await wait(120);
    document.querySelector('#chainSave').click();
    await wait(260);
    out.secondName = (document.querySelector('#presetName') || {}).value;
    out.serverPresets = await (await fetch('/api/presets')).json();
    document.querySelector('#presetSave').click();
    await wait(700);
    out.allNames = PRESETS.map(p => p.name).sort();

    // ---------- 抽屉切换：预设卡片渲染出来了吗 ----------
    setDrawerTab('preset');
    await wait(300);
    out.presetTabOn = drawerTab;
    out.tabAria = document.querySelector('#drawerTab').getAttribute('aria-pressed');
    out.tabLabel = (document.querySelector('#drawerTabLabel') || {}).textContent;
    out.cardSectionsHidden = document.querySelector('#cardSections').hidden;
    out.presetSectionsHidden = document.querySelector('#presetSections').hidden;
    out.toggleAllHidden = document.querySelector('#cardsecToggleAll').hidden;
    out.pcardCount = document.querySelectorAll('#presetSections .pcard').length;
    // 预设卡片那排格子：验的是**画出来的图形**，不是短名文字
    out.pcardIcons = [...document.querySelectorAll('#presetSections .pcard__ico')]
      .map(e => ({
        text: (e.textContent || '').trim(),
        paths: [...e.querySelectorAll('svg path')].map(p => p.getAttribute('d')),
        w: Math.round(e.getBoundingClientRect().width),
      }));
    // 功能卡片上同一个键的图形：两张卡必须**是同一张图**
    out.fcardIconPaths = [...document.querySelectorAll('#cardSections .fcard__ico svg path')]
      .map(p => p.getAttribute('d'));
    out.searchPlaceholder = (document.querySelector('#cardSearch') || {}).placeholder;

    // ---------- 点预设卡片：加载 + 切档位 ----------
    chain.length = 0; setChainMode('parallel'); renderChain();
    await wait(120);
    const first = document.querySelector('#presetSections .pcard');
    out.firstPresetId = first ? first.dataset.preset : null;
    if (first) first.click();
    await wait(400);
    out.loadedChain = chain.map(s => s.name);
    out.modeAfterLoad = chainMode;      // 必须被切成预设存的那个档位

    // ---------- 未知卡片：notice + is-unknown ----------
    // 夹具预设 `预设_98` 里有两步的 `custom=true` 且 `cardId` 不在卡片库里
    // （由 Python 侧预置进 cards.json）—— 正是 §9.1.2 的场景。
    renderPresets();
    await wait(150);
    const unknownEl = [...document.querySelectorAll('#presetSections .pcard')]
      .find(el => el.classList.contains('is-unknown'));
    out.unknownFound = !!unknownEl;
    out.unknownBadge = unknownEl
      ? (unknownEl.querySelector('.pcard__warn') || {}).textContent : null;
    out.unknownIconsOk = !!unknownEl;   // 未知预设也应正常渲染图标
    if (unknownEl) {
      chain.length = 0; renderChain();
      unknownEl.click();
      await wait(500);
      const notice = document.querySelector('#notice');
      out.noticeOpen = !!(notice && !notice.hidden);
      out.noticeTitle = (document.querySelector('#noticeTitle') || {}).textContent;
      out.noticeMsg = (document.querySelector('#noticeMsg') || {}).textContent;
      out.noticeAct = document.querySelector('#noticeAct').hidden
        ? null : document.querySelector('#noticeAct').textContent;
      out.loadedWithUnknown = chain.map(s => s.name);
      out.chainUnknownMark = [...document.querySelectorAll('#chain .chain__item')]
        .map(e => e.textContent);
      // 「查看详情」→ 打开执行链编辑器
      if (out.noticeAct) {
        document.querySelector('#noticeAct').click();
        await wait(400);
        out.editorOpen = !document.querySelector('#chainEditModal').hidden;
        out.editorRows = document.querySelectorAll('#chainEditList .cedit').length;
        out.editorUnknownRows =
          document.querySelectorAll('#chainEditList .cedit--unknown').length;
        out.editorHasRegister =
          !!document.querySelector('#chainEditList [data-ce-reg]');
        // 删掉未知那一步 → 行数减一
        const delBtn = document.querySelector('#chainEditList [data-ce-del]');
        if (delBtn) { delBtn.click(); await wait(150); }
        out.editorRowsAfterDel =
          document.querySelectorAll('#chainEditList .cedit').length;
      }
      hideNotice();
    }

    // ---------- P3 · A：右键 → 导出我没有的功能卡片 ----------
    // 下载在无头浏览器里落不了盘，所以**拦下 createObjectURL 把内容读出来** ——
    // 验的是"导出的 JSON 长什么样"，而不是"点了一下"。
    {
      let captured = null;
      const realCreate = URL.createObjectURL;
      URL.createObjectURL = (blob) => { captured = blob; return 'blob:probe'; };
      const realClick = HTMLAnchorElement.prototype.click;
      HTMLAnchorElement.prototype.click = function () { /* 别真下载 */ };
      try {
        // 右键夹具预设 → 菜单第一项
        const el = [...document.querySelectorAll('#presetSections .pcard')]
          .find(e => e.dataset.preset === 'p_unknown_fixture');
        out.ctxFound = !!el;
        if (el) {
          el.dispatchEvent(new MouseEvent('contextmenu',
            { bubbles: true, cancelable: true, clientX: 200, clientY: 200 }));
          await wait(220);
          const menu = document.querySelector('#ctxmenu');
          out.ctxOpen = !!(menu && !menu.hidden);
          const labels = [...menu.querySelectorAll('[data-pact]')]
            .map(b => b.textContent.trim());
          out.ctxLabels = labels;
          const exportBtn = [...menu.querySelectorAll('[data-pact]')]
            .find(b => b.textContent.includes('导出我没有的功能卡片'));
          out.ctxHasExport = !!exportBtn;
          if (exportBtn) {
            exportBtn.click();
            await wait(320);
            out.exportModalTitle =
              (document.querySelector('#presetModalTitle') || {}).textContent;
            out.exportName = (document.querySelector('#presetName') || {}).value;
            out.exportListShown = !document.querySelector('#presetList').hidden;
            out.exportListText =
              (document.querySelector('#presetList') || {}).textContent;
            document.querySelector('#presetSave').click();
            await wait(360);
          }
        }
      } finally {
        URL.createObjectURL = realCreate;
        HTMLAnchorElement.prototype.click = realClick;
      }
      if (captured) {
        out.exportedJson = await captured.text();
      }
    }

    // ---------- P3 · B：把未知那一步「注册为卡片」 ----------
    {
      await loadPresets();
      openChainEditor('p_unknown_fixture');
      await wait(320);
      const regBtn = document.querySelector('#chainEditList [data-ce-reg]');
      out.regBtnExists = !!regBtn;
      if (regBtn) {
        regBtn.click();
        await wait(900);
        out.regAfter = document.querySelectorAll(
          '#chainEditList .cedit--unknown').length;
        out.cardsAfterReg = CARDS.filter(c => c.custom).length;
      }
      document.querySelector('#chainEditModal [data-close]').click();
      await wait(250);
    }

    // ---------- 图标规则（三条，逐条量）----------
    // ⚠ `presetIcons` 返回的是 **`CARD_ICONS` 短名**（`tag`/`flac`…），不是 op 名。
    // 所以断言里写 `['tag','flac']` 而不是 `['tags','convert']` ——
    // 这正是第一版写错的地方（显示成 CONVERT/TAGS）。
    out.iconRule = {
      // 相邻重复合并：改标签 改标签 转FLAC → 2 个
      dedup: presetIcons([{ op: 'tags', ico: 'tag' },
                          { op: 'tags', ico: 'tag' },
                          { op: 'convert', ico: 'flac' }]),
      // ≤5 全显示
      five: presetIcons([{ op: 'convert', ico: 'flac' },
                         { op: 'normalize', ico: 'gain' }]),
      // >5：留头 3 尾 2，中间一个省略号；6 步 = 3 + … + 2 = 6 格
      six: presetIcons([{ op: 'convert', ico: 'flac' },
                        { op: 'normalize', ico: 'gain' },
                        { op: 'tags', ico: 'tag' },
                        { op: 'probe', ico: 'check' },
                        { op: 'peaks', ico: 'wave' },
                        { op: 'zip', ico: 'zip' }]),
    };
    // 省略号那格必须与其他格**同宽**：造一条 6 步链（**图标各不相同**，
    // 否则相邻合并会把它缩成 1 格），让预设网格用它的自动图标
    const SIX = [['convert', 'flac'], ['normalize', 'gain'], ['tags', 'tag'],
                 ['probe', 'check'], ['peaks', 'wave'], ['zip', 'zip']];
    chain.length = 0;
    chain.push(...SIX.map(([op, ico], i) => ({
      name: 'x' + (i + 1), op: op, params: {}, ico: ico,
      cardId: '', custom: false })));
    // 临时塞一条"用自动图标"的预设，渲染出来量格子宽度
    PRESETS.push({ id: 'p_measure', name: '预设_测', desc: '量格子宽度', mode: 'serial',
                   icons: null, createdAt: 0,
                   steps: chain.map(s => ({ name: s.name, op: s.op, params: {},
                                            ico: s.ico })) });
    renderPresets();
    await wait(250);
    const cells = [...document.querySelectorAll(
      '#presetSections .pcard[data-preset="p_measure"] .pcard__ico')];
    out.cellCount = cells.length;
    out.cellWidths = cells.map(c => Math.round(c.getBoundingClientRect().width));
    out.moreText = cells.map(c => c.textContent);

    // 复位
    chain.length = 0; setChainMode('parallel'); renderChain();
    setDrawerTab('card');
  } catch (e) {
    out.errors.push(String((e && e.stack) || e));
  }
  return out;
})()
"""


async def main() -> None:
    print("== 预设全流程（真页面）==")
    # 干净起点：清掉已有预设，并**预置一条含未知卡片的预设**当夹具。
    # 为什么由 Python 侧塞而不在页面里造：未知卡片的判据是
    # "`custom=true` 且 `cardId` 不在 CARDS 里" —— 页面里造不出来
    # （造得出来就说明它其实在库里）。手工改 cards.json 正是 §9.1.2 描述的场景
    # （导入别人的预设 / 自己删过卡）。
    saved = CARDS_JSON.read_text(encoding="utf-8") if CARDS_JSON.exists() else None
    fixture = {
        "version": 1, "cards": [], "snapshots": [],
        "presets": [{
            "id": "p_unknown_fixture", "name": "预设_98",
            "desc": "含两张已不在库里的卡片（快照）", "mode": "serial",
            "icons": None, "createdAt": 1730000000.0,
            "steps": [
                {"name": "我的转码卡", "op": "convert", "ico": "mp3",
                 "cardId": "c_已删除的卡_abc123", "custom": True,
                 "params": {"format": "flac"}},
                {"name": "转 FLAC", "op": "convert", "ico": "flac",
                 "params": {"format": "flac"}},
                {"name": "我的归档卡", "op": "zip", "ico": "zip",
                 "cardId": "c_已删除的卡_def456", "custom": True, "params": {}},
            ],
        }],
    }
    CARDS_JSON.write_text(json.dumps(fixture, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print("        已预置夹具：预设_98（含 2 张未知卡片）")

    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         BASE],
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
            check("拿到 CDP 页面", False)
            return

        async with websockets.connect(ws_url, max_size=None) as ws:
            mid = [0]

            async def send(method: str, params: dict | None = None):
                mid[0] += 1
                await ws.send(json.dumps({"id": mid[0], "method": method,
                                          "params": params or {}}))
                want = mid[0]
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=40))
                    if m.get("id") == want:
                        return m

            await send("Runtime.enable")
            # 等首屏
            n = 0
            for _ in range(60):
                m = await send("Runtime.evaluate", {
                    "expression": "document.querySelectorAll("
                                  "'#cardSections .fcard[data-card]').length",
                    "returnByValue": True})
                n = ((m.get("result") or {}).get("result") or {}).get("value") or 0
                if n >= 60:
                    break
                await asyncio.sleep(0.5)
            print(f"        渲染出 {n} 张功能卡片")

            m = await send("Runtime.evaluate", {
                "expression": PROBE, "returnByValue": True, "awaitPromise": True})
            res = ((m.get("result") or {}).get("result") or {})
            if "value" not in res:
                check("探针求值成功", False, json.dumps(m)[:500])
                return
            out = res["value"]

            check("探针没有抛异常", not out["errors"], out["errors"][:1])

            # ---- 链与对话框 ----
            # ⚠ 夹具叫 `预设_98`，所以"下一条默认名"是 **99** —— 这恰好又验了一次
            # `max+1`（而不是"按条数 +1"，那会得到 02）。断言跟着夹具走。
            check("链上是「批量改标签 → 转 FLAC」",
                  out["chainNames"] == ["批量改标签", "转 FLAC"], out["chainNames"])
            check("对话框打开了", out["modalOpen"])
            check("名称默认 = 现有最大编号+1（夹具 预设_98 → `预设_99`）",
                  out["dlgName"] == "预设_99", out["dlgName"])
            check("描述**默认非空**（不能是一排「无描述」）",
                  bool((out["dlgDesc"] or "").strip()), out["dlgDesc"])
            check("描述由链自动生成（含两步名字）",
                  "批量改标签" in (out["dlgDesc"] or "")
                  and "转 FLAC" in (out["dlgDesc"] or ""), out["dlgDesc"])
            check("模式区显示 `串行`（只读展示当前档位）",
                  "串行" in (out["dlgMeta"] or ""), out["dlgMeta"])
            check("模式区显示步数与作用域",
                  "2 步" in (out["dlgMeta"] or "") and "文件" in (out["dlgMeta"] or ""),
                  out["dlgMeta"])
            check("图标候选默认是**链上用到的**那几个",
                  set(out["iconPool"] or []) == {"tag", "flac"}, out["iconPool"])
            # ---- 模态里的图标选择器：与卡片编辑器**同一套**（§10.6.7） ----
            check("候选格子里是**画出来的 SVG**（每格至少一条 path）",
                  bool(out["iconShapes"]) and all(n > 0 for n in out["iconShapes"]),
                  out["iconShapes"])
            check("候选格子里**没有短名文字**（FLAC/TAG… 那套缩写）",
                  all(t == "" for t in (out["iconText"] or [])), out["iconText"])
            # ⚠ 自动态**不是"一个都不勾"**（§10.6.8）：草稿是 null，
            # 但格子里要把**推导出来的**那几个按选中态显示，否则用户看到
            # "一排没选中的方块"，以为图标丢了/坏了（用户就是这么报的）。
            check("自动态 `data-auto=1`，且推导出来的图标**显示为已选中**",
                  out["autoAttr"] == "1"
                  and set(out["autoChecked"] or []) == set(out["autoIcons"] or []),
                  (out["autoAttr"], out.get("autoChecked"), out.get("autoIcons")))
            check("自动态的选中样式是**虚线**（不是手选那种实心底）",
                  out["autoCheckedStyle"] == ["dashed", "rgba(0, 0, 0, 0)"],
                  out["autoCheckedStyle"])
            check("有一行文字说明当前是自动还是手选",
                  "自动" in (out["iconHint"] or ""), out["iconHint"])
            check("「全部图标」能展开候选",
                  len(out["iconPoolAll"] or []) > len(out["iconPool"] or []),
                  (out["iconPool"], out["iconPoolAll"]))
            check("保存后对话框关闭", out["modalClosed"])

            # ---- 落盘与内容 ----
            st, got = http("GET", "/api/presets")
            items = got.get("presets") or []
            # 夹具 预设_98 + 页面存的两条 = 3
            check("GET /api/presets 读得到 3 条（含夹具）", len(items) == 3,
                  [p["name"] for p in items])
            p1 = next((p for p in items if p["name"] == "预设_99"), None)
            check("有一条叫 `预设_99`（页面存的那条）", p1 is not None,
                  [p["name"] for p in items])
            if p1:
                check("`mode` 存成了 serial（漏了等于还原出一个不一样的链）",
                      p1["mode"] == "serial", p1["mode"])
                check("steps 存的是 `{op, params}`，**不是卡片名**",
                      [set(s) >= {"op", "params"} for s in p1["steps"]]
                      and [s["op"] for s in p1["steps"]] == ["tags", "convert"],
                      p1["steps"])
                check("steps 里带着 `ico` 快照（卡片删了链上还要显示图标）",
                      all(s.get("ico") for s in p1["steps"]),
                      [s.get("ico") for s in p1["steps"]])
                check("`icons` 是 null = 自动推导（不是「没有图标」）",
                      p1["icons"] is None, p1["icons"])
            check("再存一条默认名再 +1（`预设_100`）",
                  out["secondName"] == "预设_100",
                  f"{out['secondName']} ｜ 存之前服务端看到的是 "
                  f"{[p.get('name') for p in (out.get('serverPresets') or {}).get('presets', [])]}"
                  f" / nextName={((out.get('serverPresets') or {}).get('nextName'))}")
            check("新存的两条都在（预设_99 / 预设_100）",
                  sorted(n for n in out["allNames"] if n in ("预设_99", "预设_100"))
                  == ["预设_100", "预设_99"], out["allNames"])

            # ---- 抽屉切换 ----
            check("切到预设视图", out["presetTabOn"] == "preset")
            check("`aria-pressed` 跟着切", out["tabAria"] == "true", out["tabAria"])
            check("**标签文字**也换成「预设链路」（只改 title 读屏还是旧标签）",
                  out["tabLabel"] == "预设链路", out["tabLabel"])
            check("功能卡片容器被隐藏", out["cardSectionsHidden"] is True)
            check("预设容器显示出来", out["presetSectionsHidden"] is False)
            check("「全部收起」在预设视图里隐藏（预设不分段）",
                  out["toggleAllHidden"] is True)
            check("搜索框改搜预设（placeholder 变了）",
                  "预设" in (out["searchPlaceholder"] or ""),
                  out["searchPlaceholder"])
            check("渲染出 3 张预设卡片（夹具 + 新存的两条）",
                  out["pcardCount"] == 3, out["pcardCount"])
            # ---- 图标：画图形，不写短名文字（§10.6.3） ----
            _pcico = out["pcardIcons"] or []
            _pcds = [d for it in _pcico for d in (it.get("paths") or [])]
            check("预设卡片的图标是**画出来的 SVG**（每格至少一条 path）",
                  bool(_pcico) and all(it.get("paths") for it in _pcico),
                  [it.get("text") for it in _pcico][:8])
            check("图标格子里**没有短名文字**（只允许空，或省略号那一格）",
                  all((it.get("text") or "") == "" or (it.get("text") or "") == "…"
                      for it in _pcico),
                  [it.get("text") for it in _pcico][:8])
            check("图标**不是** op 名（第一版错在这里：显示成 CONVERT/TAGS 而不是 FLAC/TAG）",
                  not any(x and x.upper() in ("CONVERT", "TAGS", "NORMALIZE", "PROBE",
                                              "PEAKS", "COVER", "WAVEFORM")
                          for x in (it.get("text") for it in _pcico)),
                  [it.get("text") for it in _pcico][:8])
            check("预设卡片的图形与功能卡片是**同一张图**（同一个 `ico` 键 → 同一条 path）",
                  bool(_pcds) and set(_pcds) <= set(out["fcardIconPaths"] or []),
                  sorted(set(_pcds) - set(out["fcardIconPaths"] or []))[:3])
            check("图标格仍是 34px 方块（跨视图列宽对得齐）",
                  all(it.get("w") == 34 for it in _pcico) if _pcico else False,
                  [it.get("w") for it in _pcico][:8])

            # ---- 点预设 = 加载步骤 + 切档位 ----
            # 点的是网格里**第一张**卡，而夹具排在前面（id 排序不定，按 DOM 顺序），
            # 所以这里接受"夹具那条"或"批量改标签那条"，只要求档位跟着切。
            check("点预设把链加载进来了（步骤数 ≥ 2）",
                  len(out["loadedChain"] or []) >= 2, out["loadedChain"])
            check("**档位也切到预设存的那个**（只还原步骤 = 还原了一半）",
                  out["modeAfterLoad"] == "serial", out["modeAfterLoad"])

            # ---- 未知卡片 ----
            check("含有未知卡片的预设有 `is-unknown` 高亮",
                  out["unknownFound"] is True)
            check("角标写明「N 未知」",
                  "未知" in (out["unknownBadge"] or ""), out["unknownBadge"])
            check("点它弹 notice（不是悄悄加载半条链）",
                  out["noticeOpen"] is True)
            check("notice 标题说清有几个卡片不在库",
                  "不在当前卡片库" in (out["noticeTitle"] or ""),
                  out["noticeTitle"])
            check("notice 正文列出是哪几张（按快照执行）",
                  "（快照）" not in (out["noticeMsg"] or "")
                  and bool((out["noticeMsg"] or "").strip()), out["noticeMsg"])
            check("notice 带「查看详情」入口（不是只报错）",
                  out["noticeAct"] == "查看详情", out["noticeAct"])
            check("未知卡片**照样能加载**（不是禁用）",
                  len(out["loadedWithUnknown"] or []) >= 2, out["loadedWithUnknown"])
            check("链上那一步带「（快照）」后缀（§9.1.1 二）",
                  any("（快照）" in t for t in (out["chainUnknownMark"] or [])),
                  out["chainUnknownMark"])
            check("「查看详情」打开了执行链编辑器", out["editorOpen"] is True)
            check("编辑器列出了每一步", (out["editorRows"] or 0) >= 2, out["editorRows"])
            check("未知那一步在编辑器里被标出来",
                  (out["editorUnknownRows"] or 0) >= 1, out["editorUnknownRows"])
            check("未知步骤有「注册为卡片」按钮",
                  out["editorHasRegister"] is True)
            check("能删掉步骤（行数减一）",
                  out["editorRowsAfterDel"] == (out["editorRows"] or 0) - 1,
                  (out["editorRows"], out["editorRowsAfterDel"]))

            # ---- P3 · A：导出未知卡片 ----
            check("右键预设能弹出菜单", out.get("ctxOpen") is True)
            check("菜单里有「导出我没有的功能卡片」",
                  out.get("ctxHasExport") is True, out.get("ctxLabels"))
            check("菜单里还有「编辑执行链」「重命名」「删除」",
                  any("编辑执行链" in x for x in (out.get("ctxLabels") or []))
                  and any("重命名" in x for x in (out.get("ctxLabels") or []))
                  and any("删除" in x for x in (out.get("ctxLabels") or [])),
                  out.get("ctxLabels"))
            check("导出对话框标题对了",
                  out.get("exportModalTitle") == "导出我没有的功能卡片",
                  out.get("exportModalTitle"))
            check("默认名是 `来自预设_98的卡片`",
                  out.get("exportName") == "来自预设_98的卡片", out.get("exportName"))
            check("**列出了将要导出的卡片名**（让用户知道要给别人什么）",
                  out.get("exportListShown") is True
                  and "我的转码卡" in (out.get("exportListText") or "")
                  and "我的归档卡" in (out.get("exportListText") or ""),
                  out.get("exportListText"))
            ej = {}
            try:
                ej = json.loads(out.get("exportedJson") or "{}")
            except Exception:
                pass
            cards = ej.get("cards") or []
            check("导出的 JSON 与 `/api/cards/export` 同形（version + cards）",
                  ej.get("version") == 1 and isinstance(cards, list), list(ej))
            check("导出了 2 张卡片（只有未知的那些）", len(cards) == 2,
                  [c.get("name") for c in cards])
            # 7 个字段链上都由快照提供，重建**不需要卡片库参与**；
            # `id` 是可选的（非法就丢掉，由后端按 `op|name` 生成）
            want = {"cat", "name", "desc", "tier", "ico", "op", "params"}
            check("每张卡重建出了 7 个必填字段（cat/name/desc/tier/ico/op/params）",
                  all(want <= set(c) for c in cards),
                  [sorted(c) for c in cards])
            # `cardId` 带中文（`c_已删除的卡_abc123`）—— 后端 `validate_card`
            # 只允许字母数字下划线连字符（≤64），所以这个 id **必须被丢掉**
            # （丢掉之后后端按 `op|name` 自己生成一个）。
            # 这条用例的价值就在这儿：真实来源的 cardId 是任意字符串。
            check("非法 `cardId`（带中文）被丢掉，不原样带出去",
                  all(not c.get("id") or c["id"].isascii() for c in cards),
                  [c.get("id") for c in cards])
            check("`op` 来自快照（convert / zip）",
                  {c.get("op") for c in cards} == {"convert", "zip"},
                  [c.get("op") for c in cards])
            check("`params` 也是快照里的那一份（format=flac）",
                  next(c for c in cards if c["op"] == "convert")["params"]
                  .get("format") == "flac",
                  [c.get("params") for c in cards])

            # ---- P3 · B：注册为卡片 ----
            check("编辑器里有「注册为卡片」按钮", out.get("regBtnExists") is True)
            check("注册之后那一行不再标成未知",
                  (out.get("regAfter") or 0) < 2, out.get("regAfter"))
            check("卡片库真的多了一张自定义卡（走的是已有的导入端点）",
                  (out.get("cardsAfterReg") or 0) >= 1, out.get("cardsAfterReg"))

            # ---- 图标规则 ----            # 期望值是 **`CARD_ICONS` 短名**，不是 op 名（见 §3.8.2 五）
            ir = out["iconRule"]
            check("相邻重复合并：`改标签 改标签 转FLAC` → 2 个图标（tag/flac）",
                  ir["dedup"] == ["tag", "flac"], ir["dedup"])
            check("≤5 全显示", ir["five"] == ["flac", "gain"], ir["five"])
            check(">5 留头 3 尾 2 + 省略号（6 步 = 6 格）",
                  ir["six"] == ["flac", "gain", "tag", "…", "wave", "zip"], ir["six"])
            w = out["cellWidths"] or []
            check("6 步链渲染出 6 格（3 图标 + 省略号 + 2 图标）",
                  out.get("cellCount") == 6 and len(w) == 6, out.get("cellCount"))
            check("省略号那格与图标**同宽**（否则 6 格排布会歪）",
                  len(set(w)) == 1, w)
            check("省略号那格的内容就是 `…`",
                  "…" in (out.get("moreText") or []), out.get("moreText"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        # ⚠ 不能原样写回 `saved`：这一轮**注册为卡片**会真的往 cards.json 里
        # 加一张自定义卡，原样写回等于把测试垃圾留给用户。
        # 写一份干净的（卡片/快照/预设都空）—— 这也是本探针起点的状态。
        CARDS_JSON.write_text(
            json.dumps({"version": 1, "cards": [], "snapshots": [], "presets": []},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        _ = saved

    print()
    print(f"结果：{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
