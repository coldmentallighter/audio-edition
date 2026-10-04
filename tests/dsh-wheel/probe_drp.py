"""Inspect the DRP result on the real file in uploads/.

Read-only: calls `backend.drp` and prints what it found, so the segmentation can be
judged against the music. Run this after changing any tunable in `backend/drp.py`.

⚠ 这个脚本看的是 `uploads/` 里碰到的**第一个**音频，只能用来"肉眼看分段合不合理"。
**要判断算法对不对，用 `drp_truth.py`** —— 它拿老板手标的素材当基准（方案 C 之后，
那才是唯一算数的地方）。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from backend import audio, config, drp                                # noqa: E402


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
    print(f"file: {src.name}   {len(S)} frames @ {hz:g} Hz   {dur:.1f}s")
    print(f"params: window={drp.WINDOW_SEC}s step={drp.STEP_SEC}s "
          f"walk_tol=(DR {drp.WALK_TOL_DR} LU, 形状 {drp.WALK_TOL_SHAPE}) "
          f"max_seg={drp.MAX_SEG_SEC}s")
    print(f"        判据=(电平 {drp.TOL_LEVEL} LU, DR {drp.TOL_DR} LU, "
          f"形状 {drp.TOL_SHAPE})  min_occ={drp.MIN_OCCURRENCES}")
    print()

    pats = drp.patterns(S, t, hz)
    if not pats:
        print("no patterns found")
        return

    print(f"== {len(pats)} patterns ==")
    for p in pats:
        print(f"{p['id']}: level {p['level']:7.2f} LUFS   slope {p['slope']:.3f} LU/s"
              f"   DR {p['dr']:5.2f} LU   {len(p['occurrences'])} occurrences"
              f"   total {p['dur']:.1f}s")
        for o in p["occurrences"]:
            print(f"      {o['start']:>7.1f}s - {o['end']:>7.1f}s"
                  f"   ({o['end'] - o['start']:.1f}s)")

    pmax, pmin = drp.extremes(pats)
    print()
    print("== PMAX / PMIN ==")
    for name, p in (("PMAX", pmax), ("PMIN", pmin)):
        if p:
            o = p["occurrences"][0]
            print(f"  {name}: {p['id']}  DR {p['dr']:.2f} LU  "
                  f"first at {o['start']:.1f}s-{o['end']:.1f}s")

    print()
    occ = [(o["start"], o["end"]) for p in pats for o in p["occurrences"]]
    total = sum(e - s for s, e in occ)
    print("== coverage ==")
    print(f"  occurrence time {total:.1f}s of {dur:.1f}s "
          f"({100 * total / max(1.0, dur):.1f}%)   occurrences: {len(occ)}")
    events = []
    for s, e in occ:
        events += [(s, 1), (e, -1)]
    events.sort()
    depth = 0
    prev = None
    over = 0.0
    for x, dl in events:
        if prev is not None and depth > 1:
            over += x - prev
        depth += dl
        prev = x
    print(f"  multiply-covered: {over:.1f}s ({100 * over / max(1.0, dur):.1f}%)"
          f"   -> must be 0")
    print("  timeline:")
    print("    " + "  ".join(f"{p['id']}@{o['start']:.0f}-{o['end']:.0f}s"
                             for p in pats for o in p["occurrences"]))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
