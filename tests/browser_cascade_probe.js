/* 直接对活元素做实验：背景到底从哪来 */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const root = document.documentElement;
  const rgb = (el) => getComputedStyle(el).backgroundColor;
  try {
    for (let i = 0; i < 60 && !document.querySelector('#fileList .card'); i++) await wait(250);
    await wait(1400);
    out.themeBefore = root.dataset.theme || '(未设)';
    out.themeAttrOnHtml = [...root.attributes].map(a => `${a.name}=${a.value}`).join(' ');

    root.dataset.theme = 't2';
    delete root.dataset.mode;
    await wait(400);
    out.rootFillAccent = getComputedStyle(root).getPropertyValue('--fill-accent').trim();

    const card = document.querySelector('#fileList .card');
    const btn = card.querySelector('.transport__btn');
    out.beforePlay = { cardCls: card.className, bg: rgb(btn) };

    // 手动加 is-playing（不经播放逻辑）
    card.classList.add('is-playing');
    await wait(400);
    out.afterAddClass = { cardCls: card.className, bg: rgb(btn),
                          accent: getComputedStyle(btn).getPropertyValue('--fill-accent').trim() };

    // 去掉过渡再读一次，排除过渡中间值
    const kill = document.createElement('style');
    kill.textContent = '*,*::before,*::after{transition:none!important;animation:none!important}';
    document.head.appendChild(kill);
    await wait(120);
    out.noTransition = { bg: rgb(btn) };

    // 拆掉 class 再装回来
    card.classList.remove('is-playing');
    await wait(80);
    out.withoutClass = { bg: rgb(btn) };
    card.classList.add('is-playing');
    await wait(80);
    out.withClassAgain = { bg: rgb(btn) };

    // 全新造一个同样的结构，挂在 body 下
    const fresh = document.createElement('div');
    fresh.className = 'card is-playing';
    fresh.innerHTML = '<button class="transport__btn"></button>';
    document.body.appendChild(fresh);
    await wait(80);
    out.freshNode = { bg: rgb(fresh.querySelector('.transport__btn')) };
    fresh.remove();

    // 把 var 直接写死，看会不会变
    btn.style.background = 'var(--fill-accent)';
    await wait(80);
    out.inlineVarAccent = rgb(btn);
    btn.style.background = '';
    await wait(80);

    // 列出所有含 "transport__btn" 的规则原文
    const rules = [];
    for (const sheet of document.styleSheets) {
      let list; try { list = sheet.cssRules; } catch { continue; }
      for (const r of list) {
        if (r.selectorText && r.selectorText.includes('transport__btn')) {
          rules.push({ sel: r.selectorText, css: r.cssText.slice(0, 220) });
        }
      }
    }
    out.rules = rules;
    out.err = null;
  } catch (e) { out.err = String((e && e.stack) || e); }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
