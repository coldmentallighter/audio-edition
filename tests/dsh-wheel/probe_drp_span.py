"""DRP probe, round 2 -- the derivative SPAN, and what the rounding costs us.

Round 1 found that `|dS/dt|` comes out quantised to multiples of 0.5 LU/s:

    p50=0.500  p75=0.500  p90=1.000  max=9.500

That is not music, it is `round(x, 1)` on the S series. At 10 Hz a centred
difference divides by 0.2 s, so a 0.1 LU rounding step becomes exactly 0.5 LU/s.
Constant-duration derivatives are therefore useless for this feature.

This script compares derivative spans computed from the SAME stored series, so the
only variable is the span:

    central  (t-0.1 .. t+0.1)   -> 0.2 s
    window w (t       .. t+w)   -> w seconds

and also reports how many distinct values each produces. A feature that can only
take a handful of values cannot support "similar behaviour" matching.

READ-ONLY.
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from backend import audio, config                                     # noqa: E402


def central(vs: list[float], dt: float) -> list[float]:
    n = len(vs)
    out = [0.0] * n
    for i in range(n):
        lo, hi = max(0, i - 1), min(n - 1, i + 1)
        out[i] = (vs[hi] - vs[lo]) / ((hi - lo) * dt) if hi > lo else 0.0
    return out


def span(vs: list[float], k: int, dt: float) -> list[float]:
    """(v[i+k] - v[i]) / (k*dt) -- a k-frame span, zero where out of range."""
    n = len(vs)
    out = [0.0] * n
    for i in range(n):
        j = i + k
        out[i] = (vs[j] - vs[i]) / (k * dt) if j < n else 0.0
    return out


def report(name: str, vs: list[float], idx: list[int]) -> dict:
    x = [abs(vs[i]) for i in idx]
    s = sorted(x)
    q = lambda p: s[min(len(s) - 1, int(p * len(s)))]
    distinct = len({round(v, 4) for v in x})
    top = sorted({round(v, 4) for v in x}, reverse=True)[:6]
    print(f"  {name:22s} distinct={distinct:>5}  p50={q(.5):6.3f} p90={q(.9):6.3f}"
          f" p99={q(.99):6.3f} max={s[-1]:6.3f}   top values {top}")
    return {"distinct": distinct, "p90": q(.9), "max": s[-1]}


def main():
    src = next((p for p in config.UPLOADS.rglob("*")
                if p.suffix.lower() in (".flac", ".wav", ".mp3")), None)
    if not src:
        print("no audio in uploads/")
        return
    d = audio.loudness_timeline(src)
    ts, S = d["t"], d["S"]
    hz = float(d.get("hz") or 10.0)
    dt = 1.0 / hz
    idx = [i for i in range(len(S)) if i >= int(3.0 * hz)]
    print(f"file: {src.name}   {len(S)} frames @ {hz:g} Hz")
    print(f"evaluating on frames from {ts[idx[0]]:.1f}s onward ({len(idx)} frames)")
    print()

    print("== how many DISTINCT values does each derivative span produce? ==")
    print("   (a feature needs many, or 'similar' becomes meaningless)")
    rows = {}
    rows["central (0.2s)"] = report("central 0.2s", central(S, dt), idx)
    for w in (0.5, 1.0, 2.0, 3.0, 5.0):
        k = max(1, int(round(w * hz)))
        rows[f"span {w}s"] = report(f"span {w:g}s ({k}f)", span(S, k, dt), idx)

    print()
    print("== the same, computed WITHOUT the 0.1 LU rounding ==")
    print("   (re-derives S from ebur128's stderr Summary is not enough; this")
    print("    re-parses the raw metadata with full precision)")
    raw = _raw_S(src)
    if raw is None:
        print("   could not re-derive; skipping")
    else:
        Sr, tsr = raw
        idxr = [i for i in range(len(Sr)) if i >= int(3.0 * hz)]
        print(f"   raw S: {len(Sr)} frames, distinct values={len(set(Sr))}")
        report("raw central 0.2s", central(Sr, dt), idxr)
        for w in (1.0, 2.0, 3.0):
            k = max(1, int(round(w * hz)))
            report(f"raw span {w:g}s", span(Sr, k, dt), idxr)

    print()
    print("== verdict input: activity thresholds that leave usable segments ==")
    for name in ("central (0.2s)", "span 1.0s", "span 2.0s", "span 3.0s"):
        if name not in rows:
            continue
        p90 = rows[name]["p90"]
        print(f"  {name:22s} p90={p90:6.3f} LU/s"
              f"  -> a '3s active' segment needs |d| >= {p90:.2f} sustained")


def _raw_S(src: Path) -> tuple[list[float], list[float]] | None:
    """Re-parse the stored cache but keep full float precision on S.

    The cache already has S rounded to 0.1, so instead we re-run ebur128 once. This
    is deliberately in the probe, not in the product code.
    """
    import re
    import subprocess
    import tempfile
    from backend import runner
    from backend.toolchain import toolchain
    ff = toolchain.path_of("ffmpeg")
    if not ff:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="drp-raw-"))
    name = "s.txt"
    try:
        r = runner.run([ff, "-hide_banner", "-loglevel", "error", "-nostats",
                        "-i", str(src), "-map", "0:a:0",
                        "-af", ("ebur128=peak=none:framelog=verbose:metadata=true,"
                                f"ametadata=mode=print:file={name}"),
                        "-f", "null", "-"], timeout=300, cwd=tmp)
        if not r.ok:
            return None
        text = (tmp / name).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    ts: list[float] = []
    sv: list[float] = []
    cur_t = None
    cur_s = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("frame:"):
            if cur_t is not None and cur_s is not None:
                ts.append(cur_t)
                sv.append(cur_s)
            cur_t = cur_s = None
            for tok in line.split():
                if tok.startswith("pts_time:"):
                    cur_t = float(tok.split(":", 1)[1])
            continue
        if line.startswith("lavfi.r128.S="):
            cur_s = float(line.split("=", 1)[1])
    if cur_t is not None and cur_s is not None:
        ts.append(cur_t)
        sv.append(cur_s)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return (sv, ts) if sv else None


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
