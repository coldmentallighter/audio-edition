"""抽屉标题切换 + 图标编辑探针（方案 §7.2 / §3.8.2 三·四）。

分两段，因为第二段需要跨**刷新**，而刷新会重建页面上下文：

  第一段（同一页）：
    ① 点 `#drawerTab` → `#cardSections` 隐藏、`#presetSections` 显示，
       `aria-pressed` 与**标签文字同时**变（只改 title 读屏还是旧标签）
    ② 切到预设视图后 `#cardsecToggleAll` 隐藏、搜索框仍在但改搜预设
    ③ 点预设卡片 → 执行链变成该预设的 N 步，且**档位切到 `preset.mode`**
    ④ 链已有 2 步时点预设 → **追加**而不是覆盖

  刷新后（`Page.reload`）：
    ⑤ 仍停在预设视图（状态在 localStorage 里）

  图标编辑：
    ⑥ 默认候选 = 链上用到的；点一个 → `presetIconsDraft` 变数组、`aria-pressed` 变 true
    ⑦ 「恢复自动」→ 写回 `null`

  两个「与其他模态一致」的回归钉子（用户实测报的）：
    ⑧ 新模态也要 `bindFollow` 过（`data-follow-bound`）—— 漏登记的表现是
       模态能开能用、只是没有跟随倾斜，不报任何错
    ⑨ **「取消」与 `Esc` 都能关掉**（`data-close` 不是全局委托，
       漏一条监听就"关不掉"，用户报的正是这个）

用法：服务在 8765。
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
PORT = 9353
PROFILE = ROOT / "tests" / "_tab_profile"
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


# ---------------------------------------------------------------- 第一段：同页
PROBE_A = r"""
(async () => {
  const out = {};
  const wait = (ms) => new Promise(r => setTimeout(r, ms));

  // 起点：清链、停在功能卡片视图
  chain.length = 0; setChainMode('parallel'); renderChain();
  setDrawerTab('card');
  await wait(200);
  out.tabStart = drawerTab;

  // ① 点标题 → 切到预设
  document.querySelector('#drawerTab').click();
  await wait(320);
  out.tabAfterClick = drawerTab;
  out.ariaPressed = document.querySelector('#drawerTab').getAttribute('aria-pressed');
  out.label = (document.querySelector('#drawerTabLabel') || {}).textContent;
  out.cardsHidden = document.querySelector('#cardSections').hidden;
  out.presetsHidden = document.querySelector('#presetSections').hidden;
  // ② 联动控件
  out.toggleAllHidden = document.querySelector('#cardsecToggleAll').hidden;
  out.searchVisible = !!document.querySelector('#searchDock');
  out.placeholder = (document.querySelector('#cardSearch') || {}).placeholder;
  out.scopeVisible = !document.querySelector('#drawerScope').hidden;

  // ③ 点预设卡片：加载 + 切档位
  const target = [...document.querySelectorAll('#presetSections .pcard')]
    .find(el => el.dataset.preset === 'p_tab_fixture');
  out.foundFixture = !!target;
  if (target) target.click();
  await wait(420);
  out.loadedNames = chain.map(s => s.name);
  out.loadedMode = chainMode;

  // ④ 链已有 2 步时再点一次 → **追加**而不是覆盖（不是二次确认）
  const before = chain.length;
  if (target) target.click();
  await wait(420);
  out.lenBefore = before;
  out.lenAfterAppend = chain.length;

  // ⑥ 图标编辑
  chain.length = 0; renderChain();
  addToChain('批量改标签');
  addToChain('转 FLAC');
  await wait(150);
  document.querySelector('#chainSave').click();
  await wait(300);
  out.poolBefore = [...document.querySelectorAll('#presetIcons .iconpick__btn')]
    .map(b => b.dataset.icon);
  out.poolShapes = [...document.querySelectorAll('#presetIcons .iconpick__btn')]
    .map(b => b.querySelectorAll('svg path').length);
  out.autoAttrBefore = document.querySelector('#presetIcons').dataset.auto;
  out.autoCheckedBefore = [...document.querySelectorAll(
    '#presetIcons .iconpick__btn[aria-checked="true"]')].map(b => b.dataset.icon);
  out.autoStyleBefore = (() => {
    const on = document.querySelector('#presetIcons .iconpick__btn[aria-checked="true"]');
    return on ? getComputedStyle(on).borderStyle : null;
  })();
  out.hintBefore = (document.getElementById('presetIconsState') || {}).textContent || null;
  out.draftBefore = presetIconsDraft;
  out.autoLabel = (document.querySelector('#presetIconsAuto') || {}).textContent;
  // 点第一个图标 → 进入"手选"态
  const first = document.querySelector('#presetIcons .iconpick__btn');
  out.firstIcon = first ? first.dataset.icon : null;
  // ⚠ 名字是"BeforeClick"但取的是**点击之后**的状态（历史命名）——
  // 自动态已经把推导结果显示成选中了，所以点一下是把它**取消**掉。
  if (first) first.click();
  await wait(200);
  out.poolBeforeClick = [...document.querySelectorAll('#presetIcons .iconpick__btn')]
    .map(b => b.dataset.icon + ':' + b.getAttribute('aria-checked'));
  out.draftAfter = Array.isArray(presetIconsDraft) ? presetIconsDraft.slice() : null;
  out.autoAttrAfter = document.querySelector('#presetIcons').dataset.auto;
  out.manualStyle = (() => {
    const on = document.querySelector('#presetIcons .iconpick__btn[aria-checked="true"]');
    return on ? getComputedStyle(on).borderStyle : null;
  })();
  out.hintAfter = (document.getElementById('presetIconsState') || {}).textContent || null;
  out.poolAfterClick = [...document.querySelectorAll('#presetIcons .iconpick__btn')]
    .map(b => b.dataset.icon + ':' + b.getAttribute('aria-checked'));
  const firstNow = [...document.querySelectorAll('#presetIcons .iconpick__btn')]
    .find(b => b.dataset.icon === out.firstIcon);
  out.firstPressed = firstNow ? firstNow.getAttribute('aria-checked') : null;
  // 再点一次 → 取消选中
  if (firstNow) firstNow.click();
  await wait(140);
  out.draftAfterToggle = Array.isArray(presetIconsDraft)
    ? presetIconsDraft.slice() : null;
  // ⑦ 恢复自动 → 写回 null
  document.querySelector('#presetIconsAuto').click();
  await wait(140);
  out.draftAfterAuto = presetIconsDraft;
  document.querySelector('#presetModal [data-close]').click();
  await wait(260);

  // 留在预设视图，供刷新后验持久化
  setDrawerTab('preset');
  await wait(160);
  out.tabAtEnd = drawerTab;
  out.lsValue = (() => { try { return localStorage.getItem('ae.drawerTab'); }
                         catch { return null; } })();

  // ---------- ⑧ 动效登记：新模态必须与其它模态一致 ----------
  out.followBound = {
    meta: document.querySelector('#metaModal').dataset.followBound || null,
    card: document.querySelector('#cardModal').dataset.followBound || null,
    preset: document.querySelector('#presetModal').dataset.followBound || null,
    chainEdit: document.querySelector('#chainEditModal').dataset.followBound || null,
  };

  // ---------- ⑨ 取消路径：两个新模态都要能关 ----------
  // (a) 预设对话框：点「取消」
  {
    chain.length = 0; renderChain();
    addToChain('转 FLAC');
    await wait(120);
    document.querySelector('#chainSave').click();
    await wait(300);
    out.rmOpen = !document.querySelector('#presetModal').hidden;
    const cancel = document.querySelector('#presetModal [data-close]');
    out.rmHasCancel = !!cancel;
    if (cancel) cancel.click();
    await wait(400);
    out.rmClosedByCancel = document.querySelector('#presetModal').hidden;

    // (b) 预设对话框：Esc
    document.querySelector('#chainSave').click();
    await wait(300);
    out.rmOpen2 = !document.querySelector('#presetModal').hidden;
    document.dispatchEvent(new KeyboardEvent('keydown',
      { key: 'Escape', bubbles: true }));
    await wait(400);
    out.rmClosedByEsc = document.querySelector('#presetModal').hidden;
  }

  // (c) 编辑执行链：点「取消」，且**不能**改到预设
  {
    const p = PRESETS.find(x => x.id === 'p_tab_fixture') || PRESETS[0];
    out.cePresetId = p ? p.id : null;
    out.ceStepsBefore = p ? p.steps.length : null;
    if (p) openChainEditor(p.id);
    await wait(320);
    out.ceOpen = !document.querySelector('#chainEditModal').hidden;
    // 删掉一步（**不保存**），然后取消
    const del = document.querySelector('#chainEditList [data-ce-del]');
    out.ceDraftLen = chainEditSteps.length;
    if (del) del.click();
    await wait(150);
    out.ceDraftLenAfterDel = chainEditSteps.length;
    const cancel2 = document.querySelector('#chainEditModal [data-close]');
    out.ceHasCancel = !!cancel2;
    if (cancel2) cancel2.click();
    await wait(420);
    out.ceClosedByCancel = document.querySelector('#chainEditModal').hidden;
    // 取消后草稿要清掉（免得下次打开以为还在编辑同一条）
    out.cePidAfterCancel = chainEditPid;
    out.ceDraftAfterCancel = chainEditSteps.length;
    // 预设本身**一步都不能少**（取消 = 不落盘）
    const back = PRESETS.find(x => x.id === out.cePresetId);
    out.ceStepsAfter = back ? back.steps.length : null;
  }

  // (d) 编辑执行链：Esc
  {
    const p = PRESETS[0];
    if (p) openChainEditor(p.id);
    await wait(320);
    out.ceOpen2 = !document.querySelector('#chainEditModal').hidden;
    document.dispatchEvent(new KeyboardEvent('keydown',
      { key: 'Escape', bubbles: true }));
    await wait(420);
    out.ceClosedByEsc = document.querySelector('#chainEditModal').hidden;
  }
  chain.length = 0; renderChain();
  return out;
})()
"""

# ---------------------------------------------------------------- 刷新后
PROBE_B = r"""
(() => {
  const out = {};
  out.tabAfterReload = (typeof drawerTab !== 'undefined') ? drawerTab : null;
  out.cardsHidden = document.querySelector('#cardSections').hidden;
  out.presetsHidden = document.querySelector('#presetSections').hidden;
  out.ariaPressed = document.querySelector('#drawerTab').getAttribute('aria-pressed');
  out.label = (document.querySelector('#drawerTabLabel') || {}).textContent;
  out.pcardCount = document.querySelectorAll('#presetSections .pcard').length;
  return out;
})()
"""


async def main() -> None:
    print("== 抽屉切换 + 图标编辑（真页面）==")
    saved = CARDS_JSON.read_text(encoding="utf-8") if CARDS_JSON.exists() else None
    fixture = {
        "version": 1, "cards": [], "snapshots": [],
        "presets": [{
            "id": "p_tab_fixture", "name": "预设_切", "desc": "用于切换与追加的夹具",
            "mode": "serial", "icons": None, "createdAt": 0.0,
            "steps": [
                {"name": "转 FLAC", "op": "convert", "ico": "flac",
                 "params": {"format": "flac"}},
                {"name": "标准化 -16 LUFS", "op": "normalize", "ico": "gain",
                 "params": {"targetLufs": -16}},
            ],
        }],
    }
    CARDS_JSON.write_text(json.dumps(fixture, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print("        已预置夹具：预设_切（2 步 / serial）")

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
            check("拿到 CDP 页面", False)
            return

        async with websockets.connect(ws_url, max_size=None) as ws:
            mid = [0]

            async def send(method, params=None, timeout=40):
                mid[0] += 1
                await ws.send(json.dumps({"id": mid[0], "method": method,
                                          "params": params or {}}))
                want = mid[0]
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                    if m.get("id") == want:
                        return m

            async def evaluate(expr, await_promise=False):
                m = await send("Runtime.evaluate", {
                    "expression": expr, "returnByValue": True,
                    "awaitPromise": await_promise})
                res = ((m.get("result") or {}).get("result") or {})
                return res.get("value")

            await send("Runtime.enable")
            for _ in range(60):
                n = await evaluate("document.querySelectorAll("
                                   "'#cardSections .fcard[data-card]').length")
                if (n or 0) >= 60:
                    break
                await asyncio.sleep(0.5)

            out = await evaluate(PROBE_A, await_promise=True)
            if not isinstance(out, dict):
                check("第一段探针求值成功", False, out)
                return

            # ①
            check("点标题前停在功能卡片视图", out["tabStart"] == "card", out["tabStart"])
            check("点一下切到预设视图", out["tabAfterClick"] == "preset",
                  out["tabAfterClick"])
            check("`aria-pressed` 变 true", out["ariaPressed"] == "true",
                  out["ariaPressed"])
            check("**标签文字**也换成「预设链路」", out["label"] == "预设链路",
                  out["label"])
            check("功能卡片容器隐藏 / 预设容器显示",
                  out["cardsHidden"] is True and out["presetsHidden"] is False,
                  (out["cardsHidden"], out["presetsHidden"]))
            # ②
            check("「全部收起」在预设视图里隐藏", out["toggleAllHidden"] is True)
            check("搜索框仍在", out["searchVisible"] is True)
            check("搜索框改搜预设", "预设" in (out["placeholder"] or ""),
                  out["placeholder"])
            check("作用域显示不变", out["scopeVisible"] is True)
            # ③
            check("找到夹具预设卡片", out["foundFixture"] is True)
            check("点它把 2 步加载进来",
                  len(out["loadedNames"] or []) == 2, out["loadedNames"])
            check("**档位切到预设存的 serial**", out["loadedMode"] == "serial",
                  out["loadedMode"])
            # ④
            check("链已有步骤时点预设是**追加**（不是覆盖、也不弹二次确认）",
                  out["lenAfterAppend"] == (out["lenBefore"] or 0) * 2,
                  (out["lenBefore"], out["lenAfterAppend"]))
            # ⑥
            check("图标候选默认是链上用到的（tag/flac）",
                  set(out["poolBefore"] or []) == {"tag", "flac"}, out["poolBefore"])
            check("默认是「自动」态（草稿为 null）", out["draftBefore"] is None,
                  out["draftBefore"])
            # ---- 与卡片编辑器同一套图标组件（§10.6.7） ----
            check("候选格子里是**画出来的 SVG**（每格至少一条 path）",
                  bool(out["poolShapes"]) and all(n > 0 for n in out["poolShapes"]),
                  out["poolShapes"])
            check("自动态标在容器上（`data-auto=1`）",
                  out["autoAttrBefore"] == "1", out["autoAttrBefore"])
            check("自动态把**推导出来的**图标显示为已选中（不是一排空方块）",
                  set(out["autoCheckedBefore"] or []) == {"tag", "flac"},
                  out["autoCheckedBefore"])
            check("自动态的选中样式是虚线（区别于手选的实心底）",
                  out["autoStyleBefore"] == "dashed", out["autoStyleBefore"])
            check("有文字说明当前是自动", "自动" in (out["hintBefore"] or ""),
                  out["hintBefore"])
            check("进入手选态后 `data-auto` 归 0",
                  out["autoAttrAfter"] == "0", out["autoAttrAfter"])
            check("手选态的选中样式回到实线（editor.css 那套）",
                  out["manualStyle"] == "solid", out["manualStyle"])
            check("提示文字改口说「手动选择」",
                  "手动" in (out["hintAfter"] or ""), out["hintAfter"])
            check("按钮文字提示当前是自动", "自动" in (out["autoLabel"] or ""),
                  out["autoLabel"])
            check("点一个图标 → 进入手选态（数组）",
                  isinstance(out["draftAfter"], list), out["draftAfter"])
            # ⚠ 第一次手选是**从自动推导的结果起步再 toggle**，不是清空成一个图标。
            # 自动结果是 `['tag','flac']`，且**在格子里已经是勾上的**，
            # 所以点 `tag` 是把它**取消** → `['flac']`。
            # 这样设计是因为"我只想去掉一个图标"比"我只想要一个图标"常见得多。
            check("第一次手选从自动结果起步：点 tag → 变成 ['flac']（toggle 掉它）",
                  out["draftAfter"] == ["flac"] and out["firstIcon"] == "tag",
                  (out["draftAfter"], out["firstIcon"]))
            check("被取消的图标 `aria-checked=false`，另一个仍是 true",
                  out.get("poolAfterClick") == ["tag:false", "flac:true"],
                  out.get("poolAfterClick"))
            # 顺序按"点击顺序"追加（`push`），所以只断言**集合**回来就行 ——
            # 图标是平铺展示的，顺序不影响用户看到的东西。
            check("再点一次又加回来（toggle 是双向的）",
                  sorted(out["draftAfterToggle"] or []) == ["flac", "tag"],
                  out["draftAfterToggle"])
            check("「恢复自动」写回 null（不是空数组）",
                  out["draftAfterAuto"] is None, out["draftAfterAuto"])
            check("localStorage 记下了视图", out["lsValue"] == "preset",
                  out["lsValue"])

            # ---- ⑧ 动效登记（与其他模态一致） ----
            fb = out.get("followBound") or {}
            check("四个模态都登记了 `bindFollow`（新模态最容易漏）",
                  fb.get("meta") == "1" and fb.get("card") == "1"
                  and fb.get("preset") == "1" and fb.get("chainEdit") == "1", fb)

            # ---- ⑨ 取消路径 ----
            check("「保存预设」对话框能打开", out.get("rmOpen") is True)
            check("它有「取消」按钮", out.get("rmHasCancel") is True)
            check("点「取消」能关掉", out.get("rmClosedByCancel") is True)
            check("`Esc` 也能关掉", out.get("rmClosedByEsc") is True)
            check("「编辑执行链」能打开", out.get("ceOpen") is True)
            check("它有「取消」按钮", out.get("ceHasCancel") is True)
            check("删一步后草稿变短（确实在编辑）",
                  (out.get("ceDraftLenAfterDel") or 0)
                  == (out.get("ceDraftLen") or 0) - 1,
                  (out.get("ceDraftLen"), out.get("ceDraftLenAfterDel")))
            check("点「取消」能关掉编辑执行链", out.get("ceClosedByCancel") is True)
            check("**取消不落盘**：预设的步骤数一点没变",
                  out.get("ceStepsAfter") == out.get("ceStepsBefore"),
                  (out.get("ceStepsBefore"), out.get("ceStepsAfter")))
            check("取消后清掉草稿（免得下次打开误以为还在编辑同一条）",
                  out.get("cePidAfterCancel") is None
                  and out.get("ceDraftAfterCancel") == 0,
                  (out.get("cePidAfterCancel"), out.get("ceDraftAfterCancel")))
            check("`Esc` 也能关掉编辑执行链", out.get("ceClosedByEsc") is True)

            # ⑤ 刷新后仍在预设视图
            await send("Page.enable")
            await send("Page.reload", {"ignoreCache": True})
            await asyncio.sleep(0.2)
            ok_reload = False
            for _ in range(80):
                v = await evaluate("typeof drawerTab !== 'undefined'"
                                   " && typeof CARDS !== 'undefined'")
                if v:
                    # 再等预设网格渲染出来
                    cnt = await evaluate(
                        "document.querySelectorAll('#presetSections .pcard').length")
                    if cnt:
                        ok_reload = True
                        break
                await asyncio.sleep(0.4)
            check("刷新后页面重新就绪", ok_reload)
            out_b = await evaluate(PROBE_B)
            if isinstance(out_b, dict):
                check("**刷新后仍停在预设视图**（状态在 localStorage）",
                      out_b["tabAfterReload"] == "preset", out_b["tabAfterReload"])
                check("刷新后预设容器仍显示",
                      out_b["presetsHidden"] is False, out_b["presetsHidden"])
                check("刷新后标签与 aria 仍对",
                      out_b["label"] == "预设链路"
                      and out_b["ariaPressed"] == "true",
                      (out_b["label"], out_b["ariaPressed"]))
                check("刷新后预设卡片渲染出来了",
                      (out_b["pcardCount"] or 0) >= 1, out_b["pcardCount"])
            else:
                check("第二段探针求值成功", False, out_b)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        # 写一份干净的（本探针没动卡片库，但夹具预设必须清掉）
        CARDS_JSON.write_text(
            json.dumps({"version": 1, "cards": [], "snapshots": [], "presets": []},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        _ = saved

    print()
    print(f"结果：{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
