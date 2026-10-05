/* ============================================================================
   AudioEdition 前端 · 视图与交互
   数据全部来自 FastAPI 后端；页面启动时 FILES / TASKS / LOGS 均为空数组，
   断网时走 window.applyOffline()，只显示空状态，不伪造任何数据。
   配色契约（见 ui/theme.css 顶部）：bg 与 tint 系列配 --ink，fill 系列配 --on-fill
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

/** 从 ui/theme.css 的令牌里取色，保证波形跟随主题 */
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
      const badge = { running: 'badge--run', success: 'badge--done', failed: 'badge--failed',
                      skipped: 'badge--idle', pending: 'badge--idle' }[t.state] || 'badge--idle';
      const label = { running: '执行中', success: '成功', failed: '失败',
                      skipped: '已跳过', pending: '排队' }[t.state] || '排队';
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
  <article class="card${f.checked ? ' is-checked' : ''}${playingId === f.id ? ' is-playing' : ''}" data-id="${f.id}" tabindex="0">
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
          <span class="kv"><span class="kv__k">Loudness</span><span class="kv__v">${
            f.loudness == null || !isFinite(f.loudness)
              ? '—' : f.loudness.toFixed(1) + ' LUFS'}</span></span>
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
 * 给「新建 / 另存为」挑一个**不与现有卡片重名**的默认名：
 * `X 副本` → `X 副本 2` → `X 副本 3` …
 *
 * 这只是**默认值**上的便利。真正的判据在后端（`cards.validate_card` 拒绝重名，
 * 因为卡片库整套是按名字认卡的）；这里给一个能直接用的名字，省掉"点保存被 400
 * 顶回来、再自己想一个"这一步。用户想改成什么都行。
 */
function freeCardName(base) {
  const taken = new Set(CARDS.map(c => c.name));
  if (!taken.has(base)) return base;
  for (let i = 2; i <= 99; i++) {
    const n = `${base} ${i}`;
    if (!taken.has(n)) return n;
  }
  return `${base} ${Date.now()}`;
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
  // 这一按会不会**新建**一张卡：另存为算，**内置卡也算**（内置卡不能原地改，
  // 那个「保存」按钮的文案就是「创建卡片」）。这两种情况默认名必须避开已用掉的
  // 名字 —— 而最常见的动作恰恰是"点内置卡 → 直接保存"，预填成内置卡本名的话
  // 这一按必然被后端拒（以前更糟：它静默建出一张同名的自定义卡，而那张卡在
  // 库里点不开，详见 `cards/validate.py` 里那段注释）。
  const willCreate = mode !== 'edit' || isBuiltin;
  const name = card ? (willCreate ? freeCardName(`${card.name} 副本`) : card.name) : '';

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
        const r2 = await API.op(c.op, { ...currentTheme(), fileIds: ids, ...params });
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
  // 主题一并带上：服务端渲染的产物（响度 SVG）要靠它上色，见 `currentTheme()`
  const r = await API.op(op, { ...currentTheme(), fileIds: ids, ...(params || {}) });
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

/* 波形要用的令牌，一次读齐并按 (theme, mode) 缓存。
   原来 tok() 是在**逐柱循环内**被调用的（一根柱子一次 getComputedStyle），
   一张 900px 宽的波形约 900–1200 次 —— 全是为了确认"颜色还是那个颜色"。
   令牌只随 data-theme / data-mode 变，所以按这两个值做缓存键是完备的。 */
let _waveTok = { key: null, v: null };
function waveTokens() {
  const key = (document.documentElement.dataset.theme || '')
            + '|' + (document.documentElement.dataset.mode || '');
  if (_waveTok.key === key && _waveTok.v) return _waveTok.v;
  const v = {
    bg:     tok('--bg-surface'),
    border: tok('--border'),
    muted:  tok('--text-muted') || tok('--ink'),
    fill:   tok('--fill-primary'),
    clip:   tok('--error-solid'),
    cap:    tok('--on-fill', 0.45),
    font10: '10px ' + cssVar('--font-sans'),
    font11: '11px ' + cssVar('--font-sans'),
  };
  _waveTok = { key, v };
  return v;
}

/** 波形绘制：优先用后端真实峰值；没有数据时明确画占位，不伪造波形 */
function drawWave(canvas, id) {
  const f = FILES.find(x => x.id === id);
  if (!f) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  /* 只有尺寸真的变了才动 width/height：给它们赋值会清空画布并**重新分配后备缓冲**，
     每次重绘都做一次是纯浪费（也是 GC 的一个来源）。 */
  const bw = Math.round(w * dpr), bh = Math.round(h * dpr);
  if (canvas.width !== bw || canvas.height !== bh) { canvas.width = bw; canvas.height = bh; }

  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const T = waveTokens();
  ctx.fillStyle = T.bg;
  ctx.fillRect(0, 0, w, h);

  const data = (typeof peakCache !== 'undefined') ? peakCache.get(id) : null;
  const laneH = h / 2;
  const gap = 1.5;

  if (!data || !Array.isArray(data.peaks) || !data.peaks.length) {
    // 无峰值数据：画一条中线 + 提示，不画假波形。
    // 非音频文件要说清楚是"压根没有波形"，而不是建议他"重建峰值图" ——
    // 对一张封面图来说，那个建议是错的。
    for (let lane = 0; lane < 2; lane++) {
      const cy = lane * laneH + laneH / 2;
      ctx.fillStyle = T.border;
      ctx.fillRect(0, cy, w, 1);
    }
    const notAudio = f.kind && f.kind !== 'audio';
    const msg = notAudio
      ? `${f.kind === 'image' ? '图片' : '非音频文件'} · 没有波形`
      : '暂无峰值数据 · 右键卡片可「重建峰值图」';
    ctx.fillStyle = T.muted;
    ctx.font = T.font11;
    ctx.textAlign = 'center';
    ctx.fillText(msg, w / 2, h / 2 + 4);
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
      ctx.fillStyle = isClip ? T.clip : T.fill;
      ctx.fillRect(x, cy - amp, barW, amp * 2);
      if (!isClip) {
        ctx.fillStyle = T.cap;
        ctx.fillRect(x, cy - amp, barW, 1);
        ctx.fillRect(x, cy + amp - 1, barW, 1);
      }
    }
    ctx.fillStyle = T.border;
    ctx.fillRect(0, cy, w, 1);
  }

  ctx.fillStyle = T.muted;
  ctx.font = T.font10;
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
  /* 抽屉一动，里面的卡片就跟着移：跟随用的矩形缓存必须作废。
     拖拽时这里每帧都会调，但那时指针在把手上、不会触发 flushFollow，
     所以逐帧作废是免费的（不会变成逐帧强制布局）。 */
  invalidateFollowRects();
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
/** 收起时过滤词还在 → 搜索按钮上点一个小标记。
 *  否则"卡片怎么少了一半"没人知道是搜索过滤造成的。 */
function syncSearchFlag() {
  const q = ($('#cardSearch')?.value ?? '').trim();
  $('#searchDock').dataset.filtered = q ? 'true' : 'false';
  $('#searchToggle').title = q ? `搜索卡片（过滤中：${q}）` : '搜索卡片';
}

/** @param opts.keepQuery  收起时保留已输入的过滤词。
 *  失焦收回走这条路：只改视觉、不重渲染卡片区 —— 收回发生在 click 之前，
 *  在这里重建 #cardSections 会把 mousedown 的目标从 DOM 里摘掉，
 *  「点卡片加入执行链」就整个失效了。 */
function setSearch(open, opts = {}) {
  const dock = $('#searchDock');
  dock.dataset.search = open ? 'open' : 'closed';
  $('#searchToggle').setAttribute('aria-expanded', String(open));

  if (open) {
    if (drawerState === 'closed') applyStop('mid');
    const field = $('#cardSearch');
    requestAnimationFrame(() => field.focus());
    setTimeout(() => { if (dock.dataset.search === 'open') field.focus(); }, 380);
  } else if (opts.keepQuery) {
    syncSearchFlag();
  } else {
    // 1) 先记住哪些分组是收起的
    const collapsed = new Set(
      [...document.querySelectorAll('.cardsec')]
        .filter(s => s.dataset.collapsed === 'true')
        .map(s => s.querySelector('.cardsec__name')?.textContent ?? '')
    );

    $('#cardSearch').value = '';
    renderCardSections('');

    // 2) 重渲染后还原折叠状态
    if (collapsed.size) {
      document.querySelectorAll('.cardsec').forEach(sec => {
        const name = sec.querySelector('.cardsec__name')?.textContent ?? '';
        if (collapsed.has(name)) setCollapsed(sec, true);
      });
    }
    // 3) 顺手把“全部收起/展开”按钮文案同步一下
    syncToggleAllLabel();
    syncSearchFlag();
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
      ? `<section class="cardsec" data-collapsed="false">
           <button class="cardsec__title" type="button" aria-expanded="true">
             <span class="cardsec__arrow" aria-hidden="true"></span>
             <span class="cardsec__name">搜索结果</span>
             <span class="cardsec__count">${hit.length}</span>
           </button>
           <div class="cardsec__wrap">
             <div class="cardsec__grid">${hit.map(cardHTML).join('')}</div>
           </div>
         </section>`
      : `<p class="chain__empty">没有匹配「${query}」的卡片</p>`;
    return;
  }

  const section = (cat, list, extra = '') => {
    if (!list.length && !extra) return '';
    const count = list.length
      ? `<span class="cardsec__count">${list.length}</span>`
      : '';
    return `<section class="cardsec" data-collapsed="false">
        <button class="cardsec__title" type="button" aria-expanded="true">
          <span class="cardsec__arrow" aria-hidden="true"></span>
          <span class="cardsec__name">${escHtml(cat)}</span>
          ${count}
        </button>
        <div class="cardsec__wrap">
          <div class="cardsec__grid">
            ${list.map(cardHTML).join('')}${extra}
          </div>
        </div>
      </section>`;
  };

  const known = new Set(CATEGORIES);
  const html = CATEGORIES.map(cat => {
    if (cat === '自定义') {
      // 这一段以前只渲染「新建」按钮、return 得很早，
      // 结果 cat='自定义' 的自定义卡片建出来之后**根本不显示**。
      const extra = `<button class="fcard fcard--new" id="newCard">
          <span class="fcard__dots" aria-hidden="true"></span>
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
  invalidateFollowRects();      // 同理（见 renderPresets 末尾）：卡片元素换了一批
}
/* ---- 卡片分组折叠（模块级，只绑定一次）---- */

function setCollapsed(sec, collapsed) {
  sec.dataset.collapsed = collapsed ? 'true' : 'false';
  sec.querySelector('.cardsec__title')
     ?.setAttribute('aria-expanded', String(!collapsed));
}

function syncToggleAllLabel() {
  const btn = document.getElementById('cardsecToggleAll');
  if (!btn) return;
  const secs = document.querySelectorAll('#cardSections .cardsec');
  if (!secs.length) return;
  const anyOpen = [...secs].some(s => s.dataset.collapsed !== 'true');
  btn.textContent = anyOpen ? '全部收起' : '全部展开';
}

function bindSectionToggles() {
  const box = document.getElementById('cardSections');
  const all = document.getElementById('cardsecToggleAll');
  if (!box || box.dataset.sectionsBound) return;   // 幂等：重复调用也不会重绑
  box.dataset.sectionsBound = '1';

  box.addEventListener('click', (e) => {
    const btn = e.target.closest('.cardsec__title');
    if (!btn || !box.contains(btn)) return;
    const sec = btn.closest('.cardsec');
    if (!sec) return;
    setCollapsed(sec, sec.dataset.collapsed !== 'true');
    syncToggleAllLabel();
  });

  all?.addEventListener('click', () => {
    const secs = box.querySelectorAll('.cardsec');
    const anyOpen = [...secs].some(s => s.dataset.collapsed !== 'true');
    secs.forEach(s => setCollapsed(s, anyOpen));
    all.textContent = anyOpen ? '全部展开' : '全部收起';
  });
}
function cardHTML(c) {
  return `<button class="fcard${c.custom ? ' fcard--custom' : ''}" data-card="${c.name}"
                  draggable="true"
                  title="点击加入执行链 · 右键编辑 · 拖到顶部快照可替换">
      <span class="fcard__dots" aria-hidden="true"></span>
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
    box.innerHTML = chain.map((s, i) => {
      // 未知卡片那一步要**能被看见**（§9.1.1 二）：链上显示名加「（快照）」后缀，
      // `title` 说明"这张卡已不在卡片库，按加入时的快照执行" ——
      // 不加的话用户会以为执行的是"当前那一张同名卡"，而参数可能早就不同了。
      const unk = isUnknownStep(s);
      const label = escHtml(s.name) + (unk ? '（快照）' : '');
      const tip = unk
        ? '这张卡已不在卡片库，按加入时的快照执行（参数是当时那一份）'
        : '单击只执行这一步';
      return `${i ? '<span class="chain__arrow">→</span>' : ''}
       <span class="chain__item${unk ? ' chain__step--unknown' : ''}"
             title="${escHtml(tip)}">${label}<button data-del="${i}" aria-label="移除 ${escHtml(s.name)}">×</button></span>`;
    }).join('');
  }
  // 有链 + 有选中文件 才可执行
  const run = $('#chainRun');
  if (run) {
    const hasOp = chain.some(s => s.op);
    run.disabled = !chain.length || scope === 0;
    const modeCn = chainMode === 'serial' ? '串行' : '并行';
    run.title = !chain.length ? '执行链为空，先添加卡片'
              : scope === 0 ? '请先勾选要处理的文件'
              : !hasOp ? '链上没有可执行的后端操作'
              : `${modeCn}执行 ${chain.length} 步，作用于 ${scope} 个文件`;
  }
  renderChainMode();
  renderChainNotes();
  syncCardAddedState();
  syncCardAvailability();
}

/** 顶栏那个 串行/并行 开关（方案 §3.7）。 */
function renderChainMode() {
  const box = $('#chainMode');
  if (!box) return;
  box.hidden = !chain.length;        // 链为空时不占地方
  for (const b of box.querySelectorAll('[data-mode]')) {
    b.setAttribute('aria-pressed', String(b.dataset.mode === chainMode));
  }
}

/**
 * 链上"能跑但接不上"的提示（方案 §2.3 的 ⇥）。
 *
 * 判据来自后端：`/api/ops` 每个 op 都带 `produce`/`needs`/`gives`/`consumes`，
 * 所以前端算出来的是**同一份规则**（后端 `chain._parallel_handoff_gap`），
 * 不需要自己再维护一套。这里算的是**并行档**的断口 —— 串行档下产物真的接上了。
 *
 * ⚠⚠ **唯一该报的一格：上一步产出了新文件（`produce='derived'`），
 * 而并行档不会把它交给下一步。** 其余一律**不报**：
 *
 *   · 只读分析（`produce='none'`：探测 / 校验 / 响度总览图）—— 它什么都没留，
 *     下一步读原文件天经地义，**没丢东西**
 *   · 旁路产物（`produce='sidecar'`：波形 / 响度图 / 响度报告 / 打包）——
 *     音频原封不动，下一步读到的就是完整原文件，**也没丢东西**
 *   · 下一步压根不吃上一环（`consumes === false`：校验判定 / 打包收集）
 *
 * 这条**放宽过一次**（用户实测报的）：原来把 `sidecar`/`none` 一并当断口，
 * 于是"导出波形 PNG → 响度分析报告"也弹一条"会回到原文件" ——
 * 而那条链每一步都能正常跑完。一条链上连排两张只读卡就刷一排警告，
 * 每条都在说一件不成问题的事，会训练用户忽略所有提示。
 */
function renderChainNotes() {
  const box = $('#chainNotes');
  if (!box) return;
  if (chain.length < 2 || chainMode === 'serial' || !OPS_CATALOG) {
    box.hidden = true;
    box.innerHTML = '';
    return;
  }
  const notes = [];
  for (let i = 0; i < chain.length - 1; i++) {
    const a = chain[i].op, b = chain[i + 1].op;
    const sa = OPS_CATALOG[a], sb = OPS_CATALOG[b];
    if (!sa || !sb) continue;
    if (sb.consumes === false) continue;          // b 不吃上一环
    if (sa.produce !== 'derived') continue;       // 没产出新文件 → 没丢东西
    notes.push({
      i,
      text: `「${sb.label}」会作用在原文件上，不是「${sa.label}」的产物`,
    });
  }
  if (!notes.length) { box.hidden = true; box.innerHTML = ''; return; }
  box.hidden = false;
  box.innerHTML = notes.map(n =>
    `<span class="chain__note" title="${escHtml(n.text)}">`
    + `⚠ 第 ${n.i + 1} 步：${escHtml(n.text)}`
    + `</span>`).join('')
    + `<button class="btn btn--xs" id="chainFixSerial">改成串行</button>`;
}

/**
 * 卡片可用性置灰（方案 §3.2.4）：**链尾交出什么，决定下一张卡能不能选**。
 *
 * 与后端 `/api/ops/chain` 的校验用的是同一份字段（`needs`/`gives`/`produce`），
 * 所以这里置灰的东西后端一定也拒 —— 前端只是让用户**在点之前**就知道，
 * 而不是点了才收到 400。**判据不能只写在前端**：绕过页面直接 POST 也要拦得住。
 */
function chainTailOp() {
  return chain.length ? chain[chain.length - 1].op : null;
}

function availabilityOf(op) {
  const spec = OPS_CATALOG && OPS_CATALOG[op];
  if (!spec) return { ok: true, why: '' };
  const tail = chainTailOp();
  if (!tail) {
    // 链为空：needs 里有 upstream 的卡不能当第一张
    const needs = spec.needs || [];
    if (needs.includes('upstream')) {
      return { ok: false, why: `「${spec.label}」需要一个上游产物，不能排在最前` };
    }
    return { ok: true, why: '' };
  }
  const tailSpec = OPS_CATALOG[tail];
  if (!tailSpec) return { ok: true, why: '' };
  const give = tailSpec.gives;
  const needs = spec.needs || [];
  const acceptsAny = needs.includes('any');

  // 压缩包：**可选 + 提示**，不再硬禁。
  // 原来这里是"终态，链到头了"（`app.js` 与后端 `boundary.availability` 的第 1 步），
  // 但 `zip` 是 `mode=read` + `consumes=false`：它什么都没改动，后面的步骤
  // 照旧作用于当前文件；而且一条链本来就可以有**多个**打包步骤（每个收集
  // "上一个打包步骤之后"的产物）。判据改动见
  // `执行链打包与串行交接方案.md` §4.4 —— 前后端必须同步，否则
  // `browser_chain_probe.py` 的 op×op 对账会红。
  if (give === 'archive') {
    return { ok: true, why: `「${tailSpec.label}」交出的是压缩包，`
                          + `它不会被递给下一步（后面的步骤作用于当前文件）` };
  }

  // 能不能续下去。判据与后端 `boundary._hands_something_off` **逐字对应**：
  //  · `produce` 是 none（只读分析）/ sidecar（旁路产物）→ 它没动那个音频文件，
  //    后面的卡照旧读原文件，**能续**
  //  · 或者它交出的类型这一张卡接得住
  //  · 或者这一张卡本来就不吃上一环（`consumes === false`）
  //
  // ⚠ 反过来写（先判 `anyAccepts`、再拿 `noHandoff` 去救）踩过坑：
  // `gives='none'` 谁都不收（只有汇总类 `zip` 显式收 `none`），于是
  // 「响度分析报告」之后**除打包以外全部被置灰** —— 正常用法被前端堵死。
  // 判据是"下一步有没有音频可用"，不是"这一步产出了什么"。
  const noHandoff = tailSpec.produce === 'none' || tailSpec.produce === 'sidecar';
  const noUpstream = spec.consumes === false;
  const kindFits = acceptsAny || needs.includes(give);
  if (noHandoff || noUpstream || kindFits) {
    // 音频↔图片这类"大类对不上"的，仍然可选，但要说清会回到原文件
    if (!kindFits && !noHandoff) {
      return { ok: true, why: `「${tailSpec.label}」交出的是图片，`
                            + `「${spec.label}」会回到原文件` };
    }
    return { ok: true, why: '' };
  }
  // 上一步确实交了东西，而这一张卡既接不住那个类型、又指望上游递东西
  return { ok: false, why: `「${tailSpec.label}」交出的东西「${spec.label}」接不住` };
}

/* --------------------------------------------------------- 串行档的格式流
 *
 * **只在不串行时才是问题**（后端 `chain.check_format_flow` 的镜像）。
 *
 * 为什么前端也要算一遍：后端确实拦得住（400 + stepIdx），但那是**提交之后**才知道。
 * 用户能在串行档里把「转 MP3 320 → 转 WAV 24bit」两张卡都加进链、看不出任何异常，
 * 点执行才收到一个错 —— 这就是"串行模式下仍然可以先转 MP3 再转 WAV"的由来。
 * 边界在后端，**体验在前端**：能在点之前置灰的就别让用户点了才报错。
 *
 * 判据与后端逐条对应：
 *   · 只判**第 1 步之后**（`i > 0`）—— 单步把 MP3 转 FLAC 是"塞进无损容器"，放行
 *   · `same` / `image` / `archive` 型步骤不挡格式流（格式穿过去继续往后传）
 *   · 未知格式不猜（源后缀、卡片没写 format）
 *   · 只在 `chainMode === 'serial'` 时生效
 */

/** 有损容器（与后端 `backend/formats.py` 的 LOSSY_FORMATS 对齐）。 */
const LOSSY_FORMATS = ['mp3', 'aac', 'm4a', 'ogg', 'opus', 'wma'];
/** 无损容器（能转换到的那些，与后端 AUDIO_FORMATS - LOSSY_FORMATS 对齐）。 */
const LOSSLESS_FORMATS = ['flac', 'wav', 'aiff'];

/** 作用域里第一个文件的容器后缀。取不到就是 null（未知，不判）。 */
function sourceFormatOfScope() {
  const ids = selectedIds();
  const f = FILES.find(x => ids.includes(x.id)) || FILES[0];
  if (!f) return null;
  const p = String(f.relPath || f.name || '');
  const m = p.match(/\.([A-Za-z0-9]+)$/);
  return m ? m[1].toLowerCase() : null;
}

/** `"320k"` / `320` → `320`（kbps）。认不出返回 null（不猜）。 */
function parseBitrateKbps(v) {
  if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
  if (typeof v === 'number') return v > 0 ? v : null;
  const m = String(v).trim().match(/^(\d+(?:\.\d+)?)\s*[kK]?$/);
  if (!m) return null;
  const n = parseFloat(m[1]);
  return n > 0 ? n : null;
}

/** 这一步跑完之后的码率（kbps）。转换卡没写 bitrate → 按未知。
 *  ⚠ 与后端 `chain._step_bitrate_kbps` 同义：**不拿编解码器默认值去猜**，
 *  猜错会误拦用户（"转 MP3"不写码率时 ffmpeg 有自己的默认值）。 */
function stepBitrate(op, params) {
  const spec = OPS_CATALOG && OPS_CATALOG[op];
  if (!spec || spec.gives_formats !== 'param:format') return undefined;  // 沿用
  return parseBitrateKbps((params || {}).bitrate);
}

/** 这一步跑完之后的采样率（Hz）。只认显式的 sampleRate 参数。 */
function stepSampleRate(op, params) {
  const spec = OPS_CATALOG && OPS_CATALOG[op];
  if (!spec || spec.gives_formats !== 'param:format') return undefined;
  const n = parseInt((params || {}).sampleRate, 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** 作用域源文件的格式/码率/采样率。
 *  取不到、或几个文件不一致 ⇒ 那一项是 `null`（未知，不判）。 */
function sourceAudioOfScope() {
  const ids = selectedIds();
  const sel = FILES.filter(x => ids.includes(x.id));
  const list = sel.length ? sel : FILES.slice(0, 1);
  const kbps = new Set(), hz = new Set();
  let fmt = null;
  for (const f of list) {
    const p = String(f.relPath || f.name || '');
    const m = p.match(/\.([A-Za-z0-9]+)$/);
    if (m && !fmt) fmt = m[1].toLowerCase();
    const info = f.info || {};
    const br = parseBitrateKbps(info.bitRate ? Math.round(info.bitRate / 1000) : null);
    if (br) kbps.add(br);
    const sr = parseInt(info.sampleRate, 10);
    if (Number.isFinite(sr) && sr > 0) hz.add(sr);
  }
  return {
    fmt: fmt,
    kbps: kbps.size === 1 ? [...kbps][0] : null,
    hz: hz.size === 1 ? [...hz][0] : null,
  };
}

/** 一步之后格式变成什么。`null` = 不是音频（图片/压缩包）或未知。 */
function stepOutputFormat(op, params, incoming) {
  const spec = OPS_CATALOG && OPS_CATALOG[op];
  const mode = spec ? spec.gives_formats : null;
  if (mode === 'same') return incoming;                  // 只读 / 就地改写：格式不变
  if (mode === 'param:format') {                         // 转换：看这一步的 format 参数
    const v = String((params || {}).format || incoming || '').toLowerCase();
    return v || null;
  }
  return null;                                           // image / archive / 未知
}

/** 链上某一步**之后**的格式状态（格式 / 码率 / 采样率）。 */
function chainAudioAt(idx) {
  const src = sourceAudioOfScope();
  let cur = { fmt: src.fmt, kbps: src.kbps, hz: src.hz };
  for (let i = 0; i <= idx && i < chain.length; i++) {
    const st = chain[i];
    const fmt = stepOutputFormat(st.op, st.params, cur.fmt);
    const kb = stepBitrate(st.op, st.params);
    const hz = stepSampleRate(st.op, st.params);
    if (fmt) cur.fmt = fmt;
    if (kb !== undefined && kb) cur.kbps = kb;
    if (hz !== undefined && hz) cur.hz = hz;
  }
  return cur;
}

/**
 * 把 `card` 加进当前链是否违规。→ `null` 表示没问题；否则返回给用户看的原因。
 *
 * 两条规则，与后端 `chain.check_format_flow` 对应：
 *   ① **有损 → 无损**（只判第 1 步之后：单步"塞进无损容器"是合法用法）
 *   ② **有损 → 有损但码率/采样率调高**（**第 0 步也要判**：源文件本身有损时，
 *      第 1 步就已经在浪费体积了）
 */
function formatFlowBlocked(card) {
  if (chainMode !== 'serial' || !card || !card.op) return null;
  const src = sourceAudioOfScope();
  const inState = chain.length
    ? chainAudioAt(chain.length - 1)
    : { fmt: src.fmt, kbps: src.kbps, hz: src.hz };
  const outFmt = stepOutputFormat(card.op, card.params, inState.fmt);

  // ① 有损 → 无损（不含第 0 步）
  if (chain.length && inState.fmt && outFmt) {
    const a = inState.fmt.toLowerCase(), b = outFmt.toLowerCase();
    if (LOSSY_FORMATS.includes(a) && LOSSLESS_FORMATS.includes(b)) {
      return `上一步的产物是 ${a.toUpperCase()}（有损），这一步要转成 `
           + `${b.toUpperCase()}（无损）—— 不会恢复任何信息，只是把文件变大。`
           + `（想保留原格式就别在这条链里先转成有损；或者切到并行档）`;
    }
  }

  // ② 有损 → 有损，码率或采样率调高
  if (inState.fmt && outFmt
      && LOSSY_FORMATS.includes(inState.fmt.toLowerCase())
      && LOSSY_FORMATS.includes(outFmt.toLowerCase())) {
    const kb = stepBitrate(card.op, card.params);
    if (inState.kbps && kb && kb > inState.kbps) {
      return `这一步会把 ${inState.fmt.toUpperCase()} ${inState.kbps} kbps `
           + `重新编码成 ${outFmt.toUpperCase()} ${kb} kbps —— `
           + `有损格式每重编码一次就再丢一层信息，**调高码率不会找回**丢掉的部分，`
           + `只是文件更大。（想减小体积就往低调；或者在并行档下让它直接作用于原文件）`;
    }
    const hz = stepSampleRate(card.op, card.params);
    if (inState.hz && hz && hz > inState.hz) {
      return `这一步会把 ${inState.fmt.toUpperCase()} ${inState.hz} Hz `
           + `重新编码成 ${outFmt.toUpperCase()} ${hz} Hz —— `
           + `上采样**不会增加**任何信息，只是让文件变大。`
           + `（想降采样就往低调；或者在并行档下让它直接作用于原文件）`;
    }
  }
  return null;
}

/** 把可用性算到所有卡片上（**复用 renderChain 这一条刷新路径**，别另开销路）。 */
function syncCardAvailability() {
  if (!OPS_CATALOG || typeof document === 'undefined') return;
  for (const el of document.querySelectorAll('#cardSections .fcard[data-card]')) {
    const card = CARDS.find(c => c.name === el.dataset.card);
    if (!card || !card.op) continue;
    const av = availabilityOf(card.op);
    // 串行档还要过一遍**格式流**（有损→无损）：后端提交时会 400，
    // 前端在这里提前置灰，别让用户"点了才报错"。
    const ffWhy = av.ok ? formatFlowBlocked(card) : null;
    const ok = av.ok && !ffWhy;
    const why = av.ok ? (ffWhy || av.why) : av.why;
    el.classList.toggle('fcard--blocked', !ok);
    if (!ok) {
      el.title = why;
      el.dataset.blocked = '1';
    } else {
      delete el.dataset.blocked;
      el.title = '点击加入执行链 · 右键编辑 · 拖到顶部快照可替换'
               + (why ? `（提示：${why}）` : '');
    }
  }
}

function escHtml(s) {
  return String(s).replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/* ================================================================ 预设（§3.8.2）
 *
 * 预设 = **一条链的快照**（`steps` 里是 `op` + `params`，不是卡片名）——
 * 与"快照条"（一张卡的快捷方式）不是一回事：
 *
 *   |          | 存什么            | 点它                     |
 *   |---|---|---|
 *   | 快照条    | 一张卡的**名字**   | 往链上追加**一步**        |
 *   | 预设卡片  | 一整条链的**参数** | 把**整条链**加载进来 + 切档位 |
 *
 * 存参数而不是名字的理由：卡片可改名、可删除（§9.1.1）。存名字的话，
 * 用户改一张卡会让所有引用它的预设**静默变样**。代价是预设不跟着卡片更新 ——
 * 这是正确的取舍：预设的语义是"我当时存的那条链"。
 */

let PRESETS = [];
let presetNextName = '';        // 由后端算（max+1 不复用空洞），前端不自己推
let presetEditing = null;       // 正在编辑的预设 id；null = 走"保存新建"这条路
let presetSavePurpose = 'save'; // 'save' = 保存当前链；'rename' = 改已有预设；'export'
let presetIconsDraft = null;    // 对话框里的图标草稿（null = 自动）
let presetIconsAll = false;     // 图标选择器是否展开"全部图标"

/** 图标推导规则（§3.8.2 三）。**别散落魔数**，文档与代码都用这一份。 */
const ICON_RULES = { max: 5, head: 3, tail: 2 };

/**
 * 链 → 图标序列。`steps` 每项是 `{op, ico?}`。
 *
 * 三条规则各对应一个真实场景：
 *   · **相邻重复合并**：`改标签 → 改标签 → 转 FLAC` 显示 **2** 个（不是 3 个）
 *   · **≤5 全显示**
 *   · **>5 留头 3 尾 2**，中间一个 `…`；6 步时是 `3 + 省略号 + 2 = 6 格`
 *
 * `…` **算一格**（与图标同宽同高），否则 6 格和 5 格的排布会跳。
 *
 * ⚠ **图标取的是 `CARD_ICONS` 里的短名（`flac`/`tag`/`zip`…），不是 op 名**。
 * 第一版映射的是 `s.op`，于是预设卡片上显示的是 `CONVERT`/`TAGS` 这种
 * "给人看 op 名"的东西 —— 而功能卡片显示的是 `FLAC`/`TAG`。两处视觉语言
 * 必须一致（§3.8.2 五："沿用 `.fcard__ico` 那套"）。
 * 快照里有 `ico` 就用它（卡片被删也能显示原图标），否则按 op 查表。
 */
function presetIcons(steps) {
  const keys = (steps || [])
    .map(s => stepIconKey(s))
    .filter(Boolean)
    .filter((op, i, a) => op !== a[i - 1]);          // 相邻重复只留一个
  if (keys.length <= ICON_RULES.max) return keys;
  return [...keys.slice(0, ICON_RULES.head), '…', ...keys.slice(-ICON_RULES.tail)];
}

/**
 * 这一步的图标短名（`CARD_ICONS` 里那一套）。
 *
 * 相邻重复合并的判据用**图标名**而不是 op：两张不同 op 的卡用同一个图标时
 * （`probe` 与 `tags` 都是 `tag`），连在一起显示两个一模一样的方块没有信息量。
 */
function stepIconKey(s) {
  if (!s) return '';
  const ico = String(s.ico || '').trim();
  const iconList = (typeof CARD_ICONS !== 'undefined' && CARD_ICONS) || [];
  if (ico && (!iconList.length || iconList.includes(ico))) return ico;
  return iconTextFor(opOfStep(s));
}

/** 一步的 op：优先用 op（快照自包含），回落到 cardId 找不到就空。 */
function opOfStep(s) {
  if (!s) return '';
  if (s.op) return String(s.op);
  const c = CARDS.find(x => x.id === s.cardId);
  return c ? c.op : '';
}

/**
 * 这一步是不是"**不在卡片库里的功能卡片**"（§9.1.2 一）。
 *
 * 判据：`custom === true` **且** `cardId` 不在 `CARDS` 里。
 * 内置卡的 `cardId` 一定在（`BUILTIN_CARDS` 只增不改），所以内置卡永不触发。
 */
function isUnknownStep(s) {
  if (!s || !s.custom) return false;
  const cid = s.cardId;
  if (!cid) return true;                          // 自定义卡却没留 id → 找不到
  return !CARDS.some(c => c.id === cid);
}

/** 这条预设里有几步是未知卡片。 */
function unknownStepsOf(preset) {
  return (preset && preset.steps ? preset.steps : []).filter(isUnknownStep);
}

/** 图标字符：`op` → `CARD_ICONS` 里的短名（与功能卡片同一套视觉语言）。
 *  查不到就回落 `tag` —— `'?'` 会在预设卡片上显示成一个看不懂的方块。 */
function iconTextFor(op) {
  const spec = OPS_CATALOG && OPS_CATALOG[op];
  const ic = spec && spec.icon;
  if (ic) return ic;
  return ((typeof CARD_ICONS !== 'undefined' && CARD_ICONS && CARD_ICONS[0]) || 'tag');
}

// ------------------------------------------------------------------ 渲染

/** 抽屉标题切换状态进 localStorage（与 fxToggle、档位开关一致）。 */
const DRAWER_TAB_KEY = 'ae.drawerTab';
let drawerTab = (() => {
  try { return localStorage.getItem(DRAWER_TAB_KEY) === 'preset' ? 'preset' : 'card'; }
  catch { return 'card'; }
})();

/**
 * 切换「功能卡片 ⇄ 预设链路」。
 *
 * 跟着切的还有三个控件（否则会出现"点了没用的按钮"）：
 *   · `#cardsecToggleAll`（全部收起）：预设**不分段**，没有可收的 → 隐藏
 *   · 搜索框：仍显示，但改搜预设（按名称/描述）
 *   · `#drawerScope`（选中 N 个文件）：不变
 */
function setDrawerTab(tab) {
  drawerTab = tab === 'preset' ? 'preset' : 'card';
  try { localStorage.setItem(DRAWER_TAB_KEY, drawerTab); } catch {}
  renderDrawerTab();
}

function renderDrawerTab() {
  const isPreset = drawerTab === 'preset';
  const label = $('#drawerTabLabel');
  const tab = $('#drawerTab');
  const cards = $('#cardSections');
  const presets = $('#presetSections');
  const toggleAll = $('#cardsecToggleAll');
  if (!tab || !cards || !presets) return;
  // ⚠ `aria-pressed` 与**标签文字**都要换：只改 title 的话屏幕阅读器
  // 读到的还是「功能卡片」（§3.8.2 四）。
  tab.setAttribute('aria-pressed', String(isPreset));
  tab.title = isPreset ? '点击切换回功能卡片' : '点击切换到预设链路';
  if (label) label.textContent = isPreset ? '预设链路' : '功能卡片';
  cards.hidden = isPreset;
  presets.hidden = !isPreset;
  if (toggleAll) toggleAll.hidden = isPreset;
  const input = $('#cardSearch');
  if (input) input.placeholder = isPreset ? '输入预设名称或描述…' : '输入卡片名称或说明…';
  if (isPreset) renderPresets();
}

/** 渲染预设网格。搜索词复用 `#cardSearch`（切到预设视图时改搜预设）。 */
function renderPresets() {
  const box = $('#presetSections');
  if (!box) return;
  const q = String(($('#cardSearch') && $('#cardSearch').value) || '').trim().toLowerCase();
  const list = PRESETS.filter(p => {
    if (!q) return true;
    return String(p.name || '').toLowerCase().includes(q)
        || String(p.desc || '').toLowerCase().includes(q);
  });
  if (!PRESETS.length) {
    box.innerHTML = `<div class="preset__empty">
      还没有预设。<br>
      在顶栏把执行链排好，点 <code>保存预设</code> 就能把它存下来 ——
      预设会记住**档位**与每一步的参数，点一下即可整套还原。
    </div>`;
    return;
  }
  if (!list.length) {
    box.innerHTML = `<div class="preset__empty">没有匹配「${escHtml(q)}」的预设。</div>`;
    return;
  }
  const cards = list.map(p => {
    const icons = Array.isArray(p.icons) && p.icons.length
      ? p.icons : presetIcons(p.steps);
    const iconsHtml = icons.map(x => {
      // `…` 用同一个方块（与图标同宽），否则 6 格排布会歪
      if (x === '…') return '<span class="pcard__ico pcard__ico--more">…</span>';
      // **画图标，不写文字**：这里曾经显示 `FLAC` / `TAG` 这种短名缩写，
      // 而功能卡片上显示的是同一批短名对应的**图形**。同一个 `ico` 键在
      // 抽屉里出现两种表达（图形 vs 文字），得先学会这套缩写才看得懂。
      // `ico` 本来就是 `ICON` 表的键，`svg()` 直接吃（未命中的回落见 `svg()`）。
      return `<span class="pcard__ico">${svg(x)}</span>`;
    }).join('');
    const unknown = unknownStepsOf(p).length;
    const modeCn = p.mode === 'serial' ? '串行' : '并行';
    const n = (p.steps || []).length;
    return `<button class="pcard${unknown ? ' is-unknown' : ''}"
              data-preset="${escHtml(p.id)}"
              title="点击加载到执行链（${modeCn} · ${n} 步）">
      ${unknown ? `<span class="pcard__warn">${unknown} 未知</span>` : ''}
      <span class="pcard__dots" aria-hidden="true"></span>
      <span class="pcard__icons">${iconsHtml}</span>
      <span class="pcard__name">${escHtml(p.name)}</span>
      <span class="pcard__desc">${escHtml(p.desc || '（无描述）')}</span>
      <span class="pcard__foot">
        <span class="fcard__tier">${modeCn}</span>
        <span class="fcard__tier">${n} 步</span>
      </span>
    </button>`;
  }).join('');
  box.innerHTML = `<div class="preset__grid">${cards}</div>`;
  /* 网格换了元素，跟随光标用的矩形缓存（`rectOf`，按元素存）就该作废。
     两个视图**共用一个滚动容器**，切视图时既不 scroll 也不 resize ——
     不主动作废的话，鼠标已经在预设区、第一次 pointermove 会用功能卡片
     那边的旧坐标算 `--mx/--my`，光晕出现在离光标很远的地方。 */
  invalidateFollowRects();
}

// ------------------------------------------------------------------ 数据

async function loadPresets() {
  if (typeof API === 'undefined' || !API.presets) return;
  try {
    const r = await API.presets();
    PRESETS = (r && r.presets) || [];
    presetNextName = (r && r.nextName) || '';
  } catch (e) {
    PRESETS = [];
  }
  if (drawerTab === 'preset') renderPresets();
}

/** 把一条预设加载到执行链：**步骤 + 档位**，两件都要（§3.8.2 五）。 */
function loadPreset(pid) {
  const p = PRESETS.find(x => x.id === pid);
  if (!p) return;
  // 未知卡片**不静默加载半条链**：先算，再决定是 notice 还是普通加载
  const unknown = unknownStepsOf(p);
  addSteps((p.steps || []).map(s => ({
    cardId: s.cardId || '',
    name: s.name || (OPS_CATALOG[s.op] || {}).label || s.op,
    op: s.op,
    params: s.params || {},
    ico: s.ico || '',
    custom: !!s.custom,
  })));
  // 只还原步骤不还原档位 = 还原了一半，所以这一步不能省
  if (p.mode === 'serial' || p.mode === 'parallel') setChainMode(p.mode);
  const modeCn = p.mode === 'serial' ? '串行' : '并行';
  if (unknown.length) {
    // 用 notice 而不是 toast：toast 4.2 秒就没了，而"我有两张卡没有"
    // 是用户需要看清楚、并且会想追问的事（§9.1.2 一）
    showNotice(
      `${p.name} 里有 ${unknown.length} 个功能卡片不在当前卡片库`,
      `已按快照执行：${unknown.map(s => s.name).join('、')}`,
      'warn',
      { label: '查看详情', onClick: () => openChainEditor(p.id) });
  } else {
    toast('已加载预设', `${p.name} · ${modeCn} · ${(p.steps || []).length} 步`);
  }
}

// ------------------------------------------------------------------ 保存 / 编辑对话框

/** 链上的步骤 → 可以存进预设的快照（`op` + `params` + `ico` + 身份）。 */
function chainSnapshotSteps() {
  return chain.map(s => {
    const out = {
      name: s.name,
      op: s.op,
      // 深拷贝：预设存的是"当时那份参数"，之后改卡片/改链都不该动它
      params: JSON.parse(JSON.stringify(s.params || {})),
      ico: s.ico || iconTextFor(s.op),
    };
    if (s.cardId) out.cardId = s.cardId;
    if (s.custom) out.custom = true;
    return out;
  });
}

/** 链 → 一句默认描述（**默认必须有内容**，一排"（无描述）"等于没描述）。 */
function defaultPresetDesc() {
  const names = chain.map(s => s.name).filter(Boolean);
  return names.join(' → ').slice(0, 120);
}

/** 打开"保存为预设"对话框。 */
function openPresetSave() {
  if (!chain.length) {
    toast('执行链为空', '先把要保存的步骤加进执行链', 'error');
    return;
  }
  presetSavePurpose = 'save';
  presetEditing = null;
  presetIconsDraft = null;
  presetIconsAll = false;
  const nameEl = $('#presetName');
  if (nameEl) nameEl.value = presetNextName || '预设_01';
  const descEl = $('#presetDesc');
  if (descEl) descEl.value = defaultPresetDesc();
  $('#presetModalTitle').textContent = '保存为预设';
  $('#presetModalSub').textContent = '预设记住的是这条链的**参数快照**，之后改卡片不影响它。';
  const list = $('#presetList');
  if (list) { list.hidden = true; list.innerHTML = ''; }
  renderPresetMeta();
  renderIconPicker();
  openModal($('#presetModal'));
}

/** 打开"重命名/改描述/改图标"（右键预设）。 */
function openPresetRename(pid) {
  const p = PRESETS.find(x => x.id === pid);
  if (!p) return;
  presetSavePurpose = 'rename';
  presetEditing = pid;
  presetIconsDraft = Array.isArray(p.icons) ? p.icons.slice() : null;
  presetIconsAll = false;
  const nameEl = $('#presetName');
  if (nameEl) nameEl.value = p.name || '';
  const descEl = $('#presetDesc');
  if (descEl) descEl.value = p.desc || '';
  $('#presetModalTitle').textContent = '编辑预设';
  $('#presetModalSub').textContent = '只改这条预设本身，不动卡片库。';
  const list = $('#presetList');
  if (list) { list.hidden = true; list.innerHTML = ''; }
  renderPresetMeta();
  renderIconPicker();
  openModal($('#presetModal'));
}

/** 对话框里那行只读信息：档位 · 步数 · 作用域（§3.8.2 一）。 */
function renderPresetMeta() {
  const el = $('#presetMeta');
  if (!el) return;
  const modeCn = chainMode === 'serial' ? '串行' : '并行';
  const n = presetSavePurpose === 'rename'
    ? ((PRESETS.find(x => x.id === presetEditing) || {}).steps || []).length
    : chain.length;
  const files = selectedIds().length || FILES.length;
  el.textContent = `${modeCn} · ${n} 步 · 将作用于 ${files} 个文件`
                 + (presetSavePurpose === 'rename' ? '（档位随预设保存，在这里不可改）' : '');
}

/**
 * 图标选择器（§3.8.2 三）。
 *
 * 候选集**默认是这条链上用到过的图标**，另给一个"全部图标"的开关
 * （`CARD_ICONS` 十个，其中 `folder` 没有任何内置卡在用，正好留给用户自由选）。
 * 选完写进 `icons` 数组；「恢复自动」写回 `null`。
 *
 * ⚠ **和卡片编辑器的 `renderIconPick()` 是同一个组件**（`.iconpick__btn` +
 * `role="radio"` / `aria-checked`），这里曾经是另一套：`.iconpick__item` 按钮上
 * 写 `FLAC` / `TAG` 这种**短名文字**，也没有 3D 手感。结果是同一个应用里
 * 两处"选图标"长得不一样 —— 预设卡片上已经是图形了，弹窗里却还要认缩写。
 *
 * 与卡片编辑器唯一的区别是**多选 + 「自动」态**：预设的 `icons` 是一个序列
 * （自动推导的结果），所以是 `radiogroup` 外形下的可切换按钮；
 * `presetIconsDraft === null`（自动）时**一个都不选中**，
 * 由「恢复自动 / 自动（由链推导）」那个按钮说明当前是哪种态。
 */
/**
 * 图标选择器（§3.8.2 三，2024 复查 §10.6.8）。
 *
 * 候选集**默认是这条链上用到过的图标**，另给一个"全部图标"的开关
 * （`CARD_ICONS` 十个，其中 `folder` 没有任何内置卡在用，正好留给用户自由选）。
 * 选完写进 `icons` 数组；「恢复自动」写回 `null`。
 *
 * ⚠ **和卡片编辑器的 `renderIconPick()` 是同一个组件**（`.iconpick__btn` +
 * `role="radio"` / `aria-checked`），这里曾经是另一套：`.iconpick__item` 按钮上
 * 写 `FLAC` / `TAG` 这种**短名文字**，也没有 3D 手感。结果是同一个应用里
 * 两处"选图标"长得不一样 —— 预设卡片上已经是图形了，弹窗里却还要认缩写。
 *
 * ⚠ **自动态必须"看得见"**（用户报的"预设编辑的图标选择功能显示错误"就是这个）：
 * `presetIconsDraft === null` 时草稿是空的 → 按老写法**一个格子都不勾**，
 * 而候选集正是从链推导出来的、卡片上明明画着这两个图标 ——
 * 用户看到的是"一排没选中的方块"，看起来像坏了或没读出来。
 * 现在自动态**把推导出来的图标按选中态显示**，并加一行明确的状态提示；
 * `data-auto` 只用来把选中色换成虚线（"这是自动的、不是你手选的"）。
 */
function renderIconPicker() {
  const box = $('#presetIcons');
  if (!box) return;
  const steps = presetSavePurpose === 'rename'
    ? ((PRESETS.find(x => x.id === presetEditing) || {}).steps || [])
    : chain;
  // 候选集 = **这条链上实际用到过的图标**（按 `ico` 快照，回落到 op 查表）
  const used = [...new Set(steps.map(stepIconKey).filter(Boolean))];
  const all = (typeof CARD_ICONS !== 'undefined' && CARD_ICONS)
    ? CARD_ICONS : ['flac', 'mp3', 'wav', 'wave', 'tag', 'cover', 'gain',
                    'check', 'zip', 'folder'];
  const pool = presetIconsAll ? [...new Set([...used, ...all])] : used;
  const cur = presetIconsDraft;
  // 自动态显示的是**推导结果**（`icons: null` 在卡片上就是这一串），只是样式不同
  const auto = !cur;
  const shown = auto ? presetIcons(steps).filter(x => x !== '…') : cur;
  box.dataset.auto = auto ? '1' : '0';
  box.innerHTML = (pool.length ? pool : all).map(ic => {
    const on = shown.includes(ic);
    return `<button type="button" class="iconpick__btn" role="radio"
             data-icon="${escHtml(ic)}" aria-checked="${on}"
             title="${escHtml(ic)}${on && auto ? '（自动）' : ''}">${svg(ic)}</button>`;
  }).join('');
  // 状态提示：光靠边框/颜色说不清"自动"和"我选了两个"的区别，写出来最省事。
  // 判据用 `presetSavePurpose !== 'export'`（**不是**"`#presetList` 可见"）：
  // 那个列表在打开对话框时就被 `hidden` 了，拿它当判据会让提示永远不出现 ——
  // 而且导出对话框复用同一个模态，那里没有图标这回事。
  const isExport = presetSavePurpose === 'export';
  const hintId = 'presetIconsState';
  let hint = document.getElementById(hintId);
  if (!isExport) {
    if (!hint) {
      hint = document.createElement('p');
      hint.id = hintId;
      hint.className = 'preset__meta';
      box.after(hint);
    }
    hint.textContent = auto
      ? `当前：自动（按链推导，卡片上画的是这 ${shown.length} 个）`
      : `当前：手动选择（${shown.length} 个）`;
  } else if (hint) {
    hint.remove();
  }
  const autoBtn = $('#presetIconsAuto');
  if (autoBtn) autoBtn.textContent = auto ? '正在自动（由链推导）' : '恢复自动';
  const allBtn = $('#presetIconsAll');
  if (allBtn) allBtn.textContent = presetIconsAll ? '只用链上的图标' : '全部图标';
}

/** 保存（新建）或保存（改已有）——由 `presetSavePurpose` 决定走哪条。 */
async function commitPreset() {
  const name = String(($('#presetName') || {}).value || '').trim();
  const desc = String(($('#presetDesc') || {}).value || '').trim();
  const icons = presetIconsDraft;
  if (!name) { toast('名称不能为空', '', 'error'); return; }
  if (typeof API === 'undefined') return;
  try {
    if (presetSavePurpose === 'rename' && presetEditing) {
      const r = await API.updatePreset(presetEditing, { name, desc, icons });
      toast('已保存预设', (r.preset || {}).name || name);
    } else {
      if (presetSavePurpose !== 'save') return;     // 导出用途不走这里
      const r = await API.createPreset({
        name, desc, icons, mode: chainMode, steps: chainSnapshotSteps(),
      });
      toast('已保存预设', `${(r.preset || {}).name || name} · 可在抽屉「预设链路」看到`);
    }
    await loadPresets();
    if (drawerTab === 'preset') renderPresets();
    closeModal($('#presetModal'));
  } catch (e) {
    // 后端的 400 是**逐条原因**（"第 3 步…已跳过"），原样给用户看
    toast('保存失败', String(e.message || e), 'error');
  }
}

// ------------------------------------------------------------------ P3：未知卡片

/**
 * A. 「导出我没有的功能卡片」（§9.1.2 二 A）。
 *
 * 从链上那些未知步骤的**快照**重建卡片定义 —— 7 个字段
 * （`id` / `cat` / `name` / `desc` / `tier` / `ico` / `op` / `params`）
 * 链上都由快照提供，所以重建**不需要卡片库参与**。
 *
 * 落盘走**已有的导出格式**（`GET /api/cards/export` 那一份），
 * 这样接收方用**已有的导入端点**就能装回来 —— 不新造格式。
 */
/**
 * 从**一步**重建一张卡片定义（`注册为卡片` / 导出未知卡片 都用它）。
 *
 * ⚠ 不要绕道 `rebuiltCardsFromPreset` —— 那个会先按"未知"过滤一遍，
 * 而调用方手里这一步可能已经不带 `custom` 了（比如刚被注册过一次），
 * 于是过滤掉它、什么都没发生，而界面上看不出任何错误。
 *
 * ⚠ **`id` 必须过一遍后端那条字符规则**（`validate_card`：只允许
 * 字母数字下划线连字符，≤64）。链上快照里的 `cardId` 是**任意来源**的
 * （别的机器导出的预设、手改过的 cards.json），带中文/空格/符号都出现过 ——
 * 那种 id 直接拿去导入会被拒（`卡片 id 只能含字母数字下划线连字符`），
 * 而用户看到的是"点了注册却什么都没发生"。过不了就**丢掉 id**，
 * 让后端按 `op|name` 自己生成一个（`validate_step` 的回落逻辑）。
 */
function rebuiltCardFromStep(s) {
  const spec = OPS_CATALOG[s.op] || {};
  const rawId = String(s.cardId || '').trim();
  const idOk = /^[A-Za-z0-9_-]{1,64}$/.test(rawId);
  const out = {
    cat: spec.category || '自定义',
    name: s.name || spec.label || s.op,
    desc: spec.desc || '由执行链的一步注册而来',
    tier: String(spec.tier || '自定义').slice(0, 8),
    ico: s.ico || spec.icon || 'tag',
    op: s.op,
    params: JSON.parse(JSON.stringify(s.params || {})),
  };
  if (idOk) out.id = rawId;                    // 原 id 没冲突就沿用它
  return out;
}

/** 从链上那些未知步骤的**快照**重建卡片定义（§9.1.2 二 A）。 */
function rebuiltCardsFromPreset(preset) {
  return unknownStepsOf(preset).map(rebuiltCardFromStep);
}

/** 导出对话框（复用预设对话框，只是换了用途与按钮文字）。 */
function openPresetExport(pid) {
  const p = PRESETS.find(x => x.id === pid);
  if (!p) return;
  const cards = rebuiltCardsFromPreset(p);
  if (!cards.length) { toast('这条预设没有未知卡片', '', 'info'); return; }
  presetSavePurpose = 'export';
  presetEditing = pid;
  presetIconsDraft = Array.isArray(p.icons) ? p.icons.slice() : null;
  presetIconsAll = false;
  const nameEl = $('#presetName');
  // 默认名 `来自预设_03 的卡片`（§9.1.2 二 A）
  if (nameEl) nameEl.value = `来自${p.name}的卡片`;
  const descEl = $('#presetDesc');
  if (descEl) descEl.value = `导出这些卡片的定义，可在别的机器上导入`;
  $('#presetModalTitle').textContent = '导出我没有的功能卡片';
  $('#presetModalSub').textContent =
    '导出的是可直接导入的卡片 JSON（走已有的卡片导入格式）。';
  // **列出将要导出的卡片名**：让用户知道要给别人什么
  const list = $('#presetList');
  if (list) {
    list.hidden = false;
    list.innerHTML = `<div>将导出 <strong>${cards.length}</strong> 张卡片：</div>`
      + `<ul>${cards.map(c => `<li>${escHtml(c.name)} · ${escHtml(c.op)}</li>`).join('')}</ul>`;
  }
  renderPresetMeta();
  renderIconPicker();
  openModal($('#presetModal'));
}

/** 真正导出：拼一份与 `/api/cards/export` 同形的 JSON 并下载。 */
function downloadRebuiltCards() {
  const p = PRESETS.find(x => x.id === presetEditing);
  if (!p) return;
  const cards = rebuiltCardsFromPreset(p);
  const payload = { version: 1, cards };
  const blob = new Blob([JSON.stringify(payload, null, 2)],
                        { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${String(($('#presetName') || {}).value || '卡片').trim()}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  toast('已导出卡片', `${cards.length} 张 · 可直接用「导入卡片」装回来`);
  closeModal($('#presetModal'));
}

/**
 * B. 「编辑执行链」（§9.1.2 二 B）。
 *
 * 把这条预设的链当成**可编辑的链**：删步 / 调序 / 把某一步注册为卡片。
 * 保存时**只改预设本身**（`presets` 里的 `steps`），**不动卡片库** ——
 * 卡片库的写入只走 A 那条路（那是显式动作）。两条路分开，
 * 用户不会"顺手"改掉卡片库。
 */
let chainEditSteps = [];
let chainEditPid = null;

function openChainEditor(pid) {
  const p = PRESETS.find(x => x.id === pid);
  if (!p) return;
  chainEditPid = pid;
  chainEditSteps = JSON.parse(JSON.stringify(p.steps || []));
  $('#chainEditTitle').textContent = `编辑执行链 · ${p.name}`;
  $('#chainEditSub').textContent =
    `保存只改这条预设，不动卡片库。步骤里的参数快照就是执行时用的那一份。`;
  renderChainEditor();
  openModal($('#chainEditModal'));
}

function renderChainEditor() {
  const box = $('#chainEditList');
  if (!box) return;
  if (!chainEditSteps.length) {
    box.innerHTML = `<div class="preset__empty">
      这条链已经空了。保存之后该预设会变成 0 步 —— 想彻底删掉请用右键的「删除预设」。
    </div>`;
    return;
  }
  box.innerHTML = chainEditSteps.map((s, i) => {
    const unk = isUnknownStep(s);
    const spec = OPS_CATALOG[s.op] || {};
    const label = s.name || spec.label || s.op;
    // 参数摘要：只列**与默认值不同**的那几项，否则一行全是噪音
    const defaults = {};
    (spec.params || []).forEach(p => { defaults[p.key] = p.default; });
    const diff = Object.entries(s.params || {})
      .filter(([k, v]) => String(v) !== String(defaults[k]))
      .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join('/') : v}`)
      .slice(0, 4).join(' · ');
    return `<div class="cedit${unk ? ' cedit--unknown' : ''}">
      <span class="cedit__idx">${i + 1}</span>
      <span class="cedit__ico">${escHtml(String(s.ico || spec.icon || '?').toUpperCase())}</span>
      <span class="cedit__body">
        <span class="cedit__name">${escHtml(label)}</span>
        <span class="cedit__note">${unk
          ? '不在卡片库（按快照执行）'
          : escHtml(diff || '（默认参数）')}</span>
      </span>
      <span class="cedit__acts">
        <button class="btn btn--ghost btn--xs" data-ce-up="${i}"
                ${i === 0 ? 'disabled' : ''} title="上移">↑</button>
        <button class="btn btn--ghost btn--xs" data-ce-down="${i}"
                ${i === chainEditSteps.length - 1 ? 'disabled' : ''} title="下移">↓</button>
        ${unk ? `<button class="btn btn--ghost btn--xs" data-ce-reg="${i}"
                title="把它从快照变成卡片库里的真卡片">注册为卡片</button>` : ''}
        <button class="btn btn--ghost btn--xs" data-ce-del="${i}" title="删除这一步">删除</button>
      </span>
    </div>`;
  }).join('');
}

/** 「注册为卡片」= 单张地执行 A（§9.1.2 二 B）。 */
async function registerStepAsCard(i) {
  const s = chainEditSteps[i];
  if (!s) return;
  // 直接按这一步重建，**不过滤** —— 见 `rebuiltCardFromStep` 的注释
  const cards = [rebuiltCardFromStep(s)];
  if (typeof API === 'undefined') return;
  try {
    const r = await API.importCards({ version: 1, cards });
    const added = (r && r.added) || [];
    const skipped = (r && r.skipped) || [];
    if (added.length) {
      toast('已注册为卡片', added.map(c => c.name).join('、'));
      await reloadCards();
      // 注册成功 → 这一步不再是"未知"
      delete chainEditSteps[i].custom;
      delete chainEditSteps[i].cardId;
      renderChainEditor();
    } else {
      toast('没能注册', (skipped[0] || {}).reason || '原因未知', 'error');
    }
  } catch (e) {
    toast('注册失败', String(e.message || e), 'error');
  }
}

async function commitChainEdit() {
  if (typeof API === 'undefined' || !chainEditPid) return;
  try {
    const r = await API.updatePreset(chainEditPid, { steps: chainEditSteps });
    await loadPresets();
    if (drawerTab === 'preset') renderPresets();
    toast('已保存这条链', `${(r.preset || {}).steps.length} 步`);
    closeModal($('#chainEditModal'));
  } catch (e) {
    toast('保存失败', String(e.message || e), 'error');
  }
}

async function deletePreset(pid) {
  if (typeof API === 'undefined') return;
  const p = PRESETS.find(x => x.id === pid);
  try {
    await API.deletePreset(pid);
    await loadPresets();
    if (drawerTab === 'preset') renderPresets();
    toast('已删除预设', (p || {}).name || pid);
  } catch (e) {
    toast('删除失败', String(e.message || e), 'error');
  }
}

/**
 * 预设的右键菜单。复用同一个 `#ctxmenu` 容器与样式（抽屉里那一层），
 * 但**不复用 `openCardMenu`** —— 它的菜单项和动作全是卡片语义
 * （加入链 / 另存为 / 删除卡片），硬塞进去会把两件事搅在一起。
 * 动作靠 `data-pact` 分发，与快照菜单（`data-sact`）同一种写法。
 */
function showPresetMenu(x, y, items) {
  const el = $('#ctxmenu');
  if (!el) return;
  el.innerHTML =
    items.map(it => it.sep ? '<div class="ctxmenu__sep"></div>'
      : `<button class="ctxmenu__item${it.danger ? ' ctxmenu__item--danger' : ''}"
                 data-pact="${escHtml(it.label)}" role="menuitem">
           <span>${escHtml(it.label)}</span></button>`).join('');
  el.hidden = false;
  const r = el.getBoundingClientRect();
  el.style.left = Math.max(6, Math.min(x, innerWidth - r.width - 6)) + 'px';
  el.style.top = Math.max(6, Math.min(y, innerHeight - r.height - 6)) + 'px';
  // 每次重挂 onclick（菜单是复用的单例，旧的处理器必须被换掉）
  el.onclick = (ev) => {
    const btn = ev.target.closest('[data-pact]');
    if (!btn) return;
    const item = items.find(i => i.label === btn.dataset.pact);
    el.hidden = true;
    el.onclick = null;
    if (item && item.onClick) item.onClick();
  };
}



/* 执行链的模式（方案 §3.1.2）。**只有两档** —— 原来的"分段"档已删：
   它唯一的卖点是"每步都回到原文件"，而那正是要修的 bug。
   默认并行 = 不改变存量行为（README 里那批已实测的回归都基于它）。 */
const CHAIN_MODES = ['parallel', 'serial'];
const CHAIN_MODE_KEY = 'ae.chainMode';
let chainMode = (() => {
  try {
    const v = localStorage.getItem(CHAIN_MODE_KEY);
    return CHAIN_MODES.includes(v) ? v : 'parallel';
  } catch { return 'parallel'; }
})();

function setChainMode(mode) {
  if (!CHAIN_MODES.includes(mode) || mode === chainMode) return;
  chainMode = mode;
  try { localStorage.setItem(CHAIN_MODE_KEY, mode); } catch {}
  renderChain();
}

/**
 * 执行整条链：**一次提交**给 POST /api/ops/chain（方案 §3.2）。
 *
 * 原来是"前端循环、每步一次 HTTP"，那样 `await` 的只是"任务**建好了**"，
 * 步骤之间没有任何屏障，后端拿到的每一批也互不相干 —— 所以"链"其实接不起来。
 * 改成一次提交之后：`serial` 档的因果边在后端建链时就连好了（同一文件内
 * 第 i+1 步吃第 i 步的产物），前端不再需要（也无法）自己模拟这件事。
 *
 * @param only 只执行某一步（链上单击）。**单步不走链**：它不构成一条链，
 *             所以不受档位与可用性约束（方案 §9.13c），保持原来的一次性提交。
 */
/**
 * 用户**此刻**的主题，随执行链一起交给后端。
 *
 * 为什么要前端传：后端不知道浏览器选的是哪套主题（`data-theme` / `data-mode` 是
 * 页面上的属性，`localStorage['ae-theme']` 也在浏览器里）。而响度 SVG 是**服务端**
 * 渲染的产物，导出成独立文件后没有任何 CSS 变量可用 —— 所以颜色必须在服务端按这套
 * 主题算成实色写进文件（老板 2026-10："svg 生成的主题颜色改成用户执行链时主题的，
 * 并在导出时将颜色硬编码入文件"）。
 *
 * 取值口径与 `waveTokens()` 完全一致（同一个 `data-theme`/`data-mode`），
 * 这样画布上的波形和服务端导出的图不会出现"一个跟主题、一个不跟"。
 */
function currentTheme() {
  const r = document.documentElement;
  return { theme: r.dataset.theme || 't1',
           themeMode: r.dataset.mode === 'dark' ? 'dark' : 'light' };
}

async function runChain(only) {
  if (!chain.length) { toast('执行链为空', '先把卡片加入链', 'error'); return; }
  const ids = selectedIds();
  if (!ids.length) { toast('未选中文件', '勾选文件后再执行', 'error'); return; }

  const bar = $('.chainbar');
  bar.dataset.running = 'true';
  $('#chainRun').disabled = true;

  // 未连接后端：不伪造任务，只报错（页面上不出现假进度）
  if (typeof API === 'undefined') {
    addLog('✗ 执行链：后端未连接，未提交', 'err');
    toast('后端未连接', '启动 run.bat 后再执行', 'error');
    delete bar.dataset.running;
    renderChain();
    return;
  }

  // 先把每一步的参数解析完**再**提交：封面卡要到执行时才选图（resolveCardParams），
  // 用户取消就整条链不提交 —— 而不是"提交一半再中止"。
  // 这一步留在前端，因为选图是浏览器行为（方案 §3.2 理由 3）。
  let steps;
  try {
    steps = await resolveChainSteps(only);
  } catch (e) {
    addLog(`✗ 执行链：${e.message || e}`, 'err');
    toast('执行链未提交', String(e.message || e), 'error');
    delete bar.dataset.running;
    renderChain();
    return;
  }
  if (!steps) {                     // 用户在选图时取消了
    delete bar.dataset.running;
    renderChain();
    return;
  }

  try {
    if (only != null) {
      // 单步执行：不构成链，走原来的一次性提交
      const r = await API.op(steps[0].op, { ...currentTheme(), fileIds: ids, ...steps[0].params });
      addLog(`▶ 单步：${steps[0].name} · ${r.total || 0} 个任务`, 'ok');
      toast('已提交单步', `${steps[0].name} · ${r.total || 0} 个任务`);
    } else {
      const r = await API.chain({ mode: chainMode, fileIds: ids, steps, ...currentTheme() });
      lastChainId = r.chainId;
      const modeCn = chainMode === 'serial' ? '串行' : '并行';
      addLog(`▶ 执行链（${modeCn}）：${steps.map(s => s.name).join(' → ')}`
             + ` · ${steps.length} 步 / ${r.total} 个任务`, 'ok');
      toast('已提交执行链', `${modeCn} · ${steps.length} 步 · ${r.total} 个任务进入队列`);
      // 链上那些"能跑但接不上"的位置，如实报出来（后端算的，不阻断）
      for (const n of (r.notes || [])) {
        addLog(`⚠ 第 ${n.stepIdx + 1} 步：${n.text}`, 'warn');
      }
      if ((r.notes || []).length && chainMode !== 'serial') {
        toast('链上有步骤接不上', '点「串行」即可让产物传下去');
      }
      renderChain();
    }
    if (window.App && App.refreshQueue) App.refreshQueue();
  } catch (e) {
    addLog(`✗ 执行链失败：${e.message || e}`, 'err');
    toast('执行链失败', String(e.message || e), 'error');
  } finally {
    delete bar.dataset.running;
    renderChain();
  }
}

/** 最近一次提交的 chainId（给状态轮询用；页面刷新后仍可从库里按文件反查）。 */
let lastChainId = null;

/**
 * 把链上的每一步解析成可提交的形态：`[{cardId, name, op, params}, …]`。
 *
 * **`op` 必须带上**：卡片可以被改名、被删除（id 是身份，名字只是显示），
 * 只给 `cardId` 会让"删了卡就跑不了"重新回来（方案 §9.1.1 二）。
 * 返回 `null` 表示用户主动取消（例如选图时点了取消）。
 */
async function resolveChainSteps(only) {
  const src = (only == null) ? chain : [chain[only]];
  const usable = src.filter(s => s.op);
  if (!usable.length) {
    const bad = src.map(s => s.name).join('、');
    addLog(`⚠ 执行链：${bad} 没有绑定后端操作，未提交`, 'warn');
    toast('无可执行步骤', `${bad} 未绑定后端操作`, 'error');
    return null;
  }
  const out = [];
  for (const s of usable) {
    // 按 id 找卡片（身份用 id，名字只用来显示）。
    const card = CARDS.find(c => c.id === s.cardId)
              || CARDS.find(c => c.name === s.name)
              || { name: s.name, op: s.op, params: s.params };
    const params = await resolveCardParams(card);
    if (!params) return null;       // 用户取消
    out.push({ cardId: card.id || s.cardId || '', name: s.name, op: s.op,
               params: params || {} });
  }
  return out;
}

/**
 * 把卡片加入执行链。
 *
 * **身份用 `cardId`，名字只用来显示**（方案 §9.1）：拿名字当键会让"改个名"
 * 把链上的步骤静默弄丢。同时把 `op`/`params` 也快照一份 —— 卡片被删之后
 * 这一步仍然能跑（后端要的就是 `op` + `params`）。
 *
 * ⚠ 卡片名现在是**全局唯一**的（后端 `validate_card` 拒绝重名），但**不能**
 * 因此就把名字当身份：① 名字可以改；② 快照/预设要跨机器（对端可能没有这张卡）；
 * ③ 卡片库那一层仍是按名字索引的，所以"名字唯一"是给它兜底的约束，
 * 而不是"名字可以当 id"的许可证。
 */
function addToChain(name) {
  const card = CARDS.find(c => c.name === name);
  if (!card) return;
  // 兜底：点击那条路已经由 `dataset.blocked` 拦住了，但**程序化路径**
  // （快照拖拽、预设还原）不走点击。串行档的"有损→无损"在这里再挡一次 ——
  // 否则用户能绕过置灰把非法组合拼进链，点执行才收到 400。
  const ffWhy = formatFlowBlocked(card);
  if (ffWhy) {
    toast('这一步不能加入链', ffWhy, 'error');
    return;
  }
  addSteps([card]);
}

/** 一次往链里加一步或几步。快照、卡片点击、预设还原三条路都走它。 */
function addSteps(cards) {
  const added = [];
  for (const card of cards) {
    if (!card) continue;
    chain.push({
      cardId: card.id || '',
      name: card.name,
      op: card.op,
      params: JSON.parse(JSON.stringify(card.params || {})),   // 快照，别与卡片共享引用
      ico: card.ico || '',
      custom: !!card.custom,
    });
    added.push(card.name);
  }
  renderChain();
  if (!added.length) return;
  const scope = selectedIds().length;
  const label = added.length > 1 ? `${added.length} 张卡片` : added[0];
  toast('已加入执行链', `${label}${scope ? ` · 将作用于 ${scope} 个文件` : ' · 未选中文件'}`);
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
let noticeAction = null;

function hideNotice() {
  const n = $('#notice');
  clearTimeout(noticeTimer);
  noticeTimer = null;
  noticeAction = null;
  const act = $('#noticeAct');
  if (act) { act.hidden = true; act.onclick = null; }
  n.classList.remove('is-open');
  n.hidden = true;                    // 语义：辅助技术也认为它没了
}

function toast(title, msg = '', kind = 'info') {
  const n = $('#notice');
  $('#noticeTitle').textContent = title;
  $('#noticeMsg').textContent = msg;
  n.dataset.kind = kind;

  n.hidden = false;                   // 去掉 hidden（CSS 已覆盖成 flex，不影响布局）
  n.classList.add('is-open');         // 已经在 is-open 时再加一次是 no-op，不会重播动画

  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(hideNotice, 4200);
}

/**
 * 带**动作**的提示：`toast` 的超集（§9.1.2 一 的「查看详情」用它）。
 *
 * 与 `toast` 的差别不只是多一个按钮：
 *   · **不自动消失**（`sticky`）—— 4.2 秒不足以让用户读完"我有两张卡没有"
 *     并决定要不要看详情；自动消失等于把这件事藏起来。
 *   · 关闭时清掉回调，避免用户关掉之后又点到已经过期的动作。
 */
function showNotice(title, msg = '', kind = 'warn',
                    action = null, sticky = true) {
  toast(title, msg, kind);
  const act = $('#noticeAct');
  if (act) {
    if (action && action.label) {
      act.textContent = action.label;
      act.hidden = false;
      act.onclick = () => { const fn = action.onClick; hideNotice(); if (fn) fn(); };
    } else {
      act.hidden = true;
      act.onclick = null;
    }
  }
  if (sticky) {
    clearTimeout(noticeTimer);
    noticeTimer = null;
  }
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

/* 旋入动画播完后给模态加 .is-settled —— .modal__box 的毛玻璃从那一刻才开
   （见 ui/overlays.css：盒子在动的时候它的模糊采样区一直在动，每帧都要重新模糊）。
   两个模态的开启路径**不是同一个函数**（#cardModal 走 openModal，
   #metaModal 走 openMeta），所以抽成一个公共函数 —— 否则只改一处，
   另一个模态会永远没有毛玻璃。 */
function settleModal(el, delay = 320) {
  clearTimeout(_modalTimers.get(el));
  _modalTimers.set(el, setTimeout(() => {
    el.classList.add('is-settled');
    _modalTimers.delete(el);
    /* 旋入动画到此结束：模态盒子的位置从"动画中"变成"落定"，
       跟随用的矩形缓存必须作废（否则倾斜会按动画中的位置算）。
       放在这里而不是挂全局 transitionend —— 理由见 bindFollowRectHooks。 */
    invalidateFollowRects();
  }, delay));
}

function openModal(el) {
  clearTimeout(_modalTimers.get(el));
  _modalTimers.delete(el);
  el.classList.remove('is-open', 'is-settled');
  el.hidden = false;
  void el.offsetWidth;                              // 强制回流，让 transition 有起点
  requestAnimationFrame(() => el.classList.add('is-open'));
  settleModal(el);                                  // 时长比 CSS 的 .30s 略长，避免和过渡打架
}

function closeModal(el) {
  el.classList.remove('is-open', 'is-settled');
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
  modal.classList.remove('is-open', 'is-settled');   // 先回到关闭态，作为过渡起点
  modal.hidden = false;                        // 恢复 display（去 [hidden]{display:none} 的影响）
  void modal.offsetWidth;                      // 强制回流，让起点样式被浏览器确认
  requestAnimationFrame(() => modal.classList.add('is-open'));
  settleModal(modal);                          // 走的是 metaCloseTimer 以外的计时器，互不干扰
}
function closeMeta() {
  const modal = $('#metaModal');
  modal.classList.remove('is-open', 'is-settled');   // 触发淡出 + 模糊收起
  clearTimeout(_modalTimers.get(modal));       // 别让排队的 .is-settled 在关闭后才加上
  _modalTimers.delete(modal);
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
    const root = document.documentElement;
    /* 主题切换 = 全树令牌变化。此刻必须掐掉【各组件自己】的颜色/阴影过渡，
       否则每个带 transition 的元素都会起一段动画：实测数百个 Animation
       覆盖 background-color / border-color / box-shadow / scrollbar-color，
       每个都在重绘 → 连续掉帧（见 渲染开销优化方案.md §2.1）。

       只动 transition，**绝不动 animation**：
       平扫 vt-sweep 与径向 vt-expand / vt-contract 都是 animation
       （ui/base.css 的 @keyframes），且作用在 ::view-transition-* 伪元素上
       —— 那是独立的伪元素树，`*` 后代选择器命中不到，所以它们照常播。
       别把 css 里那条规则扩展成 `animation: none`，也别把平扫/径向
       从 animation 改写成 transition(clip-path)，那样会被它一起掐掉。 */
    root.setAttribute('data-theme-switch', '');
    /* 两个 rAF：第一个 rAF 里新主题的样式才被采用，第二个才确保这一帧已经画完 */
    const clearSwitchFlag = () => requestAnimationFrame(() => requestAnimationFrame(
      () => root.removeAttribute('data-theme-switch')));

    if (!document.startViewTransition || reduce) {
      /* try/finally：这个标记是全局生效的（它会掐掉所有组件过渡），
         万一 mutate() 抛了（localStorage 在隐私模式/配额满时会抛），
         也必须把它摘掉，否则整个页面的过渡会**永久**失效。 */
      try {
        mutate();
      } finally {
        requestAnimationFrame(redrawAllWaves);
        clearSwitchFlag();
      }
      return;
    }
    root.dataset.vtDir = dir;
    root.style.setProperty('--vt-r', Math.hypot(innerWidth, innerHeight) + 'px');
    // 15° 斜线在整幅高度上的水平偏移量
    root.style.setProperty('--vt-d', (innerHeight * Math.tan(15 * Math.PI / 180)) + 'px');
  
    const t = document.startViewTransition(mutate);
    t.finished.finally(() => {
      delete root.dataset.vtDir;
      clearSwitchFlag();
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
  // 3D 跟随开关：关掉后卡片 / 快照 / 模态框都不再随指针倾斜，
  // 径向光晕和模态的旋入动画照旧（见 bindFollow 的注释）
  const fxBtn = $('#fxToggle');
  if (fxBtn) {
    const paintFx = () => {
      const on = document.documentElement.dataset.fx !== 'off';
      fxBtn.setAttribute('aria-pressed', String(on));
      fxBtn.title = on ? '关闭 3D 跟随效果' : '开启 3D 跟随效果';
    };
    fxBtn.addEventListener('click', () => {
      const on = document.documentElement.dataset.fx !== 'off';   // 当前状态
      if (on) document.documentElement.dataset.fx = 'off';
      else delete document.documentElement.dataset.fx;
      localStorage.setItem('ae-fx3d', on ? 'off' : 'on');

      // 切到"关"时立刻归零：鼠标正停在卡片/模态上不动的话，
      // 光靠下一次 pointermove 才生效会有明显的滞后感
      if (on) {
        $$('#cardSections .fcard, .snap, .modal__box').forEach(el => {
          el.style.setProperty('--nx', '0');
          el.style.setProperty('--ny', '0');
        });
      }
      paintFx();
    });
    paintFx();
  }
  // 抽屉
  // 注意：把手不绑 click —— 点击/拖拽统一由下面的 pointer* 逻辑处理，
  // 否则 click 会与 pointerup 各切换一次，表现为「点了没反应」。
  $('#drawerClose').addEventListener('click', () => applyStop('closed'));

  // 搜索：点击唤出
  $('#searchToggle').addEventListener('click', () => setSearch(true));
  $('#searchClear').addEventListener('click', () => {
    const inp = $('#cardSearch');
    inp.value = '';
    // 搜索框在两个视图里搜不同的东西（§3.8.2 四），清空也要清对那一个
    if (drawerTab === 'preset') renderPresets(); else renderCardSections('');
    syncSearchFlag();
    inp.focus();
  });
  $('#cardSearch').addEventListener('input', e => {
    if (drawerTab === 'preset') renderPresets();
    else renderCardSections(e.target.value);
    syncSearchFlag();
  });
  $('#cardSearch').addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.stopPropagation(); setSearch(false); }
  });
  syncSearchFlag();

  /* 失焦收回：点到别处（含卡片）或 Tab 走人，都把控件收回去。
     用 pointerdown(capture) 而不是 blur —— pointerdown 排在 mousedown 之前，
     收回引起的布局变化先落定，按下/抬起命中同一个元素；
     再配合 keepQuery（不重渲染卡片区），这次点击的目标不会被换掉。 */
  document.addEventListener('pointerdown', e => {
    const dock = $('#searchDock');
    if (dock.dataset.search !== 'open') return;
    if (e.target && e.target.closest && e.target.closest('#searchDock')) return;
    setSearch(false, { keepQuery: true });
  }, true);
  $('#searchDock').addEventListener('focusout', e => {
    const dock = $('#searchDock');
    if (dock.dataset.search !== 'open') return;
    const next = e.relatedTarget;              // null＝失焦到 body/window，鼠标那侧由 pointerdown 兜住
    if (!next || dock.contains(next)) return;
    setSearch(false, { keepQuery: true });
  });

  // 卡片区（按分类分段渲染）
  $('#cardSections').addEventListener('click', e => {
    // 「新建自定义卡片」也在这一区，且会被重渲染 —— 必须一起走委托
    if (e.target.closest('#newCard')) { openCardEditor(null, 'new'); return; }
    const c = e.target.closest('[data-card]'); if (!c) return;
    // 不可选的卡片（链尾决定，方案 §3.2.4）：**不加入链，但要说明原因** ——
    // 只置灰不解释，用户会以为"功能坏了"。
    if (c.dataset.blocked === '1') {
      toast('这张卡现在不能选', c.title || '上一步交出的东西它接不住', 'error');
      return;
    }
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

  // 档位开关（方案 §3.1.2）。串行 = 每个文件一条自己的流水线：
  // 同一文件内逐步吃上一步的产物，不同文件之间并行。
  $('#chainMode')?.addEventListener('click', (e) => {
    const b = e.target.closest('[data-mode]');
    if (b) setChainMode(b.dataset.mode);
  });

  // 链上提示里的"改成串行"直达按钮（比让用户自己去顶栏找开关更直接）
  $('#chainNotes')?.addEventListener('click', (e) => {
    if (e.target.closest('#chainFixSerial')) setChainMode('serial');
  });

  // 「保存预设」尚未实现（执行链并发方案.md §3.8.2 / §3.8.3）：
  // 这里**故意不做任何事**，按钮在 index.html 里是 disabled 的。
  // 「保存预设」**已实现**（方案 §3.8.2）。
  // 历史：这里曾经弹"已保存为预设" + chain.join(' → ')，而 chain.join() 只是把名字
  // 拼成字符串 —— 没有落盘、没有列表、刷新即失，那是**说谎**。P0-0 先把它置灰，
  // 现在换成真的：命名对话框 → 图标/描述 → 落 cards.json 的 presets（含 mode）。
  // 顺带：导出脚本按钮已删除，它与保存预设互斥，理由见方案 §3.8.1。
  $('#chainSave')?.addEventListener('click', (e) => {
    e.preventDefault();
    openPresetSave();
  });

  /* ---------- 抽屉标题切换：功能卡片 ⇄ 预设链路（§3.8.2 四） ---------- */
  $('#drawerTab')?.addEventListener('click', () => {
    setDrawerTab(drawerTab === 'preset' ? 'card' : 'preset');
  });

  /* ---------- 预设网格：左键加载 / 右键菜单（§3.8.2 五 + §9.1.2 二） ---------- */
  $('#presetSections')?.addEventListener('click', e => {
    const c = e.target.closest('[data-preset]');
    if (!c) return;
    loadPreset(c.dataset.preset);
  });

  // 右键菜单：与文件卡/功能卡同一套（`showCtxMenu` 那条路）。
  // 菜单项按"这条预设有没有未知卡片"分叉 —— 没有未知卡时不显示那两个动作，
  // 免得用户点了得到一个"没有可导出的卡片"。
  $('#presetSections')?.addEventListener('contextmenu', e => {
    const c = e.target.closest('[data-preset]');
    if (!c) return;
    e.preventDefault();
    const pid = c.dataset.preset;
    const p = PRESETS.find(x => x.id === pid);
    if (!p) return;
    const unknown = unknownStepsOf(p).length;
    const items = [];
    if (unknown) {
      items.push({ label: `导出我没有的功能卡片  ${unknown} 张`,
                   onClick: () => openPresetExport(pid) });
    }
    items.push({ label: '编辑执行链', onClick: () => openChainEditor(pid) });
    items.push({ label: '重命名预设', onClick: () => openPresetRename(pid) });
    items.push({ sep: true });
    items.push({ label: '删除预设', danger: true, onClick: () => deletePreset(pid) });
    showPresetMenu(e.clientX, e.clientY, items);
  });

  /* ---------- 保存预设对话框 ---------- */
  $('#presetSave')?.addEventListener('click', () => {
    if (presetSavePurpose === 'export') downloadRebuiltCards();
    else commitPreset();
  });
  // 图标选择：点一下切换选中，写进 `presetIconsDraft`（数组 = 手选）
  $('#presetIcons')?.addEventListener('click', e => {
    const b = e.target.closest('[data-icon]');
    if (!b) return;
    const ic = b.dataset.icon;
    // 第一次手选时，草稿从"自动推导的结果"起步 —— 否则用户一点就只剩一个图标
    if (!presetIconsDraft) {
      const steps = presetSavePurpose === 'rename'
        ? ((PRESETS.find(x => x.id === presetEditing) || {}).steps || [])
        : chain;
      presetIconsDraft = presetIcons(steps).filter(x => x !== '…');
    }
    const i = presetIconsDraft.indexOf(ic);
    if (i >= 0) presetIconsDraft.splice(i, 1); else presetIconsDraft.push(ic);
    renderIconPicker();
  });
  $('#presetIconsAuto')?.addEventListener('click', () => {
    presetIconsDraft = null;                 // `null` = 自动（**不是**"没有图标"）
    renderIconPicker();
  });
  $('#presetIconsAll')?.addEventListener('click', () => {
    presetIconsAll = !presetIconsAll;
    renderIconPicker();
  });

  /* ---------- 编辑执行链（预设的 steps） ---------- */
  $('#chainEditList')?.addEventListener('click', e => {
    const up = e.target.closest('[data-ce-up]');
    const dn = e.target.closest('[data-ce-down]');
    const del = e.target.closest('[data-ce-del]');
    const reg = e.target.closest('[data-ce-reg]');
    if (up) {
      const i = Number(up.dataset.ceUp);
      [chainEditSteps[i - 1], chainEditSteps[i]] =
        [chainEditSteps[i], chainEditSteps[i - 1]];
      renderChainEditor();
    } else if (dn) {
      const i = Number(dn.dataset.ceDown);
      [chainEditSteps[i + 1], chainEditSteps[i]] =
        [chainEditSteps[i], chainEditSteps[i + 1]];
      renderChainEditor();
    } else if (del) {
      chainEditSteps.splice(Number(del.dataset.ceDel), 1);
      renderChainEditor();
    } else if (reg) {
      registerStepAsCard(Number(reg.dataset.ceReg));
    }
  });
  $('#chainEditSave')?.addEventListener('click', () => commitChainEdit());

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

  // 预设对话框（#presetModal）与编辑执行链（#chainEditModal）——
  // ⚠ **这两个必须各有一条**：`data-close` 不是全局委托，没有这条监听
  // 「取消」「点遮罩」就都不响应（用户实测报的"编辑执行链动作无法取消"）。
  // 别的模态都有，新加模态时最容易漏的就是这一行。
  $('#presetModal').addEventListener('click', e => {
    if (e.target.closest('[data-close]')) closeModal($('#presetModal'));
  });
  $('#chainEditModal').addEventListener('click', e => {
    // 取消 = **丢弃草稿**（`chainEditSteps` 是本次编辑的工作副本，
    // 只有点「保存这条链」才写回预设）。所以这里把 id 也清掉，
    // 免得下一次打开时误以为还在编辑同一条。
    if (e.target.closest('[data-close]')) {
      chainEditSteps = [];
      chainEditPid = null;
      closeModal($('#chainEditModal'));
    }
  });
  // Esc 关掉这两个（其它模态也没有，但对话框类的最该有）
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    const pm = $('#presetModal'), ce = $('#chainEditModal');
    if (ce && !ce.hidden) { chainEditSteps = []; chainEditPid = null; closeModal(ce); return; }
    if (pm && !pm.hidden) closeModal(pm);
  });

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
  $('#noticeClose').addEventListener('click', hideNotice);


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

/** 上一次渲染用的数据指纹（见下）。null = 还没渲染过 */
let _filesSig = null;

/** 用后端文件列表替换本地 mock，并重绘卡片 */
window.applyServerFiles = function (files) {
  const list = Array.isArray(files) ? files : [];

  /* 数据指纹：只取"会体现在界面上的字段"。
     api.js 的 syncAndProbe 每 2s 就来一次 reloadFiles，绝大多数时候什么都没变，
     原来却照旧重建整个列表 + 重画全部波形（trace 实测：RunMicrotasks 2.5ms +
     ParseHTML 0.3ms + Layout 0.4ms，每 2 秒一次；每个文件的 <canvas> 都被销毁重建）。
     指纹相同 → 一个 DOM 节点都不动。

     ⚠ progress 必须进指纹（否则处理中的进度条不刷新），用 Math.round 压到 1% 粒度；
       _peaks 也要进（峰值图生成好之后要能触发一次真实重绘）。
     ⚠ **loudness 必须进指纹**：它是"跑完响度分析/标准化之后才有"的字段，
       任务成功时 hasCover/tags 全都没变 —— 不进指纹就不会重绘，
       卡片上那行要等到下一次无关的变更才刷新（表现是"任务成功了但数字没变"）。
     ⚠ COVER_V 也要进：**替换一张已有封面**时 hasCover 是 1→1 不变的，
       而 waitForCover() 会先 bump COVER_V 再 renderFiles()，那一次 <img> 的
       ?v= 可能仍指向服务端上的旧图。把版本号放进指纹，后续轮询才会重绘、
       重新发起请求把新图取回来（原来靠"每 2s 无脑重绘"兜住，现在必须显式带上）。
     ⚠ 这个分支**不允许**改任何 DOM：布局规格.md §15 要求的 checked / is-playing
       还原逻辑在下面那条路径里，不会重绘就谈不上被抹掉。 */
  const sig = list.map((f) => {
    const info = f.info || {};
    const tg = info.tags || {};
    return [f.id, f.state, Math.round(((f.tasks || {}).progress) || 0),
            info.hasCover ? 1 : 0, COVER_V[f.id] || 0,
            tg.title || '', tg.artist || '', tg.album || '', tg.tracknumber || '',
            info.format || '', info.duration || 0, f._peaks || '',
            info.loudness == null ? '' : info.loudness].join('\u0001');
  }).join('\u0002');

  if (sig === _filesSig && list.length === FILES.length) {
    // 勾选数等仍可能因用户操作而变，这一项开销极小，保留
    syncBulkbar();
    return;
  }
  _filesSig = sig;

  // 刷新不能吃掉勾选。api.js 的 syncAndProbe 每 2s 就 reloadFiles 一次，
  // 而这里原来是硬写 checked:false —— 实测勾选**活不过 2 秒**
  // （点一下 → 批量栏出现 → 2s 后批量栏自己消失），
  // 批量操作因此只能在 2 秒内完成，等于不能用。
  // 勾选是"用户的选择"，不是服务端数据，刷新时按 id 继承。
  const wasChecked = new Set(FILES.filter((f) => f.checked).map((f) => f.id));
  FILES.length = 0;
  list.forEach((f) => {
    const info = f.info || {};
    const tg = info.tags || {};
    FILES.push({
      id: f.id,
      kind: f.kind || 'audio',        // audio / image / other —— 决定要不要拉波形
      title: tg.title || f.name.replace(/\.[^.]+$/, ''),
      artist: tg.artist || '—',
      album: tg.album || '—',
      track: tg.tracknumber || '—',
      format: info.format || (f.name.split('.').pop() || '').toUpperCase(),
      rate: info.sampleRate || 0,
      depth: info.bits || 0,
      // 响度是**内容测量值**：只有跑过响度分析 / 响度标准化才会有（probe 不解码，
      // 测不出来）。后端写在 `info.loudness` 上，见 `store.MEASUREMENT_KEYS`。
      // 取不到就是 null，由 renderFiles 显示 `—` —— 不要留 undefined，
      // 那个字符串会直接印到卡片上（"undefined LUFS"）。
      loudness: (info.loudness != null && info.loudness !== '')
                  ? Number(info.loudness) : null,
      ch: info.channels ? `${info.channels} ch` : '—',
      dur: info.duration || 0,
      size: fmtBytes(f.size),
      status: mapState(f.state),
      progress: (f.tasks && f.tasks.progress) || 0,
      peaks: f._peaks || 'none',
      cover: !!info.hasCover,
      checked: wasChecked.has(f.id),   // 已删除的文件自然不在新列表里，勾选随之消失
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
    // 图片这类非音频文件**没有音频流**，拉 /peaks 只会拿到 415。
    // 以前照拉不误，每刷新一次列表就失败重试一遍（实测日志里上百条 500）。
    if (f.kind !== 'audio') { f.peaks = 'not-audio'; continue; }
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
/**
 * 任务产出文件时，**主动**告诉用户去哪拿。
 *
 * 背景（用户实测报的"响度分析报告未看到产出"）：报告其实**一秒钟就生成好了**
 * （`outputs/loudness/<名>.loudness.md`），但界面上唯一的入口是**队列面板里
 * 那条任务上的「打开产物」** —— 用户不打开队列、或者队列滚动过去之后，
 * 就完全不知道东西产出来了。功能是好的，"看得见"这一层没做。
 *
 * 所以：每有一条**新**成功的任务带 output，就弹一条带动作的提示（点了直接打开）。
 * 只报新出现的，避免每次轮询都弹一遍。上限 6 条 ——
 * 批量跑 30 个文件时不要刷 30 个提示，那就从"看不见"变成"看不见别的"。
 */
let announcedOutputs = new Set();
const OUTPUT_ANNOUNCE_MAX = 6;

/** 产物该"打开"还是"下载"：压缩包/音频这类浏览器里没用的，给下载。 */
function outputActionFor(path) {
  const p = String(path || '').toLowerCase();
  if (p.endsWith('.zip') || p.endsWith('.wav') || p.endsWith('.flac')
      || p.endsWith('.aiff') || p.endsWith('.mp3') || p.endsWith('.m4a')
      || p.endsWith('.ogg') || p.endsWith('.opus') || p.endsWith('.wma')) {
    return { label: '下载', inline: false };
  }
  return { label: '打开', inline: true };   // .md / .png / .jpg 这些浏览器里直接看
}

function announceOutputs(rows) {
  const fresh = rows.filter(t => t.state === 'success' && t.result
                              && t.result.output && !announcedOutputs.has(t.id));
  if (!fresh.length) return;
  for (const t of fresh) announcedOutputs.add(t.id);
  const shown = fresh.slice(0, OUTPUT_ANNOUNCE_MAX);
  const { label, inline } = outputActionFor(shown[0].result.output);
  const who = shown.length === 1
    ? shortName(shown[0].fileId)
    : `${shown.length} 个文件`;
  const more = fresh.length > OUTPUT_ANNOUNCE_MAX
    ? `（还有 ${fresh.length - OUTPUT_ANNOUNCE_MAX} 条产物在队列里）` : '';
  showNotice(
    `已生成产物：${who}`,
    `${shown[0].result.output}${more}`,
    'ok',
    {
      label,
      onClick: () => {
        // 用新窗口打开：内联看的、以及要下载的都走同一条路
        window.open(API.outputUrl(shown[0].result.output, inline), '_blank');
      },
    },
    false);              // 不 sticky：产物通知不该把屏幕占住
}

window.applyServerQueue = function (q) {  if (!q) return;
  const rows = [...(q.running || []), ...(q.pending || []),
                ...(q.recent || []).filter((t) => !['running', 'pending'].includes(t.state))];
  TASKS.length = 0;
  rows.slice(0, 12).forEach((t) => {
    TASKS.push({
      id: t.id,
      name: `${t.type} · ${shortName(t.fileId)}`,
      state: ({ running: 'running', pending: 'pending', success: 'success',
                failed: 'failed', cancelled: 'failed',
                // `skipped` = "前序步骤失败，轮不到我"（方案 §9.7）。
                // **必须显式映射成 failed**：兜底分支是 `|| 'pending'`，
                // 漏了会把"已跳过"显示成"排队中"，用户看到的是一个永远转圈的队列。
                skipped: 'failed' })[t.state] || 'pending',
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
  // 产物主动播报（"响度分析报告未看到产出"那一类问题的正解）
  announceOutputs(rows);
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
  _filesSig = null;                // 断线：指纹作废，重连后必须整表重绘一次
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
/* ------------------------------------------------- 跟随用的元素矩形缓存
   为什么需要：flushFollow 原来每次都 el.getBoundingClientRect()。而**上一帧刚写过
   自定义属性**，样式树是脏的 —— 这次读取会强制浏览器同步布局。
   trace 里那条每帧都来的 `Layout (totalObjects 2266)` 就是这么来的
   （见 3D选项关闭状态下GPU开销问题.md 的 P4）。

   缓存策略：稳态悬停时**一次都不读**。只在几何真的可能变了的时候作废重建：
     · window resize
     · 任何滚动（滚动不冒泡，用捕获阶段）
     · 抽屉自己的 top/height 过渡结束（只认 .drawer 自身，见下）
     · 模态的旋入动画结束（settleModal 里作废）
     · 抽屉拖拽（applyTop 每帧写 --drawer-top，但那时指针在把手上，
       不会触发 flushFollow，所以逐帧作废是免费的）

   ⚠ 别在这里挂**全局** transitionend 来"兜底"：整个界面到处都有 hover 过渡
   （.btn / .iconpick__btn / .fcard / input…），指针一动就有过渡在结束
   → 缓存被不停作废 → 又变成每帧一次强制同步布局。
   实测：全局 transitionend 让模态 hover 的 Layout 从 +14 涨到 +94（30 次移动）。
   所以只认"真正改变几何"的那两个过渡：抽屉自身的 top/height。 */
let _rectGen = 0;
const _rectCache = new WeakMap();      // el → { gen, left, top, w, h }
let _rectHooksBound = false;

function invalidateFollowRects() { _rectGen++; }

function bindFollowRectHooks() {
  if (_rectHooksBound) return;
  _rectHooksBound = true;
  window.addEventListener('resize', invalidateFollowRects, { passive: true });
  window.addEventListener('scroll', invalidateFollowRects,
                          { passive: true, capture: true });
  /* 只监听抽屉自身：它一动，里面的卡片整体平移。
     e.target === drawerEl 把子元素冒泡上来的 hover 过渡挡掉（关键）。 */
  const drawerEl = document.querySelector('.drawer');
  if (drawerEl) {
    drawerEl.addEventListener('transitionend', (e) => {
      if (e.target === drawerEl
          && (e.propertyName === 'top' || e.propertyName === 'height')) {
        invalidateFollowRects();
      }
    }, { passive: true });
  }
}

function rectOf(el) {
  let r = _rectCache.get(el);
  if (!r || r.gen !== _rectGen) {
    const b = el.getBoundingClientRect();
    r = { gen: _rectGen, left: b.left, top: b.top, w: b.width || 1, h: b.height || 1 };
    _rectCache.set(el, r);
  }
  return r;
}

/*: 自己会变形的目标，命中判定要外扩多少（px）。
    必须 ≥ 倾斜造成的轮廓缩进量 ≈ 0.006 × 元素宽
    （模态盒宽上限 940px → 约 5.4px）。取 6。
    调大 `--nx/--ny` 的 1.4deg、或调小 `.modal` 的 perspective 时要重算。 */
const HIT_MARGIN = 6;

/* 布局矩形 —— **不含自身 transform**。`offset*` 是布局值，与变换无关。
   为什么要它：目标元素自己带跟随 transform 时，`getBoundingClientRect()` 给的是
   **变换后**的 AABB，"指针在不在它上面"于是成了一个随倾斜变化的量（详见
   bindFollow 的 stableHit）。这里沿 offsetParent 链累加到 container，
   再加上 container 自己（未被变换）的矩形，得到一个**固定**的参照。
   和 rectOf 一样按 _rectGen 缓存：稳态下一次布局都不读。

   ⚠ **前提：从 el 到 container 必须是"嵌套定位链"**（每一步的 offsetParent
   正好是下一步）。`offsetTop` 是相对 **offsetParent** 的 —— 若中间几层的
   offsetParent 是同一个祖先（卡片就是这样：`.fcard` 与它的几层父容器都相对
   `.drawer` 定位），逐级累加会**重复计数**，算出来的矩形是错的。
   所以不满足前提时直接退回 AABB（=旧的、会抖的那条路）：宁可抖，也不能
   让命中区错位。要把 stableHit 用到卡片上，得先按"扣除每个可滚动祖先的
   scrollTop/scrollLeft"重写这个函数 —— 别直接打开开关。 */
const _layoutCache = new WeakMap();
function layoutRectOf(container, el) {
  let r = _layoutCache.get(el);
  if (r && r.gen === _rectGen) return r;
  let x = 0, y = 0, n = el;
  while (n && n !== container) {
    x += n.offsetLeft;
    y += n.offsetTop;
    const p = n.offsetParent;
    if (p !== container && p !== n.parentElement) return rectOf(el);   // 非嵌套定位链
    n = p;
  }
  if (n !== container) return rectOf(el);
  const c = container.getBoundingClientRect();
  // 容器自己有滚动时，offset* 是"内容坐标"，要减掉滚动量才落到屏幕上
  // （模态遮罩不滚动，所以这一项对当前唯一的调用方是 0）
  r = { gen: _rectGen, left: c.left - container.scrollLeft + x,
        top: c.top - container.scrollTop + y,
        w: el.offsetWidth || 1, h: el.offsetHeight || 1 };
  _layoutCache.set(el, r);
  return r;
}

/* 通用：让 container 内的 itemSelector 元素跟随光标写入 --mx/--my/--nx/--ny
   opts.glow !== false  → 这个容器里的元素消费 --mx/--my（默认 true）。
     两个模态是 false：它们只用 --nx/--ny 做倾斜，--mx/--my 全站只有
     .fcard / .snap 的网点与径向光晕在用。于是 3D 关掉时，模态那条链路可以整个
     跳过 —— 一个字节都不写。
   opts.stableHit      → 目标元素**自己带跟随 transform**（四个模态的 .modal__box）。
     见下面那段"为什么模态要 stableHit"。 */
function bindFollow(container, itemSelector, opts = {}) {
  if (!container || container.dataset.followBound) return;
  container.dataset.followBound = '1';
  if (!window.matchMedia('(hover: hover)').matches) return;
  bindFollowRectHooks();

  const needsGlow = opts.glow !== false;

  /* ── 为什么模态要 stableHit：抖动的根因 ────────────────────────────────
     盒子的倾斜是"指针位置 → 变量 → transform"，而**浏览器命中测试**打在
     变换**之后**的几何上。于是"指针还在不在盒子上"也变成了倾斜的函数：
         指针停在盒子边缘
           → 静止：命中盒子 → 写变量 → 倾斜
           → 那一侧轮廓缩进约 5px → 不再命中 → pointerout 清掉变量
           → 回正 → 又命中 → ……            （按帧率跑的极限环）
     实测（宽盒 918×425、tilt 1.4°、`.modal` perspective 1000px、视口 966×703）：
       · 边缘内侧 1–5px 处命中目标在"静止↔倾斜"之间翻转，共 5 条带
         （`elementFromPoint` 逐点扫出来的）；
       · 轮廓单侧缩进 rotateY 5.07/5.23px、rotateX 2.08px
         （`transform-origin: 50% 10%`，转轴贴近上沿，所以上沿几乎不动）；
       · 视口 966px 时盒宽 918px（`min(940px, 100vw - 48px)`）→ 盒子边框离
         **窗口边**只有 24px，所以用户看到的现象是"鼠标到窗口边缘时模态在抖"。

     ⚠ 试过、**不行**的修法：给 `.modal__box` 加一个 `::after { inset: -8px }`
       内扩命中区。内侧那 5 条带确实没了，但伪元素是盒子的子节点、**跟着一起被
       变换**，内边距自己也会缩进 —— 内外一起扫会看到多出 6 条带，落在边缘
       **外** 2–7px。只是把不稳定搬了个地方（`ui/overlays.css` 里留了这段结论）。

     所以这类目标**不再用浏览器的命中测试**：改用 layoutRectOf() 的布局矩形
     （与 transform 无关）自己判"指针在不在盒子上"。判定边界固定在屏幕空间里，
     不随倾斜移动 —— 环就不存在了。 */
  const stableHit = opts.stableHit === true;

  /* 3D 开关关掉时：--nx/--ny 保持 0 —— 而不是"每帧写 0"。
     残留旧值这件事由开关自己负责：fxToggle 按下时会**一次性**把所有
     .fcard / .snap / .modal__box 的 --nx/--ny 归零（见 bind() 里那段）。
     CSS 侧也有兜底：消费点写的是 var(--nx, 0)，变量不存在等价于 0。
     所以这里"不写"是安全的，而"每帧写"会让模态框每帧白脏一次样式
     （它 settle 后有 backdrop-filter，一次脏 → 整页背景重绘）。
     仍然要写 --mx/--my 的只有卡片/快照（径向光晕）。 */
  const fxOff = () => document.documentElement.dataset.fx === 'off';

  /* 鼠标 1000Hz 时一帧内会来好几个 pointermove。原来每个都写 4 个变量，
     而 --mx/--my 驱动的是 mask-image / background-image（**绘制属性**，见
     ui/cardlib.css 的网点四层）—— 等于一帧内让同一张卡重绘好几次，
     每次都要重新栅格化 4 层网点再把位图传给合成器（trace 实测：
     Paint 100–400us + Layerize 30–230us + Commit 30–270us，每次移动都来一遍）。
     合并到每帧一次即可：多出来的中间态本来就看不见。 */
  const MIN_MOVE = 3;             // px：网点是软边径向遮罩，3px 以内的位移看不出来
  let rafId = 0;
  let pend = null;
  const lastXY = new WeakMap();   // el → {x, y}，用来判断"动够了没有"

  /* ── 释放动画（.is-returning）────────────────────────────────────────
     `clearFollow()` 会在摘掉 --nx/--ny **之前**给元素挂上 .is-returning，
     CSS 于是把这一次"回正"走成过渡而不是瞬变（模态原本是瞬变的：
     它的 transform 被故意移出过渡列表以省连续跟随期间的开销，见 overlays.css）。

     三条纪律，缺一个就会把那笔开销带回来或者留下脏类：
       · 指针**又进来**时立刻摘掉（cancelReturning）—— 否则连续跟随又变成
         "每帧重启过渡" → 每帧 Layout；
       · 定时器按元素存（WeakMap），不是按容器一个 —— 指针扫过卡片网格时
         同时有好几张卡在回正，共用一个 id 会漏摘（那些卡会永久停在慢缓动）；
       · 只在"确实带着倾斜"时挂 —— 指针在盒子外来回移动时 clearFollow 会被
         反复调用，不该每次都排一个定时器。
     CSS 过渡按**墙钟**计时（不是按帧），0.30s 的曲线到 0.3s 处已 ~99%，
     所以 380ms 后摘类是稳的。 */
  const RELEASE_MS = 380;
  const releaseTimers = new WeakMap();

  function markReturning(el) {
    if (releaseTimers.has(el)) return;        // 已经在窗口里，别重置定时器
    el.classList.add('is-returning');
    releaseTimers.set(el, setTimeout(() => {
      el.classList.remove('is-returning');
      releaseTimers.delete(el);
    }, RELEASE_MS));
  }

  function cancelReturning(el) {
    const t = releaseTimers.get(el);
    if (t === undefined) return;              // 常见路径：没在回正，直接返回
    clearTimeout(t);
    releaseTimers.delete(el);
    el.classList.remove('is-returning');
  }

  function clearFollow(el) {
    // 已排队的补写要丢掉，否则会把变量写回一个指针已经离开的元素
    if (pend && pend.el === el) pend = null;
    lastXY.delete(el);
    // 只有真的带着倾斜才需要"缓回"；否则（指针在盒子外游走）别反复挂类
    if (el.style.getPropertyValue('--nx') !== ''
        || el.style.getPropertyValue('--ny') !== '') {
      markReturning(el);
    }
    ['--mx', '--my', '--nx', '--ny'].forEach(p => el.style.removeProperty(p));
  }

  function flushFollow() {
    rafId = 0;
    const p = pend;
    pend = null;
    if (!p) return;
    const el = p.el;
    if (!el.isConnected) return;

    /* stableHit 用布局矩形：--nx/--ny 于是只由指针位置决定，**与当前倾斜无关**。
       （用 AABB 的话，矩形本身随倾斜/落定时刻变化，映射会跟着漂。） */
    const r = stableHit ? layoutRectOf(container, el) : rectOf(el);
    const x = p.cx - r.left;
    const y = p.cy - r.top;

    /* 位移不够就跳过。3D 关掉时也一样成立：那时只剩光晕，而光晕是软边径向遮罩，
       3px 以内的位移看不出来。（原来这里因为要"每帧写 0"而绕过了阈值，
       现在不需要写 0 了，所以阈值对所有情况都生效。） */
    const prev = lastXY.get(el);
    if (prev && Math.abs(prev.x - x) < MIN_MOVE && Math.abs(prev.y - y) < MIN_MOVE) return;
    lastXY.set(el, { x, y });

    if (needsGlow) {                      // 只有 .fcard / .snap 消费 --mx/--my
      el.style.setProperty('--mx', x + 'px');
      el.style.setProperty('--my', y + 'px');
    }
    if (!fxOff()) {                       // 3D 关掉时 --nx/--ny 保持 0（不写）
      el.style.setProperty('--nx', ((x / r.w) * 2 - 1).toFixed(3));
      el.style.setProperty('--ny', ((y / r.h) * 2 - 1).toFixed(3));
    }
  }

  container.addEventListener('pointermove', (e) => {
    /* 3D 关掉 + 这个容器不消费 --mx/--my（两个模态就是）：
       整条链路没有任何东西要写，连 rAF 都不排。 */
    if (fxOff() && !needsGlow) return;

    let el;
    if (stableHit) {
      /* 不用 e.target.closest()：那是**浏览器**的命中测试，打在倾斜后的几何上，
         正是抖动的来源（见 bindFollow 顶部）。改用固定的布局矩形自己判。
         附带好处：3D 关掉时上面那行已经返回，这里不必再判。 */
      el = container.querySelector(itemSelector);
      if (!el) return;
      const r = layoutRectOf(container, el);
      if (e.clientX < r.left - HIT_MARGIN || e.clientX > r.left + r.w + HIT_MARGIN ||
          e.clientY < r.top - HIT_MARGIN || e.clientY > r.top + r.h + HIT_MARGIN) {
        clearFollow(el);                // 真的离开了 → 回正（这一条取代了 pointerout）
        return;
      }
    } else {
      el = e.target.closest(itemSelector);
      if (!el || !container.contains(el)) return;
    }
    // 指针又进来了：立刻结束"回正窗口"，否则连续跟随会每帧重启过渡
    cancelReturning(el);
    pend = { el, cx: e.clientX, cy: e.clientY };
    if (!rafId) rafId = requestAnimationFrame(flushFollow);
  });

  if (stableHit) {
    /* 指针离开整个容器（模态遮罩是 inset:0，等于离开窗口）时回正 ——
       否则最后一帧的倾斜会一直留着。命中边界由 pointermove 那段负责。 */
    container.addEventListener('pointerleave', () => {
      const el = container.querySelector(itemSelector);
      if (el) clearFollow(el);
    });
  } else {
    container.addEventListener('pointerout', (e) => {
      const el = e.target.closest(itemSelector);
      if (!el || !container.contains(el)) return;
      const to = e.relatedTarget;
      if (to && el.contains(to)) return;
      clearFollow(el);
    });
  }
}

function initCardFollow() {
  bindFollow(document.getElementById('cardSections'), '.fcard');
}

/* 预设卡片也跟随光标（§10.6.3 之后的一批）：和功能卡片**同一套效果** ——
   3D 倾斜 + AM 网点 + 图标视差。少了这一句的表现很有欺骗性：`.pcard` 的 CSS
   全都在，只是 `--mx/--my/--nx/--ny` 永远是默认值，于是卡片看着"很正常"、
   只是不动 —— 不报任何错，只有人眼能发现。

   容器用 `#presetSections`（预设网格是它的子元素，每次 `renderPresets()`
   重写 innerHTML 也不会掉绑定，事件是委托的）。 */
function initPresetFollow() {
  bindFollow(document.getElementById('presetSections'), '.pcard');
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
  if (localStorage.getItem('ae-mode') === 'dark') document.documentElement.dataset.mode = 'dark';
  // 3D 跟随开关：默认开；用户关过就恢复成关
  if (localStorage.getItem('ae-fx3d') === 'off') document.documentElement.dataset.fx = 'off';
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
  bindSectionToggles(); 
  initCardFollow(); 
  initSnapFollow();
  /* 预设网格也要登记（和上面两个一样，漏了就是"预设卡不会动"）。
     这里只做绑定、不依赖预设数据：事件委托在容器上，`loadPresets()` 之后
     渲染出来的 `.pcard` 自动就在生效范围内。 */
  initPresetFollow();
  // ⚠ **每加一个模态都要在这里登记**，否则它的动效与其它模态不一致 ——
  // `bindFollow` 给的是"跟随鼠标的倾斜"，`openModal` 里的 `settleModal` 给的是
  // "旋入动画结束后才开毛玻璃"。漏登记的表现很隐蔽：模态**能开能用**，
  // 只是看起来"硬"一点（没有倾斜、毛玻璃从第一帧就有），
  // 不会报任何错，所以只有人眼能发现（用户就是这么发现的）。
  // 模态：要点见 bindFollow 顶部的 stableHit —— 目标自己会倾斜，
  // 命中判定必须走布局矩形，否则指针停在模态边框上会抖（按帧率跑的极限环）。
  // glow:false —— 它们只用 --nx/--ny 做倾斜，不消费 --mx/--my。
  [ 'metaModal', 'cardModal', 'presetModal', 'chainEditModal' ].forEach(id => {
    bindFollow(document.getElementById(id), '.modal__box', { glow: false, stableHit: true });
  });
  syncDrawerLeft();
  applyStop('closed');
  requestAnimationFrame(redrawAllWaves);

  // 操作目录要在首屏就拉：链上的"接不上"提示与卡片置灰都靠 `OPS_CATALOG`
  // 里的 `produce`/`needs`/`gives`/`consumes`（方案 §3.2.4）。
  // 它原来只在**打开卡片编辑器**时才加载，所以首屏那两条功能是哑的 ——
  // 表现是"卡片不置灰、链上没提示"，而控制台一声不响。
  // 放在最后、异步进行：它失败也不该拖住首屏渲染。
  if (typeof API !== 'undefined') {
    loadOpsCatalog()
      .then(() => {
        renderChain();
        /* 预设卡片的图标/相邻合并都吃 `OPS_CATALOG`（`presetIcons` → `stepIconKey`）。
           它和 `loadPresets()` 是两条**并发**的请求：谁先回来不定，而
           `renderDrawerTab()` 会在 `OPS_CATALOG` 还没到时就渲染一次预设网格 ——
           那条路径上 `iconTextFor` 查不到 op，全部回落成 `tag`，
           于是一屏一模一样的图标，直到下一次重渲染才自愈。
           这里补一次：目录到了、且正停在预设视图，就重画。 */
        if (drawerTab === 'preset') renderPresets();
      })
      .catch(() => { /* 读取失败时链的提示功能退化为不可用，不影响其它 */ });
  }

  // 预设也首屏拉一次：抽屉标题可能是"预设链路"（localStorage 记着），
  // 那时不拉就只有一个空网格。`renderDrawerTab` 顺带把两个容器切到位。
  renderDrawerTab();
  if (typeof API !== 'undefined') {
    loadPresets().catch(() => { /* 拉不到就显示空态，不影响卡片视图 */ });
  }
}

document.addEventListener('DOMContentLoaded', init);
