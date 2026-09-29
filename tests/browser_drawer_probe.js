/* 抽屉几何 + 交互自检（headless 专用，不参与生产） */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const D = () => document.querySelector('#drawer');
  const H = () => document.querySelector('#drawerHandle');
  const rect = (sel) => { const e = document.querySelector(sel); if (!e) return null;
    const r = e.getBoundingClientRect();
    return { top: Math.round(r.top), bottom: Math.round(r.bottom),
             left: Math.round(r.left), right: Math.round(r.right), h: Math.round(r.height) }; };
  const sidebar = () => Math.round(document.querySelector('.sidebar').getBoundingClientRect().right);

  /* 关键：把手由 pointer* 驱动，合成 .click() 不触发任何逻辑，
     之前用 .click() 测出来的「点击没反应」是探针自己的问题。 */
  const pe = (type, y) => H().dispatchEvent(new PointerEvent(type, {
    bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse', isPrimary: true,
    buttons: type === 'pointerup' ? 0 : 1, clientX: 800, clientY: y,
  }));
  const tap = async () => {
    const y = H().getBoundingClientRect().top + 20;
    pe('pointerdown', y); await wait(30); pe('pointerup', y); await wait(160);
  };
  const drag = async (dy, steps = 8) => {
    const y0 = H().getBoundingClientRect().top + 20;
    pe('pointerdown', y0);
    for (let i = 1; i <= steps; i++) { pe('pointermove', y0 + dy * i / steps); await wait(20); }
    pe('pointerup', y0 + dy);
    await wait(160);
  };

  try {
    for (let i = 0; i < 40 && !document.querySelectorAll('#fileList .card').length; i++) await wait(250);
    await wait(1800);                      // 等侧栏宽度稳定，看 ResizeObserver 有没有重新对齐

    out.viewport = { w: innerWidth, h: innerHeight };
    out.sidebarRight = sidebar();
    out.drawer = rect('#drawer');
    out.leftGap = out.drawer.left - out.sidebarRight;   // 期望 14，负数=压住侧栏

    const freeze = document.createElement('style');
    freeze.textContent = '*{transition:none!important;animation:none!important}';
    document.head.appendChild(freeze);

    /* ---- 三档几何 ---- */
    out.stops = {};
    for (const n of ['closed', 'mid', 'full']) {
      applyStop(n); await wait(80);
      out.stops[n] = {
        stop: D().dataset.stop,
        drawer: rect('#drawer'),
        panel: rect('.drawer__panel'),
        search: rect('.drawer__search'),
        searchInViewport: (() => { const r = rect('.drawer__search');
          return r.top >= 0 && r.bottom <= innerHeight + 1; })(),
        radius: getComputedStyle(D()).borderRadius,
        panelOverflowBelow: rect('.drawer__panel').bottom - innerHeight,
      };
    }

    /* ---- 交互 ---- */
    applyStop('closed'); await wait(80);
    out.t1_closed = D().dataset.stop;
    await tap(); out.t1_afterTap = D().dataset.stop;          // 期望 mid

    await tap(); out.t2_afterTap = D().dataset.stop;          // 期望 closed

    await tap(); out.t3_afterTap = D().dataset.stop;          // 期望 mid（不会跳 full）

    /* 展开状态下再点 -> 折叠（用户明确要求过） */
    applyStop('mid'); await wait(80);
    await tap(); out.t4_midTap = D().dataset.stop;            // 期望 closed

    /* 小幅上拖（不足半档）-> 回弹 closed。closed→mid 间距 433px，半档≈216px */
    applyStop('closed'); await wait(80);
    const spanCM = Math.abs(stops().closed.top - stops().mid.top);
    await drag(-(Math.round(spanCM * 0.2)));
    out.t5_smallDragUp = D().dataset.stop;                    // 期望 closed
    out.t5_span = spanCM;

    /* 刚过半档 -> 吸附 mid */
    applyStop('closed'); await wait(80);
    await drag(-(Math.round(spanCM * 0.55)));
    out.t6_bigDragUp = D().dataset.stop;                      // 期望 mid

    /* mid 再大幅上拖 -> full。mid→full 半档≈122px */
    applyStop('mid'); await wait(80);
    const spanMF = Math.abs(stops().mid.top - stops().full.top);
    await drag(-(Math.round(spanMF * 0.7)));
    out.t7_midToFull = D().dataset.stop;                      // 期望 full
    out.t7_span = spanMF;

    /* mid 小幅上拖（不足半档）-> 应回弹 mid，不能跳 full */
    applyStop('mid'); await wait(80);
    await drag(-(Math.round(spanMF * 0.2)));
    out.t7b_smallFromMid = D().dataset.stop;                  // 期望 mid

    /* full 大幅下拖 -> 应降一档 mid */
    applyStop('full'); await wait(80);
    await drag(Math.round(spanMF * 0.7));
    out.t8_fullDragDown = D().dataset.stop;                   // 期望 mid

    /* closed 一路拖到顶端（越过 mid）-> 仍只走一档，落 mid，不跳过 */
    applyStop('closed'); await wait(80);
    await drag(-(Math.round(spanCM * 1.4)));
    out.t9_overshoot = D().dataset.stop;                      // 期望 mid

    /* 搜索：收起状态下点击也应能看到搜索框（先把抽屉升到 mid） */
    applyStop('closed'); await wait(80);
    document.querySelector('#searchToggle').click(); await wait(500);
    out.searchToggle = { stop: D().dataset.stop, box: rect('.drawer__search'),
      dock: document.querySelector('#searchDock').dataset.search,
      inViewport: (() => { const r = rect('.drawer__search');
        return r.top >= 0 && r.bottom <= innerHeight + 1; })(),
      focus: document.activeElement ? document.activeElement.id : null };

    /* 搜索里输入要有真实过滤结果 */
    const inp = document.querySelector('#cardSearch');
    if (inp) {
      inp.value = 'FLAC'; inp.dispatchEvent(new Event('input', { bubbles: true })); await wait(80);
      out.searchHits = [...document.querySelectorAll('#cardSections .fcard:not(.fcard--new)')]
        .map(e => e.dataset.card);
      inp.value = ''; inp.dispatchEvent(new Event('input', { bubbles: true })); await wait(60);
    }
    out.cardsBack = document.querySelectorAll('#cardSections .fcard:not(.fcard--new)').length;
    out.err = null;
  } catch (e) {
    out.err = String((e && e.stack) || e);
  }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
