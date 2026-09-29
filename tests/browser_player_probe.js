/* 试听播放器探针：真播放、真跳转，并验证播放指针跟着走。
   需要 tests/seed_demo.py 先铺数据。

   注意：无头 Chrome/Edge 默认没有音频输出设备，play() 可能被挂起。
   用 --autoplay-policy=no-user-gesture-required 并检查 currentTime 是否真的在推进，
   推进了才算"在播"。 */
(async () => {
  const out = {}, wait = (ms) => new Promise(r => setTimeout(r, ms));
  const n = (sel) => document.querySelectorAll(sel).length;
  const txt = (sel) => { const e = document.querySelector(sel); return e ? e.textContent.trim() : null; };
  const el = () => document.getElementById('player');
  const headPct = (id) => {
    const b = document.querySelector(`[data-play="${id}"]`);
    return b && !b.hidden ? parseFloat(b.style.left) || 0 : null;
  };

  try {
    for (let i = 0; i < 60 && !n('#fileList .card'); i++) await wait(250);
    await wait(1600);

    out.files = n('#fileList .card');
    out.hasAudioEl = !!el();
    out.transportBtns = n('[data-play-toggle]');
    out.timeLabels = n('[data-time]');
    out.timeLabelText = txt('[data-time]');

    // 播放前指针应是隐藏的（不能像以前那样写死停在 38%）
    const ids = FILES.map(f => f.id);
    out.headsHiddenAtRest = ids.every(id => headPct(id) === null);

    // 选一个浏览器能播的（wav/flac/mp3）
    const target = FILES.find(f => /wav|flac|mp3/i.test(f.format)) || FILES[0];
    out.target = { id: target.id, name: target.title, format: target.format, dur: target.dur };

    const btn = document.querySelector(`[data-play-toggle="${target.id}"]`);
    out.btnBefore = btn ? btn.getAttribute('aria-label') : null;
    btn.click();

    // 等它真的开始推进。无头 Chrome 没有音频输出设备，音频时钟可能不走 ——
    // 所以「时钟是否推进」只记录，不作为失败条件；跳转与指针是确定性的，那才是断言点。
    let advanced = 0, t0 = el().currentTime;
    for (let i = 0; i < 40; i++) {
      await wait(120);
      if (el().currentTime > t0 + 0.05) { advanced = el().currentTime; break; }
    }
    out.clockAdvances = advanced > 0;      // 无头里通常为 false，属环境限制
    out.currentTime = +el().currentTime.toFixed(2);
    out.src = el().getAttribute('src');
    out.paused = el().paused;
    out.duration = +(el().duration || 0).toFixed(2);
    out.btnAfter = btn.getAttribute('aria-label');
    out.cardIsPlaying = !!document.querySelector(`#fileList .card.is-playing`);
    out.timeLabelAfterPlay = txt(`[data-time="${target.id}"]`);

    /* ---- 点波形跳转（确定性，不受音频时钟影响）---- */
    const wrap = document.querySelector(`[data-wave="${target.id}"]`).closest('.wave__canvasWrap');
    const ratio = 0.75;
    const seekTo = async (ratio) => {
      const r = wrap.getBoundingClientRect();
      wrap.dispatchEvent(new MouseEvent('click', {
        bubbles: true, cancelable: true,
        clientX: r.left + r.width * ratio, clientY: r.top + r.height / 2,
      }));
      await wait(300);
      const dur = el().duration || 1;
      return {
        ratio,
        currentTime: +el().currentTime.toFixed(3),
        expected: +(dur * ratio).toFixed(3),
        actualRatio: +(el().currentTime / dur).toFixed(3),
        headPct: headPct(target.id),
      };
    };

    out.seek75 = await seekTo(0.75);
    out.seek25 = await seekTo(0.25);
    out.seekWorked = Math.abs(out.seek75.actualRatio - 0.75) < 0.06
                  && Math.abs(out.seek25.actualRatio - 0.25) < 0.06;
    // 指针必须等于播放位置，不能是写死的 38%
    out.headMatchesSeek =
      out.seek75.headPct !== null && Math.abs(out.seek75.headPct - 75) < 4;

    /* ---- 暂停 ---- */
    btn.click();
    await wait(300);
    out.afterPause = { paused: el().paused, label: btn.getAttribute('aria-label') };

    /* ---- 切到另一个文件：旧卡片要复位 ---- */
    const other = FILES.find(f => f.id !== target.id);
    if (other) {
      document.querySelector(`[data-play-toggle="${other.id}"]`).click();
      await wait(900);
      out.switched = {
        src: el().getAttribute('src'),
        oldCardStillPlaying: !!document.querySelector(`#fileList .card[data-id="${target.id}"].is-playing`),
        oldHead: headPct(target.id),
        newHead: headPct(other.id),
        playingCards: n('#fileList .card.is-playing'),
      };
      el().pause();
    }

    out.err = null;
  } catch (e) {
    out.err = String((e && e.stack) || e);
  }
  const pre = document.createElement('pre');
  pre.id = 'AE_RESULT';
  pre.textContent = JSON.stringify(out, null, 1);
  document.body.appendChild(pre);
})();
