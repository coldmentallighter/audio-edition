/* 搜索栏「失焦收回」自检（headless 专用，不参与生产）
   验的是那条 bug：点搜索展开后，点别处不会收回去。
   关键点：收回发生在 click 之前，所以必须用 pointerdown 判断，
   并且不能重渲染卡片区，否则「点卡片加链」会在按下之后丢掉。 */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const $ = s => document.querySelector(s);
  const dock = () => $('#searchDock');
  const state = () => (dock() ? dock().dataset.search : null);
  const chainLen = () => document.querySelectorAll('#chain .chain__item').length;

  /* 一次真实鼠标点击：pointerdown → mousedown → mouseup → click，
     每一步都按当时的坐标重新命中元素（这才是浏览器干的事）。 */
  const realClick = async (x, y) => {
    const opts = { bubbles: true, cancelable: true, clientX: x, clientY: y,
                   pointerId: 1, pointerType: 'mouse', isPrimary: true };
    const el = () => document.elementFromPoint(x, y);
    el().dispatchEvent(new PointerEvent('pointerdown', { ...opts, buttons: 1 }));
    el().dispatchEvent(new MouseEvent('mousedown', { ...opts, buttons: 1 }));
    await wait(40);
    el().dispatchEvent(new PointerEvent('pointerup', { ...opts, buttons: 0 }));
    el().dispatchEvent(new MouseEvent('mouseup', { ...opts, buttons: 0 }));
    el().dispatchEvent(new MouseEvent('click', { ...opts, buttons: 0 }));
    await wait(120);
  };

  /* 抽屉中段里一张「真的能被点到」的卡片：中心点必须命中它自己
     （只看 rect 会挑到被滚动裁掉的那种，探针会假失败） */
  const pickCard = () => {
    const box = $('#drawer').getBoundingClientRect();
    for (const c of document.querySelectorAll('#cardSections .fcard:not(.fcard--new)')) {
      const r = c.getBoundingClientRect();
      if (!r.width || r.top <= box.top + 4 || r.bottom >= box.bottom - 8) continue;
      const x = Math.round(r.left + r.width / 2), y = Math.round(r.top + r.height / 2);
      const hit = document.elementFromPoint(x, y);
      if (hit && hit.closest('.fcard') === c) return { x, y };
    }
    return null;
  };

  try {
    for (let i = 0; i < 40 && !document.querySelectorAll('#cardSections .fcard').length; i++) await wait(250);
    await wait(600);
    applyStop('mid'); await wait(200);

    /* ---- t1：点搜索 → 展开 + 聚焦 ---- */
    $('#searchToggle').click(); await wait(500);
    out.t1_opened = { state: state(), focus: document.activeElement?.id,
                      value: $('#cardSearch').value };

    /* ---- t2：点抽屉里的卡片（搜索栏之外）→ 必须收回，且卡片点击不能丢 ---- */
    const card = pickCard();
    const before = chainLen();
    out.t2_cardFound = !!card;
    if (card) await realClick(card.x, card.y);
    out.t2_afterOutsideClick = {
      state: state(),                     // 期望 closed
      chainGrew: chainLen() - before,     // 期望 1（收回没把这次点击吃掉）
    };

    /* ---- t3：Escape 是显式取消：收回 + 清空过滤 ---- */
    $('#searchToggle').click(); await wait(420);
    const inp = $('#cardSearch');
    inp.value = 'FLAC';
    inp.dispatchEvent(new Event('input', { bubbles: true })); await wait(80);
    const filtered = document.querySelectorAll('#cardSections .fcard:not(.fcard--new)').length;
    inp.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await wait(120);
    out.t3_escape = { state: state(), value: $('#cardSearch').value,
                      cardsBack: document.querySelectorAll('#cardSections .fcard:not(.fcard--new)').length,
                      filteredWas: filtered };

    /* ---- t4：失焦收回保留过滤词（不重渲染，卡片区不会在按下之后被换掉） ---- */
    $('#searchToggle').click(); await wait(420);
    inp.value = 'FLAC';
    inp.dispatchEvent(new Event('input', { bubbles: true })); await wait(80);
    await realClick(...(() => { const r = $('#drawerClose').getBoundingClientRect();
      return [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)]; })());
    out.t4_blurKeepsQuery = {
      state: state(),
      value: $('#cardSearch').value,
      filtered: document.querySelectorAll('#cardSections .fcard:not(.fcard--new)').length,
      flagged: dock().dataset.filtered,        // 期望 true：按钮上挂"过滤中"提示
      title: $('#searchToggle').title,
    };

    /* ---- t5：再点搜索 → 过滤词还在，可以继续编辑/清除 ---- */
    $('#searchToggle').click(); await wait(420);
    $('#searchClear').click(); await wait(120);
    out.t5_reopen = {
      state: state(), value: $('#cardSearch').value,
      cardsBack: document.querySelectorAll('#cardSections .fcard:not(.fcard--new)').length,
      flagged: dock().dataset.filtered,
    };
    out.err = null;
  } catch (e) {
    out.err = String((e && e.stack) || e);
  }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
