/* 快照拖拽替换探针：卡片拖进快照=替换，快照互拖=换位，拖到 ＋ =追加，右键恢复默认。
   会真的改服务端快照，跑完恢复默认。 */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const n = (sel) => document.querySelectorAll(sel).length;
  const snapNames = () => [...document.querySelectorAll('#cardSnaps [data-snap]')]
    .map(e => e.dataset.snapName);
  const addZone = () => document.querySelector('[data-snap-add]');

  // HTML5 拖拽在无头里没法用真实指针触发，直接构造 DragEvent + DataTransfer
  const dt = () => new DataTransfer();
  const fire = (el, type, data) => {
    const e = new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: data });
    el.dispatchEvent(e);
    return e;
  };
  const dragCardTo = async (cardName, target) => {
    const card = [...document.querySelectorAll('#cardSections [data-card]')]
      .find(c => c.dataset.card === cardName);
    if (!card) return 'no-card';
    const d = dt();
    fire(card, 'dragstart', d);
    fire(target, 'dragover', d);
    fire(target, 'drop', d);
    fire(card, 'dragend', d);
    await wait(900);
    return 'ok';
  };
  const dragSnapTo = async (fromIdx, target) => {
    const s = document.querySelector(`#cardSnaps [data-snap="${fromIdx}"]`);
    if (!s) return 'no-snap';
    const d = dt();
    fire(s, 'dragstart', d);
    fire(target, 'dragover', d);
    fire(target, 'drop', d);
    fire(s, 'dragend', d);
    await wait(900);
    return 'ok';
  };

  try {
    for (let i = 0; i < 60 && !n('#cardSections .fcard'); i++) await wait(250);
    await wait(1400);

    out.snapsAtStart = snapNames();
    out.snapCount = out.snapsAtStart.length;
    out.draggable = [...document.querySelectorAll('#cardSnaps [data-snap]')]
      .every(e => e.getAttribute('draggable') === 'true');
    out.cardsDraggable = [...document.querySelectorAll('#cardSections [data-card]')]
      .every(e => e.getAttribute('draggable') === 'true');
    out.hasAddZone = !!addZone();
    out.addZoneText = addZone() ? addZone().textContent.replace(/\s+/g, ' ').trim() : null;

    /* ---- 1. 拖一张不在快照里的卡片到第 2 格 → 替换 ---- */
    const all = [...document.querySelectorAll('#cardSections [data-card]')]
      .map(c => c.dataset.card);
    const outsider = all.find(x => !out.snapsAtStart.includes(x));
    out.outsider = outsider;
    const before = snapNames();
    await dragCardTo(outsider, document.querySelector('#cardSnaps [data-snap="1"]'));
    out.afterReplace = {
      snaps: snapNames(),
      slot1Changed: snapNames()[1] === outsider,
      countUnchanged: snapNames().length === before.length,
      othersIntact: snapNames()[0] === before[0] && snapNames()[2] === before[2],
    };
    const sv = await fetch('/api/snapshots').then(r => r.json());
    out.serverAfterReplace = sv.snapshots;

    /* ---- 2. 刷新页面后是否保持（重新拉一次卡片目录） ---- */
    const cards = await fetch('/api/cards').then(r => r.json());
    out.serverCardsSnapshots = cards.snapshots;

    /* ---- 3. 快照互拖 = 换位 ---- */
    const beforeSwap = snapNames();
    await dragSnapTo(0, document.querySelector('#cardSnaps [data-snap="3"]'));
    out.afterSwap = {
      before: beforeSwap,
      after: snapNames(),
      swapped: snapNames()[0] === beforeSwap[3] && snapNames()[3] === beforeSwap[0],
      countUnchanged: snapNames().length === beforeSwap.length,
    };

    /* ---- 4. 拖卡片到 ＋ → 追加 ---- */
    const beforeAdd = snapNames();
    const another = all.find(x => !beforeAdd.includes(x));
    out.addedCard = another;
    if (another && addZone()) {
      await dragCardTo(another, addZone());
      out.afterAppend = {
        before: beforeAdd.length,
        after: snapNames().length,
        appended: snapNames()[snapNames().length - 1] === another,
        inList: snapNames().includes(another),
      };
    }

    /* ---- 5. 已在快照里的卡片拖进来 → 不重复 ---- */
    const dup = snapNames()[0];
    const beforeDup = snapNames();
    await dragCardTo(dup, document.querySelector('#cardSnaps [data-snap="4"]'));
    out.afterDup = {
      countUnchanged: snapNames().length === beforeDup.length,
      stillUnique: new Set(snapNames()).size === snapNames().length,
    };

    /* ---- 6. 拖拽视觉反馈 ---- */
    const d2 = dt();
    const card2 = document.querySelector('#cardSections [data-card]');
    fire(card2, 'dragstart', d2);
    fire(document.querySelector('#cardSnaps [data-snap="0"]'), 'dragover', d2);
    await wait(60);
    out.dropHintShown = n('#cardSnaps .snap--drop') === 1;
    fire(card2, 'dragend', d2);
    await wait(60);
    out.dropHintCleared = n('#cardSnaps .snap--drop') === 0;

    /* ---- 7. 右键 → 恢复默认 ---- */
    const s0 = document.querySelector('#cardSnaps [data-snap="0"]');
    const r = s0.getBoundingClientRect();
    s0.dispatchEvent(new MouseEvent('contextmenu', {
      bubbles: true, cancelable: true, clientX: r.left + 20, clientY: r.top + 10,
    }));
    await wait(200);
    out.ctxMenuOpen = !document.getElementById('ctxmenu').hidden;
    out.ctxItems = [...document.querySelectorAll('#ctxmenu [data-sact]')]
      .map(b => b.dataset.sact);
    document.querySelector('#ctxmenu [data-sact="reset"]').click();
    await wait(900);
    out.afterReset = snapNames();

    /* ---- 8. 全部点一遍还能加入执行链（替换后按钮没坏） ---- */
    document.querySelector('#cardSnaps [data-snap="0"]').click();
    await wait(200);
    out.chainAfterClick = typeof chain !== 'undefined' ? chain.length : -1;

    const finalSnaps = await fetch('/api/snapshots').then(r => r.json());
    out.serverFinal = finalSnaps.snapshots;
    out.serverIsDefault = JSON.stringify(finalSnaps.snapshots) === JSON.stringify(
      out.snapsAtStart);

    out.err = null;
  } catch (e) {
    out.err = String((e && e.stack) || e);
  }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
