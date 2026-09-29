/* 音量滑块探针：位置、拖动、静音、持久化、真实 audio.volume。 */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const rect = (el) => { const r = el.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height),
             left: Math.round(r.left), right: Math.round(r.right),
             top: Math.round(r.top), bottom: Math.round(r.bottom) }; };
  const el = () => document.getElementById('player');
  const range = () => document.getElementById('volRange');
  const btn = () => document.getElementById('volMute');
  const setRange = async (v) => {
    range().value = String(v);
    range().dispatchEvent(new Event('input', { bubbles: true }));
    await wait(80);
  };

  try {
    await wait(1200);

    /* ---------- 1. 位置：必须在 chainbar 内部的最右侧 ---------- */
    const bar = document.querySelector('.chainbar');
    const box = document.querySelector('.chainbar__vol');
    const acts = document.querySelector('.chainbar__acts');
    out.chainbar = rect(bar);
    out.volBox = rect(box);
    out.acts = rect(acts);
    out.insideChainbar = bar.contains(box);
    out.rightOfActs = out.volBox.left >= out.acts.right - 1;
    // 右端贴着 chainbar 的右内边（允许右边的 padding）
    out.gapToBarRight = out.chainbar.right - out.volBox.right;
    out.barHasVolOnRight = out.gapToBarRight <= 14;

    out.parts = {
      muteBtn: !!btn(), range: !!range(), value: !!document.getElementById('volVal'),
      rangeType: range() && range().type,
      min: range() && range().min, max: range() && range().max,
    };
    out.rangeBox = rect(range());
    out.btnBox = rect(btn());

    /* ---------- 2. 初始状态 ----------
       重点：全新访客（localStorage 里没有 ae-volume）必须是 100%，
       不能因为 Number(null)===0 而默认静音。 */
    out.storageBeforeAnyWrite = {
      volume: localStorage.getItem('ae-volume'),
      muted: localStorage.getItem('ae-muted'),
    };
    out.initial = {
      audioVolume: el() ? el().volume : null,
      muted: el() ? el().muted : null,
      rangeValue: range().value,
      label: document.getElementById('volVal').textContent.trim(),
      ariaPressed: btn().getAttribute('aria-pressed'),
      ariaLabel: btn().getAttribute('aria-label'),
      isMutedClass: btn().classList.contains('is-muted'),
    };
    out.firstRunIsFullVolume = !out.storageBeforeAnyWrite.volume && el().volume === 1;

    /* ---------- 3. 拖动滑块 -> 真的改 audio.volume ---------- */
    await setRange(30);
    out.at30 = {
      audioVolume: +el().volume.toFixed(2),
      rangeValue: range().value,
      label: document.getElementById('volVal').textContent.trim(),
      fillPct: range().style.getPropertyValue('--vol-pct'),
      iconHasLowWave: btn().innerHTML.includes('M15 9.5a4 4 0 0 1 0 5"') &&
                      !btn().innerHTML.includes('17.6'),
      muted: el().muted,
    };

    await setRange(90);
    out.at90 = {
      audioVolume: +el().volume.toFixed(2),
      label: document.getElementById('volVal').textContent.trim(),
      fillPct: range().style.getPropertyValue('--vol-pct'),
      iconHasBothWaves: btn().innerHTML.includes('17.6'),
    };

    /* ---------- 4. 拖到 0 = 静音外观，但 volume 属性保留 0 ---------- */
    await setRange(0);
    out.at0 = {
      audioVolume: +el().volume.toFixed(2),
      label: document.getElementById('volVal').textContent.trim(),
      ariaPressed: btn().getAttribute('aria-pressed'),
      isMutedClass: btn().classList.contains('is-muted'),
      iconIsMute: btn().innerHTML.includes('15.5 9.5l5 5'),
    };

    /* ---------- 5. 静音按钮 ---------- */
    await setRange(65);
    btn().click(); await wait(80);
    out.afterMute = {
      muted: el().muted, volume: +el().volume.toFixed(2),
      ariaPressed: btn().getAttribute('aria-pressed'),
      isMutedClass: btn().classList.contains('is-muted'),
      iconIsMute: btn().innerHTML.includes('15.5 9.5l5 5'),
      label: document.getElementById('volVal').textContent.trim(),
    };
    btn().click(); await wait(80);
    out.afterUnmute = {
      muted: el().muted, volume: +el().volume.toFixed(2),
      ariaPressed: btn().getAttribute('aria-pressed'),
      label: document.getElementById('volVal').textContent.trim(),
    };

    /* ---------- 6. 静音状态下往上拉滑块要自动解除静音 ---------- */
    btn().click(); await wait(60);              // 先静音
    const wasMuted = el().muted;
    await setRange(40);
    out.unmuteBySlider = { wasMuted, mutedNow: el().muted, volume: +el().volume.toFixed(2) };

    /* ---------- 7. 持久化 ---------- */
    await setRange(45);
    btn().click(); await wait(60);              // 静音
    out.stored = {
      volume: localStorage.getItem('ae-volume'),
      muted: localStorage.getItem('ae-muted'),
    };
    // 再点一次恢复，别把"静音"状态留给用户
    btn().click(); await wait(60);
    out.finalState = { muted: el().muted, volume: +el().volume.toFixed(2) };

    /* ---------- 8. 不影响布局：chainbar 有没有被撑破 ---------- */
    const head = document.querySelector('.header');
    out.header = rect(head);
    out.noHeaderOverflow = bar.scrollWidth <= bar.clientWidth + 1;
    out.chainHasRoom = (() => {
      const c = document.querySelector('.chain');
      return c ? rect(c).w : null;
    })();

    out.err = null;
  } catch (e) {
    out.err = String((e && e.stack) || e);
  }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
