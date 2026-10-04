"""DRP: how do the sliding WINDOW and STEP affect overlap and fragmentation?

Two things this is for:

* **overlap / coverage.** Neighbouring windows differ slightly in character and can
  land in different groups. Symptoms seen at the 1 s step: total pattern time exceeded
  the file length (145 %), and several `PT_*` interleaved over the same seconds.
* **granularity (2026-10, 方案 C).** At the section scale a 32 s passage is reported as
  TWO adjacent patterns because the window (16 s) is half its length. This table is how
  we judge whether raising the window merges them back, and what that costs in missed
  short sections (the owner's shortest tag is 12.4 s).

This sweeps both and reports:
  * coverage  = sum of occurrence durations / file duration  (want <= ~100 %)
  * overlap   = seconds claimed by more than one pattern      (want ~0)
  * how many patterns survive

READ-ONLY: drives `backend.drp` with different parameters, changes nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from backend import audio, config, drp                                # noqa: E402


def overlap_seconds(occurrences: list[tuple[float, float]]) -> float:
    """Total length covered by more than one interval."""
    if len(occurrences) < 2:
        return 0.0
    events = []
    for s, e in occurrences:
        events.append((s, 1))
        events.append((e, -1))
    events.sort()
    depth = 0
    prev = None
    over = 0.0
    for x, d in events:
        if prev is not None and depth > 1:
            over += x - prev
        depth += d
        prev = x
    return over


def main():
    src = next((p for p in config.UPLOADS.rglob("*")
                if p.suffix.lower() in (".flac", ".wav", ".mp3")), None)
    if not src:
        print("no audio in uploads/")
        return
    d = audio.loudness_timeline(src)
    t, S = d["t"], d["S"]
    hz = float(d.get("hz") or 10.0)
    dur = float(d.get("duration") or 0)
    print(f"file: {src.name}   {dur:.1f}s")
    print()
    print(f"{'step':>5} {'win':>5} {'#pat':>5} {'occ':>5} {'total':>8} {'cover%':>8}"
          f" {'overlap s':>10} {'overlap%':>9} {'max occ':>8}")
    # 2026-10 方案 C 之后扫的是**段落尺度**：老板标的段 12.4–35.6 s。
    # 窗 16 s 会把一段 32 s 的通段切成两个模式（见交接文档 §2.6 待办 9），
    # 这个表就是用来判断"抬窗能不能把它并回来、代价是漏掉多少短段"的。
    for step in (1.0, 2.0, 4.0):
        for win in (12.0, 16.0, 20.0, 24.0, 32.0):
            pats = drp.patterns(S, t, hz, window_sec=win, step_sec=step)
            occ = [(o["start"], o["end"]) for p in pats for o in p["occurrences"]]
            total = sum(e - s for s, e in occ)
            over = overlap_seconds(occ)
            mx = max((len(p["occurrences"]) for p in pats), default=0)
            print(f"{step:>5.1f} {win:>5.1f} {len(pats):>5} {len(occ):>5}"
                  f" {total:>7.1f}s {100 * total / dur:>7.1f}% {over:>9.1f}s"
                  f" {100 * over / dur:>8.1f}% {mx:>8}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
