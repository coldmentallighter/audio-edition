/* ============================================================================
   AudioEdition · UI 演示版的「假后端」
   ----------------------------------------------------------------------------
   演示版跑的是**同一份** app.js / api.js / css（见 _build/make_demo.py），
   所以不需要改前端逻辑，只要在这一层把后端换掉：

     · fetch          —— 所有 JSON 接口（文件/卡片/队列/日志/快照…）
     · XMLHttpRequest —— 拖拽导入的上传（带进度）
     · EventSource    —— SSE 状态推送（只在状态**真的变化**时推，与真实实现一致）
     · 媒体 src       —— /api/files/<id>/download 与 /cover 换成本地 assets/
     · <a href="/api/…"> —— 产物链接点了只提示，不去请求

   模拟出来的行为：
     · 提交任何卡片/右键操作都会进队列，2 并发，进度会走，然后成功/失败
     · 任务结束会写日志（措辞与真实后端一致：`▶ probe  01 夜航.flac`）
     · 成功/失败会改文件状态，队列里能看到、失败可以重试
     · 拖进来的文件真的会出现在列表里（元数据与波形是**合成**的）

   **不会**发生的事（演示版就是没有后端）：
     · 不会真的转码、不会真的改标签、不会打开资源管理器、不会下载产物
     · 这些都记一行日志到「日志输出」面板，说明是演示版
   ========================================================================== */
(function () {
  'use strict';

  const D = window.__AE_DEMO__;
  if (!D) { console.error('[demo] 没找到 data/demo-data.js'); return; }

  /* ------------------------------------------------------------------ 小工具 */

  const clone = (o) => JSON.parse(JSON.stringify(o));
  const nowMs = () => Date.now();

  function hashStr(s) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
    return h >>> 0;
  }
  function mulberry(seed) {
    return function () {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  const AUDIO_EXT = ['.flac', '.wav', '.mp3', '.m4a', '.aac', '.ogg', '.opus',
                     '.aiff', '.aif', '.wma'];
  const IMAGE_EXT = ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'];
  const extOf = (n) => { const i = String(n).lastIndexOf('.'); return i < 0 ? '' : String(n).slice(i).toLowerCase(); };

  /* -------------------------------------------------------------------- 状态 */

  const S = {
    files: clone(D.files || []),
    cards: clone(D.cards || []),
    categories: clone(D.categories || []),
    snapshots: clone(D.snapshots || []),
    snapDefaults: clone(D.snapDefaults || []),
    ops: clone(D.ops || []),
    paramTypes: D.paramTypes || [],
    icons: D.icons || [],
    tagFields: D.tagFields || [],
    builtinCount: D.builtinCount || 0,
    tags: clone(D.tags || {}),
    peaks: clone(D.peaks || {}),
    tasks: [],
    logs: [],
    seq: 0,
    counter: 0,
  };

  const byId = (id) => S.files.find((f) => f.id === id) || null;
  const uid = (p) => p + (++S.counter).toString(36).padStart(4, '0') + (nowMs() % 1000).toString(36);

  /* ---- 日志：与后端同构（seq / time / kind / message） ---- */
  function logMsg(message, kind) {
    S.seq += 1;
    const d = new Date();
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    const e = { seq: S.seq, ts: nowMs() / 1000, time: `${hh}:${mm}:${ss}`,
                kind: kind || '', source: 'demo', message };
    S.logs.push(e);
    while (S.logs.length > 400) S.logs.shift();
    return e;
  }
  /** 演示版特有的"这一步不会真的做"提示：进日志面板，不去和 toast 抢位置 */
  function sim(note) { logMsg(`ⓘ 演示版：${note}`, 'warn'); }

  (D.logs || []).forEach((e) => { S.logs.push(e); S.seq = Math.max(S.seq, e.seq || 0); });

  /* ---- 队列：把采集到的真实历史装进来 ---- */
  // 后端 `list_tasks` 是 `ORDER BY created_at DESC`（新的在前），采集到的数组也是这个顺序。
  // 这里翻成"时间正序"存放，之后所有新任务追加在后面，再用 _seq 排序输出 ——
  // 不这么做的话：新任务追加在 12 条旧任务**之后**，而前端 `rows.slice(0, 12)`
  // 只取前 12 条，于是刚提交的任务和它的产物入口都看不见（实测踩过）。
  S.taskSeq = 0;
  (D.queue && D.queue.recent ? D.queue.recent.slice().reverse() : []).forEach((t) => {
    S.tasks.push(Object.assign({ _seq: ++S.taskSeq }, t));
  });

  function nextTaskSeq() { return ++S.taskSeq; }

  /* --------------------------------------------------------------- 任务引擎 */

  // op 路由名 → 任务类型名（队列里显示的是类型名，与真实后端一致）
  const ROUTE_TO_TYPE = {
    probe: 'probe', peaks: 'peaks', convert: 'convert', normalize: 'normalize',
    rename: 'rename', verify: 'verify', zip: 'zip', tags: 'tags',
    cover: 'cover_embed', 'extract-cover': 'cover_extract',
    'remove-cover': 'cover_remove',
  };
  const TICKS = { probe: 2, peaks: 6, convert: 8, normalize: 10, rename: 3,
                  verify: 6, zip: 5, cover_embed: 7, cover_extract: 4,
                  cover_remove: 3, tags: 4 };
  const MAX_RUNNING = 2;                       // 与真实队列一致：2 并发

  function newTask(type, fileId, params) {
    const f = byId(fileId);
    const t = {
      id: uid('t_'), type, fileId,
      fileIds: [fileId],
      state: 'pending', progress: 0, params: params || {},
      result: null, error: '', createdAt: nowMs() / 1000, _seq: nextTaskSeq(),
      _ticks: TICKS[type] || 6, _done: 0, _started: 0,
      _name: f ? f.name : '',
    };
    S.tasks.push(t);
    recompute(fileId);
    return t;
  }

  /** 某个任务跑完后的结果（成功）与它对文件的影响 */
  function finishOk(t) {
    const f = byId(t.fileId);
    const stem = f ? f.name.replace(/\.[^.]+$/, '') : 'file';
    switch (t.type) {
      case 'probe':
        t.result = { format: (f && f.info && f.info.format) || '', duration: (f && f.info && f.info.duration) || 0 };
        break;
      case 'peaks':
        t.result = { output: `outputs/waveforms/${stem}.png`, buckets: 1000 };
        break;
      case 'convert': {
        const fmt = String(t.params.format || 'flac').toLowerCase();
        t.result = { output: `outputs/${stem}.${fmt}`, format: fmt };
        break;
      }
      case 'normalize':
        t.result = { output: `outputs/${stem} (normalized).wav`, targetLufs: t.params.targetLufs || -16 };
        break;
      case 'zip':
        t.result = { output: 'outputs/AudioEdition 打包.zip' };
        break;
      case 'cover_extract':
        t.result = { output: `outputs/covers/${stem}-cover.png` };
        break;
      case 'cover_embed':
        if (f) { f.info = Object.assign({}, f.info, { hasCover: true }); }
        t.result = { cover: t.params.imagePath || 'cover.png' };
        break;
      case 'cover_remove':
        if (f) { f.info = Object.assign({}, f.info, { hasCover: false }); }
        t.result = { removed: true };
        break;
      case 'tags':
        if (f) {
          const merged = Object.assign({}, (f.info && f.info.tags) || {}, t.params.tags || {});
          f.info = Object.assign({}, f.info, { tags: merged });
          S.tags[f.id] = merged;
        }
        t.result = { tags: (f && f.info && f.info.tags) || {} };
        break;
      case 'rename': {
        const pat = String(t.params.pattern || '{filename}');
        const tg = (f && f.info && f.info.tags) || {};
        let name = pat.replace(/\{(\w+)\}/g, (_, k) => (k === 'filename' ? stem : (tg[k] || '')))
                      .replace(/[\s\-_]+$/, '').trim() || stem;
        const ext = f ? extOf(f.name) : '.flac';
        if (f) {
          const parts = f.relPath.split('/');
          parts[parts.length - 1] = name + ext;
          f.name = name + ext;
          f.relPath = parts.join('/');
        }
        t.result = { output: f ? f.relPath : name + ext, renamed: true };
        break;
      }
      case 'verify':
        t.result = { ok: true, output: `${stem}：FLAC 完整性校验通过` };
        break;
      default:
        t.result = { output: `outputs/${stem}.out` };
    }
    return null;
  }

  /** 失败规则：尽量照着真实后端的脾气来 */
  function whyFail(t) {
    const f = byId(t.fileId);
    if (!f) return '文件不存在';
    if (t.type === 'cover_extract' && !(f.info && f.info.hasCover)) return '该文件没有内嵌封面';
    if (t.type === 'cover_embed' && !t.params.imagePath) return '没有指定封面图片';
    if (t.type === 'verify' && extOf(f.name) !== '.flac') return '仅支持 FLAC 文件的完整性校验';
    if (t.type === 'tags' && ['.aac', '.wma'].includes(extOf(f.name)))
      return `${extOf(f.name)} 容器不支持写入标签`;
    return null;
  }

  /** 文件状态 = 它名下所有任务的聚合（字段名对齐后端 fileState/progress/…） */
  function recompute(fid) {
    const f = byId(fid);
    if (!f) return;
    const rows = S.tasks.filter((t) => t.fileId === fid);
    const n = (st) => rows.filter((t) => t.state === st).length;
    const running = n('running'), pending = n('pending');
    const failed = n('failed'), success = n('success');
    const fails = rows.filter((t) => t.state === 'failed');
    const prog = rows.length ? Math.max.apply(null, rows.map((t) => t.progress || 0)) : 100;
    const fileState = (running || pending) ? 'processing'
                    : (failed && !success) ? 'failed'
                    : failed ? 'failed' : 'done';
    f.tasks = {
      fileState, progress: (running || pending) ? prog : 100,
      total: rows.length, running, pending, success, failed,
      lastError: fails.length ? (fails[fails.length - 1].error || '') : '',
    };
    f.state = fileState;
  }

  function startTask(t) {
    t.state = 'running';
    t._started = nowMs();
    logMsg(`▶ ${t.type}  ${t._name}`);
    recompute(t.fileId);
  }

  function endTask(t, error) {
    const secs = ((nowMs() - (t._started || nowMs())) / 1000).toFixed(1);
    if (error) {
      t.state = 'failed'; t.error = error; t.progress = 100;
      logMsg(`✗ ${t.type}  ${t._name} · ${error}`, 'err');
    } else {
      t.state = 'success'; t.progress = 100;
      finishOk(t);
      const out = (t.result && (t.result.output || t.result.cover)) || '';
      logMsg(`✓ ${t.type}  ${t._name}  ${secs}s${out ? '  → ' + out : ''}`, 'ok');
    }
    recompute(t.fileId);
  }

  function tick() {
    let running = S.tasks.filter((t) => t.state === 'running');
    // 先推进
    running.forEach((t) => {
      if (t.state !== 'running') return;
      t._done += 1;
      const pct = Math.min(97, Math.round((t._done / t._ticks) * 100));
      t.progress = Math.max(t.progress, pct);
      if (t._done >= t._ticks) endTask(t, whyFail(t));
      else recompute(t.fileId);
    });
    // 再补位
    running = S.tasks.filter((t) => t.state === 'running');
    S.tasks.filter((t) => t.state === 'pending')
      .slice(0, Math.max(0, MAX_RUNNING - running.length))
      .forEach(startTask);
  }
  setInterval(tick, 380);

  function recentTasks(limit) {
    const done = S.tasks.filter((t) => ['success', 'failed', 'cancelled'].includes(t.state));
    // 新的在前，对齐后端 `ORDER BY created_at DESC`
    done.sort((a, b) => (b._seq || 0) - (a._seq || 0));
    return done.slice(0, limit || 30);
  }
  function queueSnapshot(limit) {
    const running = S.tasks.filter((t) => t.state === 'running');
    const pending = S.tasks.filter((t) => t.state === 'pending');
    const recent = recentTasks(limit);
    return {
      running, pending, recent,
      counts: { running: running.length, pending: pending.length,
                total: running.length + pending.length + recent.length },
    };
  }

  /* 开局故意留一个失败任务：不然"失败/重试"这套界面在演示里根本看不到。
     用「提取封面」打在一个没有封面的 wav 上 —— 真实后端确实会这样失败。 */
  (function seedFailure() {
    const f = S.files.find((x) => x.kind === 'audio' && !(x.info && x.info.hasCover));
    if (!f) return;
    const t = {
      id: uid('t_'), type: 'cover_extract', fileId: f.id, fileIds: [f.id],
      state: 'failed', progress: 100, params: {}, result: null,
      error: '该文件没有内嵌封面', createdAt: nowMs() / 1000, _seq: nextTaskSeq(),
    };
    S.tasks.push(t);
    recompute(f.id);
  })();

  /* ------------------------------------------------------------- 媒体资产映射 */

  const MEDIA = (D.assets && D.assets.media) || {};
  const COVERS = (D.assets && D.assets.covers) || {};
  const mediaList = Object.keys(MEDIA).map((k) => MEDIA[k]);
  const coverList = Object.keys(COVERS).map((k) => COVERS[k]);

  /** 拖进来的新文件没有对应音频资产：轮流借用一份已有的，好让"试听"能用 */
  const mediaFor = (id) => MEDIA[id] || mediaList[hashStr(id) % Math.max(1, mediaList.length)] || '';
  const coverFor = (id) => COVERS[id] || coverList[hashStr(id) % Math.max(1, coverList.length)] || '';

  function rewriteApiUrl(raw) {
    const s = String(raw);
    let m = s.match(/^(.*?)\/api\/files\/([^/?#]+)\/download([?#].*)?$/);
    if (m) return (m[1] || '') + mediaFor(m[2]) + (m[3] || '');
    m = s.match(/^(.*?)\/api\/files\/([^/?#]+)\/cover([?#].*)?$/);
    if (m) return (m[1] || '') + coverFor(m[2]) + (m[3] || '');
    m = s.match(/^(.*?)\/api\/outputs\/(.+?)([?#].*)?$/);
    if (m) {
      const rel = decodeURIComponent(m[2]);
      const asset = /\.(png|jpe?g|webp|gif)$/i.test(rel)
        ? (D.outputSample || 'assets/outputs/sample.png')
        : (mediaList[hashStr(rel) % Math.max(1, mediaList.length)] || '');
      return (m[1] || '') + asset + (m[3] || '');
    }
    return s;
  }

  /** 在 **HTML 字符串** 层面替换 API 资源地址。
   *
   *  必须这么做，不能只补 `HTMLImageElement.prototype.src`：app.js 的封面是
   *  `coverHTML()` 拼出 `<img src="/api/files/<id>/cover?v=…">` 再 innerHTML 的，
   *  解析器写 src **不经过 JS 的 IDL setter**，prototype 补丁根本不会被调用 ——
   *  实测那些请求真的打到了静态服务器（serve.py 的"未拦截的 /api 请求"就是这么抓到的）。
   *  在字符串上换掉还顺带消掉了"先发一个坏请求、再改回来"的竞态。 */
  function rewriteHtml(text) {
    if (typeof text !== 'string' || text.indexOf('/api/') < 0) return text;
    return text.replace(
      /\/api\/(?:files\/[^/?#"'\s]+\/(?:download|cover)|outputs\/[^"'\s>]+)(?:\?[^"'\s>]*)?/g,
      (all) => { const r = rewriteApiUrl(all); return r === all ? all : r; });
  }

  /* 媒体不走 fetch：<audio>/<img> 由浏览器自己加载，所以必须改写地址。
     用 WeakMap 记住"逻辑 URL"，getter 原样返回它 ——
     app.js 里有一句 `if (el.src !== absoluteUrl('/api/files/…/download'))`，
     getter 若返回改写后的地址，它每次都会重设 src，播放会被从头打断。 */
  function patchSrc(proto) {
    const desc = Object.getOwnPropertyDescriptor(proto, 'src');
    if (!desc || !desc.set) return;
    const logical = new WeakMap();
    Object.defineProperty(proto, 'src', {
      configurable: true,
      enumerable: desc.enumerable,
      get() { return logical.has(this) ? logical.get(this) : desc.get.call(this); },
      set(v) {
        logical.set(this, v);
        desc.set.call(this, /\/api\//.test(String(v)) ? rewriteApiUrl(v) : v);
      },
    });
  }
  patchSrc(window.HTMLMediaElement.prototype);
  patchSrc(window.HTMLImageElement.prototype);

  /* 拼字符串建 DOM 的路径（app.js 大量使用） */
  (function patchHtmlSetters() {
    const d = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
    if (d && d.set) {
      Object.defineProperty(Element.prototype, 'innerHTML', {
        configurable: true, enumerable: d.enumerable, get: d.get,
        set(v) { d.set.call(this, rewriteHtml(v)); },
      });
    }
    const iah = Element.prototype.insertAdjacentHTML;
    if (iah) {
      Element.prototype.insertAdjacentHTML = function (pos, text) {
        return iah.call(this, pos, rewriteHtml(text));
      };
    }
    const sa = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function (name, value) {
      if ((name === 'src' || name === 'href') && /\/api\//.test(String(value))) {
        value = rewriteApiUrl(value);
      }
      return sa.call(this, name, value);
    };
  })();

  /* 兜底：还有别的路径漏了就事后改回来。只改"确实还是 /api/"的那些，不会来回抖。 */
  (function watchAttrs() {
    if (!window.MutationObserver) return;
    const fix = (el) => {
      const cur = el.getAttribute('src');
      if (!cur || cur.indexOf('/api/') !== 0) return;
      const r = rewriteApiUrl(cur);
      if (r !== cur) el.setAttribute('src', r);
    };
    new MutationObserver((muts) => {
      muts.forEach((m) => {
        if (m.type === 'attributes') fix(m.target);
        else if (m.addedNodes) {
          m.addedNodes.forEach((n) => {
            if (n.nodeType === 1) {
              fix(n);
              if (n.querySelectorAll) {
                n.querySelectorAll('img[src^="/api/"], audio[src^="/api/"]').forEach(fix);
              }
            }
          });
        }
      });
    }).observe(document.documentElement || document, {
      subtree: true, childList: true, attributes: true, attributeFilter: ['src'],
    });
  })();

  /* ------------------------------------------------------------------ 假 fetch */

  const ABS = (p) => new URL(p, location.href).pathname;

  function json(data, status) {
    return new Response(JSON.stringify(data), {
      status: status || 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  const fail = (status, detail) => json({ detail }, status);

  function submit(route, fileIds, params) {
    const type = ROUTE_TO_TYPE[route];
    if (!type) return { __err: [404, `未知的操作: ${route}`] };
    const ids = (fileIds || []).filter((id) => {
      const f = byId(id);
      return f && f.state !== 'deleted';
    });
    if (!ids.length) return { __err: [400, '没有可操作的文件'] };
    if (type === 'convert' && !params.format) return { __err: [400, '不支持的目标格式: '] };
    logMsg(`▶ ${type}  ${ids.length} 个文件 → ${ids.length} 个任务`);
    const taskIds = ids.map((fid) => newTask(type, fid, params).id);
    return { ok: true, batchId: uid('b_'), taskIds, total: ids.length };
  }

  /** 拖拽导入：把 File 变成库里的行（元数据与波形是合成的） */
  function ingest(files, paths) {
    const saved = [], skipped = [], errors = [];
    files.forEach((file, i) => {
      const rel = paths[i] || file.name;
      const name = rel.split('/').pop();
      const ext = extOf(name);
      if (!AUDIO_EXT.includes(ext) && !IMAGE_EXT.includes(ext)) {
        skipped.push({ name: rel, reason: `扩展名不在白名单: ${ext || '(无)'}` });
        return;
      }
      const id = uid('f_');
      const isAudio = AUDIO_EXT.includes(ext);
      const info = isAudio
        ? { format: ext.slice(1).toUpperCase(), codec: '', duration: 3 + (hashStr(name) % 400) / 100,
            sampleRate: 44100, bits: 16, channels: 2, bitRate: 1411200, size: file.size,
            hasCover: false, tags: {}, error: '' }
        : { format: 'PNG_PIPE', codec: '', duration: 0, sampleRate: 0, bits: 0, channels: 0,
            bitRate: 0, size: file.size, hasCover: true, tags: {}, error: '' };
      S.files.push({
        id, relPath: rel, name, size: file.size, state: 'uploaded', info,
        kind: isAudio ? 'audio' : 'image',
        tasks: { fileState: 'uploaded', progress: 0, total: 0, running: 0,
                 pending: 0, success: 0, failed: 0, lastError: '' },
        createdAt: nowMs() / 1000, updatedAt: nowMs() / 1000, _demo: true,
      });
      if (isAudio) S.peaks[id] = fakePeaks(id, 900);
      S.tags[id] = {};
      logMsg(`⇧ 导入 ${rel}`, 'ok');
      saved.push({ id, name, relPath: rel, size: file.size, state: 'uploaded',
                   kind: isAudio ? 'audio' : 'image' });
    });
    return {
      saved, skipped, errors, count: saved.length,
      message: `导入 ${saved.length} 个` + (skipped.length ? `，跳过 ${skipped.length}` : ''),
    };
  }

  /** 合成一份看着像音乐的波形（拖进来的文件没有真实峰值可算） */
  function fakePeaks(id, n) {
    const r = mulberry(hashStr(id));
    const shape = 0.6 + r() * 0.9;
    const rate = 1.5 + r() * 3.5;
    const out = new Array(n);
    for (let i = 0; i < n; i++) {
      const t = i / n;
      const env = Math.pow(1 - t, shape) * (0.34 + 0.66 * Math.abs(Math.sin(t * Math.PI * rate)));
      const lead = t < 0.03 ? t / 0.03 : 1;
      out[i] = Math.max(0.02, Math.min(1, (0.4 + 0.6 * r()) * env * lead + 0.02));
    }
    return out;
  }

  /* ---- 路由 ---- */
  function route(method, path, url, body) {
    const q = url.searchParams;

    if (method === 'GET' && path === '/api/health') {
      return json({ ok: true, tools: D.health.tools, server: D.health.server,
                    queue: queueSnapshot(0).counts, browser_active: true });
    }
    if (path === '/api/heartbeat') return json({ ok: true });

    if (method === 'GET' && path === '/api/ops') {
      return json({ ops: S.ops, paramTypes: S.paramTypes, icons: S.icons, categories: S.categories });
    }
    if (method === 'GET' && path === '/api/cards') {
      return json({ cards: S.cards, categories: S.categories, snapshots: S.snapshots,
                    builtinCount: S.builtinCount });
    }
    if (method === 'GET' && path === '/api/snapshots') {
      return json({ snapshots: S.snapshots, defaults: S.snapDefaults });
    }
    if (method === 'PUT' && path === '/api/snapshots') {
      const names = (body && body.snapshots) || [];
      const known = new Set(S.cards.map((c) => c.name));
      const clean = names.filter((n) => known.has(n)).slice(0, 5);
      S.snapshots = clean.length ? clean : S.snapshots;
      logMsg(`⇄ 快照已更新：${S.snapshots.join(' / ')}`, 'ok');
      return json({ ok: true, snapshots: S.snapshots });
    }
    if (method === 'DELETE' && path === '/api/snapshots') {
      S.snapshots = clone(S.snapDefaults);
      return json({ ok: true, snapshots: S.snapshots });
    }

    let m = path.match(/^\/api\/cards\/ops\/([^/]+)\/preview$/);
    if (method === 'GET' && m) {
      const op = decodeURIComponent(m[1]);
      let params = {};
      try { params = JSON.parse(q.get('params') || '{}'); } catch (e) { params = {}; }
      const spec = S.ops.find((o) => o.op === op);
      if (!spec) return fail(404, `未知的操作: ${op}`);
      return json({ op, command: renderPreview(spec, params) });
    }

    if (method === 'POST' && path === '/api/cards') {
      const p = body || {};
      if (!p.name || !String(p.name).trim()) return fail(400, '卡片名称不能为空');
      const card = {
        id: uid('c_'), cat: p.cat || '自定义', name: String(p.name).trim(),
        desc: p.desc || '', tier: p.tier || '自定义', ico: p.ico || 'tag',
        op: p.op || 'convert', params: p.params || {}, custom: true,
      };
      S.cards.push(card);
      logMsg(`✚ 新建卡片「${card.name}」· ${card.op}`, 'ok');
      return json({ ok: true, card });
    }
    m = path.match(/^\/api\/cards\/([^/]+)$/);
    if (m && method === 'PUT') {
      const c = S.cards.find((x) => x.id === m[1]);
      if (!c) return fail(404, `卡片不存在: ${m[1]}`);
      if (!c.custom) {
        // 与真实后端一致：内置卡片不能原地改，要用「另存为新卡片」
        return fail(400, '内置卡片不能修改，请用「另存为新卡片」');
      }
      Object.assign(c, body || {});
      logMsg(`✎ 修改卡片「${c.name}」`, 'ok');
      return json({ ok: true, card: c });
    }
    if (m && method === 'DELETE') {
      const i = S.cards.findIndex((x) => x.id === m[1]);
      if (i < 0) return fail(404, `卡片不存在: ${m[1]}`);
      if (!S.cards[i].custom) return fail(400, '内置卡片不能删除');
      const [c] = S.cards.splice(i, 1);
      logMsg(`🗑 删除卡片 ${c.name}`, 'warn');
      return json({ ok: true });
    }

    if (method === 'GET' && path === '/api/files') {
      const rows = S.files.filter((f) => f.state !== 'deleted');
      return json({ files: clone(rows), count: rows.length });
    }
    m = path.match(/^\/api\/files\/([^/]+)$/);
    if (m && method === 'GET') {
      const f = byId(m[1]);
      return f ? json(clone(f)) : fail(404, '文件不存在');
    }
    if (m && method === 'DELETE') {
      const f = byId(m[1]);
      if (!f) return fail(404, '文件不存在');
      f.state = 'deleted';                       // 软删除：行还在，列表里不再出现
      logMsg(`🗑 移除 ${f.name}`, 'warn');
      return json({ ok: true, purged: q.get('purge') === 'true',
                    removedFromDisk: q.get('withDisk') === 'true' });
    }

    m = path.match(/^\/api\/files\/([^/]+)\/probe$/);
    if (m && method === 'POST') {
      const r = submit('probe', [m[1]], {});
      return r.__err ? fail(r.__err[0], r.__err[1]) : json(r);
    }

    m = path.match(/^\/api\/files\/([^/]+)\/peaks$/);
    if (m && method === 'GET') {
      const f = byId(m[1]);
      if (!f) return fail(404, '文件不存在');
      if (f.kind !== 'audio') {
        return fail(415, `${extOf(f.name) || '该文件'} 不是音频，没有波形可算`);
      }
      const pk = S.peaks[f.id];
      if (!pk) return fail(415, '没有可用的峰值数据');
      return json({ fileId: f.id, buckets: pk.length, peaks: pk });
    }

    m = path.match(/^\/api\/files\/([^/]+)\/tags$/);
    if (m && method === 'GET') {
      const f = byId(m[1]);
      if (!f) return fail(404, '文件不存在');
      return json({ tags: S.tags[f.id] || (f.info && f.info.tags) || {}, fields: S.tagFields });
    }
    if (m && method === 'PUT') {
      const f = byId(m[1]);
      if (!f) return fail(404, '文件不存在');
      const tags = (body && body.tags) || {};
      S.tags[f.id] = Object.assign({}, S.tags[f.id] || {}, tags);
      f.info = Object.assign({}, f.info, { tags: S.tags[f.id] });
      logMsg(`✎ 写标签 ${f.name} · ${Object.keys(tags).length} 项`, 'ok');
      return json({ ok: true, method: 'metaflac', written: Object.keys(tags).length,
                    removed: 0, reencoded: false, tags: S.tags[f.id] });
    }

    m = path.match(/^\/api\/files\/([^/]+)\/reveal$/);
    if (m && method === 'POST') {
      const f = byId(m[1]);
      if (!f) return fail(404, '文件不存在');
      const p = '演示工作区\\uploads\\' + f.relPath.replace(/\//g, '\\');
      if (q.get('open') === 'false') {
        return json({ ok: true, path: p, opened: false, revealed: false, kind: 'working-copy' });
      }
      sim(`没有真的打开资源管理器（正式版会对 ${f.name} 执行 explorer /select）`);
      return json({ ok: true, path: p, opened: true, revealed: true, kind: 'working-copy' });
    }

    if (method === 'POST' && path === '/api/files/delete') {
      const ids = (body && body.fileIds) || [];
      const deleted = [], failed = [];
      let freed = 0;
      ids.forEach((id) => {
        const f = byId(id);
        if (!f) { failed.push({ id, error: '文件不存在' }); return; }
        freed += f.size || 0;
        f.state = 'deleted';
        deleted.push({ id, name: f.name });
      });
      logMsg(`🗑 批量删除 ${deleted.length} 个文件`, 'warn');
      return json({ ok: true, deleted, failed, count: deleted.length, freedBytes: freed,
                    withDisk: !!(body && body.withDisk), prunedDirs: 0 });
    }

    if (method === 'GET' && path === '/api/tasks/queue') return json(queueSnapshot(30));
    m = path.match(/^\/api\/tasks\/([^/]+)$/);
    if (m && method === 'GET') {
      const t = S.tasks.find((x) => x.id === m[1]);
      return t ? json(clone(t)) : fail(404, '任务不存在');
    }
    m = path.match(/^\/api\/tasks\/([^/]+)\/(cancel|retry)$/);
    if (m && method === 'POST') {
      const t = S.tasks.find((x) => x.id === m[1]);
      if (!t) return fail(404, '任务不存在');
      if (m[2] === 'cancel') {
        t.state = 'cancelled';
        logMsg(`⏹ ${t.type}  ${t._name || ''} · 已取消`, 'warn');
        recompute(t.fileId);
        return json({ ok: true, id: t.id, state: 'cancelled' });
      }
      const nt = newTask(t.type, t.fileId, t.params);
      return json({ ok: true, taskId: nt.id, retried: t.id });
    }

    m = path.match(/^\/api\/ops\/([^/]+)$/);
    if (m && method === 'POST') {
      const routeName = decodeURIComponent(m[1]);
      const p = body || {};
      const r = submit(routeName, p.fileIds, Object.assign({}, p, { fileIds: undefined }));
      return r.__err ? fail(r.__err[0], r.__err[1]) : json(r);
    }

    if (path === '/api/logs') {
      if (method === 'DELETE') {
        const n = S.logs.length;
        S.logs = [];
        return json({ cleared: n });
      }
      const since = Number(q.get('since') || 0);
      const items = S.logs.filter((e) => e.seq > since).slice(-500);
      return json({ logs: items, lastSeq: items.length ? items[items.length - 1].seq : since,
                    total: S.logs.length });
    }

    return fail(404, `演示版没有这个接口: ${method} ${path}`);
  }

  /** 等价命令预览：把参数代进后端的模板（模板本身来自真实 /api/ops） */
  function renderPreview(spec, params) {
    let cmd = spec.preview || '';
    (spec.params || []).forEach((p) => {
      let v = params[p.key];
      if (v === undefined || v === null || v === '') v = p.default;
      if (p.type === 'bool' && !v) return;
      cmd = cmd.replace(new RegExp('\\{' + p.key + '\\}', 'g'), String(v));
    });
    return cmd.replace(/\{(\w+)\}/g, (_, k) => `<${k}>`);
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    if (!/^\/api\//.test(url.pathname)) return realFetch(input, init);
    let body = null;
    if (init && init.body) { try { body = JSON.parse(init.body); } catch (e) { body = null; } }
    try {
      return Promise.resolve(route(method, ABS(url.pathname), url, body));
    } catch (e) {
      console.error('[demo] mock 路由出错', method, url.pathname, e);
      return Promise.resolve(fail(500, '演示版内部错误: ' + e.message));
    }
  };

  /* ------------------------------------------------------------------ 假 XHR */

  const RealXHR = window.XMLHttpRequest;
  function DemoXHR() {
    this.upload = {};
    this.readyState = 0; this.status = 0; this.responseText = '';
    this._aborted = false;
  }
  DemoXHR.prototype.open = function (m, u) { this.method = m; this.url = u; this.readyState = 1; };
  DemoXHR.prototype.setRequestHeader = function () {};
  DemoXHR.prototype.getAllResponseHeaders = function () { return ''; };
  DemoXHR.prototype.send = function (body) {
    if (!/\/api\/upload/.test(String(this.url))) {           // 非上传：交给真 XHR
      const x = new RealXHR();
      const self = this;
      x.open(this.method, this.url, true);
      x.onload = () => { self.status = x.status; self.responseText = x.responseText;
                         self.readyState = 4; if (self.onload) self.onload(); };
      x.onerror = () => { if (self.onerror) self.onerror(); };
      x.send(body);
      return;
    }
    const files = (body && body.getAll) ? body.getAll('files') : [];
    let paths = [];
    try { paths = JSON.parse((body && body.get && body.get('paths')) || '[]'); } catch (e) { paths = []; }
    let pct = 0;
    const timer = setInterval(() => {
      if (this._aborted) { clearInterval(timer); return; }
      pct = Math.min(1, pct + 0.14 + Math.random() * 0.12);
      if (this.upload.onprogress) {
        this.upload.onprogress({ lengthComputable: true, loaded: pct, total: 1 });
      }
      if (pct < 1) return;
      clearInterval(timer);
      const res = ingest(files, paths);
      this.status = 200;
      this.responseText = JSON.stringify(res);
      this.readyState = 4;
      if (this.onreadystatechange) this.onreadystatechange();
      if (this.onload) this.onload();
    }, 110);
  };
  DemoXHR.prototype.abort = function () {
    this._aborted = true;
    if (this.onabort) this.onabort();
  };
  window.XMLHttpRequest = DemoXHR;

  /* -------------------------------------------------------------- 假 EventSource */

  function DemoEventSource(url) {
    this.url = url; this.readyState = 0; this.onmessage = null; this.onerror = null;
    this._last = '';
    const self = this;
    this._timer = setInterval(() => {
      const snap = JSON.stringify({
        queue: queueSnapshot(30),
        files: S.files.filter((f) => f.state !== 'deleted')
          .map((f) => ({ id: f.id, state: f.state, tasks: f.tasks })),
      });
      // 与真实实现一致：**只在真的变了**才推。
      // 每 1s 无脑推会让 applyServerFiles 反复重绘（勾选也会被反复冲刷）。
      if (snap === self._last) return;
      self._last = snap;
      if (self.onmessage) self.onmessage({ data: snap });
    }, 1000);
    setTimeout(() => { self.readyState = 1; }, 0);
  }
  DemoEventSource.prototype.addEventListener = function (t, fn) {
    if (t === 'message') this.onmessage = fn;
  };
  DemoEventSource.prototype.removeEventListener = function () {};
  DemoEventSource.prototype.close = function () { clearInterval(this._timer); this.readyState = 2; };
  window.EventSource = DemoEventSource;

  /* ------------------------------------------------ 产物链接 / 剪贴板 / 角标 */

  // <a href="/api/outputs/…"> 是浏览器自己发请求的，拦不到 fetch：
  // 直接拦点击，说明"演示版没有真的产出文件"，别让它 404。
  document.addEventListener('click', (e) => {
    const a = e.target && e.target.closest && e.target.closest('a[href^="/api/"]');
    if (!a) return;
    e.preventDefault();
    e.stopPropagation();
    const href = a.getAttribute('href') || '';
    sim(`没有真的生成产物文件（${decodeURIComponent(href).slice(0, 60)}…）`);
    if (window.toast) toast('演示版', '产物文件不会真的下载', 'info');
  }, true);

  if (!navigator.clipboard) {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: () => Promise.resolve() },
    });
  } else if (!navigator.clipboard.writeText) {
    navigator.clipboard.writeText = () => Promise.resolve();
  }

  function mountBadge() {
    const b = document.getElementById('demoBadge');
    if (!b) return;
    const close = document.getElementById('demoBadgeClose');
    if (close) close.addEventListener('click', () => { b.hidden = true; });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mountBadge);
  } else {
    mountBadge();
  }

  logMsg('ⓘ 演示版已就绪：内置样例数据，操作不会真的读写音频文件', 'warn');

  /* 给自检探针用的内部入口（页面逻辑不依赖它） */
  window.__AE_DEMO_API__ = {
    state: S,
    queue: queueSnapshot,
    submit,
    rewriteApiUrl,
    files: () => S.files.filter((f) => f.state !== 'deleted'),
    tasks: () => S.tasks,
    logs: () => S.logs,
    ingest,
  };
})();
