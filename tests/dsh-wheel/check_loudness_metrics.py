"""End-to-end check of the truePeak unit fix and the new DRA / sample peak.

Cross-checks `backend.audio` against raw ffmpeg stderr on real generated files, where
the expected values are known:

  * a full-scale-ish sine -> integrated / true peak / sample peak must match stderr
  * a two-level file      -> DRA (P95-P10 of short-term loudness) must be positive
    and close to what the reference-style spread implies

Run: python check_loudness_metrics.py
"""
from __future__ import annotations

import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from backend import audio                                            # noqa: E402

# ---------------------------------------------------------------------------
# WORKAROUND for a pre-existing, unrelated bug.
#
# `backend.toolchain._probe_metaflac()` uses
# `tempfile.TemporaryDirectory(..., ignore_cleanup_errors=True)`. On Python 3.14 that
# flag no longer fully suppresses the failure path: cleanup runs `_resetperms`, which
# raises `PermissionError: [WinError 5]` and propagates out of `__exit__`. The result
# is that ANY call into the toolchain raises, so nothing that measures audio can run
# at all in this environment.
#
# The bug is in the probe, not in the measurement, so this harness pre-seeds the
# toolchain cache and skips the probe. Remove this block once toolchain is fixed.
# ---------------------------------------------------------------------------
def _seed_toolchain() -> None:
    import shutil
    from backend import toolchain as _tc
    paths = {n: shutil.which(n) for n in ("ffmpeg", "ffprobe", "flac", "metaflac")}
    _tc.toolchain.tools = {
        n: _tc.Tool(n, p, f"seeded ({n})", bool(p), "" if p else f"not found: {n}")
        for n, p in paths.items()
    }
    _tc.toolchain._probed = True


_seed_toolchain()

TMP = Path(tempfile.gettempdir()) / "loudcheck3"
TMP.mkdir(parents=True, exist_ok=True)

PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


def ff(args, timeout=180):
    return subprocess.run(args, capture_output=True, text=True, cwd=str(TMP),
                          encoding="utf-8", errors="replace", timeout=timeout)


def gen(name: str, dur: float, flt: str, sr: int = 48000) -> Path:
    """Generate a test file. Raises with ffmpeg's stderr on failure, instead of
    letting a missing file surface later as a confusing stat() error."""
    p = TMP / name
    r = ff(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=997:duration={dur}:sample_rate={sr}",
            "-af", flt, "-ac", "2", "-c:a", "pcm_s24le", str(p)])
    if r.returncode != 0 or not p.exists():
        raise RuntimeError(f"failed to generate {name}: rc={r.returncode} "
                           f"{r.stderr.strip()[:300]}")
    return p


def stderr_facts(path: Path) -> dict:
    r = ff(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
            "-af", "ebur128=peak=sample+true", "-f", "null", "-"])
    s = r.stderr
    def one(pat):
        m = re.findall(pat, s)
        return float(m[-1]) if m else None
    return {
        "I": one(r"I:\s*([-\d.]+)\s*LUFS"),
        "LRA": one(r"LRA:\s*([-\d.]+)\s*LU"),
        "sample_peak": one(r"Sample peak:\s*\n\s*Peak:\s*([-\d.]+)"),
        "true_peak": one(r"True peak:\s*\n\s*Peak:\s*([-\d.]+)"),
    }


def main():
    print("== 1. static file: every value must match raw ffmpeg stderr ==")
    quiet = gen("quiet.wav", 6, "volume=-20dB")
    facts = stderr_facts(quiet)
    data = audio.loudness_timeline(quiet, force=True)
    s = data["summary"]
    print(f"     stderr : I={facts['I']} LRA={facts['LRA']} "
          f"SP={facts['sample_peak']} TP={facts['true_peak']}")
    print(f"     ours   : I={s['integrated']} LRA={s['lra']} "
          f"SP={s['samplePeakMax']} TP={s['truePeakMax']} "
          f"PLR={s['plr']} DRA={s['dra']}")
    for key, skey in (("I", "integrated"), ("sample_peak", "samplePeakMax"),
                      ("true_peak", "truePeakMax")):
        want, got = facts[key], s[skey]
        # 0.25 dB tolerance: ebur128's stderr Summary prints ONE decimal, we keep
        # one decimal of the exact 20*log10(value), so a half-LSB disagreement is
        # expected (e.g. stderr -41.1 vs exact -40.915).
        check(f"{skey} matches ffmpeg ({want} vs {got})",
              want is not None and abs(want - got) <= 0.25, f"{want} vs {got}")

    print()
    print("== 2. true peak must be in dB now, not a 0..1 amplitude ==")
    check("truePeakMax is negative dB (a -20dB sine is well below 0)",
          s["truePeakMax"] < 0, s["truePeakMax"])
    tp_series = [v for v in data["truePeak"] if v > audio.SILENCE_LUFS]
    check("truePeak series is in dB (all negative for this file)",
          tp_series and max(tp_series) < 0, max(tp_series) if tp_series else None)
    check("PLR is small for a flat file (|I - TP| < 3)",
          abs(s["plr"]) < 3, s["plr"])

    print()
    print("== 3. the clipping test now can actually fire ==")
    # a near-full-scale file: true peak should be around 0, so "TP > 0" is meaningful
    hot = gen("hot.wav", 6, "volume=0dB")
    hotfacts = stderr_facts(hot)
    hdata = audio.loudness_timeline(hot, force=True)
    hs = hdata["summary"]
    print(f"     stderr true peak = {hotfacts['true_peak']}")
    print(f"     ours             = {hs['truePeakMax']} dBTP, sample "
          f"{hs['samplePeakMax']} dBFS")
    check("truePeakMax is a real dBTP number, not ~0.1",
          hs["truePeakMax"] < -10, hs["truePeakMax"])
    check("a -21 dBFS sine reports true peak near -21, not 0.1",
          abs(hs["truePeakMax"] - (-21.1)) < 1.5, hs["truePeakMax"])

    print()
    print("== 3b. 爆音分段：间隔合并只许吸收一帧，不许糊掉真实分段 ==")
    # 老板 2026-10 报的"地图炮"有两个根因：渲染器读了 `truePeak`（累计最大值），
    # 以及 CLIP_MERGE_GAP 从 0.5 起会把分开的爆音糊成一段。后者在这里钉住。
    #
    # 造三个满刻度爆音：[1,2] 与 [5,6] 相隔 3s，第三个紧跟在 [6.3,6.6]（**留 0.3s 空档**）。
    # 0.1s 合并 ⇒ 3 段（正确）；0.5s 合并 ⇒ 2 段（把后两个糊在一起）。
    # ⚠ `volume` 的表达式是**线性增益**不是 dB：早先写 `if(x, 0, -30)` 结果响/静整个
    # 反过来（实测输出正是反的：0–1s 反而在响）。所以写 20 / 0.03。
    #
    # ⚠⚠ 而且 ffmpeg 的 `sine` 默认只有 **−21 dBFS**（见下面第 3 节那条断言），乘 1
    # 永远到不了满刻度 ⇒ clips 会是空的。乘 20（+26dB）把它硬削到 0.000 dBFS。
    three = gen("clip3.wav", 8,
                "volume='if(between(t,1,2)+between(t,5,6)+between(t,6.3,6.6),20,"
                "0.03)':eval=frame")
    tdata = audio.loudness_timeline(three, force=True)
    ts_ = tdata["summary"]
    print(f"     sample peak = {ts_.get('samplePeakMax')} dBFS")
    clips = [tuple(c) for c in (ts_.get("clips") or [])]
    print(f"     clips = {clips}")
    check("三个分开的爆音被分成 3 段（0.3s 的空档不许被糊掉）",
          ts_.get("clipCount") == 3, clips)
    check("首段从 ~1s 开始、末段到 ~6.6s（时段位置对得上，不是下标换算）",
          clips and abs(clips[0][0] - 1.0) < 0.25 and abs(clips[-1][1] - 6.6) < 0.25,
          clips)
    check("KNOWN: 合并间隔 ≤ 0.1s（0.5s 会把后两段糊成一段 —— 实测见 audio.py）",
          audio.CLIP_MERGE_GAP <= 0.1, audio.CLIP_MERGE_GAP)
    # "这条测试真的能咬人"：同样的数据用旧参数必须给出**不同**答案
    fused = audio._merge_runs(list(tdata["peakT"]), list(tdata["peak"]),
                              lambda v: v >= 0.0, 0.5)
    check("KNOWN: 用旧的 0.5s 合并同一份数据只剩 2 段（所以这条测试有区分力）",
          len(fused) == 2, fused)

    print()
    print("== 4. DRA = P95-P10 of the short-term series ==")
    # two loudness levels 25 dB apart -> the spread must be clearly positive
    # two loudness levels 25 dB apart -> the spread must be clearly positive.
    # Two chained volume stages with `enable` beats a single `if(...)dB` expression:
    # ffmpeg's eval does not accept a `dB` suffix on a conditional result
    # ("Invalid chars 'dB' at the end of expression"), which is a real trap.
    dyn = TMP / "dynfile.wav"
    r = ff(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=60:sample_rate=48000",
            "-af", ("volume=-35dB:enable='lt(t,30)',"
                    "volume=-10dB:enable='gte(t,30)'"),
            "-ac", "2", "-c:a", "pcm_s24le", str(dyn)])
    print(f"     generated two-level file: rc={r.returncode} "
          f"exists={dyn.exists()} {r.stderr.strip()[:200]}")
    ddata = audio.loudness_timeline(dyn, force=True)
    ds = ddata["summary"]
    svals = [x for x in ddata["S"] if x > audio.SILENCE_LUFS]
    p95 = audio._percentile(svals, 95)
    p10 = audio._percentile(svals, 10)
    print(f"     S range {min(svals):.1f}..{max(svals):.1f}   "
          f"P10={p10:.1f}  P95={p95:.1f}  DRA={ds['dra']}  LRA={ds['lra']}")
    check("DRA is positive on a two-level file", ds["dra"] > 3, ds["dra"])
    check("DRA equals P95-P10 recomputed independently",
          abs(ds["dra"] - (p95 - p10)) <= 0.1, (ds["dra"], p95 - p10))
    check("DRA and LRA are different quantities (both present, not equal by luck)",
          "dra" in ds and "lra" in ds, sorted(ds))

    print()
    print("== 5. percentile helper ==")
    check("median of 1..5 is 3", audio._percentile([1, 2, 3, 4, 5], 50) == 3)
    check("P0 is the min", audio._percentile([4, 1, 9], 0) == 1)
    check("P100 is the max", audio._percentile([4, 1, 9], 100) == 9)
    check("single value", audio._percentile([7], 95) == 7)
    check("empty -> 0.0", audio._percentile([], 95) == 0.0)
    check("linear interpolation between order stats",
          abs(audio._percentile([0, 10], 25) - 2.5) < 1e-9,
          audio._percentile([0, 10], 25))

    print()
    print("== 6. _to_db ==")
    check("1.0 -> 0 dB", abs(audio._to_db(1.0)) < 1e-9)
    check("0.5 -> -6.02 dB", abs(audio._to_db(0.5) + 6.0206) < 0.01,
          audio._to_db(0.5))
    check("0 clamps to the silence floor", audio._to_db(0.0) == audio.SILENCE_LUFS)
    check("negative clamps too", audio._to_db(-1.0) == audio.SILENCE_LUFS)
    check("the -20dB sine's linear value converts as expected",
          abs(audio._to_db(0.088) + 21.11) < 0.05, audio._to_db(0.088))

    print()
    print(f"result: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
