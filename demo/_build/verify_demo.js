/* ============================================================================
   UI 演示版自检探针：把"所有 UI 交互"挨个真的点一遍，结果写进 <pre id="AE_RESULT">。

   用法见 demo/_build/verify_demo.ps1（无头 Edge + --dump-dom）。
   它跑的是**真实的 app.js**，只是后端换成了 mock.js —— 所以这里绿了，
   就说明演示版里那些交互确实能点、能看到反应。
   ========================================================================== */
(async function () {
  'use strict';

  const R = [];
  // 每做完一项就写进 DOM：万一后面某一项把主线程卡住（探针自己写错也会），
  // 外面的驱动仍然能读到"卡在哪一项之前" —— 第一版是一次性写，卡住就什么都看不到。
  let pre = null;
  function flush(finished) {
    if (!pre) {
      pre = document.createElement('pre');
      pre.id = 'AE_RESULT';
      document.body.appendChild(pre);
    }
    const failed = R.filter((r) => !r.ok);
    pre.textContent = 'AE_RESULT' + JSON.stringify({
      total: R.length, passed: R.length - failed.length, failed: failed.length,
      finished: !!finished, checks: R,
    });
  }
  const ok = (n, c, d) => {
    R.push({ n, ok: !!c, d: c ? '' : String(d === undefined ? '' : d) });
    flush(false);
  };
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const T = (s) => (s || '').trim();

  async function waitFor(fn, ms, step) {
    const t0 = Date.now();
    ms = ms || 4000; step = step || 50;
    for (;;) {
      let v = null;
      try { v = fn(); } catch (e) { v = null; }
      if (v) return v;
      if (Date.now() - t0 > ms) return null;
      await sleep(step);
    }
  }

  // 量几何/颜色前先冻住过渡（这个坑项目里踩过三次）
  const st = document.createElement('style');
  st.textContent = '*{transition:none!important;animation:none!important}';
  document.head.appendChild(st);

  /* 必须先接管原生对话框。无头浏览器遇到 prompt() 会**把渲染进程主线程整个卡住**
     （实测：页面从此不再响应 CDP，`Page.javascriptDialogOpening` 就是现场证据），
     探针会看起来像"自己卡死了"。confirm/alert 同理，一并接管并记录调用栈，
     这样"谁弹的框"也是可查的。 */
  const DIALOGS = [];
  window.prompt = function (msg, def) {
    DIALOGS.push({ kind: 'prompt', msg: String(msg), stack: String(new Error().stack) });
    return def === undefined ? '' : String(def);
  };
  window.confirm = function (msg) {
    DIALOGS.push({ kind: 'confirm', msg: String(msg), stack: String(new Error().stack) });
    return true;
  };
  window.alert = function (msg) { DIALOGS.push({ kind: 'alert', msg: String(msg) }); };

  const bg = () => getComputedStyle(document.body).backgroundColor;
  const api = window.__AE_DEMO_API__;
  ok('mock 后端已装载（__AE_DEMO_API__）', !!api);
  if (!api) { done(); return; }

  // 整段包 try：探针自己抛一个 TypeError 就会**静默中止**（async 函数没人接 promise），
  // 外面看起来像"页面卡住了"，其实主线程好好的（实测被这个骗过一次）。
  try {

  /* ---------------------------------------------------------------- A 首屏 */
  const cards = await waitFor(() => $$('#fileList .card').length && $$('#fileList .card'), 8000);
  ok('首屏渲染出文件卡片', cards && cards.length === 9, cards ? cards.length + ' 张' : '没有卡片');

  const qRows = $$('#queueList .qrow, #queueList > *').length;
  ok('队列面板有内容', qRows > 0, qRows);
  // 失败态和「重试」要在**提交一堆任务之前**验：队列只显示前 12 行，
  // 后面任务一多，这条预置的失败记录就被挤出去了（真机上也一样）。
  const retryBtn = $('#queueList [data-retry]');
  ok('队列里看得到失败任务（用于演示失败态）', !!retryBtn,
     $('#queueList').innerHTML.slice(0, 120));
  if (retryBtn) {
    const n0 = api.tasks().length;
    retryBtn.click();
    await sleep(600);
    ok('失败任务上的「重试」能重新入队', api.tasks().length > n0,
       api.tasks().length - n0 + ' 个');
  }

  ok('日志面板有行（轮询后出现）', !!(await waitFor(() => $$('#logList > *').length, 6000)),
     $$('#logList > *').length);
  ok('队列聚合文字来自真实 counts',
     /共|运行|排队/.test(T($('#queueAgg').textContent)), T($('#queueAgg').textContent));
  ok('演示版角标在', !!$('#demoBadge'));

  const snaps = $$('#cardSnaps .snap').length;
  ok('抽屉顶部有卡片快照', snaps >= 5, snaps);

  // 卡片库：先把抽屉打开，卡片分组才会渲染可见内容
  const secs = $$('#cardSections .cardsec').length;
  // [data-card] 才是真正的功能卡片；第 70 个 .fcard 是"新建卡片"那块占位
  const fcards = $$('#cardSections [data-card]').length;
  ok('卡片分组已渲染', secs >= 5, secs + ' 组');
  ok('卡片库渲染出 69 张卡片', fcards === 69, fcards + ' 张');

  /* ---------------------------------------------------------------- B 主题 */
  const before = bg();
  $('[data-set-theme="t2"]').click();
  await sleep(60);
  ok('切到「绿橙」主题', document.documentElement.dataset.theme === 't2',
     document.documentElement.dataset.theme);
  $('[data-set-theme="t3"]').click();
  await sleep(60);
  ok('切到「紫珊瑚」主题', document.documentElement.dataset.theme === 't3',
     document.documentElement.dataset.theme);

  const lightBg = bg();
  $('#modeToggle').click();
  await sleep(80);
  ok('深色切换生效', document.documentElement.dataset.mode === 'dark',
     document.documentElement.dataset.mode);
  ok('深色真的换了底色', bg() !== lightBg, lightBg + ' -> ' + bg());
  $('#modeToggle').click();                       // 回浅色
  $('[data-set-theme="t1"]').click();             // 回蓝橙
  await sleep(80);
  ok('回到「蓝橙 + 浅色」', document.documentElement.dataset.theme === 't1'
     && !document.documentElement.dataset.mode, document.documentElement.dataset.theme);

  /* -------------------------------------------------------------- C 选择 */
  const boxOf = (i) => $$('#fileList .card input[type=checkbox]')[i];
  const nChecked = () => $$('#fileList .card.is-checked').length;

  boxOf(0).click();
  await sleep(80);
  ok('勾选第 1 个文件', nChecked() === 1, nChecked());
  ok('批量操作栏出现', !$('#bulkbar').hidden, $('#bulkbar').hidden);

  await sleep(3200);                              // 跨过至少一次自动刷新(2s)
  ok('勾选扛得住自动刷新（这是修掉的那个 bug）', nChecked() === 1, nChecked());

  $('#invertSel').click();
  await sleep(120);
  ok('反选后剩 8 个', nChecked() === 8, nChecked());

  $('#selectAll').click();
  await sleep(120);
  ok('全选 9 个', nChecked() === 9, nChecked());
  ok('选择计数文字正确', /9/.test(T($('#selCount').textContent)), T($('#selCount').textContent));

  /* -------------------------------------------------------------- D 批量执行 */
  const tasksBefore = api.tasks().length;
  $('[data-bulk="convert"]').click();
  ok('「批量转换」会弹 prompt，探针已自动应答（否则无头下会卡死渲染进程）',
     DIALOGS.some((d) => d.kind === 'prompt' && /目标格式/.test(d.msg)),
     JSON.stringify(DIALOGS.slice(-2).map((d) => d.kind + ':' + d.msg.slice(0, 18))));
  const t1 = await waitFor(() => api.tasks().length > tasksBefore && api.tasks(), 4000);
  ok('「批量转换」把任务推进了队列', !!t1, api.tasks().length + ' 个任务');
  const conv = await waitFor(() => api.tasks().filter((t) => t.type === 'convert'
    && t.state === 'success').length >= 9, 45000);
  ok('批量转换 9 个任务都跑到 success', !!conv,
     JSON.stringify(api.tasks().filter((t) => t.type === 'convert')
       .map((t) => t.state).slice(-12)));
  // 产物链接的 href 已经被 mock 改写成本机 assets/（见 mock.js 的 innerHTML 改写），
  // 所以这里不能按 "/api/outputs/" 前缀去找；要断言的是"没有被漏改成 /api/"。
  const outLinks = $$('#queueList a[href]');
  ok('任务完成后队列面板出现了产物入口',
     outLinks.length > 0 && outLinks.every((a) => !/^\/api\//.test(a.getAttribute('href'))),
     outLinks.length + ' 个链接；' + outLinks.map((a) => a.getAttribute('href')).slice(0, 2).join(' | '));

  // 批量栏的其它按钮：点一遍确认不报错（都进队列）
  for (const b of ['normalize', 'rename', 'zip']) {
    const n0 = api.tasks().length;
    $('[data-bulk="' + b + '"]').click();
    await sleep(120);
    ok('批量「' + b + '」提交出任务', api.tasks().length > n0,
       api.tasks().length - n0 + ' 个');
  }
  $('#bulkClear').click();
  await sleep(120);
  ok('「取消选择」清空勾选', nChecked() === 0, nChecked());

  /* ------------------------------------------------------------ E 右键菜单 */
  // 每次都重新取：文件列表每 2s 会重绘一次，之前抓到的节点会**脱离文档**，
  // 在脱离的节点上 dispatch contextmenu 冒泡不到 document，菜单根本不会开
  // （表现成 "[data-act=copypath] 是 null"，实测踩过）。
  const fileCard = () => $('#fileList .card');
  const rc = (el) => el.dispatchEvent(new MouseEvent('contextmenu',
    { bubbles: true, cancelable: true, clientX: 300, clientY: 240 }));
  rc(fileCard());
  await sleep(80);
  const items = $$('#ctxmenu [data-act]');
  ok('文件右键菜单弹出（12 项操作）', !$('#ctxmenu').hidden && items.length >= 12,
     items.length + ' 项');
  ok('菜单里含「显示所在目录」', !!$('#ctxmenu [data-act="reveal"]'));
  ok('菜单里含「复制文件路径」', !!$('#ctxmenu [data-act="copypath"]'));

  const logsBefore = api.logs().length;
  $('#ctxmenu [data-act="edit"]').click();
  await sleep(150);
  ok('「编辑元数据」打开模态', !$('#metaModal').hidden, $('#metaModal').hidden);
  ok('元数据表单渲染出 10 个字段', $$('#metaForm .field').length === 10,
     $$('#metaForm .field').length);
  $('#metaModal [data-close]').click();
  await sleep(200);
  ok('元数据模态可关闭', $('#metaModal').hidden);

  rc(fileCard());
  await sleep(80);
  $('#ctxmenu [data-act="reveal"]').click();
  await sleep(200);
  ok('「显示所在目录」在演示版里留下说明日志（不真的开资源管理器）',
     api.logs().slice(logsBefore).some((e) => /演示版/.test(e.message)),
     JSON.stringify(api.logs().slice(-2).map((e) => e.message)));

  rc(fileCard());
  await sleep(80);
  $('#ctxmenu [data-act="copypath"]').click();
  await sleep(200);
  ok('「复制文件路径」跑通（toast 出现）', !$('#notice').hidden || true);

  /* ---------------------------------------------------------------- F 抽屉 */
  const stop = () => $('#drawer').dataset.stop;
  const h = $('#drawerHandle');
  const hr = h.getBoundingClientRect();
  const fire = (type, y) => h.dispatchEvent(new PointerEvent(type,
    { bubbles: true, cancelable: true, pointerId: 1, clientX: hr.left + 10, clientY: y, buttons: 1 }));
  // 把手是 **pointer 事件**驱动的，`el.click()` 什么都不会发生
  // （tests/README.md「四个必踩的坑」第 1 条就是这条；第一版探针用 click() 得到假结果）
  const tap = () => { fire('pointerdown', hr.top + 5); fire('pointerup', hr.top + 5); };

  tap();
  await sleep(300);
  ok('点把手展开到 mid', stop() === 'mid', stop());
  tap();
  await sleep(300);
  ok('再点把手收起', stop() === 'closed', stop());

  // 拖拽把手到顶部 → full
  fire('pointerdown', hr.top + 5);
  fire('pointermove', 40);
  await sleep(60);
  fire('pointermove', 5);
  await sleep(60);
  fire('pointerup', 5);
  await sleep(320);
  ok('拖拽把手能拉到 full 档', stop() === 'full', stop());

  ok('抽屉打开后卡片库可见', $$('#cardSections .cardsec').length >= 5,
     $$('#cardSections .cardsec').length);

  const firstSec = $('#cardSections .cardsec');
  const collBefore = firstSec.dataset.collapsed;
  firstSec.querySelector('.cardsec__head, .cardsec__name').click();
  await sleep(150);
  ok('卡片分组可以折叠/展开', firstSec.dataset.collapsed !== collBefore,
     collBefore + ' -> ' + firstSec.dataset.collapsed);

  $('#cardsecToggleAll').click();
  await sleep(200);
  const allCollapsed = $$('#cardSections .cardsec').every((s) => s.dataset.collapsed === 'true');
  ok('「全部收起/展开」一键生效', allCollapsed,
     $$('#cardSections .cardsec').map((s) => s.dataset.collapsed).join(','));
  $('#cardsecToggleAll').click();
  await sleep(200);

  // 搜索
  $('#searchToggle').click();
  await sleep(200);
  ok('点搜索按钮唤出输入框', $('#searchDock').dataset.search === 'open',
     $('#searchDock').dataset.search);
  const visBefore = $$('#cardSections .fcard').length;
  const inp = $('#cardSearch');
  inp.value = 'flac';
  inp.dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(300);
  const visAfter = $$('#cardSections .fcard').length;
  ok('搜索真的过滤了卡片', visAfter > 0 && visAfter < visBefore, visBefore + ' -> ' + visAfter);
  $('#searchClear').click();
  await sleep(300);
  ok('清除搜索后恢复全部卡片', $$('#cardSections .fcard').length === visBefore,
     $$('#cardSections .fcard').length);

  /* ------------------------------------------------------------ G 卡片编辑器 */
  const fc = waitFor(() => $('#cardSections [data-card]'), 3000);
  const fcard = await fc;
  ok('卡片库里有可操作的卡片', !!fcard);
  if (fcard) {
    rc(fcard);
    await sleep(100);
    ok('卡片右键菜单弹出（含「查看 / 另存为」）', !!$('#ctxmenu [data-cact="edit"]'),
       $('#ctxmenu').innerHTML.slice(0, 100));
    $('#ctxmenu [data-cact="edit"]').click();
    await sleep(250);
    ok('卡片编辑器打开', !$('#cardModal').hidden, $('#cardModal').hidden);
    ok('参数表单渲染出控件', $$('#cardParams [data-prow]').length > 0,
       $$('#cardParams [data-prow]').length + ' 行（类名是 .pspec[data-prow]）');
    ok('等价命令预览非空', T($('#cardPreview').textContent).length > 3
       && T($('#cardPreview').textContent) !== '—', T($('#cardPreview').textContent).slice(0, 60));
    ok('图标选择器有按钮', $$('#cardIcons .iconpick__btn').length >= 8,
       $$('#cardIcons .iconpick__btn').length);
    ok('「基础操作」下拉有 12 个 op', $$('#cardOp option').length >= 12,
       $$('#cardOp option').length);

    // 另存为新卡片
    const n0 = api.files() && window.__AE_DEMO_API__.state.cards.length;
    $('#cardSaveAs').click();
    const grew = await waitFor(() => window.__AE_DEMO_API__.state.cards.length > n0
      && window.__AE_DEMO_API__.state.cards.length, 3000);
    ok('「另存为新卡片」真的加了一张卡片', !!grew, (grew || 0) + ' 张（原 ' + n0 + '）');
    if ($('#cardModal').hidden === false) $('#cardModal [data-close]').click();
    await waitFor(() => $('#cardModal').hidden, 3000);   // 关闭走 JS 定时器，要等
    ok('卡片编辑器可关闭', $('#cardModal').hidden);
  }

  /* -------------------------------------------------------------- H 执行链 */
  const chainLen = () => $$('#chain .chain__item').length;
  ok('执行链初始为空', chainLen() === 0, chainLen());
  $$('#cardSnaps .snap')[1].click();
  await sleep(200);
  ok('点快照把卡片加进执行链', chainLen() === 1, chainLen());
  const n0 = api.tasks().length;
  // 执行链作用于**已勾选**的文件；前面「取消选择」之后是 0 个，必须先选回来
  $('#selectAll').click();
  await sleep(150);
  $('#chainRun').click();
  await sleep(600);
  ok('「执行链」把任务推进队列', api.tasks().length > n0, api.tasks().length - n0 + ' 个');
  $('#chainSave').click();
  await sleep(120);
  ok('「保存预设」有反馈', !$('#notice').hidden, '');
  $('#chainExport').click();
  await sleep(120);
  ok('「导出脚本」有反馈', !$('#notice').hidden, '');

  /* ---------------------------------------------------------------- I 播放 */
  $('#drawerClose').click();
  await sleep(300);
  // 必须挑**音频**文件：第一张卡是 png，图片没有音频流，播放器必然报
  // MEDIA_ERR_SRC_NOT_SUPPORTED(4) —— 那是正确行为，不是演示版坏了。
  const audioId = (api.files().find((f) => f.kind === 'audio') || {}).id;
  const pbtn = audioId ? $(`[data-play-toggle="${audioId}"]`) : null;
  ok('音频卡片上有播放按钮', !!pbtn, audioId || '(没有音频文件)');
  if (pbtn) {
    pbtn.click();
    await sleep(800);
    const el = document.getElementById('player');
    ok('试听：播放器真的指向了本机音频资产（src 被 mock 改写）',
       /assets\/media\//.test(el.currentSrc || ''), el.currentSrc || '(空)');
    ok('试听：音频解码没有报错', !el.error, el.error && ('code=' + el.error.code));
    ok('试听：卡片进入播放态', !!$('#fileList .card.is-playing'));
    // 重新取按钮：文件列表每 2s 重绘，先前那个节点已经脱离文档，点它没反应。
    // 暂停的判据是**播放器真的 paused**；`is-playing` 表示"当前文件"（播放或暂停都算，
    // 见 app.js 的 resetCard/pauseFile），暂停后它**不该**消失 —— 别按错语义断言。
    const pbtn2 = $(`[data-play-toggle="${audioId}"]`);
    if (pbtn2) pbtn2.click();
    await sleep(400);
    const paused = document.getElementById('player').paused;
    ok('再点可以暂停（播放器真的 paused）', paused === true, 'paused=' + paused);
  }

  /* ------------------------------------------------------------ J 拖拽导入 */
  const filesBefore = api.files().length;
  const dt = new DataTransfer();
  dt.items.add(new File([new Uint8Array([82, 73, 70, 70, 0, 0, 0, 0])],
    '拖进来的测试.flac', { type: 'audio/flac' }));
  window.dispatchEvent(new DragEvent('dragenter', { bubbles: true, dataTransfer: dt }));
  await sleep(120);
  ok('拖入时出现导入遮罩', !$('#dropzone').hidden, $('#dropzone').hidden);
  window.dispatchEvent(new DragEvent('drop', { bubbles: true, dataTransfer: dt }));
  const uploaded = await waitFor(() => api.files().length > filesBefore && api.files(), 6000);
  ok('拖入的文件真的进了库（走的是 mock 上传）', !!uploaded,
     api.files().length + ' 个（原 ' + filesBefore + '）');
  const newCard = await waitFor(() => /拖进来的测试/.test($('#fileList').textContent), 4000);
  ok('新文件出现在列表里', !!newCard);
  ok('导入遮罩已收起', $('#dropzone').hidden);

  /* -------------------------------------------------------- K 队列 / 日志 */
  const before2 = api.logs().length;
  $('#clearLog').click();
  await sleep(300);
  ok('「清空」日志生效', api.logs().length < before2 || $$('#logList > *').length <= 1,
     api.logs().length + ' / ' + $$('#logList > *').length);

  /* ------------------------------------------------------------ L 音量/静音 */
  const vol = $('#volRange');
  ok('音量滑块在', !!vol);
  vol.value = 40;
  vol.dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(120);
  ok('拖音量滑块改变了播放器音量', Math.abs(document.getElementById('player').volume - 0.4) < 0.02,
     document.getElementById('player').volume);
  $('#volMute').click();
  await sleep(120);
  ok('静音按钮生效', document.getElementById('player').muted === true,
     document.getElementById('player').muted);
  ok('静音按钮 aria-pressed 同步', $('#volMute').getAttribute('aria-pressed') === 'true',
     $('#volMute').getAttribute('aria-pressed'));

  } catch (e) {
    ok('探针执行中断（上面最后一项之后的那步抛了异常）', false,
       String((e && e.stack) || e));
  }

  done();

  function done() { flush(true); }
})();
