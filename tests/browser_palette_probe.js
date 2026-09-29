/* 配色落地验证：从浏览器**算完的**样式里读实际颜色，
   确认 theme.css 的值真的作用到组件上（而不是只写在文件里）。

   这一版重写过断言。原来的写法是把期望值写成十六进制字面量
   （`t2AccentIsOldError: tokens["--fill-accent"] === "#FFE5B4"`），
   于是 theme.css 一改色，探针就红 —— 而它红的含义是"你改了颜色"，
   不是"有 bug"。真正该验的是**关系**：

     · 三个角色（主色 / 第二色 / 错误色）必须两两不同
     · 第二色必须真的落到组件上（进度条填充 = --fill-accent）
     · 主题切换器上的色点必须同时含主色与第二色（第二色在静态界面里只有这里看得见）
     · 错误色必须是暖红族（对调之后不该再是奶油色 / 黄绿）
     · t2 主色必须确实是"降饱和的绿"
     · 六套主题都要给全令牌，且 body 底色跟着主题走

   注意：测量前必须先冻住过渡，否则 --virtual-time-budget 不推进 CSS 过渡，
   读到的是切换前的旧值（本文件踩过一次，见 tests/README.md 第 2 个坑）。 */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const root = document.documentElement;
  const cs = (el, p) => getComputedStyle(el).getPropertyValue(p).trim();
  const v = (name) => getComputedStyle(root).getPropertyValue(name).trim();
  const norm = (c) => {
    const p = document.createElement('span');
    p.style.color = c;
    document.body.appendChild(p);
    const s = getComputedStyle(p).color;
    p.remove();
    return s;
  };
  const parse = (s) => {
    const m = String(s).match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[,\s/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const lum = ({ r, g, b }) => {
    const f = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (a, b) => {
    const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05);
  };
  const hex = (s) => {
    const c = parse(s);
    return c ? "#" + [c.r, c.g, c.b].map(x => Math.round(x).toString(16).padStart(2, "0")).join("").toUpperCase() : s;
  };
  // rgb() 串，用来在渐变字符串里找颜色
  const rgbStr = (hexColor) => {
    const c = parse(norm(hexColor));
    return c ? `rgb(${c.r}, ${c.g}, ${c.b})` : "\u0000";
  };
  // HSL 饱和度 0~100：验"绿色降饱和"这件事，比抄十六进制稳
  const saturation = (hexColor) => {
    const c = parse(norm(hexColor));
    if (!c) return null;
    const [r, g, b] = [c.r, c.g, c.b].map(x => x / 255);
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    const l = (mx + mn) / 2;
    return d === 0 ? 0 : +(100 * d / (1 - Math.abs(2 * l - 1))).toFixed(1);
  };
  // 暖红族：R 明显最大（错误色该长这样，而不是奶油色）
  const isWarmRed = (hexColor) => {
    const c = parse(norm(hexColor));
    return !!c && c.r > c.g && c.r > c.b;
  };

  const checks = {};
  const fail = [];
  const must = (name, ok, detail) => { checks[name] = detail === undefined ? ok : detail; if (!ok) fail.push(name); };

  try {
    // ---- 冻住过渡：不然下面读到的全是切换前的值 ----
    const freeze = document.createElement('style');
    freeze.textContent = '*{transition:none!important;animation:none!important}';
    document.head.appendChild(freeze);

    await wait(900);

    const TOKENS = ["--bg-app", "--fill-primary", "--fill-accent", "--error-solid",
                    "--error-tint", "--on-fill", "--ink"];
    out.themes = {};
    for (const theme of ["t1", "t2", "t3"]) {
      for (const mode of ["light", "dark"]) {
        root.dataset.theme = theme;
        if (mode === "dark") root.dataset.mode = "dark"; else delete root.dataset.mode;
        await wait(80);

        const row = { tokens: {} };
        TOKENS.forEach(t => { row.tokens[t] = hex(norm(v(t))); });

        const prog = document.querySelector('.progress > i');
        if (prog) row.progressFill = hex(cs(prog, 'background-color'));
        const chip = document.querySelector('.chain__item');
        if (chip) row.chainChip = hex(cs(chip, 'background-color'));
        row.bodyBg = hex(cs(document.body, 'background-color'));

        // 色点是**渐变**，background-color 永远是透明（探针原来读它，
        // 于是六套主题全部得到 #000000）。要读 background-image。
        const dot = document.querySelector(`.dot--${theme}`);
        if (dot) row.switcherDotImage = cs(dot, 'background-image');

        out.themes[`${theme}/${mode}`] = row;
      }
    }
    root.dataset.theme = "t1";
    delete root.dataset.mode;

    const keys = Object.keys(out.themes);
    const lights = { t1: out.themes["t1/light"], t2: out.themes["t2/light"], t3: out.themes["t3/light"] };

    // 1) 六套主题都要给全这 7 个令牌，且都是合法颜色
    must('allTokensPresent', keys.every(k =>
      TOKENS.every(t => /^#[0-9A-F]{6}$/.test(out.themes[k].tokens[t]))),
      keys.map(k => `${k}:${TOKENS.filter(t => !/^#[0-9A-F]{6}$/.test(out.themes[k].tokens[t])).join(',') || 'ok'}`));

    // 2) 三个色角色两两不同 —— 这就是"第二色没变 / 和错误色撞车"的判据
    for (const [name, row] of Object.entries(lights)) {
      const three = new Set([row.tokens['--fill-primary'], row.tokens['--fill-accent'], row.tokens['--error-solid']]);
      must(`${name}RolesDistinct`, three.size === 3, [...three]);
    }

    // 3) 第二色真的落到组件上：进度条填充 = --fill-accent
    must('progressUsesAccent', Object.keys(lights).every(k =>
      out.themes[`${k}/light`].progressFill === out.themes[`${k}/light`].tokens['--fill-accent']),
      Object.fromEntries(Object.keys(lights).map(k =>
        [`${k}/light`, [out.themes[`${k}/light`].progressFill, out.themes[`${k}/light`].tokens['--fill-accent']]])));

    // 4) 主题切换器的色点必须是"主色 + 第二色"双色渐变。
    //    app.css 里这三个点是硬编码的（刻意的：预览别家主题），所以这里比对
    //    它有没有跟 theme.css 脱节 —— t2 就漏跟过一次。
    for (const theme of ["t1", "t2", "t3"]) {
      const row = out.themes[`${theme}/light`];
      const img = row.switcherDotImage || '';
      const p = rgbStr(row.tokens['--fill-primary']);
      const a = rgbStr(row.tokens['--fill-accent']);
      must(`dot${theme}TwoTone`,
        img.includes('gradient') && img.includes(p) && img.includes(a),
        { dot: img, wantPrimary: p, wantAccent: a });
    }

    // 5) 错误色必须是暖红族（对调后 t2/t3 不该再是奶油色 / 黄绿）
    for (const [name, row] of Object.entries(lights)) {
      must(`${name}ErrorIsWarmRed`, isWarmRed(row.tokens['--error-solid']), row.tokens['--error-solid']);
    }

    // 6) t2 的绿必须确实是降过饱和的（源色板 S≈53.1%，这里要求 <40）
    const t2sat = saturation(lights.t2.tokens['--fill-primary']);
    must('t2PrimaryDesaturated', t2sat !== null && t2sat < 40, { saturation: t2sat });

    // 7) body 底色要跟着主题走（冻住过渡之后才量得准）
    must('bodyBgFollowsTheme', keys.every(k => out.themes[k].bodyBg === out.themes[k].tokens['--bg-app']),
      Object.fromEntries(keys.map(k => [k, [out.themes[k].bodyBg, out.themes[k].tokens['--bg-app']]])));

    // 浏览器里再算一遍对比度，和 theme_check.py 的结果互相印证
    const pairs = [
      ["t2/light", "--ink", "--bg-app"], ["t2/light", "--ink", "--error-tint"],
      ["t2/light", "--on-fill", "--fill-accent"],
      ["t3/light", "--ink", "--bg-app"], ["t3/light", "--ink", "--error-tint"],
      ["t3/light", "--on-fill", "--fill-accent"],
    ];
    out.contrast = {};
    for (const [key, a, b] of pairs) {
      const ta = parse(norm(out.themes[key].tokens[a]));
      const tb = parse(norm(out.themes[key].tokens[b]));
      out.contrast[`${key} ${a}/${b}`] = +ratio(ta, tb).toFixed(2);
    }
    // 8) 对照度设一道下限（theme_check.py 的口径是 AA 4.5）
    must('contrastMeetsAA', Object.values(out.contrast).every(r => r >= 4.5), out.contrast);

    out.checks = checks;
    out.failed = fail;
    out.allOk = fail.length === 0;
    out.err = null;
  } catch (e) {
    out.err = String((e && e.stack) || e);
    out.allOk = false;
  }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
