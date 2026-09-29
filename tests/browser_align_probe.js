/* 工作区横向对齐：文件卡片、抽屉、搜索栏应该共用同一条左右边界 */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const rect = (sel) => { const e = document.querySelector(sel); if (!e) return null;
    const r = e.getBoundingClientRect();
    return { l: Math.round(r.left), r: Math.round(r.right), w: Math.round(r.width),
             t: Math.round(r.top), b: Math.round(r.bottom), h: Math.round(r.height) }; };
  try {
    for (let i = 0; i < 60 && !document.querySelector('#fileList .card'); i++) await wait(250);
    await wait(1400);
    const freeze = document.createElement('style');
    freeze.textContent = '*{transition:none!important;animation:none!important}';
    document.head.appendChild(freeze);
    applyStop('mid');
    await wait(120);

    out.viewport = { w: innerWidth, h: innerHeight };
    out.sidebar = rect('.sidebar');
    out.drawer = rect('#drawer');
    out.searchDock = rect('.drawer__search');
    out.card = rect('#fileList .card');
    out.list = rect('#fileList');
    out.header = rect('.header');
    out.main = rect('.main') || rect('.workspace');
    out.leftGap = out.drawer.l - out.sidebar.r;
    out.rightGap = out.viewport.w - out.drawer.r;
    // 比的是**内容框**（.filelist），不是卡片本身：卡片在列表里还让出
    // 4px 的滚动条/内边距，拿卡片比会得出左右不对称的假结论。
    // 设计上抽屉比内容各宽 6px（抽屉悬浮留白 EDGE=14，.workspace 内边距 20px），
    // 所以这里检查"左右对称 + 差值在预期内"，而不是断言完全相等。
    out.cardLeftDelta = out.list.l - out.drawer.l;
    out.cardRightDelta = out.drawer.r - out.list.r;
    out.cardInsetFromList = out.card.l - out.list.l;
    out.aligned = Math.abs(out.cardLeftDelta) <= 8 && Math.abs(out.cardRightDelta) <= 8;
    out.symmetric = Math.abs(out.cardLeftDelta - out.cardRightDelta) <= 1;
    out.files = document.querySelectorAll('#fileList .card').length;
    out.err = null;
  } catch (e) { out.err = String((e && e.stack) || e); }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
