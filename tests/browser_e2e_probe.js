/* 端到端：用页面自己的逻辑（勾选 → 加链 → 执行 → 轮询）打真后端。
   目的：证明页面上看到的队列/日志/文件都不是写死的。 */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const n = (sel) => document.querySelectorAll(sel).length;
  const txt = (sel) => { const e = document.querySelector(sel); return e ? e.textContent.trim() : null; };

  try {
    for (let i = 0; i < 40 && !n('#fileList .card').length; i++) await wait(250);
    await wait(1500);

    out.start = { files: FILES.length, ids: FILES.slice(0, 2).map(f => f.id),
                  logs: n('#logList .log'), queue: n('#queueList .task'),
                  agg: txt('#queueAgg'), sel: txt('#selCount') };

    /* 1) 全选 —— 之前这里有过反馈环 bug，只剩 1 个被勾上 */
    const all = document.querySelector('#selectAll');
    all.checked = true;
    all.dispatchEvent(new Event('change', { bubbles: true }));
    await wait(200);
    out.selected = FILES.filter(f => f.checked).length;
    out.selectedTotal = FILES.length;
    out.bulkCount = txt('#bulkCount');
    out.bulkVisible = !document.querySelector('#bulkbar').hidden;

    /* 2) 反选 —— 应变成 0 个 */
    document.querySelector('#invertSel').click();
    await wait(200);
    out.afterInvert = FILES.filter(f => f.checked).length;

    /* 3) 只勾第一个文件，避免对全部音频做大操作 */
    FILES.forEach(f => { f.checked = false; });
    FILES[0].checked = true;
    document.querySelector('#selectAll').checked = false;
    syncBulkbar();
    await wait(120);
    out.scoped = FILES.filter(f => f.checked).map(f => f.id);

    /* 4) 把「生成峰值图」加入执行链并执行（真调 /api/ops/peaks） */
    out.chainBefore = chain.length;
    addToChain('生成峰值图');
    await wait(150);
    out.chainAfter = chain.map(s => ({ name: s.name, op: s.op }));
    out.chainRendered = n('.chain__item');
    out.drawerScope = txt('#drawerScope');

    const beforeLogs = n('#logList .log');
    await runChain();
    await wait(600);
    out.logsGrewBy = n('#logList .log') - beforeLogs;
    out.chainAfterRun = n('.chain__item');

    /* 5) 轮询几轮，等任务进队列并落日志 */
    for (let i = 0; i < 12; i++) { if (window.App && App.refreshQueue) await App.refreshQueue(); await wait(400); }

    out.queue = n('#queueList .task');
    out.agg = txt('#queueAgg');
    out.queueFirst = [...document.querySelectorAll('#queueList .task')].slice(0, 3)
      .map(e => e.textContent.replace(/\s+/g, ' ').trim().slice(0, 70));
    out.logs = n('#logList .log');
    out.lastLogs = [...document.querySelectorAll('#logList .log')].slice(-4)
      .map(e => e.textContent.replace(/\s+/g, ' ').trim().slice(0, 78));
    out.logsEmpty = !!document.querySelector('#logList .empty');

    /* 6) 文件状态是否被后端结果刷新（不是本地写死的 progress） */
    out.stateAfter = FILES.slice(0, 3).map(f => f.title.slice(0, 14) + '=' + f.status + '/' + f.progress);

    /* 7) 波形数据源：真峰值还是空提示 */
    const cv = document.querySelector('#fileList canvas');
    if (cv) {
      const px = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
      let lit = 0; for (let i = 3; i < px.length; i += 4) if (px[i] > 8) lit++;
      out.wave = { w: cv.width, h: cv.height, lit };
    }
    out.peakStates = FILES.slice(0, 3).map(f => f.peaks);

    out.err = null;
  } catch (e) {
    out.err = String((e && e.stack) || e);
  }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
