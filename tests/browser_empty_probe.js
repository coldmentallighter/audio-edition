/* 首帧空页面断言：打开就必须是空的，且各个计数都不能残留假数字。 */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const n = (sel) => document.querySelectorAll(sel).length;
  const txt = (sel) => { const e = document.querySelector(sel); return e ? e.textContent.trim() : null; };

  try {
    // 首帧：DOMContentLoaded 之后立刻抓一次，看有没有假数据闪一下
    out.atBoot = {
      fileCards: n('#fileList .card'),
      emptyShown: !!document.querySelector('#fileList .empty'),
      emptyText: txt('#fileList .empty strong'),
      selCount: txt('#selCount'),
      queueAgg: txt('#queueAgg'),
      logLines: n('#logList .log'),
      cards: n('#cardSections .fcard:not(.fcard--new)'),
    };

    // 等后端首轮返回后再抓一次
    await wait(2600);
    out.afterLoad = {
      fileCards: n('#fileList .card'),
      filesModel: typeof FILES !== 'undefined' ? FILES.length : -1,
      emptyShown: !!document.querySelector('#fileList .empty'),
      emptyText: txt('#fileList .empty strong'),
      emptyHint: txt('#fileList .empty span'),
      selCount: txt('#selCount'),
      queueAgg: txt('#queueAgg'),
      queueRows: n('#queueList .task'),
      queueEmptyHint: !!document.querySelector('#queueList .empty'),
      logLines: n('#logList .log'),
      bulkHidden: document.querySelector('#bulkbar').hidden,
      cards: n('#cardSections .fcard:not(.fcard--new)'),
      snaps: n('#cardSnaps [data-snap]'),
      waveCanvas: n('#fileList canvas'),
      coverCells: n('#fileList [data-cover]'),
    };

    // 空列表时批量栏不该出现，全选也不该能勾到东西
    document.querySelector('#selectAll').checked = true;
    document.querySelector('#selectAll').dispatchEvent(new Event('change', { bubbles: true }));
    await wait(200);
    out.selectAllOnEmpty = {
      selected: typeof FILES !== 'undefined' ? FILES.filter(f => f.checked).length : -1,
      bulkHidden: document.querySelector('#bulkbar').hidden,
    };

    const api = await fetch('/api/files').then(r => r.json());
    out.apiFiles = api.files.length;

    out.err = null;
  } catch (e) {
    out.err = String((e && e.stack) || e);
  }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
