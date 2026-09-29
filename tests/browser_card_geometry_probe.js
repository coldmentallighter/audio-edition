/* 加了试听控件之后，卡片与波形区的高度有没有被撑坏 */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const rect = (el) => { const r = el.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height),
             top: Math.round(r.top), bottom: Math.round(r.bottom) }; };
  try {
    for (let i = 0; i < 60 && !document.querySelector('#fileList .card'); i++) await wait(250);
    await wait(1500);

    const card = document.querySelector('#fileList .card');
    const wave = card.querySelector('.wave');
    const wrap = card.querySelector('.wave__canvasWrap');
    const cv = card.querySelector('canvas');
    const meta = card.querySelector('.wave__meta');
    const trans = card.querySelector('.transport');
    const btn = card.querySelector('.transport__btn');
    const mc = card.querySelector('.metacard');
    const st = card.querySelector('.statuscol');

    out.card = rect(card);
    out.wave = rect(wave);
    out.canvasWrap = rect(wrap);
    out.canvas = rect(cv);
    out.canvasAttr = { w: cv.width, h: cv.height };
    out.meta = rect(meta);
    out.transport = rect(trans);
    out.playBtn = rect(btn);
    out.metacard = rect(mc);
    out.statuscol = rect(st);

    // 卡片有没有内容溢出（出现滚动条 / 子元素超出底边）
    out.cardScrollOverflow = card.scrollHeight - card.clientHeight;
    const kids = [...card.querySelectorAll('.card__body > *')].map(rect);
    out.childrenBottom = Math.max(...kids.map(k => k.bottom));
    out.overflowBottom = out.childrenBottom - out.card.bottom;

    // 波形区里所有子元素都不该互相压住
    out.waveChildren = [...wave.children].map(e => ({ cls: e.className, ...rect(e) }));
    out.canvasMinHeightOk = out.canvasWrap.h >= 62;

    // 指针默认隐藏
    const head = card.querySelector('.wave__play');
    out.headHidden = head.hidden;
    out.headLeft = head.style.left;

    // 卡片在列表里的间距是否正常（相邻卡不重叠）
    const cards = [...document.querySelectorAll('#fileList .card')].map(rect);
    out.cardTops = cards.map(c => c.top);
    out.noOverlap = cards.every((c, i) => i === 0 || c.top >= cards[i - 1].bottom);

    out.err = null;
  } catch (e) { out.err = String((e && e.stack) || e); }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
