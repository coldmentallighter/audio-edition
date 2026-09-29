/* ============================================================================
   AudioEdition · 前端与本地后端的桥接层
     · API 封装
     · 拖拽导入文件 / 文件夹（含 webkitRelativePath 还原目录结构）
     · 单文件右键菜单
     · SSE 实时同步文件与任务状态
   依赖 app.js 暴露的 $ / $$ / toast / renderFiles 等。
   ========================================================================== */
'use strict';

const API = (() => {
  const BASE = '';                                  // 同源：后端同时托管前端
  async function j(method, path, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) {
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body);
    }
    const r = await fetch(BASE + path, opt);
    const text = await r.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = { _raw: text }; }
    if (!r.ok) {
      const msg = data.detail || data.message || data.error || `HTTP ${r.status}`;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    return data;
  }
  return {
    health:      () => j('GET', '/api/health'),
    cards:       () => j('GET', '/api/cards'),
    logs:        (since = 0) => j('GET', `/api/logs?since=${since}`),
    clearLogs:   () => j('DELETE', '/api/logs'),
    files:       () => j('GET', '/api/files'),
    file:        (id) => j('GET', `/api/files/${id}`),
    probe:       (id) => j('POST', `/api/files/${id}/probe`),
    peaks:       (id, b = 1000) => j('GET', `/api/files/${id}/peaks?buckets=${b}`),
    tags:        (id) => j('GET', `/api/files/${id}/tags`),
    putTags:     (id, tags, clearMissing) => j('PUT', `/api/files/${id}/tags`, { tags, clearMissing }),
    del:         (id, withDisk = false) => j('DELETE', `/api/files/${id}?withDisk=${withDisk}`),
    delMany:     (fileIds, withDisk = true) =>
                   j('POST', '/api/files/delete', { fileIds, withDisk }),
    tasks:       () => j('GET', '/api/tasks/queue'),
    task:        (id) => j('GET', `/api/tasks/${id}`),
    cancelTask:  (id) => j('POST', `/api/tasks/${id}/cancel`),
    retryTask:   (id) => j('POST', `/api/tasks/${id}/retry`),
    op:          (name, payload) => j('POST', `/api/ops/${name}`, payload),
    ops:         () => j('GET', '/api/ops'),
    previewCard: (op, params) =>
                   j('GET', `/api/cards/ops/${op}/preview?params=${encodeURIComponent(JSON.stringify(params || {}))}`),
    createCard:  (card) => j('POST', '/api/cards', card),
    updateCard:  (id, card) => j('PUT', `/api/cards/${id}`, card),
    deleteCard:  (id) => j('DELETE', `/api/cards/${id}`),
    snapshots:   () => j('GET', '/api/snapshots'),
    putSnapshots: (names) => j('PUT', '/api/snapshots', { snapshots: names }),
    resetSnapshots: () => j('DELETE', '/api/snapshots'),
    // 任务结果里的 output 是相对项目根的（outputs/waveforms/x.png），
    // 而 /api/outputs/ 收的是相对 outputs/ 的路径 —— 这里统一剥掉前缀
    outputUrl:   (rel, inline = true) => {
                   const clean = String(rel || '').replace(/^\/+/, '').replace(/^outputs\//, '');
                   const path = clean.split('/').map(encodeURIComponent).join('/');
                   return `/api/outputs/${path}?inline=${inline}`;
                 },
    coverUrl:    (id) => `/api/files/${id}/cover`,
    dlUrl:       (id) => `/api/files/${id}/download`,
  };
})();

/* ------------------------------------------------------------ 拖拽导入 */

const DND = (() => {
  let depth = 0;                       // dragenter/leave 会成对乱序，用计数稳住
  let cancelled = false;
  let xhr = null;

  const ALLOW_AUDIO = new Set(['.flac', '.wav', '.mp3', '.m4a', '.aac', '.ogg',
                               '.opus', '.aiff', '.aif', '.wma']);
  const ALLOW_IMAGE = new Set(['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif']);

  const ext = (n) => { const i = n.lastIndexOf('.'); return i < 0 ? '' : n.slice(i).toLowerCase(); };
  const isAllowed = (n) => ALLOW_AUDIO.has(ext(n)) || ALLOW_IMAGE.has(ext(n));

  /** 递归读取拖入的目录项（Chrome/Edge 支持 webkitGetAsEntry） */
  async function readEntry(entry, prefix = '', out = []) {
    if (!entry) return out;
    if (entry.isFile) {
      const file = await new Promise((res, rej) => entry.file(res, rej));
      out.push({ file, rel: prefix + entry.name });
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      // readEntries 一次最多返回 100 条，必须循环取空
      for (;;) {
        const batch = await new Promise((res, rej) => reader.readEntries(res, rej));
        if (!batch.length) break;
        for (const e of batch) await readEntry(e, prefix + entry.name + '/', out);
      }
    }
    return out;
  }

  /** 从 DataTransfer 收集 {file, rel} 列表；不支持 entry 时退化为普通文件 */
  async function collect(dt) {
    const items = Array.from(dt.items || []);
    const entries = items
      .filter((it) => it.kind === 'file')
      .map((it) => (it.webkitGetAsEntry ? it.webkitGetAsEntry() : null));

    if (entries.some(Boolean)) {
      const out = [];
      for (const e of entries) await readEntry(e, '', out);
      if (out.length) return out;
    }
    return Array.from(dt.files || []).map((f) => ({
      file: f,
      rel: f.webkitRelativePath || f.name,
    }));
  }

  /** 带进度与取消的上传（fetch 不支持上传进度，必须用 XHR） */
  function upload(pairs, { onProgress, onDone, onError }) {
    const fd = new FormData();
    const paths = [];
    for (const { file, rel } of pairs) {
      fd.append('files', file, file.name);
      paths.push(rel);
    }
    fd.append('paths', JSON.stringify(paths));

    xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload');
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      xhr = null;
      if (xhr_cancelled) { onDone && onDone(null); return; }
      let data = {};
      try { data = JSON.parse(xhr_text || '{}'); } catch { /* ignore */ }
      onDone && onDone(data);
    };
    let xhr_text = '';
    let xhr_cancelled = false;
    xhr.onreadystatechange = () => {
      if (xhr && xhr.readyState === 4) xhr_text = xhr.responseText || '';
    };
    xhr.onerror = () => { xhr = null; onError && onError(new Error('网络错误')); };
    xhr.onabort = () => { xhr = null; onDone && onDone(null); };
    xhr.send(fd);
    return {
      cancel() { cancelled = true; if (xhr) xhr.abort(); },
    };
  }

  /* -------------------- 事件绑定 -------------------- */

  function bind() {
    const zone = document.getElementById('dropzone');
    const title = document.getElementById('dropTitle');
    const hint = document.getElementById('dropHint');

    const show = (on) => { zone.hidden = !on; };

    window.addEventListener('dragenter', (e) => {
      if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
      e.preventDefault();
      depth++;
      if (depth === 1) {
        title.textContent = '松开以导入';
        hint.textContent = '支持音频文件与整个文件夹';
        show(true);
      }
    });

    window.addEventListener('dragover', (e) => {
      if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
    });

    window.addEventListener('dragleave', (e) => {
      if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) show(false);
    });

    window.addEventListener('drop', async (e) => {
      if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
      e.preventDefault();
      depth = 0;
      show(false);

      title.textContent = '正在读取…';
      hint.textContent = '';

      let pairs = [];
      try {
        pairs = await collect(e.dataTransfer);
      } catch (err) {
        toast('读取失败', String(err.message || err), 'error');
        return;
      }
      if (!pairs.length) { toast('没有可导入的文件', '', 'error'); return; }

      const allowed = pairs.filter((p) => isAllowed(p.file.name));
      const rejected = pairs.length - allowed.length;
      if (!allowed.length) {
        toast('没有支持的格式', `已忽略 ${rejected} 个非音频/图片文件`, 'error');
        return;
      }

      const dirs = new Set(allowed.map((p) => p.rel.split('/').slice(0, -1).join('/')).filter(Boolean));
      runUpload(allowed, rejected, dirs.size);
    });
  }

  function runUpload(pairs, rejected, dirCount) {
    const bar = document.getElementById('uploadbar');
    const label = document.getElementById('uploadLabel');
    const pct = document.getElementById('uploadPct');
    const fill = document.getElementById('uploadFill');
    bar.hidden = false;
    payload_holder = {};

    const job = upload(pairs, {
      onProgress: (t) => {
        const p = Math.round(t * 100);
        fill.style.width = p + '%';
        pct.textContent = p + '%';
        label.textContent = `正在导入 ${pairs.length} 个文件`
          + (dirCount ? ` · ${dirCount} 个目录` : '');
      },
      onDone: async (data) => {
        bar.hidden = true;
        fill.style.width = '0%';
        if (!data) { toast('已取消导入', ''); return; }
        const n = data.count || 0;
        const parts = [`导入 ${n} 个文件`];
        if (dirCount) parts.push(`${dirCount} 个目录`);
        if (rejected) parts.push(`忽略 ${rejected} 个非音频`);
        if ((data.errors || []).length) parts.push(`失败 ${data.errors.length}`);
        toast(n ? '导入完成' : '未导入任何文件', parts.join(' · '),
              n ? 'info' : 'error');
        if ((data.errors || []).length) {
          console.warn('导入失败项:', data.errors);
        }
        // 刷新列表并自动 probe 新文件
        await App.reloadFiles();
        const newIds = (data.saved || []).map((s) => s.id);
        if (newIds.length) App.autoProbe(newIds);
      },
      onError: (err) => {
        bar.hidden = true;
        toast('导入失败', String(err.message || err), 'error');
      },
    });
    payload_holder.job = job;
    document.getElementById('uploadCancel').onclick = () => job.cancel();
  }

  let payload_holder = {};

  return { bind, upload, collect, isAllowed };
})();

/* ------------------------------------------------------------ 右键菜单 */

const CTX = (() => {
  const I = {
    edit:   '<path d="M4 20h4L20 8l-4-4L4 16z"/>',
    convert:'<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
    cover:  '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="12" cy="12" r="3.2"/>',
    extract:'<path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 18v2h16v-2"/>',
    peaks:  '<path d="M3 12h3l2-6 3 12 3-9 2 5h5"/>',
    gain:   '<path d="M12 3v18M5 8v8M19 8v8"/>',
    rename: '<path d="M4 7h10M4 12h7M4 17h10"/><path d="M18 9l3 3-3 3"/>',
    verify: '<path d="M9 12.5l2 2 4.5-5"/><circle cx="12" cy="12" r="9"/>',
    reveal: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    del:    '<path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/>',
  };
  const icon = (k) =>
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
       stroke-linecap="round" stroke-linejoin="round">${I[k] || ''}</svg>`;

  let currentId = null;

  function items(file) {
    const isFlac = String(file.name || '').toLowerCase().endsWith('.flac');
    const hasCover = !!(file.info && file.info.hasCover);
    return [
      { k: 'edit',    label: '编辑元数据',  key: 'Enter',  act: () => openMeta(file.id) },
      { sep: true },
      { k: 'probe',   label: '重新探测',                 act: () => run('probe', file.id) },
      { k: 'convert', label: '转换为…',     key: '⌘1',    act: () => quickConvert(file.id) },
      { k: 'gain',    label: '响度标准化 -16',            act: () => run('normalize', file.id, { targetLufs: -16 }) },
      { k: 'cover',   label: hasCover ? '替换封面…' : '嵌入封面…', act: () => pickCover(file.id) },
      { k: 'extract', label: '提取封面',                  act: () => run('extract-cover', file.id), disabled: !hasCover },
      { k: 'peaks',   label: '重建峰值图',                act: () => run('peaks', file.id) },
      { sep: true },
      { k: 'rename',  label: '按标签重命名',  key: 'F2',   act: () => run('rename', file.id, { pattern: '{artist} - {title}' }) },
      { k: 'verify',  label: 'FLAC 完整性校验',           act: () => run('verify', file.id), disabled: !isFlac },
      { k: 'reveal',  label: '显示所在目录',              act: () => revealFile(file) },
      { sep: true },
      { k: 'del',     label: '删除文件…',    key: 'Del',   act: () => confirmDelete(file), danger: true },
    ];
  }

  function open(x, y, file) {
    currentId = file.id;
    const el = document.getElementById('ctxmenu');
    const rows = items(file).map((it) => {
      if (it.sep) return '<div class="ctxmenu__sep"></div>';
      return `<button class="ctxmenu__item${it.danger ? ' ctxmenu__item--danger' : ''}"
                data-act="${it.k}" ${it.disabled ? 'disabled' : ''} role="menuitem">
                ${icon(it.k)}<span>${it.label}</span>
                ${it.key ? `<kbd>${it.key}</kbd>` : ''}
              </button>`;
    }).join('');
    el.innerHTML =
      `<div class="ctxmenu__head" title="${esc(file.name)}">${esc(file.name)}</div>${rows}`;
    el.hidden = false;
    el.dataset.fileId = file.id;
    el._file = file;
    el._items = items(file);

    // 先显示再量尺寸，避免读到 0
    const r = el.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    el.style.left = Math.max(6, Math.min(x, vw - r.width - 6)) + 'px';
    el.style.top = Math.max(6, Math.min(y, vh - r.height - 6)) + 'px';
  }

  function close() {
    const el = document.getElementById('ctxmenu');
    // onclick 也要清：卡片右键菜单会给同一个元素挂 onclick，
    // 不清掉的话下次打开文件菜单时那个旧处理器还活着
    if (el) { el.hidden = true; el.innerHTML = ''; el._items = null; el.onclick = null; }
    currentId = null;
  }

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  /* -------------------- 动作 -------------------- */

  async function run(opName, fileId, extra) {
    try {
      const r = await API.op(opName, { fileIds: [fileId], ...(extra || {}) });
      toast('已提交', `${labelOf(opName)} · 进入队列`);
      App.refreshQueue();
    } catch (e) {
      toast('提交失败', String(e.message || e), 'error');
    }
  }

  const labelOf = (op) => ({
    probe: '重新探测', peaks: '重建峰值图', normalize: '响度标准化',
    rename: '按标签重命名', verify: '完整性校验',
    'extract-cover': '提取封面', 'remove-cover': '删除封面',
  }[op] || op);

  async function quickConvert(fileId) {
    const fmt = prompt('目标格式（flac / wav / mp3 / m4a / ogg / opus）', 'flac');
    if (!fmt) return;
    const f = fmt.trim().toLowerCase().replace(/^\./, '');
    const allowed = ['flac', 'wav', 'mp3', 'm4a', 'aac', 'ogg', 'opus', 'aiff', 'wma'];
    if (!allowed.includes(f)) { toast('不支持的格式', f, 'error'); return; }
    let extra = { format: f };
    if (f === 'mp3' || f === 'm4a' || f === 'aac' || f === 'ogg' || f === 'opus') {
      const br = prompt('码率（96k/128k/160k/192k/256k/320k）', '192k');
      if (br) extra.bitrate = br.trim();
    }
    if (f === 'flac') {
      const lv = prompt('FLAC 压缩等级 0-8（越高越小越慢）', '5');
      if (lv !== null && lv !== '') extra.compressionLevel = Number(lv);
    }
    run('convert', fileId, extra);
  }

  async function pickCover(fileId) {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = 'image/jpeg,image/png,image/webp,image/bmp';
    inp.onchange = async () => {
      const f = inp.files && inp.files[0];
      if (!f) return;
      // 先把封面图片上传进受管目录，再由后端嵌入（后端只认 uploads/ 内的路径）
      try {
        const res = await new Promise((resolve, reject) => {
          const job = DND.upload([{ file: f, rel: 'covers/' + f.name }], {
            onProgress: () => {},
            onDone: (d) => (d ? resolve(d) : reject(new Error('已取消'))),
            onError: reject,
          });
          void job;
        });
        const img = (res.saved || [])[0];
        if (!img) throw new Error('封面上传失败');
        await API.op('cover', { fileIds: [fileId], imagePath: img.relPath,
                                pictureType: 'Front Cover' });
        toast('已提交', '嵌入封面 · 进入队列');
        App.refreshQueue();
      } catch (e) {
        toast('嵌入封面失败', String(e.message || e), 'error');
      }
    };
    inp.click();
  }

  async function revealFile(file) {
    try {
      await navigator.clipboard.writeText(file.relPath || file.name);
      toast('路径已复制', file.relPath || file.name);
    } catch {
      toast('文件位置', file.relPath || file.name);
    }
  }

  async function confirmDelete(file) {
    if (!confirm(`删除「${file.name}」？\n\n仅从列表移除（软删除）。`)) return;
    try {
      await API.del(file.id, false);
      toast('已移除', file.name);
      App.reloadFiles();
    } catch (e) {
      toast('删除失败', String(e.message || e), 'error');
    }
  }

  /* -------------------- 事件 -------------------- */

  function bind() {
    const el = document.getElementById('ctxmenu');

    el.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-act]');
      if (!btn) return;
      const key = btn.dataset.act;
      const it = (el._items || []).find((x) => x.k === key);
      const file = el._file;
      close();
      if (it && it.act) it.act(file);
    });

    // 右键：只在文件卡片上生效
    document.addEventListener('contextmenu', (e) => {
      const card = e.target.closest('.card');
      if (!card) return;
      e.preventDefault();
      const file = App.fileById(card.dataset.id);
      if (!file) return;
      open(e.clientX, e.clientY, file);
    });

    // 任意点击 / Esc / 滚动 / 失焦都关闭
    document.addEventListener('click', (e) => {
      if (!e.target.closest('#ctxmenu')) close();
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
    window.addEventListener('blur', close);
    window.addEventListener('resize', close);
  }

  return { bind, close };
})();

/* ------------------------------------------------------------ App 桥接 */

const App = (() => {
  let cache = [];                       // 当前文件列表（含 info），供右键菜单查

  async function reloadFiles() {
    try {
      const d = await API.files();
      cache = d.files || [];
      if (typeof window.applyServerFiles === 'function') {
        window.applyServerFiles(cache);
      }
      return cache;
    } catch (e) {
      toast('读取文件列表失败', String(e.message || e), 'error');
      return cache;
    }
  }

  function fileById(id) {
    return cache.find((f) => f.id === id) || null;
  }

  async function autoProbe(ids) {
    for (const id of ids) {
      try { await API.probe(id); } catch { /* 单个失败不影响其他 */ }
    }
    setTimeout(refreshQueue, 300);
    setTimeout(reloadFiles, 1200);
  }

  async function refreshQueue() {
    try {
      const q = await API.tasks();
      if (typeof window.applyServerQueue === 'function') {
        window.applyServerQueue(q);
      }
    } catch { /* 静默 */ }
  }

  /* SSE：状态变化时自动刷新（本地单机，1 秒粒度足够） */
  function startEvents() {
    let es;
    try {
      es = new EventSource('/api/events?interval=1');
    } catch {
      return;
    }
    es.onmessage = (ev) => {
      try {
        const d = JSON.parse(ev.data);
        if (typeof window.applyServerFiles === 'function' && d.files) {
          // SSE 的 files 只有状态，合并进 cache，避免丢掉 info
          const byId = new Map((d.files || []).map((f) => [f.id, f]));
          cache = cache.map((f) => {
            const s = byId.get(f.id);
            return s ? { ...f, state: s.state, tasks: s.tasks } : f;
          });
          window.applyServerFiles(cache);
        }
        if (typeof window.applyServerQueue === 'function') {
          window.applyServerQueue(d.queue);
        }
      } catch { /* ignore */ }
    };
    es.onerror = () => { /* EventSource 会自动重连 */ };
    return es;
  }

  /* 日志：轮询新增（后端日志是进程内环形缓冲，没有推送通道） */
  let lastSeq = 0;
  async function pollLogs() {
    try {
      const d = await API.logs(lastSeq);
      const items = d.logs || [];
      if (items.length && typeof window.applyServerLogs === 'function') {
        window.applyServerLogs(items);
      }
      if (d.lastSeq) lastSeq = d.lastSeq;
    } catch { /* 静默 */ }
  }

  async function boot() {
    CTX.bind();
    DND.bind();
    try {
      const h = await API.health();
      if (!h.ok) {
        const bad = Object.entries(h.tools || {})
          .filter(([, v]) => !v.ok).map(([k]) => k);
        toast('工具链不完整', `缺少：${bad.join('、')}`, 'error');
      }
      if (typeof window.applyServerHealth === 'function') window.applyServerHealth(h);
    } catch (e) {
      // 连不上后端：清空写死数据，明确告知，不显示假数据
      if (typeof window.applyOffline === 'function') window.applyOffline();
      toast('无法连接后端', '请先运行 run.bat / run.sh 启动服务', 'error');
      return;
    }
    // 卡片定义也来自后端
    try {
      const c = await API.cards();
      if (typeof window.applyServerCards === 'function') window.applyServerCards(c);
    } catch { /* 用内置兜底 */ }

    await reloadFiles();
    await refreshQueue();
    await pollLogs();
    startEvents();
    setInterval(pollLogs, 1500);
    setInterval(refreshQueue, 2000);
  }

  return { boot, reloadFiles, refreshQueue, fileById, autoProbe, pollLogs,
           get cache() { return cache; } };
})();

document.addEventListener('DOMContentLoaded', () => { App.boot(); });
