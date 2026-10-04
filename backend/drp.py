"""Dynamic-range pattern (DRP) detection -- **section scale, repeats only (方案 C)**.

老板 2026-10 定的口径（见 `tests/dsh-wheel/drp_truth.py`，那是唯一基准）
--------------------------------------------------------------------
1. **一个模式必须出现 ≥ 2 次**。只出现一次的编号**不算模式** —— "那就不允许只出现
   一次，就只是不存在"。所以本模块永远不输出"只出现一次"的东西（`MIN_OCCURRENCES`）。
2. **宁可漏，不可错**（方案 C）。老板在两个素材上手标了 20 段，实测结论是：光靠响度
   统计量**分不开**音乐结构 ——

   * tau 里 `PT_1/PT_3/PT_4/PT_6/PT_7` 的平均响度全挤在 −26.3…−27.6 LUFS；
   * **曲线形状也分不开**：tau 异类之间的最小形状距离 `0.27`，比每一对同类
     （`0.92…1.58`）都小；CQ 同样是异类最小 `0.26`。
   * 频谱质心同样分不开（tau 异类差 5 Hz，同类 `PT_2` 内部却差 987 Hz）。

   所以本模块**不追求把 20 段都还原出来**，只报"高置信重复"：三个判据必须**同时**
   满足。这不是妥协后的近似，而是唯一能保证不误报的判据组合 —— 见下面那条假阳性。

   被判据挡掉的真重复（比如 CQ 的 `PT_1` 与 `PT_3` 两对）就当**没有模式**：
   这是老板明确选的取舍。

3. **段落尺度**，不是 3–5 s。老板标的 20 段无一短于 12.4 s，最长的 35.6 s。
   所以窗口抬到 16 s（`WINDOW_SEC`），段长上界靠 `_walk` 的自然延伸。

三个判据为什么必须同时用 —— 一个实测的假阳性
---------------------------------------------
CQ 里这两对，**形状距离几乎一样**：

| 对 | 形状 | Δ电平 | ΔDR | Δ质心 |
|---|---|---|---|---|
| 真：`PT_4@103` ↔ `PT_4@228` | 0.237 | **0.47** | 1.22 | 246 |
| 假：`PT_4@228` ↔ `PT_1@260` | 0.260 | **17.77** | 10.40 | 2829 |

假的那对**形状比真的还近**（0.260 < 0.237 的邻居）。所以"只比形状"必然误报；
"形状 + **绝对电平**"才能把它挡掉 —— 这也解释了为什么形状要先 **z 归一化**
（只比形状不比电平），再单独卡电平，而不是直接比原始曲线的欧氏距离。

算法（仍然是两段式，但判据换了）
--------------------------------
1. **分段** `_segments_from_windows`：16 s 窗 / 2 s 步滑动，下一窗与**当前**窗足够
   接近就继续延伸。局部连续性用 `WALK_*`（比判据紧），因为它回答的是"这是不是同一段
   连续通段"，而不是"这算不算一个模式"。单趟左→右构造 ⇒ 段与段**天然互不重叠**。
   局部平坦（斜率低于中位数）的窗**自成一段并被排除在匹配之外** —— 平台段不含动态信息。
2. **匹配** `_complete_linkage`：完全链接聚类，距离取三项归一化超限的**最大值**

       d(A,B) = max( |Δ电平| / TOL_LEVEL,
                     |ΔDR|  / TOL_DR,
                     shape(A,B) / TOL_SHAPE )

   三项都在容差内才合并（`d <= 1.0`）。完全链接是必须的：实测单链接会把 16 个窗
   链成一个横跨 6 LU 的"模式"。

为什么用 z 归一化重采样曲线而不是 DTW
-------------------------------------
段长不等（12–40 s），所以形状要先重采样到固定点数。老板标的同类段长度比最高约
2.9×（12.4 s vs 35.6 s），这个量级下线性重采样和 DTW 的结论一致，而 DTW 是
`O(n²)` 且需要额外依赖 —— 用不上。（这是实测定论，不是省事：见 `drp_truth.py`
里 `curve()` 用的就是同一套重采样，真/假阳性的分离度已经足够。）
"""
from __future__ import annotations

import statistics

# ---------------------------------------------------------------- tunables

#: 滑动窗口长度，秒。**段落尺度**：老板标的段最短 12.4 s，最长 35.6 s。
#: 取 16 s 让窗能从段的任意起点落进去；更短的段（< 16 s）本来也不该单独成模式。
WINDOW_SEC = 16.0
#: 滑动步长，秒。2 s 让段的端点准确到 2 s 以内，同时把候选数压到几十个。
STEP_SEC = 2.0
#: ---- 留档：段延伸（segment extension）为什么被取消 ----
#:
#: ⚠ 这一段留档：**延伸试过三种判据，实测全部有害，最后取消了**。三次都记在这里，
#: 免得下次有人再试一遍：
#:
#: 1. **`|Δ窗均值| ≤ 0.75 LU`**（原版）—— `PT_4` 斜率 0.8 LU/s，16 s 窗每走 2 s
#:    均值就漂 ~1.6 LU，判据永不成立，`_walk` 一次都不延伸。
#: 2. **`ΔDR + 形状`** —— 形状在**相邻**窗之间完全不可用：只差 2 s 的两窗形状距离是
#:    **CQ 1.09–1.16 / tau 0.48–1.02**，而真正重复的两段之间只有 **0.24**
#:    （z 归一化形状对相位极敏感，只在"起点对齐"时才有意义）。实测 CQ 111 段
#:    **0 段延伸**。
#: 3. **只用 `ΔDR`** —— 这个**会**延伸（CQ 17/40 段延伸、最长 36 s；tau 10 段里 6 段
#:    延伸、顶到 48 s 上限），但**把身份弄坏了**：tau 原本正确的
#:    `PT_2@14 ↔ PT_2@88` 变成了错的 `PT_4@63 ↔ PT_1@152`，CQ 还把正确的
#:    `PT_1@4 ↔ PT_1@260`（spread 0.28，最紧的一对）弄丢了。
#:
#: 结论：**单位就是窗本身**，不延伸。粒度问题改用 `_merge_compound`（确定性合并，
#: 见那里的实测）解决。
#: 延伸段长的上界，秒。**现在是给 `_merge_compound` 当合并后的段长上限用的**
#: （延伸已经取消，见下面那段留档）。老板标的 20 段最长 35.6 s。
MAX_SEG_SEC = 48.0

#: 合并"紧挨着重复"的两个模式时，接缝允许的最大间隔（秒）。
#: 接缝必须是**近乎相接**的：那正是"一段被切成两个"的特征。
COMPOUND_GAP_SEC = 4.0
#: 导数跨度，秒。瞬时差分会被 0.02 LU 的量化台阶主导（见 probe_drp_span.py）。
SLOPE_SPAN_SEC = 2.0
#: S 窗需要多长才开始有意义
S_HEAD_SEC = 3.0
#: 一个模式至少要出现几次。老板：**只出现一次的不算模式**。
MIN_OCCURRENCES = 2

#: ---- 判据容差（三项必须同时满足）----
#: 平均短时响度之差。这一项是挡假阳性的主力：CQ 那对假阳性的 Δ 是 17.8 LU。
TOL_LEVEL = 1.0           # LU
#: P95−P10 之差。"动态"本身当然要比。
TOL_DR = 2.0              # LU
#: z 归一化曲线（重采样到 `SHAPE_N` 点）的欧氏距离。**只比形状，不比电平**。
TOL_SHAPE = 0.60

#: 形状重采样点数
SHAPE_N = 100
#: 分段要够"有动态"才参与匹配（DR 低于这个值的段是平台，比了也没意义）
MIN_DR = 1.0              # LU

#: 静音下限（EBU 的绝对门限再往下一点，纯粹用来避开地板值）
SILENCE_FLOOR = -120.0

#: 相邻的**同一个模式**、间隔小于这个秒数就合并成一段。
#:
#: 为什么需要（老板 2026-10："相邻的同类型的动态模式要合并"）：分段是滑窗滑出来的，
#: 一段连续的通段被端点或冲突消解切开，就会变成两段 —— 实测
#: `鹿乃 - ウミユリ海底譚.flac` 里 `PT_3` 是 115.9–131.8 与 132.9–140.8，中间只隔 1.1s，
#: 图上看着像两个模式，其实是同一段。
#:
#: 阈值取**最小模式时长**那一档（3s）：隔得比一段模式本身还短，那本来就是同一段。
#: ⚠ 合并前必须确认那段空隙**没有别的模式占着**，否则会破坏 `_resolve_conflicts`
#: 保证的"出现之间互不重叠"（`check_drp.py` 钉着这条）。
MERGE_GAP_SEC = 3.0


def _merge_adjacent(pats: list[dict], gap: float = MERGE_GAP_SEC) -> None:
    """同一个模式、间隔 < `gap` 且空隙**没被别的模式占用**的相邻出现并成一段。

    原地改 `pats[i]["occurrences"]`（保留最早的起点与最晚的终点）。
    """
    if not pats:
        return
    for i, p in enumerate(pats):
        others = [o for j, q in enumerate(pats) if j != i for o in q["occurrences"]]
        merged: list[dict] = []
        for o in sorted(p["occurrences"], key=lambda x: x["start"]):
            if merged:
                prev = merged[-1]
                blocked = any(prev["end"] < b["end"] and b["start"] < o["start"]
                              for b in others)
                if o["start"] - prev["end"] < gap and not blocked:
                    prev["end"] = max(prev["end"], o["end"])
                    prev["end_idx"] = max(prev["end_idx"], o["end_idx"])
                    continue
            merged.append(dict(o))
        p["occurrences"] = merged


# ---------------------------------------------------------------- helpers

def _fmean(xs):
    return statistics.fmean(xs) if xs else 0.0


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile. Duplicated from backend.audio on purpose so
    this module stays standalone while the algorithm is still being tuned."""
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * max(0.0, min(100.0, pct)) / 100.0
    lo, hi = int(pos // 1), int(-(-pos // 1))
    if lo == hi:
        return xs[int(pos)]
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def dr_of(vals: list[float]) -> float:
    """P95 − P10，LU。**动态范围**的统一定义（`audio.py` 的汇总值也走这里）。"""
    return _percentile(vals, 95) - _percentile(vals, 10)


def slope_series(S: list[float], hz: float, span_sec: float = SLOPE_SPAN_SEC
                 ) -> list[float]:
    """`|S(t+span) - S(t)| / span` per frame, in LU/s. Last `span` frames use the
    longest available span rather than 0, so the tail is not silently dropped."""
    n = len(S)
    if n < 2 or hz <= 0:
        return [0.0] * n
    k = max(1, int(round(span_sec * hz)))
    dt = 1.0 / hz
    out = [0.0] * n
    for i in range(n):
        j = min(n - 1, i + k)
        out[i] = abs(S[j] - S[i]) / ((j - i) * dt) if j > i else 0.0
    return out


def _resample(vals: list[float], n: int = SHAPE_N) -> list[float]:
    """线性重采样到 `n` 点 —— 段长不等，必须先拉到同一长度才能比形状。"""
    m = len(vals)
    if m == 0:
        return [0.0] * n
    if m == 1:
        return [vals[0]] * n
    out = []
    for k in range(n):
        pos = (m - 1) * k / (n - 1)
        lo = int(pos)
        hi = min(m - 1, lo + 1)
        out.append(vals[lo] + (vals[hi] - vals[lo]) * (pos - lo))
    return out


def shape_of(vals: list[float], n: int = SHAPE_N) -> list[float]:
    """一段 S 曲线 → 重采样 + **z 归一化**的形状向量。

    z 归一化是**故意的**，不是顺手：归一化之后这个向量**只含形状信息**，
    电平高低由 `TOL_LEVEL` 单独管。直接比原始曲线的欧氏距离会让"一高一低的
    同形段"因为电平差被算成不同形状，而那恰恰是我们要分开判断的两件事
    （CQ 那对假阳性形状 0.260 < 真阳性 0.237，但电平差 17.8 LU —— 见模块 docstring）。
    """
    xs = _resample(vals, n)
    mu = _fmean(xs)
    sd = statistics.pstdev(xs) if len(xs) > 1 else 0.0
    if sd <= 1e-9:                      # 平段：形状无意义，返回全零
        return [0.0] * n
    return [(v - mu) / sd for v in xs]


def shape_dist(a: list[float], b: list[float]) -> float:
    """两个形状向量的欧氏距离，按点数归一（0 = 完全同形）。"""
    if not a or len(a) != len(b):
        return float("inf")
    return (sum((x - y) ** 2 for x, y in zip(a, b)) / len(a)) ** 0.5


def _exceedance(a: dict, b: dict) -> float:
    """三项判据的归一化超限度：`<= 1.0` 表示三项都在容差内。

    取**最大值**而不是加权和：任何一项超了都不算同一个模式，加权和会让
    "电平差很多但形状很像"（正是 CQ 那对假阳性）靠形状把总分拉回容差内。
    """
    return max(abs(a["level"] - b["level"]) / TOL_LEVEL,
               abs(a["dr"] - b["dr"]) / TOL_DR,
               shape_dist(a["shape"], b["shape"]) / TOL_SHAPE)


def _complete_linkage(feats: list[dict]) -> list[list[int]]:
    """Agglomerative complete-linkage clustering, then cut where a pair exceeds
    tolerance in ANY of the three criteria.

    Why not one-pass greedy: a single pass kept spilling adjacent sliding windows into
    fresh groups. Every window of one continuous passage has legitimately slightly
    different `(level, dr, shape)`, and a greedy "first group that accepts me" rule let
    a later window fail the per-member test against its natural group and start a new
    one. The result was two ids both describing 66-75 s.

    Complete linkage means every member of a cluster is within tolerance of every other,
    so a chain of "each step is close" cannot smuggle in a group that spans far-apart
    values. That is exactly the anti-false-positive guarantee 方案 C needs.
    """
    n = len(feats)
    if n == 0:
        return []
    clusters: list[list[int] | None] = [[i] for i in range(n)]

    def worst(a: list[int], b: list[int]) -> float:
        d = 0.0
        for i in a:
            for j in b:
                d = max(d, _exceedance(feats[i], feats[j]))
                if d > 1.0:
                    return d          # early out: already unmergeable
        return d

    while True:
        best = None
        best_d = 1.0
        ids = [k for k, c in enumerate(clusters) if c]
        for ai in range(len(ids)):
            for bi in range(ai + 1, len(ids)):
                a, b = clusters[ids[ai]], clusters[ids[bi]]
                d = worst(a, b)
                if d <= best_d:
                    best_d, best = d, (ids[ai], ids[bi])
        if best is None:
            return [c for c in clusters if c]
        ai, bi = best
        clusters[ai] = clusters[ai] + clusters[bi]
        clusters[bi] = None


def _merge_runs(idxs: list[int], step: int = 1) -> list[tuple[int, int]]:
    """Merge window START indices that belong to one continuous passage.

    ⚠ `step` is the sliding step in FRAMES, and it is required. With a 1 s step at
    10 Hz consecutive windows start 10 frames apart, not 1. Assuming adjacency meant
    `i <= p + 1` never fired, so every window of a passage was reported as its own
    occurrence -- PT_1 showed 8 occurrences for what is really 3 passages. This is
    the bug that made the sliding-window version look worse than fixed windows.

    Two windows are contiguous when their starts differ by exactly `step`; a larger
    gap (or a smaller one, impossible here) starts a new run.
    """
    if not idxs:
        return []
    runs = []
    s = p = idxs[0]
    for i in idxs[1:]:
        if i - p <= step:
            p = i
        else:
            runs.append((s, p))
            s = p = i
    runs.append((s, p))
    return runs


# ---------------------------------------------------------------- main entry

def _segments_from_windows(S: list[float], slope: list[float], hz: float,
                           win: int, step: int, head: int) -> list[dict]:
    """Stage 1 -- cut the file into sliding windows (the matching units).

    **就是一个每 `step` 帧一个、长 `win` 帧的滑窗**，没有延伸。为什么没有：
    延伸试过三种判据、实测全部有害，留档在 `COMPOUND_GAP_SEC` 上面那段。

    两个筛选：

    * 开头的 `head` 帧丢掉（S 窗还没填满，那一段的值不是节目内容）；
    * 局部平坦的窗（斜率低于全曲中位数）丢掉 —— 平台段不含动态模式信息，
      而且它们的 z 归一化形状是常向量，留着只会互相"匹配"上。

    窗与窗之间**天然互相重叠**（步长 < 窗长）。这不是 bug：重叠由
    `_resolve_conflicts` 收掉 —— 那是唯一保证"每次出现互不重叠"的地方。
    """
    floors = [abs(slope[i]) for i in range(head, len(S))]
    act_floor = statistics.median(floors) if floors else 0.0

    segs: list[dict] = []
    n = len(S)
    a = head
    while a + win <= n:
        sp = _fmean(slope[a:a + win])
        if sp >= act_floor:
            segs.append({"start_idx": a, "end_idx": a + win,
                         "level": _fmean(S[a:a + win]), "slope": sp})
        a += step
    return segs


def _merge_compound(pats: list[dict], step_sec: float = STEP_SEC,
                    gap: float = COMPOUND_GAP_SEC) -> list[dict]:
    """把"**每一次出现都紧挨着**的两个模式"并成一个。

    这是粒度问题的解药，不是猜的：老板标的 `PT_4` 是一段 32 s 的通段，而匹配单位
    是 16 s 的窗，于是它被报成**两个相邻模式**（一个含前 16 s、一个含后 16 s）——
    两处副本都一致地这么切。

    证据门槛（**必须是确定性的，否则宁可不动**）：A 的每一次出现后面都紧跟 B 的一次
    出现，出现**次数相等**，而且**接缝宽度处处一致**（与中位接缝之差不超过一个步长），
    接缝本身不超过 `gap` 秒（"紧挨着"）。任一条不满足就不并 —— 这样合并**不会引入
    新的误报**：它只在"两处副本都同样地相邻"时才动手。

    合并后的段长受 `MAX_SEG_SEC` 限制；并出来的出现是"从 A 的起点到 B 的终点"的
    连续区间。原地改并返回新列表（可能反复合并，所以是迭代的）。
    """
    if len(pats) < 2:
        return pats
    changed = True
    while changed and len(pats) >= 2:
        changed = False
        pats.sort(key=lambda p: p["occurrences"][0]["start"])
        for i in range(len(pats) - 1):
            a, b = pats[i], pats[i + 1]
            oa, ob = a["occurrences"], b["occurrences"]
            if len(oa) != len(ob) or len(oa) < MIN_OCCURRENCES:
                continue
            seams = []
            pairs = []
            for x in oa:
                # A 的这次出现之后、最近的一次 B 出现
                y = min((q for q in ob if q["start"] >= x["end"] - step_sec),
                        key=lambda q: q["start"], default=None)
                if y is None or y["start"] - x["end"] > gap:
                    pairs = []
                    break
                seams.append(y["start"] - x["end"])
                pairs.append((x, y))
            if not pairs:
                continue
            mid = sorted(seams)[len(seams) // 2]
            if any(abs(s - mid) > step_sec for s in seams):
                continue                      # 接缝不一致 ⇒ 不是"每次都挨着"
            merged = [{"start": x["start"], "end": y["end"],
                       "start_idx": x["start_idx"], "end_idx": y["end_idx"]}
                      for x, y in pairs]
            if any(m["end"] - m["start"] > MAX_SEG_SEC for m in merged):
                continue
            # ⚠ 合并**不能**破坏"每次出现互不重叠"这条不变量（`check_drp.py` 钉着它）。
            # 被并的两段虽然紧挨着，但第三个模式的某次出现可能正好落在那条缝里 ——
            # 那种情况下不并。
            others = [o for j, q in enumerate(pats) if j not in (i, i + 1)
                      for o in q["occurrences"]]
            if any(m["start"] < o["end"] and o["start"] < m["end"]
                   for m in merged for o in others):
                continue
            pats[i] = {**a, "occurrences": merged,
                       # 合并后的段是 A∪B，`spread` 只能取两半里差的那个
                       # （它描述的是"组内一致性"，对并集没有对应含义）
                       "spread": max(a.get("spread", 0.0), b.get("spread", 0.0)),
                       "compound": True}
            del pats[i + 1]
            changed = True
            break                             # 列表变了，重新扫
    return pats


def _resolve_conflicts(pats: list[dict], min_idx: int) -> list[dict]:
    """Make occurrences mutually exclusive: each frame belongs to ONE pattern.

    Segmentation already guarantees the *segments* do not overlap, but two segments
    built from adjacent positions can end up in different clusters, and then their
    patterns overlap -- measured at ~19 % of the file, e.g. three patterns all covering
    97-105 s. After this pass the overlap is 0.

    Assignment rule: process occurrences longest-first and claim frames greedily, so
    the longer (more informative) occurrence wins a contested stretch.

    ⚠ `min_idx` is required. Trimming a contested occurrence can leave a stub far
    shorter than a real section -- observed as a 0.9 s "occurrence". Any fragment below
    `min_idx` frames is discarded, and a pattern left with fewer than two occurrences
    stops being a pattern at all.
    """
    entries = []                       # (length, pat_index, occ_index, a, b)
    for pi, p in enumerate(pats):
        for oi, o in enumerate(p["occurrences"]):
            entries.append((o["end_idx"] - o["start_idx"], pi, oi,
                            o["start_idx"], o["end_idx"]))
    entries.sort(key=lambda t: -t[0])
    claimed = [False] * (max((e[4] for e in entries), default=0) + 1)
    kept: dict[tuple[int, int], tuple[int, int]] = {}
    for _, pi, oi, a, b in entries:
        if any(claimed[a:b]):
            free = [i for i in range(a, b) if not claimed[i]]
            if len(free) < min_idx:
                continue
            # only the longest contiguous free run is usable as an occurrence
            a2, b2 = max(_merge_runs(free, 1), key=lambda r: r[1] - r[0])
            if b2 - a2 + 1 < min_idx:
                continue
            a, b = a2, b2 + 1
        for i in range(a, b):
            claimed[i] = True
        kept[(pi, oi)] = (a, b)

    out = []
    for pi, p in enumerate(pats):
        occ = []
        for oi, o in enumerate(p["occurrences"]):
            if (pi, oi) not in kept:
                continue
            a, b = kept[(pi, oi)]
            occ.append({"start": round(o["start"] + (a - o["start_idx"]) * 0.1, 2),
                        "end": round(o["start"] + (b - 1 - o["start_idx"]) * 0.1, 2),
                        "start_idx": a, "end_idx": b})
        if len(occ) >= 2:
            out.append({**p, "occurrences": occ})
    return out


def patterns(S: list[float], t: list[float], hz: float,
             *, window_sec: float = WINDOW_SEC, step_sec: float = STEP_SEC,
             min_occurrences: int = MIN_OCCURRENCES) -> list[dict]:
    """Detect recurring dynamic patterns (two-stage: segment, then match).

    Stage 1 (`_segments_from_windows`) cuts the file into non-overlapping dynamic
    segments at SECTION scale. Stage 2 clusters those segments with complete linkage
    over the three simultaneous criteria (level / DR / z-normalised shape) and keeps
    the clusters that appear in at least `min_occurrences` well-separated places.

    Everything that does not clearly repeat is **dropped, not guessed** -- 方案 C.
    A quiet file (or a file whose repeats are all borderline) legitimately returns [].

    Returns a list sorted by first occurrence, each::

        {
          "id": "PT_1",
          "occurrences": [{"start": s, "end": e, "start_idx": i, "end_idx": j}, ...],
          "level": <mean S over all occurrences, LUFS>,
          "slope": <mean |dS/dt| over all occurrences, LU/s>,
          "dr":    <P95-P10 of S inside the pattern, LU>,
          "dur":   <total seconds across all occurrences>,
          "spread":<worst normalised criterion exceedance inside the group, <= 1.0>,
        }
    """
    n = len(S)
    if n < 4 or hz <= 0:
        return []
    head = int(round(S_HEAD_SEC * hz))
    win = max(2, int(round(window_sec * hz)))
    step = max(1, int(round(step_sec * hz)))
    slope = slope_series(S, hz)

    segs = _segments_from_windows(S, slope, hz, win, step, head)
    # 平台段（DR 太低）不参与匹配：形状是常向量，比了只会互相"匹配"上
    usable = []
    for s in segs:
        vals = S[s["start_idx"]:s["end_idx"]]
        if not vals:
            continue
        s = {**s, "dr": dr_of(vals), "shape": shape_of(vals)}
        if s["dr"] >= MIN_DR:
            usable.append(s)
    segs = usable
    if len(segs) < min_occurrences:
        return []

    groups = _complete_linkage(segs)

    out: list[dict] = []
    for g in groups:
        if len(g) < min_occurrences:
            continue
        members = sorted(g, key=lambda j: segs[j]["start_idx"])
        occ = [{"start": round(t[segs[j]["start_idx"]], 2),
                "end": round(t[segs[j]["end_idx"] - 1], 2),
                "start_idx": segs[j]["start_idx"],
                "end_idx": segs[j]["end_idx"]} for j in members]
        vals = [v for o in occ for v in S[o["start_idx"]:o["end_idx"]]]
        slp = _fmean([v for o in occ for v in slope[o["start_idx"]:o["end_idx"]]])
        # 组内最差的一对判据超限度：<= 1.0 就是"三项全过"，越小越紧
        spread = max((_exceedance(segs[i], segs[j])
                      for ai, i in enumerate(members) for j in members[ai + 1:]),
                     default=0.0)
        out.append({
            "occurrences": occ,
            "level": round(_fmean(vals), 2),
            "slope": round(slp, 3),
            "dr": round(dr_of(vals), 2),
            "dur": round(sum(o["end"] - o["start"] for o in occ), 1),
            "spread": round(spread, 3),
        })

    out.sort(key=lambda p: p["occurrences"][0]["start"])
    out = _resolve_conflicts(out, win)
    # 冲突消解会把一段连续的通段切碎；先把同一个模式的碎段缝回去（见 `_merge_adjacent`）
    _merge_adjacent(out)
    # 再把"每次都紧挨着"的两个模式并成一个（见 `_merge_compound`：一段 32s 的通段
    # 会被 16s 的窗切成两个模式，两处副本都一致地这么切）
    out = _merge_compound(out, step_sec)
    # 合并让出现变长了，统计量得按新的出现重算
    for p in out:
        vals = [v for o in p["occurrences"] for v in S[o["start_idx"]:o["end_idx"]]]
        slp = [v for o in p["occurrences"] for v in slope[o["start_idx"]:o["end_idx"]]]
        p["level"] = round(_fmean(vals), 2)
        p["slope"] = round(_fmean(slp), 3)
        p["dr"] = round(dr_of(vals), 2)
        p["dur"] = round(sum(o["end"] - o["start"] for o in p["occurrences"]), 1)
    # 老板：只出现一次的不算模式 —— 缝合/裁剪之后可能只剩一次，这里再筛一遍
    out = [p for p in out if len(p["occurrences"]) >= min_occurrences]
    out.sort(key=lambda p: p["occurrences"][0]["start"])
    for i, p in enumerate(out, 1):
        p["id"] = f"PT_{i}"
    return out


def extremes(pats: list[dict]) -> tuple[dict | None, dict | None]:
    """(PMAX, PMIN) -- the patterns with the largest / smallest `dr`."""
    if not pats:
        return None, None
    return (max(pats, key=lambda p: p["dr"]),
            min(pats, key=lambda p: p["dr"]))
