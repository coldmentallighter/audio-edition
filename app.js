/* ============================================================================
   AudioEdition 前端 · 视图与交互
   数据全部来自 FastAPI 后端；页面启动时 FILES / TASKS / LOGS 均为空数组，
   断网时走 window.applyOffline()，只显示空状态，不伪造任何数据。
   配色契约（见 theme.css 顶部）：bg 与 tint 系列配 --ink，fill 系列配 --on-fill
   ========================================================================== */

'use strict';

/* ------------------------------------------------------------------ 工具 */

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** 确定性伪随机：让波形每次刷新一致 */
function mulberry32(seed) {
  return function () {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashStr(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function fmtDur(sec) {
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** 从 theme.css 的令牌里取色，保证波形跟随主题 */
function tok(name, alpha) {
  const v = cssVar(name);
  if (!alpha || alpha >= 1) return v;
  // 令牌可能是 #RRGGBB 或 rgb(r g b / a)
  if (v.startsWith('#')) {
    const n = parseInt(v.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
  }
  return v;
}

/* ------------------------------------------------- 运行时数据（全空） */

/*
 * 这三张表一律从后端来，页面启动时必须是空的：
 *   FILES ← GET /api/files      (window.applyServerFiles)
 *   TASKS ← GET /api/tasks      (window.applyServerQueue)
 *   LOGS  ← GET /api/logs       (window.applyServerLogs)
 * 之前这里写死过示例数据，导致断网或首帧时页面显示看似真实的假文件/假日志。
 * 现在空数组 + renderEmptyState() 明确提示，绝不再伪造。
 */
const FILES = [];
const TASKS = [];
const LOGS = [];

/**
 * 卡片目录 / 快照：**全部来自后端**，前端不再留兜底表。
 *
 * 这里曾经放了一份本地副本当"断网兜底"，但它是纯粹的漂移源：
 * 服务端补了两张波形卡之后它没跟着变（13 vs 15），而且按项目
 * "断网就明确说不可用、不伪造数据"的原则，断网时显示一排
 * 点下去执行不了的卡片，本身就是假数据。
 *
 * CATEGORIES 保留一个引导值：/api/ops 回来之前 renderCardSections()
 * 需要有东西可遍历；loadOpsCatalog() 拿到真值后会整体替换。
 */
let CATEGORIES = ['格式转换', '元数据', '封面', '响度', '峰值', '校验/打包', '自定义'];

const CARDS = [];
const SNAPS = [];

/** 后端连不上时置位，用来显示明确提示而不是一片空白 */
let offlineMode = false;

const STATUS_LABEL = {
  uploaded: '待分析', ready: '就绪', processing: '处理中',
  done: '已完成', failed: '失败', pending: '排队中',
};
const STATUS_BADGE = {
  uploaded: 'badge--idle', ready: 'badge--done', processing: 'badge--run',
  done: 'badge--done', failed: 'badge--failed', pending: 'badge--idle',
};

/* ---------------------------------------------------------------- 图标 */

const ICON = {  flac:   '<path d="M4 14v-4M8 17V7M12 20V4M16 17V7M20 14v-4"/>',
  mp3:    '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
  wav:    '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M7 9v6M11 7v10M15 9v6M19 11v2"/>',
  wave:   '<path d="M2 12h3l2-6 3 12 3-9 2 5h7"/>',
  tag:    '<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0l-7.2-7.2A2 2 0 0 1 2.8 12V4.8A2 2 0 0 1 4.8 2.8H12a2 2 0 0 1 1.4.6l7.2 7.2a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1.3"/>',
  cover:  '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="12" cy="12" r="3.2"/><circle cx="12" cy="12" r=".6"/>',
  gain:   '<path d="M12 3v18M5 8v8M19 8v8M8.5 5.5v13M15.5 5.5v13"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  zip:    '<rect x="4" y="3" width="16" height="18" rx="2.5"/><path d="M12 3v6M12 12v1.5M12 16.5v1.5"/>',
  check:  '<path d="M9 12.5l2 2 4.5-5"/><circle cx="12" cy="12" r="9"/>',
  play:   '<path d="M7 4.5l12 7.5-12 7.5z" fill="currentColor" stroke-width="1"/>',
  pause:  '<path d="M8.5 4.5v15M15.5 4.5v15" stroke-width="2.4"/>',
  vol:    '<path d="M4 9.5h3.2L11 6v12l-3.8-3.5H4z"/><path d="M15 9.5a4 4 0 0 1 0 5"/><path d="M17.6 7a7.5 7.5 0 0 1 0 10"/>',
  volLow: '<path d="M4 9.5h3.2L11 6v12l-3.8-3.5H4z"/><path d="M15 9.5a4 4 0 0 1 0 5"/>',
  volMute:'<path d="M4 9.5h3.2L11 6v12l-3.8-3.5H4z"/><path d="M15.5 9.5l5 5M20.5 9.5l-5 5"/>',
};

/**
 * 未命中的图标会回落成波形 —— 这条回落**必须留**（不能因为少个 SVG 就让
 * 整张卡片渲染不出来），但不能**静默**：同一个 key 只警告一次，
 * 免得控制台被刷屏，同时又能立刻发现"后端加了图标、前端没加 SVG"。
 */
const _missingIcons = new Set();
const svg = (k, sw = 1.8) => {
  if (!ICON[k] && !_missingIcons.has(k)) {
    _missingIcons.add(k);
    console.warn(`[icon] 没有 "${k}" 的 SVG，已回落成波形。`
      + '请同步 app.js 的 ICON 与 backend/cards.py 的 CARD_ICONS。');
  }
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}"`
       + ` stroke-linecap="round" stroke-linejoin="round">${ICON[k] || ICON.wave}</svg>`;
};

/* ---------------------------------------------------------- 渲染：侧栏 */

function renderQueue() {
  const box = $('#queueList');
  if (!TASKS.length) {
    box.innerHTML = `
      <div class="empty empty--sm">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.2 2.2M16.2 16.2l2.2 2.2M18.4 5.6l-2.2 2.2M7.8 16.2l-2.2 2.2"/>
        </svg>
        <strong>队列为空</strong>
        <span>选中文件后执行卡片操作</span>
      </div>`;
  } else {
    box.innerHTML = TASKS.map(t => {
      const badge = { running: 'badge--run', success: 'badge--done', failed: 'badge--failed', pending: 'badge--idle' }[t.state] || 'badge--idle';
      const label = { running: '执行中', success: '成功', failed: '失败', pending: '排队' }[t.state];
      return `<article class="task task--${t.state === 'success' ? 'done' : t.state}">
        <div class="task__top">
          <span class="badge ${badge}">${label}</span>
          <span class="task__name" title="${t.name}">${t.name}</span>
        </div>
        <div class="task__meta">${t.meta}${t.eta ? ' · ' + t.eta : ''}</div>
        ${t.state === 'running' ? `<div class="progress"><i style="width:${t.pct}%"></i></div>` : ''}
        ${t.err ? `<div class="task__err">${t.err}</div>` : ''}
        ${t.output
          ? `<div class="task__meta task__out">
               <a class="btn btn--xs" href="${API.outputUrl(t.output)}" target="_blank"
                  rel="noopener" title="打开 outputs/${escHtml(t.output)}">打开产物</a>
               <a class="btn btn--xs btn--ghost" href="${API.outputUrl(t.output, false)}"
                  download title="下载到本地">下载</a>
             </div>` : ''}
        ${t.state === 'failed' && t.retryId != null
          ? `<div class="task__meta" style="margin-top:6px"><button class="btn btn--xs" data-retry="${t.retryId}">重试</button></div>` : ''}
      </article>`;
    }).join('');
  }
  // 聚合数字从真实 TASKS 推导；连上后端后由 applyServerQueue() 用服务端 counts 覆盖
  const n = (s) => TASKS.filter(t => t.state === s).length;
  const agg = $('#queueAgg');
  if (agg) agg.textContent = `运行 ${n('running')} · 排队 ${n('pending')} · 成功 ${n('success')} · 失败 ${n('failed')}`;
}

function renderLogs() {
  $('#logList').innerHTML = LOGS.map(l =>
    `<div class="log ${l.k ? 'log--' + l.k : ''}"><span class="log__t">${l.t}</span><span class="log__m">${l.m}</span></div>`
  ).join('');
}

/** 追加一行日志（并滚动到底） */
function addLog(msg, kind = '') {
  const now = new Date();
  const t = [now.getHours(), now.getMinutes(), now.getSeconds()]
    .map(n => String(n).padStart(2, '0')).join(':');
  LOGS.push({ t, m: msg, k: kind });
  renderLogs();
  const box = $('#logList');
  if (box) box.scrollTop = box.scrollHeight;
}

/** 秒 → m:ss（超过一小时给 h:mm:ss）；未知时长给 --:--，
 *  别显示 0:00 —— 那看起来像一个长度为零的坏文件 */
function fmtClock(sec, unknown = '--:--') {
  const v = Number(sec);
  if (!isFinite(v) || v <= 0) return unknown;
  const s = Math.max(0, Math.floor(v));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
  const p = (n) => String(n).padStart(2, '0');
  return h ? `${h}:${p(m)}:${p(ss)}` : `${m}:${p(ss)}`;
}

/* ------------------------------------------------------ 渲染：文件卡片 */

/**
 * 封面格。
 * 有内嵌封面就直接显示真图（GET /api/files/{id}/cover，后端从音频里抽出来），
 * 加载失败（比如那张图其实解不出来）或本来就没有 → 退回占位图标。
 * 整格可点：无封面 → 导入；有封面 → 换一张。
 */
const COVER_V = {};   // id → 时间戳，换过封面后用来破浏览器缓存

function coverHTML(f) {
  const set = `<button class="cover__act" data-cover-set="${f.id}" title="导入封面">更换</button>`;
  const del = `<button class="cover__act cover__act--danger" data-cover-del="${f.id}" title="移除内嵌封面">移除</button>`;
  const img = f.cover
    ? `<img class="cover__img" src="/api/files/${f.id}/cover?v=${COVER_V[f.id] || 0}"
            alt="${escHtml(f.title)} 的封面" loading="lazy"
            data-cover-img="${f.id}">`
    : '';
  return `<div class="cover${f.cover ? ' cover--has' : ''}" data-cover="${f.id}"
               title="${f.cover ? '已嵌入封面 · 点击更换' : '无封面 · 点击导入'}">
      ${img}
      <span class="cover__ph">${svg('cover')}</span>
      <span class="cover__ovl">${f.cover ? set + del : set.replace('更换', '导入')}</span>
    </div>`;
}

function renderFiles() {
  $('#fileList').innerHTML = FILES.map(f => `
  <article class="card${f.checked ? ' is-checked' : ''}" data-id="${f.id}" tabindex="0">
    <label class="check card__pick" title="选中以批量操作">
      <input type="checkbox" data-check="${f.id}" ${f.checked ? 'checked' : ''}>
      <span></span>
    </label>

    <div class="card__body">
      <!-- 波形区 -->
      <div class="wave">
        <div class="wave__canvasWrap">
          <canvas class="wave__canvas" data-wave="${f.id}" aria-label="${f.title} 的全曲峰值图"
                  title="点击波形跳转到该位置"></canvas>
          <div class="wave__legend">L / R 峰值</div>
          <div class="wave__play" data-play="${f.id}"${playingId === f.id ? '' : ' hidden'}></div>
        </div>
        <div class="wave__meta">
          <div class="transport">
            <button class="transport__btn" data-play-toggle="${f.id}"
                    aria-label="${playingId === f.id ? '暂停' : '试听'} ${f.title}"
                    title="${playHint(f)}">${playingId === f.id ? svg('pause') : svg('play')}</button>
            <span class="transport__time" data-time="${f.id}">${fmtClock(0, '0:00')} / ${fmtClock(f.dur)}</span>
          </div>
          <span class="kv"><span class="kv__k">Loudness</span><span class="kv__v">${f.loudness} LUFS</span></span>
          <span class="kv"><span class="kv__k">Type</span><span class="kv__v">${f.format}</span></span>
          <span class="kv"><span class="kv__k">Duration</span><span class="kv__v">${fmtDur(f.dur)}</span></span>
          <span class="kv"><span class="kv__k">SampleRate</span><span class="kv__v">${(f.rate / 1000).toFixed(1)} kHz</span></span>
          <span class="kv"><span class="kv__k">Bitdepth</span><span class="kv__v">${f.depth ? f.depth + ' bit' : '—'}</span></span>
          <span class="kv"><span class="kv__k">Channel</span><span class="kv__v">${f.ch}</span></span>
        </div>
      </div>

      <!-- 元数据卡：点击整块区域即可编辑 -->
      <div class="metacard">
        ${coverHTML(f)}
        <div class="metabox" data-edit="${f.id}" role="button" tabindex="0"
             title="点击编辑元数据" aria-label="编辑 ${f.title} 的元数据">
          <div class="metabox__row"><span class="metabox__k">Title</span><span class="metabox__v" title="${f.title}">${f.title}</span></div>
          <div class="metabox__row"><span class="metabox__k">Artist</span><span class="metabox__v">${f.artist}</span></div>
          <div class="metabox__row"><span class="metabox__k">Album</span><span class="metabox__v">${f.album}</span></div>
          <div class="metabox__row"><span class="metabox__k">Track</span><span class="metabox__v">${f.track} / ${f.size}</span></div>
        </div>
      </div>

      <!-- 状态条 -->
      <div class="statuscol">
        <span class="badge ${STATUS_BADGE[f.status]}">${STATUS_LABEL[f.status]}</span>
        ${f.status === 'processing'
          ? `<div class="progress statuscol__prog"><i style="width:${f.progress}%"></i></div><span class="statuscol__info">${f.progress}%</span>`
          : f.status === 'failed'
            ? `<span class="statuscol__info">退出码 1</span><button class="statuscol__act">重试</button>`
            : `<span class="statuscol__info">峰值图<br>${f.peaks === 'cached' ? '已缓存' : f.peaks === 'pending' ? '待生成' : '未分析'}</span>`}
      </div>
    </div>
  </article>`).join('');

  $$('[data-wave]').forEach(cv => drawWave(cv, cv.dataset.wave));
}

/* ------------------------------------------------------ 卡片编辑器 */

/**
 * 操作目录（/api/ops）：每个 op 认哪些参数、取值、默认值、说明、命令模板。
 * 编辑器靠它把表单渲染出来，用户不必去翻源码猜参数。
 */
let OPS_CATALOG = null;      // op → spec
let CARD_ICONS = [];
let editingCard = null;      // 正在编辑的卡片快照（含 custom 标记）
let editingIsNew = false;

async function loadOpsCatalog() {
  if (OPS_CATALOG) return OPS_CATALOG;
  const d = await API.ops();
  OPS_CATALOG = {};
  (d.ops || []).forEach(o => { OPS_CATALOG[o.op] = o; });
  CARD_ICONS = d.icons || [];
  // 分类也要同步：CATEGORIES 原来是写死的，后端新增一个分类时，
  // 落到该分类的卡片既不显示也不报错。这里按后端顺序重建，
  // 「自定义」固定放最后（那是「新建卡片」入口所在的段落）。
  if (Array.isArray(d.categories) && d.categories.length) {
    CATEGORIES.length = 0;
    d.categories.filter(c => c && c !== '自定义').forEach(c => CATEGORIES.push(c));
    CATEGORIES.push('自定义');
    const inp = $('#cardSearch');
    renderCardSections(inp ? inp.value : '');
  }
  return OPS_CATALOG;
}

/** 参数的 onlyIf 是否成立（比如 compressionLevel 只在 flac 时才有意义） */
function paramApplies(spec, params) {
  if (!spec.onlyIf) return true;
  const cur = params[spec.onlyIf.key];
  return spec.onlyIf.in.map(String).includes(String(cur ?? ''));
}

/** 从表单里读出当前参数值 */
function collectParams() {
  const out = {};
  $$('#cardParams [data-pkey]').forEach(el => {
    const key = el.dataset.pkey;
    const type = el.dataset.ptype;
    if (type === 'bool') out[key] = el.checked;
    else if (type === 'int') out[key] = el.value === '' ? '' : Number(el.value);
    else if (type === 'float') out[key] = el.value === '' ? '' : Number(el.value);
    else out[key] = el.value;
  });
  return out;
}

/** 渲染参数表单。op 变化或某个参数影响 onlyIf 时重渲染。 */
function renderParamForm(op, values) {
  const box = $('#cardParams');
  const spec = OPS_CATALOG && OPS_CATALOG[op];
  if (!spec) { box.innerHTML = '<p class="chain__empty">先选一个基础操作</p>'; return; }
  if (!spec.params.length) {
    box.innerHTML = '<p class="chain__empty">这个操作没有可调参数，直接保存即可。</p>';
    return;
  }
  // 先把默认值铺一遍再判断 onlyIf：否则新建时 format 还没值，
  // 「FLAC 压缩等级」会被判成"用不到"而隐藏（而默认格式恰恰就是 flac）
  const vals = {};
  spec.params.forEach(p => { if (p.default !== undefined) vals[p.key] = p.default; });
  Object.assign(vals, values || {});

  box.innerHTML = spec.params.map(p => {
    const on = paramApplies(p, vals);
    const v = vals[p.key] !== undefined ? vals[p.key] : p.default;
    const need = p.required ? '<em>必填</em>' : '';
    let ctl = '';

    if (!on) {
      const who = p.onlyIf ? (OPS_CATALOG[op].params.find(x => x.key === p.onlyIf.key) || {}).label : '';
      return `<div class="pspec pspec--off" data-prow="${p.key}">
          <div class="pspec__label">${escHtml(p.label)}${need}</div>
          <div class="pspec__ctl"><span class="pspec__na">当前「${escHtml(who || '')}」用不到这项</span></div>
        </div>`;
    }

    if (p.type === 'enum') {
      ctl = `<select class="input" data-pkey="${p.key}" data-ptype="enum">${
        (p.options || []).map(o =>
          `<option value="${escHtml(o.value)}"${String(o.value) === String(v ?? '') ? ' selected' : ''}>${
            escHtml(o.label)}</option>`).join('')}</select>`;
    } else if (p.type === 'bool') {
      ctl = `<label class="pspec__check">
          <input type="checkbox" data-pkey="${p.key}" data-ptype="bool"${v ? ' checked' : ''}>
          <span>${v ? '已开启' : '已关闭'}</span></label>`;
    } else if (p.type === 'tags') {
      const text = (v && typeof v === 'object')
        ? Object.entries(v).map(([k, x]) => `${k}=${x}`).join('\n')
        : String(v || '');
      ctl = `<textarea class="input" data-pkey="${p.key}" data-ptype="tags"
                placeholder="album=合集&#10;genre=民谣">${escHtml(text)}</textarea>`;
    } else if (p.type === 'int' || p.type === 'float') {
      const step = p.type === 'int' ? '1' : 'any';
      const mm = `${p.min !== undefined ? ` min="${p.min}"` : ''}${p.max !== undefined ? ` max="${p.max}"` : ''}`;
      ctl = `<input class="input" type="number" step="${step}"${mm}
                data-pkey="${p.key}" data-ptype="${p.type}"
                value="${v === undefined || v === null ? '' : v}"
                placeholder="${escHtml(String(p.default ?? ''))}">`;
    } else {
      ctl = `<input class="input" data-pkey="${p.key}" data-ptype="text"
                value="${escHtml(String(v ?? ''))}"
                placeholder="${escHtml(p.placeholder || '')}">`;
    }

    // 取值范围/选项直接写进说明，省得用户试
    let range = '';
    if (p.type === 'int' || p.type === 'float') {
      if (p.min !== undefined && p.max !== undefined) range = `<code>${p.min} ~ ${p.max}</code> `;
    }
    const def = (p.default !== undefined && p.default !== '' && p.type !== 'tags')
      ? ` 默认 <code>${escHtml(String(p.default))}</code>` : '';

    return `<div class="pspec" data-prow="${p.key}">
        <div class="pspec__label">${escHtml(p.label)}${need}</div>
        <div class="pspec__ctl">${ctl}</div>
        <div class="pspec__desc">${range}${escHtml(p.desc || '')}${def}</div>
      </div>`;
  }).join('');
}

/** 命令预览：交给后端渲染，保证和真正执行的白名单一致 */
let previewTimer = null;
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(refreshPreview, 180);
}
async function refreshPreview() {
  const op = $('#cardOp').value;
  const el = $('#cardPreview');
  if (!op) { el.textContent = '—'; return; }
  try {
    const d = await API.previewCard(op, collectParams());
    el.textContent = d.command || '—';
  } catch (e) {
    el.textContent = `预览失败：${e.message || e}`;
  }
}

function renderIconPick(selected) {
  $('#cardIcons').innerHTML = CARD_ICONS.map(k =>
    `<button type="button" class="iconpick__btn" role="radio" data-ico="${k}"
             aria-checked="${k === selected}" title="${k}">${svg(k)}</button>`).join('');
}

function showCardErr(msg) {
  const el = $('#cardErr');
  if (!msg) { el.hidden = true; el.textContent = ''; return; }
  el.hidden = false;
  el.textContent = msg;
}

/**
 * 打开编辑器。
 * @param card  预填的卡片；null = 全新
 * @param mode  'edit' 原地编辑 | 'saveas' 另存为新卡片 | 'new' 新建
 */
async function openCardEditor(card, mode) {
  if (typeof API === 'undefined') { toast('后端未连接', '无法编辑卡片', 'error'); return; }
  try { await loadOpsCatalog(); }
  catch (e) { toast('读取操作目录失败', String(e.message || e), 'error'); return; }

  editingIsNew = (mode !== 'edit');
  const isBuiltin = !!(card && !card.custom);
  editingCard = card ? { ...card } : null;

  const opKeys = Object.keys(OPS_CATALOG);
  const op = (card && card.op) || opKeys[0];
  const name = card ? (mode === 'saveas' ? `${card.name} 副本` : card.name) : '';

  $('#cardModalTitle').textContent =
    mode === 'new' ? '新建卡片'
    : mode === 'saveas' ? '另存为新卡片'
    : (isBuiltin ? '内置卡片 → 另存为新卡片' : '编辑卡片');

  $('#cardModalSub').textContent =
    mode === 'new' ? '选一个基础操作，填参数，起个名字就能用'
    : mode === 'saveas' ? `基于「${card.name}」创建一张新的自定义卡片`
    : isBuiltin ? '内置卡片不能直接改，保存会创建一张新的自定义卡片'
    : `自定义卡片 · ${card.id}`;

  $('#cardName').value = name;
  $('#cardDesc').value = card ? card.desc : '';

  // 分类：已有的 + 自定义
  $('#cardCat').innerHTML = CATEGORIES.map(c =>
    `<option value="${escHtml(c)}"${card && card.cat === c ? ' selected' : ''}>${escHtml(c)}</option>`).join('');
  if (!card) $('#cardCat').value = '自定义';

  // 基础操作
  $('#cardOp').innerHTML = opKeys.map(k =>
    `<option value="${k}"${k === op ? ' selected' : ''}>${escHtml(OPS_CATALOG[k].label)} · ${k}</option>`).join('');

  $('#cardOpDesc').textContent = OPS_CATALOG[op].desc || '';
  renderIconPick((card && card.ico) || OPS_CATALOG[op].icon);
  renderParamForm(op, card ? card.params : null);
  showCardErr('');

  const del = $('#cardDelete');
  del.hidden = !(card && card.custom && mode === 'edit');

  const saveAs = $('#cardSaveAs');
  // 内置卡片走「保存」就已经是新建了，再摆一个「另存为」纯属重复
  saveAs.hidden = (mode === 'new') || !(card && card.custom);

  $('#cardSave').textContent = (mode === 'edit' && card && card.custom) ? '保存' : '创建卡片';

  openModal($('#cardModal'));
  refreshPreview();
  setTimeout(() => $('#cardName').focus(), 30);
}

/** 收集表单 → 卡片对象 */
function readCardForm(id) {
  return {
    id,
    name: $('#cardName').value.trim(),
    cat: $('#cardCat').value,
    desc: $('#cardDesc').value.trim(),
    ico: ($('#cardIcons [aria-checked="true"]') || {}).dataset
          ? $('#cardIcons [aria-checked="true"]').dataset.ico : undefined,
    op: $('#cardOp').value,
    params: collectParams(),
  };
}

async function saveCard(asNew) {
  const card = editingCard;
  const isCustomEdit = !asNew && card && card.custom && !editingIsNew;
  const payload = readCardForm(isCustomEdit ? card.id : undefined);
  if (!payload.name) { showCardErr('卡片名称不能为空'); return; }

  const btn = asNew ? $('#cardSaveAs') : $('#cardSave');
  btn.disabled = true;
  showCardErr('');
  try {
    const r = isCustomEdit
      ? await API.updateCard(card.id, payload)
      : await API.createCard(payload);
    addLog(`✚ 卡片「${r.card.name}」已${isCustomEdit ? '更新' : '创建'} · ${r.card.op}`, 'ok');
    toast(isCustomEdit ? '卡片已更新' : '卡片已创建', r.card.name);
    closeModal($('#cardModal'));
    await reloadCards();
  } catch (e) {
    showCardErr(String(e.message || e));
  } finally {
    btn.disabled = false;
  }
}

async function deleteCard() {
  const card = editingCard;
  if (!card || !card.custom) return;
  if (!confirm(`删除卡片「${card.name}」？\n\n只删这张卡，不影响已经产生的文件和任务。`)) return;
  try {
    await API.deleteCard(card.id);
    addLog(`🗑 卡片「${card.name}」已删除`, 'warn');
    toast('卡片已删除', card.name);
    closeModal($('#cardModal'));
    await reloadCards();
  } catch (e) {
    showCardErr(String(e.message || e));
  }
}

/** 重新拉卡片目录（新建/改名/删除后） */
async function reloadCards() {
  if (typeof API === 'undefined') return;
  const d = await API.cards();
  window.applyServerCards(d);
  // 执行链里可能挂着一张已被删掉的卡
  chain = chain.filter(s => CARDS.some(c => c.name === s.name));
  renderChain();
}

/**
 * 参数表单里改了会影响 onlyIf 的字段（比如 format）时，
 * 只重渲染参数区，但把用户已经填过的值带上，别把输入清空。
 */
function syncParamForm() {
  const op = $('#cardOp').value;
  const spec = OPS_CATALOG && OPS_CATALOG[op];
  if (!spec) return;
  const before = collectParams();
  const needRerender = spec.params.some(p => p.onlyIf);
  if (!needRerender) return;
  // 只有当某个 onlyIf 的判定结果和当前 DOM 状态不一致时才重渲染，
  // 否则每敲一个字都会重建表单、输入框失焦
  const mismatch = spec.params.some(p => {
    const row = $(`#cardParams [data-prow="${p.key}"]`);
    if (!row) return false;
    const displayed = !row.classList.contains('pspec--off');
    return displayed !== paramApplies(p, before);
  });
  if (!mismatch) return;
  renderParamForm(op, before);
}

/**
 * 执行卡片前的参数补齐 + 可执行性检查。
 *
 * 有些操作的参数没法预先写死在卡片里 —— 封面图就是典型：
 * 卡片不该记住某一张具体图片，所以 `imagePath` 留空，执行时才选。
 *
 * @returns {Promise<object|null>} 补齐后的参数；null = 用户取消或这张卡还不可执行
 */
async function resolveCardParams(card) {
  const params = { ...(card.params || {}) };

  // 标签卡：卡片不该写死标签值（写死就会把占位文字真的写进文件），
  // 所以「空」和「有键但值为空」两种都就地问一次 —— 内置卡开箱即用，
  // 和封面卡"执行时选图"是同一个思路。
  if (card.op === 'tags') {
    const t = params.tags || {};
    const keys = Object.keys(t);
    const isEmpty = keys.length === 0;
    const allBlank = !isEmpty && keys.every(k => !String(t[k]).trim());

    if (isEmpty && params.clearMissing) {
      // 「清空全部标签」的空是**有意为之**，不能当成"还没填"去弹输入框；
      // 但它会删数据，所以要确认一次。
      if (!confirm('清空这些文件的全部标签？\n\n此操作不可撤销。')) return null;
    } else if (isEmpty || allBlank) {
      const prefill = keys.length ? keys.map(k => `${k}=`).join('\n') : 'album=';
      const raw = prompt('要写入的标签，每行一条 key=value（例：album=合集）', prefill);
      if (raw === null) return null;                  // 取消
      const tags = {};
      raw.split(/\r?\n/).forEach((line) => {
        const i = line.indexOf('=');
        if (i > 0) tags[line.slice(0, i).trim()] = line.slice(i + 1).trim();
      });
      if (!Object.keys(tags).length) {
        addLog(`⚠ 「${card.name}」没有解析出任何标签，未提交`, 'warn');
        toast('没有解析出标签', '需要 key=value 格式，例如 album=合集', 'error');
        return null;
      }
      params.tags = tags;
    }
  }

  if (card.op === 'cover' && !params.imagePath) {
    if (typeof DND === 'undefined') { toast('后端未连接', '无法选图', 'error'); return null; }
    const img = await pickFile(IMG_ACCEPT);
    if (!img) return null;                       // 用户取消，静默返回
    try {
      toast('正在上传封面', img.name);
      const saved = await uploadCoverImage(img);
      params.imagePath = saved.relPath;
    } catch (e) {
      addLog(`✗ 封面上传失败：${e.message || e}`, 'err');
      toast('封面上传失败', String(e.message || e), 'error');
      return null;
    }
  }
  return params;
}

/* ------------------------------------------------------ 卡片右键菜单 */

function openCardMenu(x, y, name) {
  const card = CARDS.find(c => c.name === name);
  if (!card) return;
  const el = $('#ctxmenu');
  const inChain = chain.some(s => s.name === name);

  const items = [
    { k: 'add',    label: inChain ? '已在执行链中' : '加入执行链',
      ico: 'add', disabled: inChain },
    { k: 'run',    label: '立即执行（作用于已勾选文件）', ico: 'run' },
    { sep: true },
    { k: 'edit',   label: card.custom ? '编辑卡片…' : '查看 / 另存为…', ico: 'edit' },
    { k: 'saveas', label: '另存为新卡片…', ico: 'copy' },
    { sep: true },
    { k: 'help',   label: '这个操作认哪些参数？', ico: 'help' },
    { k: 'json',   label: '复制卡片 JSON', ico: 'json' },
  ];
  if (card.custom) {
    items.push({ sep: true });
    items.push({ k: 'del', label: '删除卡片…', ico: 'del', danger: true });
  }

  const ICONS = {
    add:  '<path d="M12 5v14M5 12h14"/>',
    run:  '<path d="M8 5.5v13l11-6.5z"/>',
    edit: '<path d="M4 20h4L20 8l-4-4L4 16z"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M6 15V5a2 2 0 0 1 2-2h8"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.2 2.4c-.5.2-.7.6-.7 1.1v.5"/><circle cx="12" cy="17" r=".8"/>',
    json: '<path d="M8 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h2M16 4h2a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-2"/><path d="M10 9l-2 3 2 3M14 9l2 3-2 3"/>',
    del:  '<path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/>',
  };
  const icon = (k) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[k] || ''}</svg>`;

  el.innerHTML =
    `<div class="ctxmenu__head">${escHtml(card.name)}<span class="ctxmenu__tag">${
      card.custom ? '自定义' : '内置'}</span></div>` +
    items.map(it => it.sep ? '<div class="ctxmenu__sep"></div>'
      : `<button class="ctxmenu__item${it.danger ? ' ctxmenu__item--danger' : ''}"
                  data-cact="${it.k}" ${it.disabled ? 'disabled' : ''} role="menuitem">
           ${icon(it.ico)}<span>${it.label}</span></button>`).join('');
  el.hidden = false;
  el.dataset.cardName = name;

  const r = el.getBoundingClientRect();
  el.style.left = Math.max(6, Math.min(x, innerWidth - r.width - 6)) + 'px';
  el.style.top = Math.max(6, Math.min(y, innerHeight - r.height - 6)) + 'px';

  el.onclick = async (ev) => {
    const btn = ev.target.closest('[data-cact]');
    if (!btn) return;
    const act = btn.dataset.cact;
    el.hidden = true;
    el.onclick = null;
    const c = CARDS.find(x => x.name === name);
    if (!c) return;

    if (act === 'add') { addToChain(name); return; }
    if (act === 'run') {
      const ids = selectedIds();
      if (!ids.length) { toast('未选中文件', '先勾选文件', 'error'); return; }
      if (!c.op) { toast('这张卡没绑定操作', name, 'error'); return; }
      const params = await resolveCardParams(c);   // 封面卡在这里选图
      if (!params) return;
      try {
        const r2 = await API.op(c.op, { fileIds: ids, ...params });
        addLog(`▶ ${c.name} · ${ids.length} 个文件 → ${r2.total || 0} 个任务`, 'ok');
        toast('已提交', `${c.name} · ${r2.total || 0} 个任务`);
        if (window.App && App.refreshQueue) App.refreshQueue();
      } catch (e) {
        addLog(`✗ ${c.name} 失败：${e.message || e}`, 'err');
        toast('提交失败', String(e.message || e), 'error');
      }
      return;
    }
    if (act === 'edit') { openCardEditor(c, 'edit'); return; }
    if (act === 'saveas') { openCardEditor(c, 'saveas'); return; }
    if (act === 'del') { editingCard = c; deleteCard(); return; }
    if (act === 'help') { openCardEditor(c, 'edit'); toast('参数说明', '右侧有每个参数的取值范围与作用'); return; }
    if (act === 'json') {
      const json = JSON.stringify({ name: c.name, cat: c.cat, desc: c.desc, ico: c.ico,
                                    op: c.op, params: c.params }, null, 2);
      try {
        await navigator.clipboard.writeText(json);
        toast('已复制卡片 JSON', '可直接粘进 cards.json 或分享');
      } catch {
        toast('复制失败', '浏览器拒绝了剪贴板访问', 'error');
      }
    }
  };
}

/* ------------------------------------------------------ 封面 / 批量动作 */

const IMG_ACCEPT = 'image/jpeg,image/png,image/webp,image/bmp,image/gif';

/**
 * 弹文件选择器，返回 File 或 null（取消）。
 * 必须能感知「取消」：批量按钮是 `b.disabled=true → await runBulk → finally 复位`，
 * 如果这个 promise 永远不 settle，按钮就永久卡在禁用态。
 * Chrome 113+ 有 cancel 事件；老浏览器用「窗口重新获得焦点」兜底。
 */
function pickFile(accept) {
  return new Promise((resolve) => {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = accept;
    let done = false;
    const finish = (f) => { if (done) return; done = true; resolve(f || null); };
    inp.addEventListener('change', () => finish(inp.files && inp.files[0]));
    if ('oncancel' in HTMLInputElement.prototype) {
      inp.addEventListener('cancel', () => finish(null));
    } else {
      window.addEventListener('focus', () => setTimeout(() => finish(null), 600), { once: true });
    }
    inp.click();
  });
}

/** 把一批文件推进某个 op；返回后端回执 */
async function submitOp(op, ids, params) {
  const r = await API.op(op, { fileIds: ids, ...(params || {}) });
  addLog(`▶ ${op} · ${ids.length} 个文件 → ${r.total || 0} 个任务`, 'ok');
  if (window.App && App.refreshQueue) App.refreshQueue();
  return r;
}

/** 上传一张封面图到受管目录，返回 saved 记录 */
async function uploadCoverImage(img) {
  const res = await new Promise((resolve, reject) => {
    DND.upload([{ file: img, rel: 'covers/' + img.name }], {
      onProgress: () => {}, onDone: (d) => (d ? resolve(d) : reject(new Error('已取消'))),
      onError: reject,
    });
  });
  const saved = (res.saved || [])[0];
  if (!saved) throw new Error((res.errors && res.errors[0] && res.errors[0].reason) || '封面上传失败');
  return saved;
}

/**
 * 选一张本地图片，上传到受管目录，再嵌进指定文件。
 * 后端只认 uploads/ 内的路径，所以必须先上传。
 */
async function importCover(id) {
  const f = FILES.find(x => x.id === id);
  if (!f) return;
  const img = await pickFile(IMG_ACCEPT);
  if (!img) return;
  if (typeof DND === 'undefined' || typeof API === 'undefined') {
    toast('后端未连接', '无法导入封面', 'error'); return;
  }
  toast('正在上传封面', img.name);
  try {
    const saved = await uploadCoverImage(img);
    await submitOp('cover', [id], { imagePath: saved.relPath, pictureType: 'Front Cover' });
    toast('已提交', `嵌入封面 → ${f.title}`);
    await waitForCover(id);
  } catch (err) {
    addLog(`✗ 导入封面失败：${err.message || err}`, 'err');
    toast('导入封面失败', String(err.message || err), 'error');
  }
}

/** 嵌入是异步任务，轮询几次直到 hasCover 变 true，然后把图换掉 */
async function waitForCover(id, tries = 20) {
  for (let i = 0; i < tries; i++) {
    await new Promise(r => setTimeout(r, 500));
    if (!window.App || !App.reloadFiles) return;
    await App.reloadFiles();
    const f = FILES.find(x => x.id === id);
    if (f && f.cover) {
      COVER_V[id] = Date.now();       // 破缓存，否则 <img> 还是旧的 404
      renderFiles();
      renderEmptyState();
      toast('封面已嵌入', f.title);
      return;
    }
  }
}

async function removeCover(id) {
  const f = FILES.find(x => x.id === id);
  if (!f) return;
  if (!confirm(`移除「${f.title}」的内嵌封面？`)) return;
  try {
    await submitOp('remove-cover', [id]);
    toast('已提交', `移除封面 → ${f.title}`);
    setTimeout(() => { if (window.App && App.reloadFiles) App.reloadFiles(); }, 900);
  } catch (e) {
    toast('移除封面失败', String(e.message || e), 'error');
  }
}

/** 批量栏的每个动作都真的落到后端，不再只弹一个 toast */
async function runBulk(kind, ids) {
  const n = ids.length;
  if (typeof API === 'undefined') { toast('后端未连接', '批量操作无法执行', 'error'); return; }

  try {
    switch (kind) {
      case 'delete': {
        const names = FILES.filter(f => ids.includes(f.id)).map(f => f.title);
        const head = names.slice(0, 5).map(t => `· ${t}`).join('\n');
        const more = names.length > 5 ? `\n… 等 ${names.length} 个` : '';
        if (!confirm(`删除 ${n} 个文件？\n\n${head}${more}\n\n`
                   + `会同时删掉 uploads/ 里的工作副本（你磁盘上的原文件不受影响）。`)) return;
        const r = await API.delMany(ids, true);
        const freed = (r.freedBytes || 0) / 1048576;
        addLog(`🗑 删除 ${r.count} 个文件 · 释放 ${freed.toFixed(1)} MB`
               + (r.failed && r.failed.length ? ` · ${r.failed.length} 个失败` : ''),
               r.failed && r.failed.length ? 'warn' : 'ok');
        toast(`已删除 ${r.count} 个`, `释放 ${freed.toFixed(1)} MB`
              + (r.failed && r.failed.length ? ` · ${r.failed.length} 个失败` : ''));
        if (r.failed && r.failed.length) {
          r.failed.forEach(x => addLog(`✗ 删除失败 ${x.id}：${x.error}`, 'err'));
        }
        await App.reloadFiles();
        break;
      }

      case 'cover': {
        // 批量嵌同一张图
        const img = await pickFile(IMG_ACCEPT);
        if (!img) return;
        const saved = await uploadCoverImage(img);
        await submitOp('cover', ids, { imagePath: saved.relPath, pictureType: 'Front Cover' });
        toast('已提交', `${n} 个文件嵌入同一张封面`);
        break;
      }

      case 'normalize':
        await submitOp('normalize', ids, { targetLufs: -16 });
        toast('已提交', `${n} 个文件 · 标准化 -16 LUFS`);
        break;

      case 'convert': {
        const fmt = (prompt('目标格式（flac / wav / mp3 / m4a / ogg / opus）', 'flac') || '').trim().toLowerCase().replace(/^\./, '');
        if (!fmt) return;
        if (!['flac', 'wav', 'mp3', 'm4a', 'aac', 'ogg', 'opus', 'aiff', 'wma'].includes(fmt)) {
          toast('不支持的格式', fmt, 'error'); return;
        }
        const params = { format: fmt };
        if (['mp3', 'm4a', 'aac', 'ogg', 'opus'].includes(fmt)) {
          const br = prompt('码率（96k/128k/160k/192k/256k/320k）', '192k');
          if (br) params.bitrate = br.trim();
        }
        if (fmt === 'flac') {
          const lv = prompt('FLAC 压缩等级 0-8', '5');
          if (lv !== null && lv !== '') params.compressionLevel = Number(lv);
        }
        await submitOp('convert', ids, params);
        toast('已提交', `${n} 个文件 → ${fmt.toUpperCase()}`);
        break;
      }

      case 'rename': {
        const pat = prompt('重命名模板（{artist} {title} {album} {track}）', '{artist} - {title}');
        if (!pat) return;
        await submitOp('rename', ids, { pattern: pat });
        toast('已提交', `${n} 个文件按模板重命名`);
        break;
      }

      case 'zip':
        await submitOp('zip', ids, {});
        toast('已提交', `${n} 个文件打包`);
        break;

      case 'tag': {
        const raw = prompt('写入标签，每行一条 key=value（例：album=合集）', 'album=');
        if (!raw) return;
        const tags = {};
        raw.split(/\r?\n/).forEach((line) => {
          const i = line.indexOf('=');
          if (i > 0) tags[line.slice(0, i).trim()] = line.slice(i + 1).trim();
        });
        if (!Object.keys(tags).length) { toast('格式不对', '需要 key=value', 'error'); return; }
        await submitOp('tags', ids, { tags });
        toast('已提交', `${n} 个文件写入 ${Object.keys(tags).length} 个标签`);
        break;
      }

      default:
        toast('未实现', kind, 'error');
    }
  } catch (e) {
    addLog(`✗ 批量 ${kind} 失败：${e.message || e}`, 'err');
    toast(`批量${kind}失败`, String(e.message || e), 'error');
  }
}

/* --------------------------------------------------------------- 试听 */

/**
 * 内置播放器（需求 §4.4：点击跳转、播放指针随播放移动）。
 *
 * 全页共用一个 <audio>：天生保证同一时刻只有一个文件在放，
 * 也免去给每张卡各建一个解码器。
 * 音频走 /api/files/{id}/download —— 后端支持 Range，所以能拖动/跳转。
 */
const PLAYER_MIME = {
  flac: 'audio/flac', wav: 'audio/wav', mp3: 'audio/mpeg', m4a: 'audio/mp4',
  aac: 'audio/aac', ogg: 'audio/ogg', opus: 'audio/ogg; codecs=opus',
  aiff: 'audio/aiff', aif: 'audio/aiff', wma: 'audio/x-ms-wma',
};

let playingId = null;      // 当前正在播放（或暂停但仍是"当前"）的文件
let rafId = null;

const playerEl = () => document.getElementById('player');

/** 浏览器能不能解这个格式；不能就别让用户点了没反应 */
function canPlay(f) {
  const el = playerEl();
  if (!el) return '';
  const mime = PLAYER_MIME[String(f.format || '').toLowerCase()] || '';
  if (!mime) return '';
  return el.canPlayType(mime) || '';
}

function playHint(f) {
  const support = canPlay(f);
  if (support === '') return `试听 ${f.title}（浏览器可能不支持 ${f.format}）`;
  if (support === 'maybe') return `试听 ${f.title}（${f.format} 支持情况不确定）`;
  return `试听 ${f.title}`;
}

/** 播放指针 + 时间标签，跟着当前播放位置走 */
function syncPlayhead() {
  const el = playerEl();
  if (!el) return;
  const cur = el.currentTime || 0;
  const total = (isFinite(el.duration) && el.duration > 0) ? el.duration : 0;
  const pct = total ? Math.min(100, (cur / total) * 100) : 0;
  const bar = $(`[data-play="${playingId}"]`);
  if (bar) { bar.hidden = false; bar.style.left = pct + '%'; }
  const lab = $(`[data-time="${playingId}"]`);
  if (lab) lab.textContent = `${fmtClock(cur, '0:00')} / ${fmtClock(total)}`;
}

/** timeupdate 只有 ~4Hz，指针会一跳一跳；播放中用 rAF 平滑推进 */
function tick() {
  syncPlayhead();
  const el = playerEl();
  if (el && !el.paused && !el.ended) rafId = requestAnimationFrame(tick);
  else rafId = null;
}

function stopTick() {
  if (rafId) cancelAnimationFrame(rafId);
  rafId = null;
}

/** 把卡片上的按钮/指针恢复成"未播放"的样子 */
function resetCard(id) {
  if (!id) return;
  const btn = $(`[data-play-toggle="${id}"]`);
  if (btn) { btn.innerHTML = svg('play'); btn.setAttribute('aria-label', '试听'); }
  const bar = $(`[data-play="${id}"]`);
  if (bar) { bar.hidden = true; bar.style.left = '0%'; }
  const card = $(`#fileList .card[data-id="${id}"]`);
  if (card) card.classList.remove('is-playing');
}

/** 暂停某个文件（不切歌） */
function pauseFile(id) {
  const el = playerEl();
  if (!el || playingId !== id) return;
  el.pause();
  stopTick();
  const btn = $(`[data-play-toggle="${id}"]`);
  if (btn) { btn.innerHTML = svg('play'); btn.setAttribute('aria-label', '继续试听'); }
}

/** 播放/暂停切换 */
async function togglePlay(id) {
  const el = playerEl();
  if (!el) return;
  const f = FILES.find(x => x.id === id);
  if (!f) return;

  // 点到另一张卡：先停掉旧的，换源
  if (playingId && playingId !== id) {
    resetCard(playingId);
    el.pause();
    stopTick();
    playingId = null;
  }

  if (playingId === id && !el.paused) { pauseFile(id); return; }

  if (el.src !== absoluteUrl(`/api/files/${id}/download`)) {
    el.src = `/api/files/${id}/download`;
  }
  playingId = id;
  const btn = $(`[data-play-toggle="${id}"]`);
  if (btn) { btn.innerHTML = svg('pause'); btn.setAttribute('aria-label', '暂停'); }
  const card = $(`#fileList .card[data-id="${id}"]`);
  if (card) card.classList.add('is-playing');

  try {
    await el.play();
    addLog(`▶ 试听 ${f.title}`, 'ok');
    stopTick();
    rafId = requestAnimationFrame(tick);
  } catch (err) {
    // 浏览器不支持该编码时 play() 会 reject —— 明确告诉用户，别静默失败
    resetCard(id);
    playingId = null;
    const why = (err && err.name === 'NotSupportedError')
      ? `浏览器不支持 ${f.format}，可先转成 MP3/FLAC 再试听`
      : String((err && err.message) || err);
    addLog(`✗ 试听失败：${f.title} · ${why}`, 'err');
    toast('无法试听', why, 'error');
  }
}

/** 点波形跳转（需求 §4.4：点击跳转播放位置）
 *  wrap 必须由调用方传进来：这是事件委托，e.currentTarget 是 #fileList 而不是波形框，
 *  拿它量宽度会让跳转位置整体偏掉（实测点 75% 落到 45%）。 */
function seekFromEvent(e, id, wrap) {
  const el = playerEl();
  const r = wrap.getBoundingClientRect();
  if (!r.width) return;
  const pct = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));

  if (playingId !== id) {
    // 还没在放这个文件：先切过去，等元数据到了再定位
    togglePlay(id).then(() => {
      const total = (isFinite(el.duration) && el.duration > 0) ? el.duration : 0;
      if (total) el.currentTime = pct * total;
      syncPlayhead();
    });
    return;
  }
  const total = (isFinite(el.duration) && el.duration > 0) ? el.duration : 0;
  if (total) el.currentTime = pct * total;
  syncPlayhead();
}

function bindPlayer() {
  const el = playerEl();
  if (!el) return;

  el.addEventListener('loadedmetadata', () => {
    // 用真实时长校正标签（列表里的 dur 来自探测，可能为 0）
    if (playingId) {
      const lab = $(`[data-time="${playingId}"]`);
      if (lab) lab.textContent = `${fmtClock(el.currentTime, '0:00')} / ${fmtClock(el.duration)}`;
    }
  });
  el.addEventListener('play', () => { stopTick(); rafId = requestAnimationFrame(tick); });
  el.addEventListener('pause', stopTick);
  el.addEventListener('timeupdate', syncPlayhead);
  el.addEventListener('ended', () => {
    stopTick();
    const id = playingId;
    resetCard(id);
    const lab = $(`[data-time="${id}"]`);
    const f = FILES.find(x => x.id === id);
    if (lab) lab.textContent = `${fmtClock(0, '0:00')} / ${fmtClock(f ? f.dur : 0)}`;
    playingId = null;
  });
  el.addEventListener('error', () => {
    if (!playingId) return;
    const id = playingId;
    const f = FILES.find(x => x.id === id);
    resetCard(id);
    playingId = null;
    addLog(`✗ 试听失败：${f ? f.title : id} · 音频加载出错`, 'err');
    toast('无法试听', '音频加载出错，可能是格式不受支持', 'error');
  });

  // 点波形跳转
  $('#fileList').addEventListener('click', e => {
    const wrap = e.target.closest('.wave__canvasWrap');
    if (!wrap) return;
    const cv = wrap.querySelector('[data-wave]');
    if (cv) seekFromEvent(e, cv.dataset.wave, wrap);
  });
  // 播放按钮
  $('#fileList').addEventListener('click', e => {
    const btn = e.target.closest('[data-play-toggle]');
    if (!btn) return;
    e.stopPropagation();
    togglePlay(btn.dataset.playToggle);
  });
}

/** <audio>.src 永远是绝对 URL，比较前先归一化，否则每次点都重设 src 导致从头播 */
function absoluteUrl(u) {
  try { return new URL(u, location.href).href; } catch { return u; }
}

/* ------------------------------------------------------------- 音量 */

const VOL_KEY = 'ae-volume';
const MUTE_KEY = 'ae-muted';

/**
 * chainbar 右端的音量滑块，控制共用的 <audio id="player">。
 * 三个状态（大/小/静音）图标跟着变，静音用错误色 —— 否则"没声音"很难排查。
 */
function bindVolume() {
  const range = $('#volRange');
  const btn = $('#volMute');
  const val = $('#volVal');
  const el = playerEl();
  if (!range || !btn || !el) return;

  // 恢复上次的音量。注意别直接 Number(getItem())：
  // 没有这个键时 getItem 返回 null，而 Number(null) === 0，
  // 于是首次打开音量会是 0（静音）—— 正好和预期相反。
  const raw = localStorage.getItem(VOL_KEY);
  const saved = raw === null ? NaN : Number(raw);
  el.volume = (isFinite(saved) && saved >= 0 && saved <= 1) ? saved : 1;
  el.muted = localStorage.getItem(MUTE_KEY) === '1';

  const paint = () => {
    const pct = Math.round(el.volume * 100);
    const muted = el.muted || el.volume === 0;
    range.value = String(pct);
    range.style.setProperty('--vol-pct', pct + '%');
    // 数字始终显示真实音量：静音时 volume 仍保留原值，写"静音"会丢信息，
    // 而且两字中文比数字宽，切换时会把滑块挤动
    if (val) val.textContent = String(pct);
    btn.innerHTML = svg(muted ? 'volMute' : (pct < 55 ? 'volLow' : 'vol'));
    btn.setAttribute('aria-label', muted ? '取消静音' : '静音');
    btn.setAttribute('aria-pressed', String(muted));
    btn.classList.toggle('is-muted', muted);
  };

  range.addEventListener('input', () => {
    const v = Number(range.value) / 100;
    el.volume = v;
    // 从 0 往上拉就自动解除静音，否则用户会觉得"拖了滑块还是没声"
    if (v > 0 && el.muted) { el.muted = false; localStorage.setItem(MUTE_KEY, '0'); }
    localStorage.setItem(VOL_KEY, String(v));
    paint();
  });

  btn.addEventListener('click', () => {
    el.muted = !el.muted;
    // 静音时保留 volume：取消静音要能回到原来的音量，而不是 0
    if (!el.muted && el.volume === 0) el.volume = 0.6;
    localStorage.setItem(MUTE_KEY, el.muted ? '1' : '0');
    localStorage.setItem(VOL_KEY, String(el.volume));
    paint();
  });

  // 别的地方（将来的快捷键、系统媒体键）改了音量也能同步到滑块
  el.addEventListener('volumechange', paint);
  paint();
}

/* --------------------------------------------------------- 波形绘制 */

/** 波形绘制：优先用后端真实峰值；没有数据时明确画占位，不伪造波形 */
function drawWave(canvas, id) {
  const f = FILES.find(x => x.id === id);
  if (!f) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  canvas.width = w * dpr; canvas.height = h * dpr;

  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  ctx.fillStyle = tok('--bg-surface');
  ctx.fillRect(0, 0, w, h);

  const data = (typeof peakCache !== 'undefined') ? peakCache.get(id) : null;
  const laneH = h / 2;
  const gap = 1.5;

  if (!data || !Array.isArray(data.peaks) || !data.peaks.length) {
    // 无峰值数据：画一条中线 + 提示，不画假波形
    for (let lane = 0; lane < 2; lane++) {
      const cy = lane * laneH + laneH / 2;
      ctx.fillStyle = tok('--border');
      ctx.fillRect(0, cy, w, 1);
    }
    ctx.fillStyle = tok('--text-muted') || tok('--ink');
    ctx.font = '11px ' + cssVar('--font-sans');
    ctx.textAlign = 'center';
    ctx.fillText('暂无峰值数据 · 右键卡片可「重建峰值图」', w / 2, h / 2 + 4);
    ctx.textAlign = 'left';
    return;
  }

  const pk = data.peaks;
  const barW = 2, step = barW + 1;
  const n = Math.max(1, Math.floor(w / step));
  // 峰值点比像素列多时做聚合取最大，少时重复采样
  const clip = 0.995;

  for (let lane = 0; lane < 2; lane++) {
    const cy = lane * laneH + laneH / 2;
    const maxAmp = laneH / 2 - gap;
    for (let i = 0; i < n; i++) {
      const a0 = Math.floor(i * pk.length / n);
      const a1 = Math.max(a0 + 1, Math.floor((i + 1) * pk.length / n));
      let amp01 = 0;
      for (let k = a0; k < a1 && k < pk.length; k++) {
        if (pk[k] > amp01) amp01 = pk[k];
      }
      const amp = Math.max(0.6, amp01 * maxAmp);
      const x = i * step;
      const isClip = amp01 >= clip;
      ctx.fillStyle = isClip ? tok('--error-solid') : tok('--fill-primary');
      ctx.fillRect(x, cy - amp, barW, amp * 2);
      if (!isClip) {
        ctx.fillStyle = tok('--on-fill', 0.45);
        ctx.fillRect(x, cy - amp, barW, 1);
        ctx.fillRect(x, cy + amp - 1, barW, 1);
      }
    }
    ctx.fillStyle = tok('--border');
    ctx.fillRect(0, cy, w, 1);
  }

  ctx.fillStyle = tok('--text-muted') || tok('--ink');
  ctx.font = '10px ' + cssVar('--font-sans');
  ctx.fillText('L', 6, laneH / 2 - 3);
  ctx.fillText('R', 6, laneH + laneH / 2 - 3);
}

function redrawAllWaves() {
  $$('[data-wave]').forEach(cv => drawWave(cv, cv.dataset.wave));
}

/* ------------------------------------------------------------ 抽屉 */

/**
 * 三档 = (盒顶 top, 盒高 box)。盒子 left/right 固定，面板自动占满
 * 盒内剩余高度（box − 快照条），因此搜索栏永远贴着盒底，不会被挤出视口。
 *
 *   closed  top = vh − bar          box = bar          只露快照条，面板在边缘之下
 *   mid     top = vh − box − EDGE   box = bar + 56vh   底部离边 EDGE，四角圆角
 *   full    top = 0                 box = vh           撑满整个窗口高度
 */
const SNAP_MIN = 4;                      // 拖拽位移阈值(px)
// 抽屉四周的悬浮留白。
// 注意它**不等于**工作区内容的留白：.workspace 是 padding: 18px 20px 0，
// 所以抽屉比文件列表左右各宽 6px（实测抽屉 283–1358、列表 289–1352）。
// 这是有意保留的"悬浮面板比内容略宽"的观感；要改成严格对齐就把 EDGE 调成 20。
const EDGE = 14;

let drawerState = 'closed';
let curTop = 0;                          // 当前盒顶

const VH = () => window.innerHeight;

/** 三档的 (top, box) */
function stops() {
  const vh = VH();
  const bar = $('#drawer').querySelector('.drawer__bar').getBoundingClientRect().height || 73;
  const midBox = bar + Math.round(vh * 0.56);
  return {
    // 面板完全在窗口下边缘之下：盒顶 = vh − bar
    closed: { top: Math.round(vh - bar),        box: Math.round(bar) },
    // 盒底离窗口底 EDGE
    mid:    { top: Math.round(vh - EDGE - midBox), box: midBox },
    // 顶部贴窗口顶端、底部贴页面底端
    full:   { top: 0,                           box: Math.round(vh) },
  };
}
const drawer = document.querySelector('.drawer');
const backdrop = document.querySelector('.drawer-backdrop');

function applyStop(stop) {
  const s = stops()[stop];              // 每次现算，依赖 VH() 和 bar
  drawer.dataset.stop = stop;           // 触发 CSS 的圆角 / backdrop 规则
  drawer.style.setProperty('--drawer-top', s.top + 'px');
  drawer.style.setProperty('--drawer-box', s.box + 'px');
}
backdrop.addEventListener('click', () => {
  if (drawer.dataset.dragging === 'true') return;  // 拖拽中忽略
  if (drawer.dataset.stop === 'closed') return;    // 已收起，双保险
  applyStop('mid');
});
/** 按左侧栏的【实际渲染宽度】对齐抽屉左边，避免用 --sidebar-w 推算而压到侧栏下面 */
function syncDrawerLeft() {
  const sb = document.querySelector('.sidebar');
  if (!sb) return;
  const r = sb.getBoundingClientRect();
  // 侧栏右缘 + EDGE。用 EDGE 而不是 .workspace 的 20px 内边距，
  // 是为了让悬浮抽屉比内容略宽一点（见 EDGE 处的说明）。
  $('#drawer').style.setProperty('--drawer-left', Math.round(r.right + EDGE) + 'px');
}

/** 按"盒顶"写出 (top, box)。
 *  @param grow  true = mid→full 段内按比例伸长盒高（吸附时用）
 *               false = 盒高只按档位取，拖拽途中不伸长（严格 1:1 跟手） */
/** 盒高 = 盒顶的分段线性函数，全区间连续 */
function boxForTop(top) {
  const s = stops();
  if (top >= s.mid.top) {
    // mid.top → closed.top：midBox → bar
    const span = Math.max(1, s.closed.top - s.mid.top);
    const t = Math.min(1, Math.max(0, (top - s.mid.top) / span));
    return Math.round(s.mid.box + t * (s.closed.box - s.mid.box));
  }
  // full.top → mid.top：vh → midBox
  const span = Math.max(1, s.mid.top - s.full.top);
  const t = Math.min(1, Math.max(0, (s.mid.top - top) / span));
  return Math.round(s.mid.box + t * (s.full.box - s.mid.box));
}

function applyTop(top, stopName) {          // ← 去掉 grow 参数
  const s = stops();
  curTop = Math.round(top);
  const box = boxForTop(curTop);
  const d = $('#drawer');
  d.style.setProperty('--drawer-top', curTop + 'px');
  d.style.setProperty('--drawer-box', box + 'px');
  if (stopName) {
    drawerState = stopName;
    d.dataset.stop = stopName;
  }
  $('#drawerHandle').setAttribute('aria-expanded', String(drawerState !== 'closed'));
  return curTop;
}

function applyStop(name) {
  applyTop(stops()[name].top, name);
}

/** 点击把手：已展开 → 收起；收起 → 展开到 mid。
 *  想继续拉到 full 请用拖拽（点击不该一次跳到底）。 */
function cycleDrawer() {
  const cur = drawer.dataset.stop;
  applyStop(cur === 'mid' ? 'closed' : 'mid');
}

/* -------- 搜索：点击唤出 + 缓出，不允许半开 -------- */
function setSearch(open) {
  const dock = $('#searchDock');
  dock.dataset.search = open ? 'open' : 'closed';
  $('#searchToggle').setAttribute('aria-expanded', String(open));
  if (open) {
    // 抽屉收着的时候点搜索，面板整个在窗口下边缘之外，
    // 只把 dock 打开的话用户什么都看不到 —— 先把抽屉升到 mid。
    if (drawerState === 'closed') applyStop('mid');
    // .searchfield 是 max-width:0 → 100% 的缓出，宽度为 0 时 focus() 不生效，
    // 所以在过渡结束后补一次聚焦。
    const field = $('#cardSearch');
    requestAnimationFrame(() => field.focus());
    setTimeout(() => { if (dock.dataset.search === 'open') field.focus(); }, 380);
  } else {
    $('#cardSearch').value = '';
    renderCardSections('');
  }
}

/** 抽屉顶部那排快照。
 *  可拖拽：把卡片拖进来=替换该位；快照互拖=换位；拖到末尾的 ＋ =追加。
 *  改完存到 /api/snapshots，下次打开还是你的排列。 */
function renderSnaps() {
  const bar = $('#cardSnaps');
  bar.innerHTML = SNAPS.map((s, i) =>
    `<button class="snap" draggable="true" data-snap="${i}" data-snap-name="${escHtml(s.name)}"
             title="${escHtml(s.name)} · 拖拽可换位，把卡片拖进来可替换">
       <span class="snap__ico">${svg(s.ico)}</span>
       <span class="snap__txt">
         <span class="snap__name">${escHtml(s.name)}</span>
         <span class="snap__hint">${escHtml(s.hint || '')}</span>
       </span>
     </button>`).join('')
    + `<button class="snap snap--add" data-snap-add="1" title="把卡片拖到这里追加一个快照">
         <span class="snap__ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="1.8" stroke-linecap="round"><path d="M12 6v12M6 12h12"/></svg></span>
         <span class="snap__txt"><span class="snap__name">拖卡片到此</span>
           <span class="snap__hint">追加快照</span></span>
       </button>`;
  syncSnapDragState();
}

/** 拖动来源不同，光标提示也不同 */
function syncSnapDragState() {
  const add = $('[data-snap-add]');
  if (add) add.hidden = SNAPS.length >= 12;
}

/** 保存快照顺序（失败就把界面回滚成服务端的样子） */
async function saveSnapshots(names) {
  if (typeof API === 'undefined') { toast('后端未连接', '快照无法保存', 'error'); return; }
  try {
    const d = await API.putSnapshots(names);
    window.applyServerCards({ snapshots: d.snapshots });
    addLog(`⇄ 快照已更新：${d.snapshots.join(' / ')}`, 'ok');
  } catch (e) {
    addLog(`✗ 快照保存失败：${e.message || e}`, 'err');
    toast('快照保存失败', String(e.message || e), 'error');
    if (window.App && App.reloadFiles) { /* 触发一次重载把界面拉回服务端状态 */ }
    try {
      const d = await API.cards();
      window.applyServerCards(d);
    } catch { /* 忽略 */ }
  }
}

/* 卡片按分类分段，每段一个大标题（不再用侧边导航） */
function renderCardSections(q = '') {
  const box = $('#cardSections');
  const query = q.trim();

  if (query) {
    const hit = CARDS.filter(c => c.name.includes(query) || c.desc.includes(query)
                                  || c.cat.includes(query));
    box.innerHTML = hit.length
      ? `<section class="cardsec">
           <h3 class="cardsec__title">搜索结果
             <span class="cardsec__count">${hit.length}</span></h3>
           <div class="cardsec__grid">${hit.map(cardHTML).join('')}</div>
         </section>`
      : `<p class="chain__empty">没有匹配「${query}」的卡片</p>`;
    return;
  }

  const section = (cat, list, extra = '') => {
    if (!list.length && !extra) return '';
    const count = list.length ? `<span class="cardsec__count">${list.length}</span>` : '';
    return `<section class="cardsec">
        <h3 class="cardsec__title">${escHtml(cat)}${count}</h3>
        <div class="cardsec__grid">${list.map(cardHTML).join('')}${extra}</div>
      </section>`;
  };

  const known = new Set(CATEGORIES);
  const html = CATEGORIES.map(cat => {
    if (cat === '自定义') {
      // 这一段以前只渲染「新建」按钮、return 得很早，
      // 结果 cat='自定义' 的自定义卡片建出来之后**根本不显示**。
      const extra = `<button class="fcard fcard--new" id="newCard">
          <span class="fcard__ico">${svg('wave')}</span>
          <span class="fcard__name">新建自定义卡片</span>
          <span class="fcard__desc">选基础操作 → 填参数 → 命名保存</span>
        </button>`;
      return section(cat, CARDS.filter(c => c.cat === cat), extra);
    }
    return section(cat, CARDS.filter(c => c.cat === cat));
  }).join('');

  // 兜底：卡片落在 CATEGORIES 之外的分类时，**必须照样渲染出来**。
  // 否则后端一旦新增分类（或前端分类表过期），那些卡片会凭空消失且毫无提示。
  const strays = [...new Set(CARDS.map(c => c.cat).filter(c => !known.has(c)))];
  const tail = strays.map(cat => {
    const list = CARDS.filter(c => c.cat === cat);
    addLog(`⚠ 卡片分类「${cat}」不在已知分类里，已单独成段显示（${list.length} 张）`, 'warn');
    return section(cat, list);
  }).join('');

  // 断网或卡片库为空时给明确说法，而不是一片空白
  const empty = (!CARDS.length)
    ? `<div class="empty empty--sm">
         <strong>${offlineMode ? '后端未连接' : '没有卡片'}</strong>
         <span>${offlineMode ? '功能卡片由后端提供，启动 run.bat / run.sh 后刷新页面'
                             : '正在读取卡片目录…'}</span>
       </div>`
    : '';

  box.innerHTML = empty + html + tail;
}

function cardHTML(c) {
  return `<button class="fcard${c.custom ? ' fcard--custom' : ''}" data-card="${c.name}"
                  draggable="true"
                  title="点击加入执行链 · 右键编辑 · 拖到顶部快照可替换">
      <span class="fcard__top">
        <span class="fcard__ico">${svg(c.ico)}</span>
        <span class="fcard__name">${c.name}</span>
      </span>
      <span class="fcard__desc">${c.desc}</span>
      <span class="fcard__foot">
        <span class="fcard__tier">${c.tier}</span>
        <span class="fcard__tier">${c.cat}</span>
      </span>
    </button>`;
}

let chain = [];

function renderChain() {
  const box = $('#chain');
  const scope = selectedIds().length;
  if (!chain.length) {
    box.innerHTML = '<span class="chain__empty">点击上方卡片加入执行链，例如：转 FLAC → 标准化 -16 → 嵌入封面</span>';
  } else {
    box.innerHTML = chain.map((s, i) =>
      `${i ? '<span class="chain__arrow">→</span>' : ''}
       <span class="chain__item" title="单击只执行这一步">${escHtml(s.name)}<button data-del="${i}" aria-label="移除 ${escHtml(s.name)}">×</button></span>`).join('');
  }
  // 有链 + 有选中文件 才可执行
  const run = $('#chainRun');
  if (run) {
    const hasOp = chain.some(s => s.op);
    run.disabled = !chain.length || scope === 0;
    run.title = !chain.length ? '执行链为空，先添加卡片'
              : scope === 0 ? '请先勾选要处理的文件'
              : !hasOp ? '链上没有可执行的后端操作'
              : `按顺序执行 ${chain.length} 步，作用于 ${scope} 个文件`;
  }
  syncCardAddedState(); 
}

function escHtml(s) {
  return String(s).replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/**
 * 执行整条链：逐步调用后端 /api/ops/<op>。
 * 后端会为该步的【每个文件】各建一个任务并进队列。
 * @param only 只执行某一步（链上单击）
 */
async function runChain(only) {
  if (!chain.length) { toast('执行链为空', '先把卡片加入链', 'error'); return; }
  const ids = selectedIds();
  if (!ids.length) { toast('未选中文件', '勾选文件后再执行', 'error'); return; }

  const steps = (only == null) ? chain : [chain[only]];
  const usable = steps.filter(s => s.op);

  const bar = $('.chainbar');
  bar.dataset.running = 'true';
  $('#chainRun').disabled = true;

  // 未连接后端：不伪造任务，只报错（页面上不出现假进度）
  if (typeof API === 'undefined') {
    addLog(`✗ 执行链：后端未连接，${steps.length} 步未提交`, 'err');
    toast('后端未连接', '启动 run.bat 后再执行', 'error');
    delete bar.dataset.running;
    renderChain();
    return;
  }

  // 链上的卡片都没带 op（纯本地卡片）：同样不伪造任务
  if (!usable.length) {
    const bad = steps.map(s => s.name).join('、');
    addLog(`⚠ 执行链：${bad} 没有绑定后端操作，未提交`, 'warn');
    toast('无可执行步骤', `${bad} 未绑定后端操作`, 'error');
    delete bar.dataset.running;
    renderChain();
    return;
  }

  try {
    let total = 0;
    for (const s of usable) {
      // 链上的步骤也要走一遍参数补齐：封面卡在执行时才选图，
      // 用户取消就中止整条链（已经提交的步骤不回滚，如实报告）
      const card = CARDS.find(c => c.name === s.name) || { name: s.name, op: s.op, params: s.params };
      const params = await resolveCardParams(card);
      if (!params) {
        if (total) {
          addLog(`⚠ 执行链在第 ${usable.indexOf(s) + 1} 步中止，已提交 ${total} 个任务`, 'warn');
          toast('执行链已中止', `前 ${total} 个任务已进队列`);
        }
        return;                       // finally 里会复位按钮与状态
      }
      const r = await API.op(s.op, { fileIds: ids, ...params });
      total += r.total || 0;
    }
    addLog(`▶ 执行链：${usable.map(s => s.name).join(' → ')} · ${usable.length} 步 / ${total} 个任务`, 'ok');
    toast('已提交执行链', `${usable.length} 步 · ${total} 个任务进入队列`);
    if (window.App && App.refreshQueue) App.refreshQueue();
  } catch (e) {
    addLog(`✗ 执行链失败：${e.message || e}`, 'err');
    toast('执行链失败', String(e.message || e), 'error');
  } finally {
    delete bar.dataset.running;
    renderChain();
  }
}

/** 把卡片加入执行链；卡片若带后端 op 则记录，供执行链调用 */
function addToChain(name) {
  const card = CARDS.find(c => c.name === name);
  chain.push({ name, op: card && card.op, params: (card && card.params) || {} });
  renderChain();
  const scope = selectedIds().length;
  toast('已加入执行链', `${name}${scope ? ` · 将作用于 ${scope} 个文件` : ' · 未选中文件'}`);
}

/* --------------------------------------------------------- 选择与批量 */

const selectedIds = () => FILES.filter(f => f.checked).map(f => f.id);
let focusedId = null;

function syncBulkbar() {
  const n = selectedIds().length;
  const total = FILES.length;
  $('#bulkbar').hidden = n === 0;
  $('#bulkCount').textContent = `已选 ${n} 个文件`;
  const all = $('#selectAll');
  all.checked = n === total && total > 0;
  all.indeterminate = n > 0 && n < total;     // 半选态
  $('#selCount').textContent = n ? `已选 ${n} / ${total} 个文件` : `共 ${total} 个文件`;
  $('#drawerScope').textContent = n ? `将作用于 ${n} 个文件` : '未选中文件';
  renderChain();               // 执行链按钮的可用性跟随选中数
}

function setChecked(id, on) {
  const f = FILES.find(x => x.id === id);
  if (!f) return;
  f.checked = on;
  const card = $(`.card[data-id="${id}"]`);
  if (card) card.classList.toggle('is-checked', on);
  const cb = $(`[data-check="${id}"]`);
  if (cb) cb.checked = on;
  syncBulkbar();
}

function setFocused(id) {
  focusedId = id;
  $$('.card').forEach(c => c.classList.toggle('is-focused', c.dataset.id === id));
}

/* ------------------------------------------------------------- 通知 */

let noticeTimer = null;
function toast(title, msg = '', kind = 'info') {
  const n = $('#notice');
  $('#noticeTitle').textContent = title;
  $('#noticeMsg').textContent = msg;
  n.dataset.kind = kind;
  n.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { n.hidden = true; }, 4200);
}

/* ------------------------------------------------------------- 模态 */

const META_FIELDS = [
  ['title', 'Title', true], ['artist', 'Artist', false],
  ['album', 'Album', true], ['albumartist', 'Album Artist', false],
  ['genre', 'Genre', false], ['date', 'Year', false],
  ['tracknumber', 'Track', false], ['discnumber', 'Disc', false],
  ['composer', 'Composer', false], ['comment', 'Comment', true],
];

let metaEditId = null;
let metaCloseTimer = null;
/** 打开元数据编辑：有后端时拉真实标签，否则退回本地 mock */
const _modalTimers = new WeakMap();

function openModal(el) {
  clearTimeout(_modalTimers.get(el));
  _modalTimers.delete(el);
  el.classList.remove('is-open');
  el.hidden = false;
  void el.offsetWidth;                              // 强制回流，让 transition 有起点
  requestAnimationFrame(() => el.classList.add('is-open'));
}

function closeModal(el) {
  el.classList.remove('is-open');
  clearTimeout(_modalTimers.get(el));               // 防抖：关-开-关 连点时不会误隐藏
  _modalTimers.set(el, setTimeout(() => {
    el.hidden = true;
    _modalTimers.delete(el);
  }, 200));                                          // 与 CSS 过渡时长一致
}
async function openMeta(id) {

  const f = FILES.find(x => x.id === id);
  if (!f) return;
  metaEditId = id;

  let tags = null, fields = META_FIELDS;
  const hasApi = typeof window.API === 'object' || typeof API !== 'undefined';
  if (hasApi) {
    try {
      const d = await API.tags(id);
      tags = d.tags || {};
      if (Array.isArray(d.fields) && d.fields.length) {
        fields = d.fields.map(k => [k, k.replace(/^\w/, c => c.toUpperCase()),
                                   k === 'title' || k === 'album' || k === 'comment']);
      }
    } catch (e) {
      toast('读取标签失败', String(e.message || e), 'error');
      return;
    }
  }
  const src = tags || {
    title: f.title, artist: f.artist, album: f.album,
    genre: '', date: '', tracknumber: f.track, comment: '',
  };

  $('#metaModalSub').textContent =
    `${f.format}${f.rate ? ' · ' + (f.rate / 1000).toFixed(1) + ' kHz' : ''} · ${f.size}`;
  $('#metaForm').innerHTML = fields.map(([key, label, wide]) =>
    `<div class="field${wide ? ' field--wide' : ''}">
       <label for="mf-${key}">${label}</label>
       <input class="input" id="mf-${key}" data-key="${key}"
              value="${String(src[key] ?? '').replace(/"/g, '&quot;')}">
     </div>`).join('');
  const modal = $('#metaModal');
  clearTimeout(metaCloseTimer);                // 取消未播完的关闭
  modal.classList.remove('is-open');           // 先回到关闭态，作为过渡起点
  modal.hidden = false;                        // 恢复 display（去 [hidden]{display:none} 的影响）
  void modal.offsetWidth;                      // 强制回流，让起点样式被浏览器确认
  requestAnimationFrame(() => modal.classList.add('is-open'));
}
function closeMeta() {
  const modal = $('#metaModal');
  modal.classList.remove('is-open');           // 触发淡出 + 模糊收起
  clearTimeout(metaCloseTimer);
  metaCloseTimer = setTimeout(() => {          // 等过渡播完再设置 hidden（纯语义）
    modal.hidden = true;
  }, 200);                                     // 对应 .14s
}

/** 保存：走 PUT /api/files/{id}/tags（不重新编码），失败则回退到本地提示 */
async function saveMeta() {
  const tags = {};
  $$('#metaForm [data-key]').forEach(inp => { tags[inp.dataset.key] = inp.value; });
  const hasApi = typeof API !== 'undefined';
  if (hasApi && metaEditId) {
    try {
      const r = await API.putTags(metaEditId, tags, true);
      closeMeta();                                        // ← 改这里
      toast('元数据已写入', `${r.method} · 写入 ${r.written} 项 · 未重新编码`);
      if (window.App && App.reloadFiles) setTimeout(() => App.reloadFiles(), 300);
      return;
    } catch (e) {
      toast('写入失败', String(e.message || e), 'error');
      return;
    }
  }
  closeMeta();                                            // ← 改这里
  toast('元数据已写入', '未重新编码，无损');
}

/* --------------------------------------------------------------- 事件 */

function bind() {
  bindPlayer();
  bindVolume();

  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // dir: 'expand'（变亮）| 'contract'（变暗）
  function switchTheme(dir, mutate) {
    if (!document.startViewTransition || reduce) {
      mutate();
      requestAnimationFrame(redrawAllWaves);
      return;
    }
    const root = document.documentElement;
    root.dataset.vtDir = dir;
    root.style.setProperty('--vt-r', Math.hypot(innerWidth, innerHeight) + 'px');
    // 15° 斜线在整幅高度上的水平偏移量
    root.style.setProperty('--vt-d', (innerHeight * Math.tan(15 * Math.PI / 180)) + 'px');
  
    const t = document.startViewTransition(mutate);
    t.finished.finally(() => {
      delete root.dataset.vtDir;
      requestAnimationFrame(redrawAllWaves);
    });
  }

    // 主题按钮
  $$('[data-set-theme]').forEach(b => b.addEventListener('click', () => {
    switchTheme('sweep', () => {
      document.documentElement.dataset.theme = b.dataset.setTheme;   // 't1' / 't2' / 't3'
      $$('[data-set-theme]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
      localStorage.setItem('ae-theme', b.dataset.setTheme);
    });
  }));
  
  // 明暗按钮：wasDark → 现在是黑切白 → 用 contract；否则用 expand
  $('#modeToggle').addEventListener('click', () => {
    const wasDark = document.documentElement.dataset.mode === 'dark';
    switchTheme(wasDark ? 'contract' : 'expand', () => {
      if (wasDark) delete document.documentElement.dataset.mode;
      else document.documentElement.dataset.mode = 'dark';
      localStorage.setItem('ae-mode', wasDark ? 'light' : 'dark');
    });
  });
  
  // 抽屉
  // 注意：把手不绑 click —— 点击/拖拽统一由下面的 pointer* 逻辑处理，
  // 否则 click 会与 pointerup 各切换一次，表现为「点了没反应」。
  $('#drawerClose').addEventListener('click', () => applyStop('closed'));

  // 搜索：点击唤出
  $('#searchToggle').addEventListener('click', () => setSearch(true));
  $('#searchClear').addEventListener('click', () => {
    const inp = $('#cardSearch');
    inp.value = '';
    renderCardSections('');
    inp.focus();
  });
  $('#cardSearch').addEventListener('input', e => renderCardSections(e.target.value));
  $('#cardSearch').addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.stopPropagation(); setSearch(false); }
  });

  // 卡片区（按分类分段渲染）
  $('#cardSections').addEventListener('click', e => {
    // 「新建自定义卡片」也在这一区，且会被重渲染 —— 必须一起走委托
    if (e.target.closest('#newCard')) { openCardEditor(null, 'new'); return; }
    const c = e.target.closest('[data-card]'); if (!c) return;
    addToChain(c.dataset.card);
  });

  // 队列里失败任务的重试（按钮每次由 renderQueue 重建，必须用事件委托）
  $('#queueList').addEventListener('click', async e => {
    const btn = e.target.closest('[data-retry]');
    if (!btn) return;
    btn.disabled = true;
    try {
      const r = await API.retryTask(btn.dataset.retry);
      addLog(`↻ 重试任务 #${btn.dataset.retry} → ${r.state || 'queued'}`, 'warn');
      if (window.App && App.refreshQueue) App.refreshQueue();
    } catch (err) {
      addLog(`✗ 重试失败：${err.message || err}`, 'err');
      toast('重试失败', String(err.message || err), 'error');
      btn.disabled = false;
    }
  });

  $('#chain').addEventListener('click', e => {
    const del = e.target.closest('[data-del]');
    if (del) {                       // 删除该步（不要继续走到"执行单步"分支）
      chain.splice(Number(del.dataset.del), 1);
      renderChain();
      return;
    }
    if (e.target.closest('button')) return;
    // 单击某一步：只执行这一步
    const item = e.target.closest('.chain__item');
    if (!item) return;
    const idx = Array.from($$('#chain .chain__item')).indexOf(item);
    if (idx >= 0 && chain[idx] != null) runChain(idx);
  });

  $('#chainRun').addEventListener('click', () => runChain());

  $('#chainSave').addEventListener('click', () =>
    chain.length ? toast('已保存为预设', chain.join(' → '))
                 : toast('执行链为空', '先添加卡片', 'error'));
  $('#chainExport').addEventListener('click', () =>
    chain.length ? toast('已复制等价命令', 'ffmpeg -i in.mp3 -c:a flac …（示意）')
                 : toast('执行链为空', '先添加卡片', 'error'));

  $('#cardSnaps').addEventListener('click', e => {
    const b = e.target.closest('[data-snap]'); if (!b) return;
    addToChain(SNAPS[Number(b.dataset.snap)].name);
  });

  /* ---------- 快照拖拽：卡片拖进来=替换，快照互拖=换位，拖到 ＋ =追加 ----------
     注意 effectAllowed 与 dropEffect 必须兼容：浏览器在 dragover 阶段就会比对，
     不允许的组合会**直接不派发 drop**（表现为"拖上去松手没反应"，且没有任何报错）。
     之前来源写 'copy'、落点写 'move'，两者不容 → 卡片永远替换不了。
     现在来源统一 'all'（或 copyMove），落点按场景用 copy/move 都能通过。 */
  const DRAG_MIME = 'application/x-ae-snap';
  const readDrag = (e) => {
    const dt = e.dataTransfer;
    if (!dt) return null;
    // 自定义 MIME 读不到时退到 text/plain —— 部分路径会把它剥掉，
    // 静默失败最难查，多一条退路
    const raw = dt.getData(DRAG_MIME) || dt.getData('text/plain') || '';
    if (!raw) return null;
    try { return JSON.parse(raw); }
    catch { return { kind: 'unknown', name: raw }; }
  };
  const clearDropHints = () =>
    $$('#cardSnaps .snap--drop').forEach(x => x.classList.remove('snap--drop'));
  const dropSlotOf = (e) => e.target.closest('[data-snap], [data-snap-add]');

  // 从卡片区拖出：只允许「功能卡片」作为来源
  $('#cardSections').addEventListener('dragstart', e => {
    const c = e.target.closest('[data-card]');
    if (!c) return;
    e.dataTransfer.effectAllowed = 'copyMove';
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify({ kind: 'card', name: c.dataset.card }));
    e.dataTransfer.setData('text/plain', JSON.stringify({ kind: 'card', name: c.dataset.card }));
  });

  // 从快照条拖出：记录来源下标
  $('#cardSnaps').addEventListener('dragstart', e => {
    const s = e.target.closest('[data-snap]');
    if (!s) return;
    e.dataTransfer.effectAllowed = 'copyMove';
    const payload = JSON.stringify({ kind: 'snap', index: Number(s.dataset.snap),
                                     name: s.dataset.snapName });
    e.dataTransfer.setData(DRAG_MIME, payload);
    e.dataTransfer.setData('text/plain', payload);
  });

  // dragenter 也要 preventDefault：有些浏览器只在 enter 时判定是否接受，
  // 只写 dragover 会出现"高亮正常但松手不触发 drop"
  $('#cardSnaps').addEventListener('dragenter', e => {
    if (!dropSlotOf(e)) return;
    e.preventDefault();
  });
  $('#cardSnaps').addEventListener('dragover', e => {
    const slot = dropSlotOf(e);
    if (!slot) return;
    e.preventDefault();                       // 不 preventDefault 就不会触发 drop
    // 来源 effectAllowed 是 copyMove，这里 copy/move 都在允许集合内
    e.dataTransfer.dropEffect = slot.dataset.snapAdd ? 'copy' : 'move';
    clearDropHints();
    slot.classList.add('snap--drop');
  });
  $('#cardSnaps').addEventListener('dragleave', e => {
    if (!e.relatedTarget || !$('#cardSnaps').contains(e.relatedTarget)) clearDropHints();
  });
  $('#cardSnaps').addEventListener('drop', async e => {
    const slot = dropSlotOf(e);
    if (!slot) return;
    e.preventDefault();
    clearDropHints();
    const src = readDrag(e);
    if (!src) {
      // 读不到载荷时明确报错，不要静默什么都不做
      addLog('✗ 快照拖拽：读不到拖拽数据', 'err');
      toast('拖拽失败', '读不到拖拽数据，请再试一次', 'error');
      return;
    }

    const isAdd = !!slot.dataset.snapAdd;
    const to = isAdd ? SNAPS.length : Number(slot.dataset.snap);
    const next = SNAPS.map(s => s.name);

    if (src.kind === 'card') {
      if (!src.name) return;
      if (isAdd) {
        if (next.includes(src.name)) { toast('已经在快照里了', src.name); return; }
        next.push(src.name);
      } else {
        if (next[to] === src.name) return;
        // 这张卡已经在别的格子里 → 两格互换，而不是复制一份。
        // 复制会让后端去重时把被顶掉的那张直接丢掉，用户看着像"凭空少了一个"。
        const from = next.indexOf(src.name);
        if (from >= 0) [next[from], next[to]] = [next[to], next[from]];
        else next[to] = src.name;
      }
    } else if (src.kind === 'snap') {
      if (isAdd) return;                      // 快照拖到 ＋ 没意义
      const from = Number(src.index);
      if (!Number.isInteger(from) || from === to || !next[from]) return;
      // 拖到别的格 = 两格互换（比"插入"更符合"替换"的直觉）
      [next[from], next[to]] = [next[to], next[from]];
    } else return;

    // 先乐观更新界面，失败再回滚
    window.applyServerCards({ snapshots: next });
    await saveSnapshots(next);
  });
  $('#cardSnaps').addEventListener('dragend', clearDropHints);
  // dragend 是在**拖动源**上触发的：从卡片区拖过来时它不会经过 #cardSnaps，
  // 只挂上面那一处的话，落点高亮会一直留着。document 上再兜一次。
  document.addEventListener('dragend', clearDropHints);
  document.addEventListener('drop', clearDropHints);

  // 右键快照：恢复默认 / 换掉这一格
  $('#cardSnaps').addEventListener('contextmenu', e => {
    const s = e.target.closest('[data-snap]');
    if (!s) return;
    e.preventDefault();
    const idx = Number(s.dataset.snap);
    const el = $('#ctxmenu');
    el.innerHTML = `
      <div class="ctxmenu__head">快照 · ${escHtml(SNAPS[idx].name)}</div>
      <button class="ctxmenu__item" data-sact="run">加入执行链</button>
      <button class="ctxmenu__item" data-sact="full">打开卡片库挑一张替换…</button>
      <div class="ctxmenu__sep"></div>
      <button class="ctxmenu__item" data-sact="reset">恢复默认 5 个快照</button>`;
    el.hidden = false;
    const r = el.getBoundingClientRect();
    el.style.left = Math.max(6, Math.min(e.clientX, innerWidth - r.width - 6)) + 'px';
    el.style.top = Math.max(6, Math.min(e.clientY, innerHeight - r.height - 6)) + 'px';
    el.onclick = async (ev) => {
      const btn = ev.target.closest('[data-sact]');
      if (!btn) return;
      el.hidden = true;
      el.onclick = null;
      const act = btn.dataset.sact;
      if (act === 'run') { addToChain(SNAPS[idx].name); return; }
      if (act === 'full') {
        applyStop('full'); setSearch(false);
        toast('把卡片拖到快照上即可替换', `正在替换第 ${idx + 1} 个`);
        window.__snapReplaceHint = idx;
        return;
      }
      if (act === 'reset') {
        try {
          const d = await API.resetSnapshots();
          window.applyServerCards({ snapshots: d.snapshots });
          toast('快照已恢复默认', d.snapshots.join(' / '));
          addLog('⇄ 快照恢复默认', 'ok');
        } catch (err) {
          toast('恢复失败', String(err.message || err), 'error');
        }
      }
    };
  });

  /* ---------- 功能卡片：右键菜单（编辑 / 另存为 / 删除） ---------- */
  $('#cardSections').addEventListener('contextmenu', e => {
    const el = e.target.closest('[data-card]');
    if (!el) return;
    e.preventDefault();
    openCardMenu(e.clientX, e.clientY, el.dataset.card);
  });
  // 也支持键盘唤出（Shift+F10 / 菜单键），不然只能用鼠标
  $('#cardSections').addEventListener('keydown', e => {
    if (e.key !== 'ContextMenu' && !(e.shiftKey && e.key === 'F10')) return;
    const el = e.target.closest('[data-card]');
    if (!el) return;
    e.preventDefault();
    const r = el.getBoundingClientRect();
    openCardMenu(r.left + 24, r.top + 20, el.dataset.card);
  });

  /* ---------- 卡片编辑器 ---------- */
  // 「新建自定义卡片」在 #cardSections 里，而那个容器会被 renderCardSections()
  // 整个重建（搜索、卡片增删都会触发）—— 直接绑在按钮上，第一次重渲染就失效。
  // 所以走委托，和卡片点击放在同一个处理器里。
  $('#cardSave').addEventListener('click', () => saveCard(false));
  $('#cardSaveAs').addEventListener('click', () => saveCard(true));
  $('#cardDelete').addEventListener('click', () => deleteCard());

  $('#cardOp').addEventListener('change', e => {
    const op = e.target.value;
    $('#cardOpDesc').textContent = (OPS_CATALOG[op] || {}).desc || '';
    // 换操作就重置参数为该操作的默认值，别把上一个操作的参数带过去
    renderParamForm(op, null);
    const cur = $('#cardIcons [aria-checked="true"]');
    if (cur) renderIconPick((OPS_CATALOG[op] || {}).icon || 'wave');
    schedulePreview();
  });

  // 参数变化：可能影响 onlyIf（比如 format 从 flac 改成 mp3），需要重渲染
  $('#cardParams').addEventListener('input', () => { syncParamForm(); schedulePreview(); });
  $('#cardParams').addEventListener('change', () => { syncParamForm(); schedulePreview(); });

  $('#cardIcons').addEventListener('click', e => {
    const b = e.target.closest('[data-ico]'); if (!b) return;
    $$('#cardIcons .iconpick__btn').forEach(x => x.setAttribute('aria-checked', String(x === b)));
  });

  // 卡片编辑器（#cardModal）—— 取消按钮关的是它自己
  $('#cardModal').addEventListener('click', e => {
    if (e.target.closest('[data-close]')) closeModal($('#cardModal'));
  });
  
  // 元数据编辑器（#metaModal）—— 取消按钮关它自己
  $('#metaModal').addEventListener('click', e => {
    if (e.target.closest('[data-close]')) closeMeta();
  });
  $('#metaSave').addEventListener('click', () => { saveMeta(); });

  // 文件卡片：勾选 / 聚焦 / 点元数据框开编辑
  $('#fileList').addEventListener('click', e => {
    const pick = e.target.closest('[data-check]');
    if (pick) { setChecked(pick.dataset.check, pick.checked); return; }
    if (e.target.closest('.metabox')) {         // 整块元数据框即编辑入口
      openMeta(e.target.closest('.metabox').dataset.edit);
      return;
    }
    const card = e.target.closest('.card');
    if (card) setFocused(card.dataset.id);
  });

  // 波形点击跳转与播放按钮由 bindPlayer() 用事件委托接管
  // （之前这里给当时的 canvas 直接绑了 click，卡片一重渲染就全成死代码，
  //   而且只挪指针不碰播放位置 —— 假的跳转）

  // 全选 / 反选 / 取消
  // 注意：必须先把目标状态存成局部量。setChecked → syncBulkbar 会重算
  // #selectAll.checked（只选中 1/6 时它被置回 false），
  // 若在循环里继续读 e.target.checked，就会"选一个又反选回去"，最终只有 1 个生效。
  $('#selectAll').addEventListener('change', e => {
    const on = e.target.checked;
    FILES.forEach(f => setChecked(f.id, on));
    syncBulkbar();                            // 循环结束后再统一校正
  });
  $('#invertSel').addEventListener('click', () => {
    const next = FILES.map(f => ({ id: f.id, on: !f.checked }));
    next.forEach(x => setChecked(x.id, x.on));
    syncBulkbar();
  });
  $('#bulkClear').addEventListener('click', () => {
    FILES.forEach(f => setChecked(f.id, false));
    syncBulkbar();
  });

  // 元数据框：点击（或回车/空格）打开编辑模态
  // 文件卡片：勾选 / 聚焦 / 点元数据框开编辑
  $('#fileList').addEventListener('keydown', e => {
    // 元数据框：Enter / 空格 打开编辑
    const box = e.target.closest('.metabox');
    if (box && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      openMeta(box.dataset.edit);
      return;
    }
  
    // 空格：切播放/暂停
    if (e.key !== ' ') return;
  
    // ① 焦点在播放按钮上
    //    不 preventDefault 的话，keyup 还会补一次 click → 一按切两次
    const playBtn = e.target.closest('[data-play-toggle]');
    if (playBtn) {
      e.preventDefault();
      togglePlay(playBtn.dataset.playToggle);
      return;
    }
  
    // ② 焦点在卡片本身上（tabindex="0"），且不在任何控件内 → 空格也切播放
    const card = e.target.closest('.card');
    if (card && !e.target.closest('button, input, textarea, select, [contenteditable]')) {
      e.preventDefault();
      togglePlay(card.dataset.id);
    }
  });

  // 封面格：点「导入/更换」选图并嵌入；点「移除」调 remove-cover。
  // 图加载失败（后端 404 或图坏了）就把 cover--has 摘掉，占位图标自动显示。
  $('#fileList').addEventListener('error', e => {
    const img = e.target.closest && e.target.closest('.cover__img');
    if (img) img.closest('.cover').classList.remove('cover--has');
  }, true);   // 捕获阶段：img 的 error 不冒泡
  $('#fileList').addEventListener('click', e => {
    const setBtn = e.target.closest('[data-cover-set]');
    if (setBtn) { e.stopPropagation(); importCover(setBtn.dataset.coverSet); return; }
    const delBtn = e.target.closest('[data-cover-del]');
    if (delBtn) { e.stopPropagation(); removeCover(delBtn.dataset.coverDel); return; }
    const cell = e.target.closest('[data-cover]');
    if (cell) { e.stopPropagation(); importCover(cell.dataset.cover); }
  });

  $('#bulkbar').addEventListener('click', async e => {
    const b = e.target.closest('[data-bulk]'); if (!b) return;
    const ids = selectedIds();
    if (!ids.length) { toast('未选中文件', '先勾选再操作', 'error'); return; }
    b.disabled = true;
    try {
      await runBulk(b.dataset.bulk, ids);
    } finally {
      b.disabled = false;
    }
  });

  // 日志清空
  $('#clearLog').addEventListener('click', () => { $('#logList').innerHTML = ''; });

  // 通知
  $('#noticeClose').addEventListener('click', () => { $('#notice').hidden = true; });


  // 侧栏分隔条拖拽
  const pane = $('.pane--queue');
  const sp = $('#sidebarSplitter');
  let splitDrag = false;
  const onMove = e => {
    if (!splitDrag) return;
    const side = $('.sidebar').getBoundingClientRect();
    const pct = ((e.clientY - side.top) / side.height) * 100;
    pane.style.flexBasis = Math.max(18, Math.min(72, pct)) + '%';
  };
  sp.addEventListener('pointerdown', e => {
    splitDrag = true; sp.setPointerCapture(e.pointerId); document.body.style.userSelect = 'none';
  });
  sp.addEventListener('pointermove', onMove);
  sp.addEventListener('pointerup', () => { splitDrag = false; document.body.style.userSelect = ''; });

  // 抽屉把手：拖动改变"面板高"，松手吸附到最近一档
  const handle = $('#drawerHandle');
  let drag = null;                          // {y0, top0, moved}

  /** 当前档位序号（'closed' | 'mid' | 'full'） */
  const ORDER = ['closed', 'mid', 'full'];

  /** 离给定盒顶最近的一档。
   *  拖拽途中刷新 data-stop（圆角/背板不滞后）与松手吸附都用它。
   *  档距不等（closed→mid 433px、mid→full 243px），所以只能比距离，
   *  不能用固定像素阈值。 */
  function nearestStop(top) {
    const s = stops();
    let best = ORDER[0], bestD = Infinity;
    for (const name of ORDER) {
      const d = Math.abs(top - s[name].top);
      if (d < bestD) { bestD = d; best = name; }
    }
    return best;
  }

  handle.addEventListener('pointerdown', e => {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    drag = { y0: e.clientY, top0: curTop, moved: false };
    document.body.style.userSelect = 'none';
  });

  handle.addEventListener('pointermove', e => {
    if (!drag) return;
    const dy = e.clientY - drag.y0;          // 向下拖为正 → 收起方向
    if (!drag.moved && Math.abs(dy) < SNAP_MIN) return;
    if (!drag.moved) {
      drag.moved = true;
      $('#drawer').dataset.dragging = 'true'; // 拖拽中关过渡，跟手
    }
    // 1:1 跟手：盒顶一路跟着指针走，**只在整个区间的两端夹住**。
    // 之前这里把行程限死在"本档 ↔ 相邻档"之间（一次手势只走一档），
    // 于是从 closed 拖到顶端也只到 mid、松手还回弹；现在放开整段，
    // 一次手势可以从 closed 直接拖到 full。
    const s = stops();
    const top = Math.max(s.full.top, Math.min(s.closed.top, drag.top0 + dy));
    // 途中就把 data-stop 指向"松手会落到的档"，圆角与背板才不会等到松手才变
    applyTop(top, nearestStop(top));
  });

  const endDrag = () => {
    if (!drag) return;
    const wasDrag = drag.moved;
    document.body.style.userSelect = '';
    delete $('#drawer').dataset.dragging;    // 恢复过渡 → 吸附过程有动画

    if (!wasDrag) {
      cycleDrawer();                         // 无位移 = 点击：展开 / 折叠
      drag = null;
      return;
    }

    // 吸附到最近一档：拖过中点就换档，不到中点回弹。
    // 中点天然是"两档之间"，所以"不足一半回弹"这条依然成立。
    applyStop(nearestStop(curTop));
    drag = null;
  };
  handle.addEventListener('pointerup', endDrag);
  handle.addEventListener('pointercancel', endDrag);

  // 键盘可达：Enter / Space 三档轮换
  handle.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); cycleDrawer(); }
  });

  // 窗口尺寸变化时重绘波形，并按新视口重算抽屉档位与左边距
  let rt = null;
  window.addEventListener('resize', () => {
    syncDrawerLeft();
    applyStop(drawerState);
    clearTimeout(rt);
    rt = setTimeout(redrawAllWaves, 120);
  });

  // 侧栏宽度可能在「没有窗口 resize」的情况下变化：字体加载完成、出现/消失
  // 滚动条、队列列表变长。只在 init 里量一次会留下固定偏差（实测 init 时
  // 侧栏右缘 252，稳定后 282，抽屉按 266 落下 → 压住侧栏 16px）。
  // 所以直接观察侧栏本身，任何宽度变化都重新对齐。
  const sb = document.querySelector('.sidebar');
  if (sb && typeof ResizeObserver !== 'undefined') {
    let lastRight = sb.getBoundingClientRect().right;
    new ResizeObserver(() => {
      const r = Math.round(sb.getBoundingClientRect().right);
      if (r === lastRight) return;
      lastRight = r;
      syncDrawerLeft();
    }).observe(sb);
  }

  // 快捷键：Esc 关抽屉/模态
  window.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (!$('#metaModal').hidden) $('#metaModal').hidden = true;
    else if (drawerState !== 'closed') applyStop('closed');
  });
}

/* ------------------------------------------------- 后端数据接入（api.js 调用） */

/** 用后端文件列表替换本地 mock，并重绘卡片 */
window.applyServerFiles = function (files) {
  const list = Array.isArray(files) ? files : [];
  FILES.length = 0;
  list.forEach((f) => {
    const info = f.info || {};
    const tg = info.tags || {};
    FILES.push({
      id: f.id,
      title: tg.title || f.name.replace(/\.[^.]+$/, ''),
      artist: tg.artist || '—',
      album: tg.album || '—',
      track: tg.tracknumber || '—',
      format: info.format || (f.name.split('.').pop() || '').toUpperCase(),
      rate: info.sampleRate || 0,
      depth: info.bits || 0,
      ch: info.channels ? `${info.channels} ch` : '—',
      dur: info.duration || 0,
      size: fmtBytes(f.size),
      status: mapState(f.state),
      progress: (f.tasks && f.tasks.progress) || 0,
      peaks: f._peaks || 'none',
      cover: !!info.hasCover,
      checked: false,
      err: (f.tasks && f.tasks.lastError) || '',
      _server: f,
    });
  });
  renderFiles();
  renderEmptyState();
  syncBulkbar();
  // 正在试听的文件被删了/被别的操作换掉了：停掉播放器，别留一个指向 404 的 audio
  if (playingId && !FILES.some(f => f.id === playingId)) {
    const el = playerEl();
    if (el) { el.pause(); el.removeAttribute('src'); el.load(); }
    stopTick();
    playingId = null;
  }
  // 波形：优先用后端真实峰值，取不到才退回落空
  requestAnimationFrame(() => { loadAllPeaks(); });
};

/** 列表为空时给出明确提示，而不是显示任何假数据 */
function renderEmptyState() {
  const box = $('#fileList');
  if (FILES.length) return;
  box.innerHTML = `
    <div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"
           stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>
      </svg>
      <strong>还没有音频文件</strong>
      <span>把文件或文件夹拖到页面上即可导入</span>
    </div>`;
}

/** 逐个拉取真实峰值数据并缓存 */
const peakCache = new Map();
async function loadAllPeaks() {
  if (typeof API === 'undefined') return;
  for (const f of FILES) {
    if (peakCache.has(f.id)) { f.peaks = 'cached'; continue; }
    try {
      const d = await API.peaks(f.id, 1000);
      peakCache.set(f.id, d);
      f.peaks = 'cached';
    } catch {
      f.peaks = 'none';
    }
  }
  $$('[data-wave]').forEach(cv => drawWave(cv, cv.dataset.wave));
}

function fmtBytes(n) {
  if (!n) return '—';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}

function mapState(s) {
  return ({ uploaded: 'uploaded', ready: 'ready', processing: 'processing',
            done: 'done', failed: 'failed', deleted: 'deleted' })[s] || 'ready';
}

/** 用后端队列替换左侧任务列表（字段名对齐后端 TaskRow） */
window.applyServerQueue = function (q) {
  if (!q) return;
  const rows = [...(q.running || []), ...(q.pending || []),
                ...(q.recent || []).filter((t) => !['running', 'pending'].includes(t.state))];
  TASKS.length = 0;
  rows.slice(0, 12).forEach((t) => {
    TASKS.push({
      id: t.id,
      name: `${t.type} · ${shortName(t.fileId)}`,
      state: ({ running: 'running', pending: 'pending', success: 'success',
                failed: 'failed', cancelled: 'failed' })[t.state] || 'pending',
      pct: t.progress || 0,
      meta: t.state === 'pending' ? '排队中'
            : (t.result && t.result.output) || (t.state === 'running' ? '执行中' : ''),
      err: t.error || '',
      retryId: t.state === 'failed' ? t.id : null,
      // 产物路径：成功且有输出时给个下载入口，不然生成的东西只能自己去翻文件夹
      output: (t.state === 'success' && t.result && t.result.output) || null,
    });
  });
  renderQueue();
  const c = q.counts || {};
  const agg = $('#queueAgg');
  if (agg) {
    agg.textContent = (c.total === 0)
      ? '队列为空'
      : `运行 ${c.running || 0} · 排队 ${c.pending || 0} · 共 ${c.total || 0}`;
  }
};

function shortName(fid) {
  if (!fid) return '批量';
  const f = FILES.find((x) => x.id === fid);
  if (f) return (f.title || f.id).slice(0, 20);
  // recent 里会残留已删除文件的历史任务，别把裸 id 丢给用户看
  return '已移除的文件';
}

/** 日志面板的真实来源。首次接入时清掉内置示例行，避免残留假日志 */
let logsPrimed = false;
window.applyServerLogs = function (items) {
  if (!logsPrimed) { LOGS.length = 0; logsPrimed = true; }
  for (const e of items) LOGS.push({ t: e.time, m: e.message, k: e.kind || '' });
  while (LOGS.length > 500) LOGS.shift();
  renderLogs();
  const box = $('#logList');
  if (box) box.scrollTop = box.scrollHeight;
};

/** 卡片与快照由后端提供 */
window.applyServerCards = function (payload) {
  if (!payload) return;
  if (Array.isArray(payload.cards) && payload.cards.length) {
    CARDS.length = 0;
    payload.cards.forEach(c => CARDS.push(c));
  }
  if (Array.isArray(payload.categories) && payload.categories.length) {
    CATEGORIES.length = 0;
    payload.categories.forEach(c => CATEGORIES.push(c));
  }
  if (Array.isArray(payload.snapshots) && payload.snapshots.length) {
    const byName = new Map(CARDS.map(c => [c.name, c]));
    SNAPS.length = 0;
    payload.snapshots.forEach((n) => {
      const c = byName.get(n);
      if (c) SNAPS.push({ name: c.name, hint: c.tier, ico: c.ico, op: c.op, params: c.params });
    });
  }
  renderSnaps();
  renderCardSections();
};

/** 连不上后端：清空所有写死数据，不留假象 */
window.applyOffline = function () {
  FILES.length = 0;
  TASKS.length = 0;
  LOGS.length = 0;
  // 卡片与快照也要清：它们同样是"从后端来的数据"。
  // 断网时留着本地副本 = 显示一排点下去执行不了的卡片，那就是假数据。
  CARDS.length = 0;
  SNAPS.length = 0;
  offlineMode = true;
  logsPrimed = true;               // 不要再回填内置示例
  renderFiles();
  renderEmptyState();
  renderQueue();
  renderLogs();
  renderSnaps();
  renderCardSections('');
  const agg = $('#queueAgg');
  if (agg) agg.textContent = '未连接';
};

window.applyServerHealth = function (h) {
  const engine = document.querySelector('.engine');
  if (!engine || !h.tools) return;
  engine.innerHTML = Object.entries(h.tools).map(([k, v]) =>
    `<span class="engine__k">${k}</span><span class="engine__v">${
      v.ok ? ((v.version || '').match(/\d+\.\d+(\.\d+)?/) || ['OK'])[0] : '缺失'
    }</span>`).join('');
};
/* 通用：让 container 内的 itemSelector 元素跟随光标写入 --mx/--my/--nx/--ny */
function bindFollow(container, itemSelector) {
  if (!container || container.dataset.followBound) return;
  container.dataset.followBound = '1';
  if (!window.matchMedia('(hover: hover)').matches) return;

  container.addEventListener('pointermove', (e) => {
    const el = e.target.closest(itemSelector);
    if (!el || !container.contains(el)) return;

    const r = el.getBoundingClientRect();
    const w = r.width || 1;
    const h = r.height || 1;
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;

    el.style.setProperty('--mx', x + 'px');
    el.style.setProperty('--my', y + 'px');
    el.style.setProperty('--nx', ((x / w) * 2 - 1).toFixed(3));
    el.style.setProperty('--ny', ((y / h) * 2 - 1).toFixed(3));
  });

  container.addEventListener('pointerout', (e) => {
    const el = e.target.closest(itemSelector);
    if (!el || !container.contains(el)) return;
    const to = e.relatedTarget;
    if (to && el.contains(to)) return;
    ['--mx', '--my', '--nx', '--ny'].forEach(p => el.style.removeProperty(p));
  });
}

function initCardFollow() {
  bindFollow(document.getElementById('cardSections'), '.fcard');
}

function initSnapFollow() {
  bindFollow(document.querySelector('.snaps'), '.snap');
}

function initCardTap() {
  const container = document.getElementById('cardSections');
  if (!container || container.dataset.tapBound) return;
  container.dataset.tapBound = '1';

  container.addEventListener('pointerdown', (e) => {
    const card = e.target.closest('.fcard');
    if (!card || !container.contains(card)) return;

    // 避免连点堆动画
    card.classList.remove('is-tapped');
    // 强制重排，让 animation 重头播
    void card.offsetWidth;
    card.classList.add('is-tapped');

    // 动画结束就移除类，不然下一次点击不会重播
    const onEnd = () => {
      card.classList.remove('is-tapped');
      card.removeEventListener('animationend', onEnd);
    };
    card.addEventListener('animationend', onEnd, { once: true });
  });
}

/* 标记/取消某张卡片的「已加入」状态 */
function markCardAdded(card, added) {
  if (!card) return;
  card.classList.toggle('is-added', !!added);
  card.setAttribute('aria-pressed', added ? 'true' : 'false');
}

/* 根据执行链当前内容，同步所有卡片的对勾 */
function syncCardAddedState() {
  const names = new Set(chain.map(s => s.name));

  // 卡片
  document.querySelectorAll('#cardSections .fcard').forEach(card => {
    const on = names.has(card.dataset.card);
    card.classList.toggle('is-added', on);
    card.setAttribute('aria-pressed', on ? 'true' : 'false');
  });

  // 快照条
  document.querySelectorAll('.snap').forEach(snap => {
    const name = snap.dataset.snapName;
    const on = !!name && names.has(name);
    snap.classList.toggle('is-added', on);
    snap.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

/* --------------------------------------------------------------- 启动 */

function init() {
  const t = localStorage.getItem('ae-theme');
  if (t) {
    document.documentElement.dataset.theme = t;
    $$('[data-set-theme]').forEach(x => x.setAttribute('aria-pressed', String(x.dataset.setTheme === t)));
  }
  if (localStorage.getItem('ae-mode') === 'dark') document.documentElement.dataset.mode = 'dark';

  renderQueue();
  renderLogs();
  renderFiles();
  renderEmptyState();
  renderSnaps();
  renderCardSections();
  renderChain();
  setSearch(false);
  syncBulkbar();
  bind();

  initCardFollow(); 
  initSnapFollow();

  syncDrawerLeft();
  applyStop('closed');
  requestAnimationFrame(redrawAllWaves);
}

document.addEventListener('DOMContentLoaded', init);
