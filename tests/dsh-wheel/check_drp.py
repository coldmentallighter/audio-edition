"""Regression test for `backend.drp` -- dynamic-range pattern detection.

Checks the invariants that the algorithm must hold no matter what the tunables are,
then runs it on the real file in `uploads/` if one is present.

The structural invariants matter more than the exact pattern count, because the
tunables in `backend/drp.py` were chosen from a sweep on a single file:
  * occurrences must be MUTUALLY EXCLUSIVE (0 % overlap) -- this is what the
    conflict-resolution pass guarantees, and it is easy to regress
  * every occurrence must be at least the minimum pattern duration
  * a pattern has >= 2 occurrences by definition
  * occurrences are ordered and inside the file
  * `dr` is P95-P10, so it is >= 0 and <= the level span of the pattern

Run: python check_drp.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from backend import audio, config, drp                                # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


def overlap_seconds(occ: list[tuple[float, float]]) -> float:
    ev = []
    for s, e in occ:
        ev += [(s, 1), (e, -1)]
    ev.sort()
    depth = 0
    prev = None
    over = 0.0
    for x, d in ev:
        if prev is not None and depth > 1:
            over += x - prev
        depth += d
        prev = x
    return over


def main():
    # ---------------------------------------------------------------- synthetic
    print("== synthetic signals ==")
    hz = 10.0
    n = 1200                                    # 120 s
    t = [i / hz for i in range(n)]

    # a constant tone: no dynamics, so no patterns
    S_flat = [-20.0] * n
    check("a perfectly flat series yields no patterns",
          drp.patterns(S_flat, t, hz) == [],
          drp.patterns(S_flat, t, hz))

    # two identical loud shapes separated by silence -> should find at least one
    #
    # ⚠ 段落长度必须 ≥ `drp.WINDOW_SEC`（现在是 16 s，方案 C 之后是**段落尺度**）。
    # 原来这里是 10 s 的两段 —— 窗比段还长，测试的尺度和算法的尺度对不上，
    # 结果"找到几个模式"随窗长变而变、断言条数悄悄从 25 掉到 21。
    # 现在用 20 s 的段，并把模式数**钉死**，以后再缩水会直接红。
    S_two = [-40.0] * n
    for k in range(0, n):
        tt = t[k]
        if 10 <= tt < 30 or 70 <= tt < 90:
            S_two[k] = -20.0 + 3.0 * ((k % 10) - 5) / 5.0
    pats = drp.patterns(S_two, t, hz)
    check("two identical shaped passages are detected", len(pats) >= 1,
          f"{len(pats)} patterns")
    check("KNOWN: the two 20s passages yield exactly ONE pattern "
          "(pin the count so a coverage drop shows up as a failure)", len(pats) == 1,
          [p["id"] for p in pats])
    check("KNOWN: that pattern's two occurrences are the two passages (they are "
          "~60s apart), not two slices of the same one",
          len(pats[0]["occurrences"]) == 2
          and abs((pats[0]["occurrences"][1]["start"]
                   - pats[0]["occurrences"][0]["start"]) - 60) <= 4,
          [(o["start"], o["end"]) for o in pats[0]["occurrences"]] if pats else None)
    check("KNOWN: both occurrences are >= the window (a pattern is never "
          "shorter than the matching unit)",
          all(o["end"] - o["start"] >= drp.WINDOW_SEC - 1.01
              for p in pats for o in p["occurrences"]),
          [[(o["start"], o["end"]) for o in p["occurrences"]] for p in pats])

    # ---------------------------------------------------------------- invariants
    print()
    print("== invariants (on whatever patterns we just found) ==")
    for p in pats:
        occ = [(o["start"], o["end"]) for o in p["occurrences"]]
        check(f"{p['id']} has >= 2 occurrences", len(occ) >= 2, len(occ))
        check(f"{p['id']} occurrences are ordered",
              all(a[1] <= b[0] for a, b in zip(occ, occ[1:])), occ)
        check(f"{p['id']} each occurrence >= the min duration",
              all(e - s >= drp.WINDOW_SEC - 1.01 for s, e in occ), occ)
        check(f"{p['id']} dr >= 0", p["dr"] >= 0, p["dr"])

    print()
    print("== the invariant that regressed once: no overlap ==")
    allocc = [(o["start"], o["end"]) for p in pats for o in p["occurrences"]]
    check("occurrences are mutually exclusive (0 s overlap)",
          overlap_seconds(allocc) < 1e-9, overlap_seconds(allocc))

    # ---------------------------------------------------------------- helpers
    print()
    print("== helper units ==")
    check("_percentile P0 = min", drp._percentile([3, 1, 2], 0) == 1)
    check("_percentile P100 = max", drp._percentile([3, 1, 2], 100) == 3)
    check("_percentile interpolates", abs(drp._percentile([0, 10], 25) - 2.5) < 1e-9)
    check("_percentile of empty is 0", drp._percentile([], 50) == 0.0)
    # _merge_runs needs the sliding STEP, not 1 -- the bug that broke merging
    check("_merge_runs merges windows `step` apart",
          drp._merge_runs([0, 10, 20, 30], 10) == [(0, 30)],
          drp._merge_runs([0, 10, 20, 30], 10))
    check("_merge_runs splits on a gap > step",
          drp._merge_runs([0, 10, 50], 10) == [(0, 10), (50, 50)],
          drp._merge_runs([0, 10, 50], 10))
    check("slope_series is |dS/dt| in LU/s",
          abs(drp.slope_series([0.0, 2.0, 2.0], 10.0)[0] - 10.0) < 1e-9,
          drp.slope_series([0.0, 2.0, 2.0], 10.0))
    check("slope_series handles a 1-frame input",
          drp.slope_series([5.0], 10.0) == [0.0])

    # ---------------------------------------------------------------- real file
    print()
    print("== real file (uploads/) ==")
    # ⚠ `uploads/` 里是什么素材**不由这个测试决定** —— 用户随时会换文件。
    # 所以这里只钉**结构不变量**，不假定"一定有模式"：方案 C（老板 2026-10 定的
    # "宁可漏不可错"）下，一个没有清晰重复的文件**返回空列表是正确结果**。
    # 实测就撞上了：工作区换成 `INFinite - Stellar.flac` 之后这里是 0 个模式，
    # 而旧断言写着 `len(rp) >= 1` —— 于是测试红了，代码却是对的。
    # "真有重复时必须找得到"由合成信号那一段 + `drp_truth.py`（老板手标素材）负责。
    src = next((p for p in config.UPLOADS.rglob("*")
                if p.suffix.lower() in (".flac", ".wav", ".mp3")), None)
    if not src:
        print("        (no audio in uploads/, skipped)")
    else:
        d = audio.loudness_timeline(src)
        rp = drp.patterns(d["S"], d["t"], float(d.get("hz") or 10.0))
        dur = float(d.get("duration") or 0)
        occ = [(o["start"], o["end"]) for p in rp for o in p["occurrences"]]
        print(f"        {src.name}: {len(rp)} patterns, "
              f"{len(occ)} occurrences, "
              f"{sum(e - s for s, e in occ):.1f}s of {dur:.1f}s")
        check("real file: no overlap", overlap_seconds(occ) < 1e-9,
              overlap_seconds(occ))
        check("real file: every occurrence within the file",
              all(0 <= s < e <= dur + 0.5 for s, e in occ), occ[:3])
        check("real file: every occurrence >= the min duration",
              all(e - s >= drp.WINDOW_SEC - 1.01 for s, e in occ), occ[:3])
        check("real file: 没有只出现一次的模式（老板定的硬规则）",
              all(len(p["occurrences"]) >= 2 for p in rp),
              [len(p["occurrences"]) for p in rp])
        pmax, pmin = drp.extremes(rp)
        check("PMAX/PMIN 要么都是 None（没模式），要么 PMAX.dr >= PMIN.dr",
              (pmax is None and pmin is None)
              or (pmax is not None and pmin is not None
                  and pmax["dr"] >= pmin["dr"]),
              (pmax and pmax["dr"], pmin and pmin["dr"]))
        check("patterns are numbered in first-occurrence order",
              [p["id"] for p in rp] == [f"PT_{i}" for i in range(1, len(rp) + 1)],
              [p["id"] for p in rp])
        if not rp:
            print("        KNOWN：方案 C 下这个素材确实可能一个模式都没有"
                  "（没有清晰重复 = 正确返回空）")

    print()
    print(f"result: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
