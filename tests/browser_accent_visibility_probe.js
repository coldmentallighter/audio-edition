/* 第二颜色在"默认界面"上到底看不看得见 + 主题色点是否双色。
   注意：先注入 transition:none —— headless 下 --virtual-time-budget 不推进 CSS 过渡，
   切换主题后带 transition 的属性会一直返回**过渡前**的旧值（这个坑已经踩过两次）。 */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const root = document.documentElement;
  const bgOf = (sel) => {
    const e = document.querySelector(sel);
    return e ? getComputedStyle(e).backgroundColor : null;
  };
  const hex = (s) => {
    if (!s) return null;
    const m = String(s).match(/rgba?\(([^)]+)\)/);
    if (!m) return s;
    const p = m[1].split(/[,\s/]+/).filter(Boolean).map(Number);
    if (p.length > 3 && p[3] === 0) return 'transparent';
    return '#' + p.slice(0, 3).map(x => Math.round(x).toString(16).padStart(2, '0')).join('').toUpperCase();
  };
  const v = (n) => getComputedStyle(root).getPropertyValue(n).trim();

  try {
    // 关键：先冻住过渡，否则切主题后读到的是旧值
    const freeze = document.createElement('style');
    freeze.textContent = '*{transition:none!important;animation:none!important}';
    document.head.appendChild(freeze);

    for (let i = 0; i < 60 && !document.querySelector('#fileList .card'); i++) await wait(250);
    await wait(1400);

    out.perTheme = {};
    for (const t of ['t1', 't2', 't3']) {
      root.dataset.theme = t;
      delete root.dataset.mode;
      await wait(120);
      out.perTheme[t] = {
        fillPrimary: v('--fill-primary'),
        fillAccent: v('--fill-accent'),
        // 默认状态下唯一稳定露出的两个地方
        snapIconBg: hex(bgOf('.snap__ico')),
        snapIconFound: !!document.querySelector('.snap__ico'),
        dotBackgroundImage: (() => {
          const d = document.querySelector(`.dot--${t}`);
          return d ? getComputedStyle(d).backgroundImage : null;
        })(),
      };
    }

    root.dataset.theme = 't2';
    await wait(120);
    out.t2 = {
      fillAccent: v('--fill-accent'),
      snapIconBg: hex(bgOf('.snap__ico')),
      snapIconColor: hex(getComputedStyle(document.querySelector('.snap__ico')).color),
      snapCount: document.querySelectorAll('.snap__ico').length,
      // 抽屉收起时快照条是唯一可见部分 —— 这就是"默认界面"
      drawerStop: document.getElementById('drawer').dataset.stop,
      snapBarVisible: (() => {
        const r = document.querySelector('.drawer__bar').getBoundingClientRect();
        return r.top < innerHeight && r.bottom > 0;
      })(),
      // 其余仍只在瞬时状态出现
      bulkbarHidden: document.getElementById('bulkbar').hidden,
      playingCards: document.querySelectorAll('.card.is-playing').length,
    };

    out.err = null;
  } catch (e) { out.err = String((e && e.stack) || e); }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
