/* 卡片执行链路：每张内置卡都必须真的能提交（不是 404）。
   重点看曾经必然 404 的「提取封面」「删除封面」，以及新补的「批量改标签」「嵌入封面」。 */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const picked = [], failed = [];

  try {
    for (let i = 0; i < 60 && !document.querySelector('#fileList .card'); i++) await wait(250);
    await wait(1500);

    out.cardCount = CARDS.length;
    out.categories = [...CATEGORIES];
    out.ops = [...new Set(CARDS.map(c => c.op))].sort();
    out.sections = [...document.querySelectorAll('#cardSections .cardsec__title')]
      .map(e => e.textContent.replace(/\s+/g, ' ').trim());
    // 每张卡片都必须渲染出来（曾经分类对不上就会凭空消失）
    const rendered = new Set([...document.querySelectorAll('#cardSections [data-card]')]
      .map(e => e.dataset.card));
    out.renderedAll = CARDS.every(c => rendered.has(c.name));
    out.missingCards = CARDS.filter(c => !rendered.has(c.name)).map(c => c.name);

    // 勾一个文件。
    // **不能无脑用 FILES[0]**：种子数据里第一个常是 PNG，而"嵌入封面"对 PNG
    // 是**正确地拒绝**（容器不支持），于是探针会在"嵌封面真能成功"这条路径
    // 从没被走到的前提下全绿 —— 这正是本文件开头警告的那类假通过。
    // 所以优先挑一个**音频**文件，封面类操作再优先挑支持内嵌封面的容器。
    const imageExts = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']);
    const coverExts = new Set(['flac', 'mp3', 'm4a', 'wma']);
    // `format` 是 ffprobe 的格式名，PNG 会报成 `png_pipe` 而不是 `png`，
    // 所以图片判定要连前缀一起看，否则封面图会被当成"音频"选走。
    const isImage = (f) => /^(png|jpe?g|webp|gif|bmp|image2?|png_pipe|mjpeg)/i.test(String(f.format || ''));
    // 前端的文件对象用的是 `title` + `format`，不是 `name`
    // （`name` 只在后端上传响应里出现，早期版本的探针就是读错了字段，
    //  于是所有文件都被当成"不是音频"，挑中的目标一直是 undefined）。
    const extOf = (f) => String(f.format || f.title || '').split('.').pop().toLowerCase();
    const labelOf = (f) => f.title || f.name || f.id;
    const audio = FILES.filter(f => !isImage(f) && !imageExts.has(extOf(f)));
    const coverable = audio.filter(f => coverExts.has(extOf(f)));
    out.targetPicked = {
      total: FILES.length, audio: audio.length, coverable: coverable.length,
      exts: FILES.map(extOf), audioNames: audio.map(labelOf),
    };
    const targetFor = (op) =>
      (['cover', 'extract-cover', 'remove-cover'].includes(op) && coverable.length
        ? coverable[0]
        : (audio.length ? audio[0] : FILES[0]));
    // 库里一个能内嵌封面的容器都没有时，"三步都成功"这条断言不成立（是数据问题，
    // 不是代码问题），所以要区分开报，不能把种子数据的锅算到代码上。
    out.coverPathTestable = coverable.length > 0;

    FILES.forEach(f => { f.checked = false; });
    const primary = targetFor('convert');
    primary.checked = true;
    out.primaryTarget = labelOf(primary);
    syncBulkbar();
    await wait(150);

    // 逐张卡片走「立即执行」的真实路径（拦截 fetch 看状态码）
    const realFetch = window.fetch;
    const calls = [];
    window.fetch = async (url, init) => {
      const u = String(url);
      let res;
      try { res = await realFetch(url, init); }
      catch (e) { calls.push({ url: u, status: 'ERR ' + e.message }); throw e; }
      if (u.includes('/api/ops/')) calls.push({ url: u.replace(location.origin, ''), status: res.status });
      return res;
    };

    // 封面卡会弹文件选择器：给它一个真实图片，走完上传
    const blob = await realFetch('/api/files').then(r => r.json());
    const png = (() => {
      // 用 canvas 造一张真 PNG
      const c = document.createElement('canvas'); c.width = c.height = 8;
      const x = c.getContext('2d'); x.fillStyle = '#3A7BD5'; x.fillRect(0, 0, 8, 8);
      return new Promise(res => c.toBlob(res, 'image/png'));
    })();
    const realClick = HTMLInputElement.prototype.click;
    HTMLInputElement.prototype.click = function () {
      if (this.type === 'file') {
        const f = new File([out.__pngBlob || new Blob()], 'probe.png', { type: 'image/png' });
        const dt = new DataTransfer(); dt.items.add(f);
        this.files = dt.files;
        this.dispatchEvent(new Event('change', { bubbles: true }));
        return;
      }
      return realClick.apply(this, arguments);
    };
    out.__pngBlob = await png;
    // 标签卡会就地 prompt，给它一个确定答案
    const realPrompt = window.prompt;
    window.prompt = () => 'comment=codeprobe';

    // 69 张卡全部真跑会把 2 并发队列压满、几分钟才排空，
    // 而且"路由/参数是否正确"和"卡片数量"无关 —— 每个 op 验一张就够。
    // 所以这里**按 op 去重**：12 个 op 各挑一张代表卡真执行。
    const byOp = new Map();
    for (const card of CARDS) if (!byOp.has(card.op)) byOp.set(card.op, card);
    const sample = [...byOp.values()];
    out.sampleCount = sample.length;

    // 后端起封面任务是否真的成功。
    // `recent` 里混着**上次运行和种子脚本留下的历史任务**，直接统计会把旧失败
    // 算到这一次头上。所以先记下已经存在的 task id，结束时只看新增的那些。
    const coverTypes = new Set(['cover_embed', 'cover_extract', 'cover_remove']);
    const seenBefore = new Set();
    for (const t of ((await realFetch('/api/tasks/queue').then(r => r.json())).recent || [])) {
      if (coverTypes.has(t.type)) seenBefore.add(t.id);
    }

    for (const card of sample) {
      calls.length = 0;
      const el = [...document.querySelectorAll('#cardSections [data-card]')]
        .find(e => e.dataset.card === card.name);
      if (!el) { failed.push({ name: card.name, why: 'not rendered' }); continue; }
      // 直接调用卡片的执行分支，避免依赖右键菜单定位
      try {
        const params = await resolveCardParams(card);
        if (!params) { failed.push({ name: card.name, op: card.op, why: 'resolveCardParams 返回 null' }); continue; }
        const tgt = targetFor(card.op);
        const r = await API.op(card.op, { fileIds: [tgt.id], ...params });
        picked.push({ name: card.name, op: card.op, file: labelOf(tgt), total: r.total ?? r.count ?? '?' });
      } catch (e) {
        failed.push({ name: card.name, op: card.op, why: String(e.message || e) });
      }
      const bad = calls.filter(c => c.status === 404);
      if (bad.length) failed.push({ name: card.name, op: card.op, why: '404 ' + bad.map(b => b.url).join(',') });
      await wait(120);
    }
    window.fetch = realFetch;
    HTMLInputElement.prototype.click = realClick;
    window.prompt = realPrompt;

    out.executed = picked;
    out.failed = failed;
    out.allOk = failed.length === 0;
    // 全部卡片都要渲染出来（数量与 op 是两回事，这条不能按 op 去重）
    out.totalCards = CARDS.length;

    for (let i = 0; i < 30; i++) {
      const q = await realFetch('/api/tasks/queue').then(r => r.json());
      const fresh = [...(q.recent || [])].filter(t => coverTypes.has(t.type) && !seenBefore.has(t.id));
      out.serverCover = {
        embed: fresh.filter(t => t.type === 'cover_embed').map(t => [t.state, t.error || '']),
        extract: fresh.filter(t => t.type === 'cover_extract').map(t => [t.state, t.error || '']),
        remove: fresh.filter(t => t.type === 'cover_remove').map(t => [t.state, t.error || '']),
      };
      // 封面/提取/删除各至少一条，且都不在 pending，才算跑完
      const done = (a) => a.length && a.every(x => x[0] !== 'pending' && x[0] !== 'running');
      if (done(out.serverCover.embed) && done(out.serverCover.extract) && done(out.serverCover.remove)) break;
      await wait(400);
    }
    // 三步都真的成功了吗（挑的文件若是不支持内嵌封面的容器，这里就会红）
    out.serverCoverOk = ['embed', 'extract', 'remove'].every(
      k => (out.serverCover[k] || []).length && out.serverCover[k].every(x => x[0] === 'success'));
    if (out.coverPathTestable && !out.serverCoverOk) {
      failed.push({ name: '(封面三步)', op: 'cover', why: '有可嵌容器，但 embed/extract/remove 没有全部成功' });
      out.allOk = false;
      out.failed = failed;
    }

    out.err = null;
  } catch (e) { out.err = String((e && e.stack) || e); }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
