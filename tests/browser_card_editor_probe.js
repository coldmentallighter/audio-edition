/* 卡片编辑 / 另存为 / 新建 / 参数说明 的浏览器探针。
   会真的创建、修改、删除自定义卡片（跑完自己清理）。 */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const n = (sel) => document.querySelectorAll(sel).length;
  const txt = (sel) => { const e = document.querySelector(sel); return e ? e.textContent.trim() : null; };
  const open = () => !document.getElementById('cardModal').hidden;
  const cardNames = () => [...document.querySelectorAll('#cardSections [data-card]')]
    .map(e => e.dataset.card);
  const rightClick = async (el) => {
    const r = el.getBoundingClientRect();
    el.dispatchEvent(new MouseEvent('contextmenu', {
      bubbles: true, cancelable: true, clientX: r.left + 20, clientY: r.top + 12,
    }));
    await wait(150);
  };
  const menuItems = () => [...document.querySelectorAll('#ctxmenu [data-cact]')]
    .map(b => b.dataset.cact + (b.disabled ? '(disabled)' : ''));

  try {
    for (let i = 0; i < 60 && !n('#cardSections .fcard'); i++) await wait(250);
    await wait(1200);

    /* ---------- 1. 工具参数说明接口 ---------- */
    const ops = await fetch('/api/ops').then(r => r.json());
    out.opsCount = ops.ops.length;
    out.opsWithParams = ops.ops.filter(o => o.params.length)
      .map(o => `${o.op}:${o.params.length}`);
    const conv = ops.ops.find(o => o.op === 'convert');
    out.convertParams = conv.params.map(p => ({
      key: p.key, type: p.type, label: p.label,
      hasDesc: !!p.desc, def: p.default,
      range: (p.min !== undefined ? `${p.min}~${p.max}` : (p.options ? `${p.options.length}项` : '')),
      onlyIf: p.onlyIf ? `${p.onlyIf.key}∈${p.onlyIf.in.join('/')}` : null,
    }));
    out.everyParamHasDesc = ops.ops.every(o => o.params.every(p => p.desc && p.label));

    /* ---------- 2. 右键内置卡片 ---------- */
    const builtinEl = document.querySelector('#cardSections [data-card]');
    out.builtinName = builtinEl.dataset.card;
    await rightClick(builtinEl);
    out.menuOpen = !document.getElementById('ctxmenu').hidden;
    out.builtinMenu = menuItems();
    out.builtinTag = txt('#ctxmenu .ctxmenu__tag');

    /* 内置卡片的「编辑」应打开编辑器并提示会另存 */
    document.querySelector('#ctxmenu [data-cact="edit"]').click();
    await wait(600);
    out.editorOpened = open();
    out.titleForBuiltin = txt('#cardModalTitle');
    out.subForBuiltin = txt('#cardModalSub');
    out.saveBtnTextForBuiltin = txt('#cardSave');
    out.saveAsHiddenForBuiltin = document.getElementById('cardSaveAs').hidden;
    out.deleteHiddenForBuiltin = document.getElementById('cardDelete').hidden;
    out.prefilledName = document.getElementById('cardName').value;
    // 内置卡那个「保存」是**新建**（按钮文案就是「创建卡片」），所以默认名必须
    // 避开内置卡本名：卡片名全局唯一，预填本名会被后端 400 顶回来
    // （以前是静默建出一张同名的自定义卡，而那张卡在库里点不开）。
    out.prefilledNameAvoidsBuiltin = out.prefilledName !== out.builtinName;
    out.opDescShown = (txt('#cardOpDesc') || '').slice(0, 40);
    out.previewShown = txt('#cardPreview');
    out.paramRows = n('#cardParams .pspec');
    out.iconChoices = n('#cardIcons .iconpick__btn');
    document.querySelector('#cardModal [data-close]').click();
    await wait(200);

    /* ---------- 3. 新建卡片（含参数说明渲染 + 命令预览） ---------- */
    document.querySelector('#newCard').click();
    await wait(700);
    out.newOpened = open();
    out.titleForNew = txt('#cardModalTitle');
    const catSel = document.getElementById('cardCat');
    const opSel = document.getElementById('cardOp');
    out.catOptions = [...catSel.options].map(o => o.value);
    out.opOptions = [...opSel.options].map(o => o.value);

    // 选 convert，检查条件参数是否按 format 显隐
    opSel.value = 'convert';
    opSel.dispatchEvent(new Event('change', { bubbles: true }));
    await wait(250);
    const rowState = () => ({
      rows: n('#cardParams .pspec'),
      off: n('#cardParams .pspec--off'),
      hasCompression: !!document.querySelector('[data-pkey="compressionLevel"]'),
      hasBitrate: !!document.querySelector('[data-pkey="bitrate"]'),
    });
    out.convertDefault = rowState();                       // flac → 有压缩等级、无码率
    const fmt = document.querySelector('[data-pkey="format"]');
    fmt.value = 'mp3';
    fmt.dispatchEvent(new Event('change', { bubbles: true }));
    await wait(300);
    out.convertMp3 = rowState();                           // mp3 → 有码率
    out.paramDescsShown = n('#cardParams .pspec__desc');
    out.descSample = (txt('#cardParams .pspec__desc') || '').slice(0, 50);
    // 命令预览要跟着参数变
    fmt.value = 'mp3';
    const br = document.querySelector('[data-pkey="bitrate"]');
    if (br) { br.value = '320k'; br.dispatchEvent(new Event('change', { bubbles: true })); }
    await wait(500);
    out.previewMp3 = txt('#cardPreview');

    // 填名字保存
    document.getElementById('cardName').value = '探针-转MP3-320';
    document.getElementById('cardDesc').value = '探针创建的卡片';
    document.getElementById('cardCat').value = '自定义';
    await wait(150);
    const before = cardNames().length;
    document.getElementById('cardSave').click();
    await wait(1400);
    out.afterCreate = {
      modalClosed: !open(),
      cardsBefore: before,
      cardsAfter: cardNames().length,
      created: cardNames().includes('探针-转MP3-320'),
      sections: [...document.querySelectorAll('#cardSections .cardsec__title')]
        .map(e => e.textContent.replace(/\s+/g, ' ').trim()),
    };
    const apiAfter = await fetch('/api/cards').then(r => r.json());
    const mine = apiAfter.cards.find(c => c.name === '探针-转MP3-320');
    out.createdOnServer = mine ? { op: mine.op, params: mine.params, custom: mine.custom, id: mine.id } : null;

    /* ---------- 4. 右键自定义卡片 → 编辑改名 → 保存 ---------- */
    const mineEl = [...document.querySelectorAll('#cardSections [data-card]')]
      .find(e => e.dataset.card === '探针-转MP3-320');
    out.foundMineEl = !!mineEl;
    if (mineEl) {
      await rightClick(mineEl);
      out.customMenu = menuItems();
      out.customTag = txt('#ctxmenu .ctxmenu__tag');
      document.querySelector('#ctxmenu [data-cact="edit"]').click();
      await wait(600);
      out.editTitle = txt('#cardModalTitle');
      out.editDeleteHidden = document.getElementById('cardDelete').hidden;
      out.editSaveAsHidden = document.getElementById('cardSaveAs').hidden;
      out.editNamePrefilled = document.getElementById('cardName').value;
      out.editOpPrefilled = document.getElementById('cardOp').value;
      out.editBitratePrefilled = (document.querySelector('[data-pkey="bitrate"]') || {}).value;

      document.getElementById('cardName').value = '探针-改名后';
      document.getElementById('cardSave').click();
      await wait(1400);
      const api2 = await fetch('/api/cards').then(r => r.json());
      out.afterRename = {
        renamed: api2.cards.some(c => c.name === '探针-改名后'),
        oldGone: !api2.cards.some(c => c.name === '探针-转MP3-320'),
        sameCount: api2.cards.filter(c => c.custom).length,
      };
    }

    /* ---------- 5. 另存为新卡片（不覆盖原卡） ---------- */
    const renamedEl = [...document.querySelectorAll('#cardSections [data-card]')]
      .find(e => e.dataset.card === '探针-改名后');
    if (renamedEl) {
      await rightClick(renamedEl);
      document.querySelector('#ctxmenu [data-cact="saveas"]').click();
      await wait(600);
      out.saveAsTitle = txt('#cardModalTitle');
      out.saveAsName = document.getElementById('cardName').value;
      document.getElementById('cardName').value = '探针-副本';
      document.getElementById('cardSaveAs').click();
      await wait(1400);
      const api3 = await fetch('/api/cards').then(r => r.json());
      out.afterSaveAs = {
        copyExists: api3.cards.some(c => c.name === '探针-副本'),
        originalKept: api3.cards.some(c => c.name === '探针-改名后'),
        customCount: api3.cards.filter(c => c.custom).length,
      };
    }

    /* ---------- 6. 内置卡片不允许改/删 ---------- */
    const b2 = document.querySelector('#cardSections [data-card]');
    await rightClick(b2);
    out.builtinMenuHasDelete = menuItems().some(x => x.startsWith('del'));
    document.getElementById('ctxmenu').hidden = true;

    /* ---------- 7. 服务端校验：非法参数要被拒 ---------- */
    const bad = async (body) => {
      const r = await fetch('/api/cards', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await r.json().catch(() => ({}));
      return { status: r.status, detail: (d.detail || '').slice(0, 60) };
    };
    out.rejects = {
      unknownOp: await bad({ name: 'x', op: 'nope' }),
      unknownParam: await bad({ name: 'x', op: 'convert', params: { bogus: 1 } }),
      badEnum: await bad({ name: 'x', op: 'convert', params: { format: 'exe' } }),
      outOfRange: await bad({ name: 'x', op: 'normalize', params: { targetLufs: -99 } }),
      noName: await bad({ op: 'convert' }),
      // 卡片名全局唯一：撞内置卡的名字必须被拒（用户实测踩的坑 —— 同名的新卡
      // 在库里永远点不开，因为卡片库是按名字认卡的）
      dupBuiltinName: await bad({ name: out.builtinName, op: 'probe', cat: '自定义' }),
      dupCustomName: await bad({ name: '探针-改名后', op: 'probe', cat: '自定义' }),
    };

    /* ---------- 8. 清理：删掉探针建的卡 ---------- */
    const apiFinal = await fetch('/api/cards').then(r => r.json());
    const junk = apiFinal.cards.filter(c => c.custom && /^探针-/.test(c.name));
    for (const c of junk) {
      await fetch(`/api/cards/${c.id}`, { method: 'DELETE' });
    }
    await reloadCards();
    await wait(600);
    const apiCleaned = await fetch('/api/cards').then(r => r.json());
    out.cleanup = {
      deleted: junk.length,
      customLeft: apiCleaned.cards.filter(c => c.custom).length,
      totalCards: apiCleaned.cards.length,
      builtinCount: apiCleaned.builtinCount,
      probeGone: !apiCleaned.cards.some(c => /^探针-/.test(c.name)),
    };
    out.finalNames = cardNames();

    out.err = null;
  } catch (e) {
    out.err = String((e && e.stack) || e);
  }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
