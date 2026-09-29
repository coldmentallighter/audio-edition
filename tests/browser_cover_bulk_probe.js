/* 封面显示 + 批量删除 + 封面导入接线 的浏览器探针。
   需要在 tests/seed_demo.py 铺完数据之后跑（页面里没法造音频文件）。 */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const n = (sel) => document.querySelectorAll(sel).length;
  const txt = (sel) => { const e = document.querySelector(sel); return e ? e.textContent.trim() : null; };

  // 批量删除会弹 confirm；headless 里默认返回 false，会走不到删除分支。
  // 这里只替换确认框，其余逻辑全是页面上真实的代码。
  window.confirm = () => true;

  try {
    for (let i = 0; i < 60 && !n('#fileList .card'); i++) await wait(250);
    await wait(1500);

    /* ---------- 1. 封面显示 ---------- */
    out.files = n('#fileList .card');
    out.coverCells = n('#fileList [data-cover]');
    out.coverImgs = n('#fileList .cover__img');
    out.coverHasClass = n('#fileList .cover--has');

    const img = document.querySelector('#fileList .cover__img');
    if (img) {
      out.imgSrc = img.getAttribute('src');
      // naturalWidth>0 才说明图真的解码出来了，不是坏链
      for (let i = 0; i < 40 && !img.complete; i++) await wait(100);
      out.imgLoaded = img.complete && img.naturalWidth > 0;
      out.imgSize = img.naturalWidth + 'x' + img.naturalHeight;
      const r = img.getBoundingClientRect();
      out.imgBox = Math.round(r.width) + 'x' + Math.round(r.height);
    }
    // 无封面的格子不该有 img
    const noCover = [...document.querySelectorAll('#fileList [data-cover]')]
      .filter(c => !c.querySelector('.cover__img'));
    out.cellsWithoutImg = noCover.length;
    out.phVisibleOnPlain = noCover.length
      ? getComputedStyle(noCover[0].querySelector('.cover__ph')).display !== 'none' : null;

    /* ---------- 2. 封面导入接线：点封面/更换应弹出文件选择器 ---------- */
    let pickerOpened = 0;
    const realClick = HTMLInputElement.prototype.click;
    HTMLInputElement.prototype.click = function () {
      if (this.type === 'file') { pickerOpened++; out.pickerAccept = this.accept; return; }
      return realClick.apply(this, arguments);
    };
    const setBtn = document.querySelector('[data-cover-set]');
    out.hasSetBtn = !!setBtn;
    if (setBtn) { setBtn.click(); await wait(150); }
    const cell = document.querySelector('#fileList [data-cover]');
    if (cell) { cell.click(); await wait(150); }
    out.pickerOpened = pickerOpened;
    HTMLInputElement.prototype.click = realClick;

    // 移除封面按钮是否存在（有封面的格子才有）
    out.hasDelBtn = !!document.querySelector('[data-cover-del]');

    /* ---------- 2b. 取消文件选择：按钮不能永久卡在禁用态 ----------
       pickFile 靠 cancel 事件感知取消；如果感知不到，runBulk 的 promise
       永不 settle，finally 不执行，按钮就一直是 disabled。 */
    const realClick2 = HTMLInputElement.prototype.click;
    HTMLInputElement.prototype.click = function () {
      if (this.type === 'file') {
        // 模拟用户点了「取消」
        this.dispatchEvent(new Event('cancel'));
        return;
      }
      return realClick2.apply(this, arguments);
    };
    // 只选中一个文件，然后点批量嵌封面再取消
    FILES.forEach(f => { f.checked = false; });
    FILES[0].checked = true;
    syncBulkbar();
    await wait(120);
    const coverBulkBtn = document.querySelector('#bulkbar [data-bulk="cover"]');
    coverBulkBtn.click();
    await wait(600);
    out.afterCancel = {
      btnDisabled: coverBulkBtn.disabled,       // 期望 false
      filesUnchanged: FILES.length,
    };
    // 再点一次删除并取消（confirm 已被 stub 成 true，这里只验证不卡住）
    HTMLInputElement.prototype.click = realClick2;
    await wait(100);

    /* ---------- 3. 批量删除 ---------- */
    const before = FILES.length;
    // 勾选那 3 个「待删」文件
    const targets = FILES.filter(f => /待删/.test(f.title));
    out.targets = targets.map(f => f.title);
    FILES.forEach(f => { f.checked = targets.includes(f); });
    syncBulkbar();
    await wait(150);
    out.selected = FILES.filter(f => f.checked).length;
    out.bulkVisible = !document.querySelector('#bulkbar').hidden;
    out.bulkCount = txt('#bulkCount');
    out.bulkButtons = [...document.querySelectorAll('#bulkbar [data-bulk]')]
      .map(b => b.dataset.bulk);

    const delBtn = document.querySelector('#bulkbar [data-bulk="delete"]');
    delBtn.click();
    for (let i = 0; i < 50 && FILES.length >= before; i++) await wait(200);
    await wait(900);

    out.filesAfter = n('#fileList .card');
    out.filesModelAfter = FILES.length;
    out.removedFromModel = before - FILES.length;
    out.stillHasTargets = FILES.filter(f => /待删/.test(f.title)).length;
    out.bulkHiddenAfter = document.querySelector('#bulkbar').hidden;
    out.selCount = txt('#selCount');

    // 后端确认
    const api = await fetch('/api/files').then(r => r.json());
    out.apiFiles = api.files.length;
    out.apiHasTargets = api.files.filter(f => /待删/.test(f.name)).length;
    out.apiHasCoverFile = api.files.filter(f => f.info && f.info.hasCover).length;

    out.lastLogs = [...document.querySelectorAll('#logList .log')].slice(-4)
      .map(e => e.textContent.replace(/\s+/g, ' ').trim().slice(0, 76));

    out.err = null;
  } catch (e) {
    out.err = String((e && e.stack) || e);
  }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
