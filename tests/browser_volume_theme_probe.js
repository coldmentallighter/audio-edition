/* 音量滑块在 6 套主题下的可辨识度：
   已填充轨道 / 空轨道 / 数值文字 三者相对 chainbar 底色都要看得出。 */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const root = document.documentElement;

  const parse = (c) => {
    const s = String(c).trim();
    // color-mix() 的 computed 形式是 color(srgb r g b / a)，分量是 0..1 浮点
    const cm = s.match(/^color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+))?\)$/);
    if (cm) {
      return { r: +cm[1] * 255, g: +cm[2] * 255, b: +cm[3] * 255,
               a: cm[4] === undefined ? 1 : +cm[4] };
    }
    const m = s.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[,\s/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  /* 主题令牌是 #RRGGBB 十六进制，直接正则匹配 rgb() 会得到 null。
     交给浏览器归一化：把值塞进一个元素的 color，再读 computed color。 */
  const norm = (value) => {
    const probe = document.createElement('span');
    probe.style.color = value;
    if (!probe.style.color) return null;          // 值本身不合法
    probe.style.display = 'none';
    document.body.appendChild(probe);
    const c = parse(getComputedStyle(probe).color);
    probe.remove();
    return c;
  };
  const lum = ({ r, g, b }) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (a, b) => {
    const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05);
  };
  // 把半透明前景叠到底色上再比，否则带 alpha 的颜色会比出假结果
  const over = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1,
  });

  try {
    await wait(1300);
    const bar = document.querySelector('.chainbar');
    const range = document.getElementById('volRange');
    const btn = document.getElementById('volMute');
    const val = document.getElementById('volVal');

    const cs = (el, prop) => getComputedStyle(el).getPropertyValue(prop);
    const readVar = (name) => getComputedStyle(root).getPropertyValue(name).trim();

    // 固定到 50%：这样渐变两段（已填充 / 空轨道）都有实际宽度可取样。
    // 否则上一轮 localStorage 里的值会让每次跑的数字不一样。
    range.value = '50';
    range.dispatchEvent(new Event('input', { bubbles: true }));
    await wait(80);

    out.rows = [];
    for (const theme of ['t1', 't2', 't3']) {
      for (const mode of ['light', 'dark']) {
        root.dataset.theme = theme;
        if (mode === 'dark') root.dataset.mode = 'dark'; else delete root.dataset.mode;
        await wait(60);

        const barBg = parse(cs(bar, 'background-color'));
        const muted = norm(readVar('--ink-error'));
        const label = parse(cs(val, 'color'));

        // 轨道是渐变，直接从浏览器算完的 background-image 里取实际色停：
        // 这样连 color-mix() 解析后的结果也是真的，而不是我按令牌猜的。
        // 注意渐变有 4 个停点（0 / pct / pct / 100），取第 1 个当已填充、最后 1 个当空轨道。
        const grad = cs(range, 'background-image');
        const stops = (grad.match(/(?:rgba?\([^)]+\)|color\(srgb[^)]+\))/g) || []).map(parse);
        const filled = stops[0] || null;
        const empty = stops[stops.length - 1] || null;
        const rawGrad = grad;
        const pctNow = +range.value;

        if (!barBg || !filled || !empty || !muted || !label) {
          out.rows.push({ theme, mode, error: 'color parse failed',
            raw: { barBg: cs(bar, 'background-color'), grad, label: cs(val, 'color') } });
          continue;
        }

        const filledR = ratio(over(filled, barBg), barBg);
        const emptyR = ratio(over(empty, barBg), barBg);
        const labelR = ratio(over(label, barBg), barBg);
        const mutedR = ratio(over(muted, barBg), barBg);

        out.rows.push({
          theme, mode,
          barBg: cs(bar, 'background-color'),
          labelColor: cs(val, 'color'),
          filledContrast: +filledR.toFixed(2),
          emptyContrast: +emptyR.toFixed(2),
          labelContrast: +labelR.toFixed(2),
          mutedContrast: +mutedR.toFixed(2),
          // 已填充 vs 空轨道：滑块能看出"拖到哪了"
          filledVsEmpty: +ratio(over(filled, barBg), over(empty, barBg)).toFixed(2),
          // 拇指边框同 --fill-primary，非透明即有形
          thumbVisible: filled.a > 0,
          labelText: val.textContent.trim(),
          rangeValue: range.value,
          muteColorApplied: getComputedStyle(btn).color,
          rawGrad,
        });
      }
    }
    root.dataset.theme = 't1';
    delete root.dataset.mode;
    await wait(60);

    const bad = out.rows.filter(r =>
      r.filledContrast < 1.5 || r.emptyContrast < 1.2 ||
      r.labelContrast < 4.5 || r.filledVsEmpty < 1.2);
    out.problems = bad.map(r => `${r.theme}/${r.mode}`);
    out.allOk = bad.length === 0;
    out.minLabelContrast = Math.min(...out.rows.map(r => r.labelContrast));
    out.minFilledContrast = Math.min(...out.rows.map(r => r.filledContrast));
    out.minEmptyContrast = Math.min(...out.rows.map(r => r.emptyContrast));
    out.minFilledVsEmpty = Math.min(...out.rows.map(r => r.filledVsEmpty));
    out.err = null;
  } catch (e) { out.err = String((e && e.stack) || e); }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
