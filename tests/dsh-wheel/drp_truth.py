"""Ground truth for the dynamic-pattern (DRP) redesign -- the owner's manual tags.

    python tests/dsh-wheel/drp_truth.py

两个素材（老板手标，放在 `target/`，**不进仓库**）：

| 文件 | 角色 | 内容 |
|---|---|---|
| `target/CQ.wav` | **目标**（位置基本准确） | 10 段：`PT_1 ×2, PT_2 ×1, PT_3 ×2, PT_4 ×2, PT_6 ×1, E ×2` |
| `target/wooden - tau.wav` | 参考（标得宽容） | 10 段，全 `PT_*`：`PT_1 ×3, PT_2 ×2, PT_3…PT_7 各 ×1` |

老板定的口径（2026-10）：

* **一个模式必须出现 ≥2 次** —— 只出现一次的编号不算模式（"那就不允许只出现一次"）；
* `E` = 无法分析的部分，**仅供参考**；E 里可能有没标上的模式区域，所以它**不是**负样本；
* CQ 里**没有 PT_5** 不是漏标 —— "就只是不存在"。

这个脚本做两件事：

1. **把标记本身钉住**（段数、编号、时长、重复关系）—— 算法换了，基准不能变；
2. **把"为什么不能照旧做"的实测证据钉住** —— 三处分性实验的结论
   （响度特征分不开 / 曲线形状分不开 / 频谱质心分不开），以及
   **一个必须避开的假阳性陷阱**。C 方案（只报高置信重复）的阈值就是照着这里定的。

⚠ `target/` 是 gitignored 的，文件不在就 SKIP（不是失败）。
"""
from __future__ import annotations

import json
import math
import re
import statistics as st
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent          # tests/dsh-wheel → 仓库根
sys.path.insert(0, str(ROOT))

from backend import config                                         # noqa: E402
from backend.toolchain import toolchain                            # noqa: E402

TARGET = ROOT / "target"
CQ = TARGET / "CQ.wav"
TAU = TARGET / "wooden - tau.wav"

#: 曲线形状比较时重采样到多少点
SHAPE_N = 100

#: C 方案（只报高置信重复）的阈值。**不是拍脑袋** —— 见 `main()` 里的标定表：
#: 真阳性 PT_4 那一对是 `形状 0.24 / Δ电平 0.47 / Δ质心 246`，
#: 而假阳性（PT_4@227 对 PT_1@259）**形状只有 0.26**、Δ电平却是 **17.8 LU** ——
#: 所以"只比形状"必然误报，必须同时卡绝对电平。
TOL_SHAPE = 0.60        # z 归一化曲线的距离（0 = 完全同形）
TOL_LEVEL = 1.0         # LU：平均短时响度之差
TOL_DR = 2.0            # LU：P95−P10 之差
TOL_CENTROID = 400.0    # Hz：频谱质心均值之差

#: 段落尺度的下界（老板标的段没有一个短于 12s）
MIN_SECTION_SEC = 12.0

PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


# --------------------------------------------------------------------- 读标记
def chapters(path: Path) -> list[tuple[str, float, float]]:
    ffprobe = toolchain.require("ffprobe")
    r = subprocess.run([ffprobe, "-v", "error", "-show_chapters", "-of", "json",
                        str(path.resolve())], capture_output=True, text=True)
    out = []
    for c in json.loads(r.stdout or "{}").get("chapters") or []:
        out.append((str((c.get("tags") or {}).get("title") or "").strip(),
                    float(c["start_time"]), float(c["end_time"])))
    return out


def counts(rows) -> dict[str, int]:
    d: dict[str, int] = {}
    for tag, _, _ in rows:
        d[tag] = d.get(tag, 0) + 1
    return d


# --------------------------------------------------------------------- 特征
def curve(S: list[float], t: list[float], a: float, b: float,
          n: int = SHAPE_N) -> list[float] | None:
    """一段 S 曲线 → 重采样到 `n` 点 + **z 归一化**（只比形状，不比绝对电平）。"""
    seg = [(x, v) for x, v in zip(t, S) if a <= x < b]
    if len(seg) < 8:
        return None
    xs = [p[0] for p in seg]
    vs = [p[1] for p in seg]
    out = []
    for k in range(n):
        u = a + (b - a) * k / (n - 1)
        j = 0
        while j < len(xs) - 2 and xs[j + 1] < u:
            j += 1
        x0, x1, v0, v1 = xs[j], xs[j + 1], vs[j], vs[j + 1]
        out.append(v0 + (v1 - v0) * ((u - x0) / (x1 - x0) if x1 > x0 else 0.0))
    m = sum(out) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in out) / n) or 1.0
    return [(v - m) / sd for v in out]


def shape_dist(c1: list[float], c2: list[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(c1, c2)) / len(c1))


def spectral(path: Path) -> tuple[list[float], list[float]]:
    """逐帧频谱质心（Hz）。**结果按文件缓存** —— `aspectralstats` 不解就白跑几十秒。"""
    import backend.audio as A                                       # noqa: PLC0415
    key = A.file_key(path)
    cache = config.CACHE / f"centroid-{key}.json"
    if cache.exists():
        try:
            d = json.loads(cache.read_text(encoding="utf-8"))
            return d["t"], d["c"]
        except Exception:                                          # noqa: BLE001
            pass
    ff = toolchain.require("ffmpeg")
    # 临时名按 pid+key 唯一（`audio.py` 那条"队列 2 并发抢同一个文件"的教训）
    import os                                                       # noqa: PLC0415
    tmp = config.CACHE / f"spec-{key}-{os.getpid()}.txt"
    tmp.unlink(missing_ok=True)
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-nostats",
                    "-i", str(path.resolve()), "-map", "0:a:0",
                    "-af", "aspectralstats,ametadata=mode=print:file=" + tmp.name,
                    "-f", "null", "-"], cwd=str(config.CACHE),
                   capture_output=True)
    ts: list[float] = []
    cs: list[float] = []
    cur: float | None = None
    acc: dict[str, list[float]] = {}
    txt = tmp.read_text(encoding="utf-8", errors="replace") if tmp.exists() else ""
    tmp.unlink(missing_ok=True)

    def flush() -> None:
        if cur is None or not acc.get("centroid"):
            return
        ts.append(cur)
        cs.append(sum(acc["centroid"]) / len(acc["centroid"]))

    for line in txt.splitlines():
        line = line.strip()
        if "pts_time:" in line:
            flush()
            acc = {}
            try:
                cur = float(line.split("pts_time:", 1)[1])
            except ValueError:
                cur = None
        else:
            m = re.match(r"lavfi\.aspectralstats\.\d+\.(\w+)=(.*)", line)
            if m and cur is not None:
                try:
                    acc.setdefault(m.group(1), []).append(float(m.group(2)))
                except ValueError:
                    pass
    flush()
    try:
        cache.write_text(json.dumps({"t": ts, "c": cs}), encoding="utf-8")
    except OSError:
        pass
    return ts, cs


def section_features(path: Path) -> list[dict]:
    """每一段的实测特征。"""
    import backend.audio as A                                       # noqa: PLC0415
    import backend.drp as D                                         # noqa: PLC0415
    d = A.loudness_timeline(path)
    t, S = d["t"], d["S"]
    slope = D.slope_series(S, d["hz"])
    sts, cen = spectral(path)
    out = []
    for tag, a, b in chapters(path):
        idx = [i for i, x in enumerate(t) if a <= x < b]
        if len(idx) < 5:
            continue
        vals = [S[i] for i in idx]
        sl = [slope[i] for i in idx if slope[i] is not None]
        ci = [c for x, c in zip(sts, cen) if a <= x < b]
        out.append({
            "tag": tag, "start": round(a, 2), "end": round(b, 2),
            "dur": round(b - a, 2),
            "mean": round(sum(vals) / len(vals), 2),
            "sd": round(st.pstdev(vals), 2),
            "dr": round(D._percentile(vals, 95) - D._percentile(vals, 10), 2),
            "slope": round(sum(sl) / len(sl), 3) if sl else 0.0,
            "centroid": round(sum(ci) / len(ci)) if ci else 0,
            "curve": curve(S, t, a, b),
        })
    return out


def pair(feats: list[dict], tag_a: str, start_a: float,
         tag_b: str, start_b: float) -> dict | None:
    a = next((f for f in feats
              if f["tag"] == tag_a and abs(f["start"] - start_a) < 0.5), None)
    b = next((f for f in feats
              if f["tag"] == tag_b and abs(f["start"] - start_b) < 0.5), None)
    if not a or not b or not a["curve"] or not b["curve"]:
        return None
    return {"shape": round(shape_dist(a["curve"], b["curve"]), 3),
            "d_level": round(abs(a["mean"] - b["mean"]), 2),
            "d_dr": round(abs(a["dr"] - b["dr"]), 2),
            "d_centroid": abs(a["centroid"] - b["centroid"])}


def main() -> int:
    if not (CQ.exists() and TAU.exists()):
        print(f"  SKIP  {TARGET} 下没有 CQ.wav / wooden - tau.wav（gitignored，不进仓库）")
        return 0

    print("== 标记本身（换了算法也不能变）==")
    cq_rows, tau_rows = chapters(CQ), chapters(TAU)
    cq, tau = counts(cq_rows), counts(tau_rows)
    check("CQ 有 10 段", len(cq_rows) == 10, len(cq_rows))
    check("tau 有 10 段", len(tau_rows) == 10, len(tau_rows))
    check("CQ 的编号是 PT_1×2 / PT_2×1 / PT_3×2 / PT_4×2 / PT_6×1 + E×2",
          cq == {"PT_1": 2, "PT_2": 1, "PT_3": 2, "PT_4": 2, "PT_6": 1, "E": 2}, cq)
    check("CQ **没有 PT_5**（老板：就只是不存在）", "PT_5" not in cq)
    check("tau 的编号是 PT_1×3 / PT_2×2 / PT_3…PT_7 各 ×1",
          tau == {"PT_1": 3, "PT_2": 2, "PT_3": 1, "PT_4": 1, "PT_5": 1,
                  "PT_6": 1, "PT_7": 1}, tau)
    check("tau 没有 E（全部可分析）", "E" not in tau)
    for name, rows in (("CQ", cq_rows), ("tau", tau_rows)):
        durs = [b - a for _, a, b in rows]
        check(f"{name}：每段都 ≥ {MIN_SECTION_SEC:g}s（段落尺度，不是 3–5s）",
              min(durs) >= MIN_SECTION_SEC, round(min(durs), 2))
        check(f"{name}：段首尾相接、无缝隙",
              all(abs(rows[i + 1][1] - rows[i][2]) < 0.01 for i in range(len(rows) - 1)))
    check("至少一半编号只出现一次 —— 所以『模式』不能定义成『≥2 次』"
          "（老板 2026-10 定：那就**不算模式**）",
          sum(1 for v in cq.values() if v == 1) + sum(1 for v in tau.values() if v == 1)
          >= 7, (cq, tau))

    print()
    print("== 每段的实测特征 ==")
    feats = {p.name: section_features(p) for p in (CQ, TAU)}
    for name, fs in feats.items():
        print(f"-- {name}")
        print("   %-5s %7s %7s %6s %6s %6s %9s" % ("tag", "start", "dur", "mean",
                                                   "DR", "slope", "centroid"))
        for f in fs:
            print("   %-5s %7.1f %7.1f %6.2f %6.2f %6.3f %9d" % (
                f["tag"], f["start"], f["dur"], f["mean"], f["dr"], f["slope"],
                f["centroid"]))

    print()
    print("== 可分性实验（为什么不能照旧做）==")
    for name, fs in feats.items():
        same, diff = [], []
        for i in range(len(fs)):
            for j in range(i + 1, len(fs)):
                if fs[i]["tag"] == "E" or fs[j]["tag"] == "E":
                    continue
                dd = shape_dist(fs[i]["curve"], fs[j]["curve"])
                (same if fs[i]["tag"] == fs[j]["tag"] else diff).append(dd)
        print(f"-- {name}")
        if same and diff:
            print(f"   同类形状距离 {[round(x, 2) for x in sorted(same)]}"
                  f" | 异类最小 {min(diff):.2f}（同类最大 {max(same):.2f}）")
            check(f"{name}：形状**分不开**（异类最小 ≤ 同类最大）",
                  min(diff) <= max(same), (round(min(diff), 2), round(max(same), 2)))
        else:
            print("   n/a")

    print()
    print("== C 方案的标定：真阳性 vs 必须避开的假阳性 ==")
    cqf = feats[CQ.name]
    pos = pair(cqf, "PT_4", 103.1, "PT_4", 227.6)
    neg = pair(cqf, "PT_4", 227.6, "PT_1", 259.6)
    print("   真阳性 PT_4@103 vs PT_4@228 :", pos)
    print("   假阳性 PT_4@228 vs PT_1@260 :", neg)
    if pos and neg:
        check("真阳性：形状近 + 电平近 + 质心近（三项全过）",
              pos["shape"] <= TOL_SHAPE and pos["d_level"] <= TOL_LEVEL
              and pos["d_centroid"] <= TOL_CENTROID, pos)
        check("KNOWN: 假阳性的**形状比真阳性还近** —— 只比形状必然误报",
              neg["shape"] <= pos["shape"] + 0.1,
              (neg["shape"], pos["shape"]))
        check("但它的 Δ电平 远超容差，所以『形状 + 绝对电平』能把它挡掉",
              neg["d_level"] > TOL_LEVEL, neg["d_level"])
        check("真阳性落在 C 的阈值内、假阳性落在阈值外",
              all(pos[k] <= t for k, t in (("shape", TOL_SHAPE), ("d_level", TOL_LEVEL),
                                           ("d_centroid", TOL_CENTROID)))
              and neg["d_level"] > TOL_LEVEL)

    print()
    print("== 算法实际输出 vs 基准（方案 C：只报高置信重复）==")
    import backend.drp as D                                         # noqa: PLC0415
    import backend.audio as A                                       # noqa: PLC0415

    def tag_at(path, x):
        for tg, a, b in chapters(path):
            if a <= x < b:
                return tg
        return "?"

    got: dict[str, list[list[str]]] = {}
    for name, path in (("CQ.wav", CQ), ("wooden - tau.wav", TAU)):
        d = A.loudness_timeline(path)
        pats = D.patterns(d["S"], d["t"], float(d.get("hz") or 10.0))
        print(f"-- {name}: window={D.WINDOW_SEC:g}s 步长={D.STEP_SEC:g}s "
              f"判据=(电平 {D.TOL_LEVEL}LU / DR {D.TOL_DR}LU / 形状 {D.TOL_SHAPE})"
              f" → 报出 {len(pats)} 个模式")
        got[name] = []
        for p in pats:
            occ = "  ".join(f"{o['start']:.1f}–{o['end']:.1f}" for o in p["occurrences"])
            # 每个出现落在哪个手标段里（用来一眼看出"命中了哪些真重复"）
            tags = [tag_at(path, (o["start"] + o["end"]) / 2) for o in p["occurrences"]]
            got[name].append(tags)
            print(f"   {p['id']}  DR {p['dr']:5.2f}  电平 {p['level']:7.2f}  "
                  f"spread {p['spread']:.2f}  {occ}   落在 {tags}")
        check(f"{name}：没有任何模式只出现一次（老板定的硬规则）",
              all(len(p["occurrences"]) >= 2 for p in pats),
              [len(p["occurrences"]) for p in pats])
        check(f"{name}：出现之间互不重叠",
              all(not (p["occurrences"][i]["end"] > p["occurrences"][i + 1]["start"])
                  for p in pats for i in range(len(p["occurrences"]) - 1)))

    # 身份正确性：报出的每个模式，它的**每一次出现都落在同一个编号**的段里。
    # 这是"算法对不对"的唯一判据 —— 位置差个一两秒无所谓，认错结构就是错。
    for name in got:
        check(f"{name}：每个模式的所有出现都落在**同一个手标编号**里",
              all(len(set(tags)) == 1 and tags[0] != "?" for tags in got[name]),
              got[name])
    # 方案 C 下 CQ 的正确答案是**两个**：`PT_1` 那对和 `PT_4` 那对
    # （`PT_3` 那对会被 DR 判据挡掉，`PT_2`/`PT_6` 只出现一次、本来就不算模式）
    check("KNOWN: CQ 报出 2 个模式 —— PT_1 对 + PT_4 对（方案 C 的预期答案）",
          sorted(t[0] for t in got["CQ.wav"]) == ["PT_1", "PT_4"], got["CQ.wav"])
    check("KNOWN: tau 报出 1 个模式 —— PT_2 对", 
          [t[0] for t in got["wooden - tau.wav"]] == ["PT_2"], got["wooden - tau.wav"])
    print()
    print(f"结果：{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
