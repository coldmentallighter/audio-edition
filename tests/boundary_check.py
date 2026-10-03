"""接触面与边界的纯函数自检（不需要服务、不需要浏览器）。

对应《执行链并发方案.md》§7.1 的第一组断言：
  · 接触面登记完整（每个 op 都有，取值合法）
  · 声明的 produce/mode 与 handler 真实行为一致（这一条要起服务，见 tests/contract_probe.py）
  · 矩阵全格可算、取值合法
  · 抽样锚点（ANCHORS）全部成立 —— 改矩阵时若变了，这里会红，问"你是故意的吗"
  · 卡片可用性：链尾决定可选性，且**只有 zip 之后应当全不可选**
  · 两个视图同源：矩阵的 handoff_broken(a,b) 与 availability(a,b) 的提示不打架
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend import formats, tasks                                 # noqa: E402
from backend.cards import contract                                  # noqa: E402
from backend.cards.boundary import (ANCHORS, BROKEN, EXCLUSIVE,     # noqa: E402
                                    ORDERED, PARALLEL, availability,
                                    compile_rules, compatible,
                                    handoff_broken, relation)
from backend.cards.specs import OPS                                 # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


OPS_LIST = list(OPS)
MATRIX = compile_rules()
VALID = {PARALLEL, ORDERED, EXCLUSIVE, BROKEN}


print("== 1. 接触面登记 ==")
errs = contract.validate_contract()
check("每个 op 都登记了接触面，且取值合法", not errs, errs)
check(f"接触面覆盖全部 {len(OPS_LIST)} 个 op", set(contract.CONTRACT) == set(OPS_LIST),
      set(contract.CONTRACT) ^ set(OPS_LIST))
check("produce 与 gives 一致（produce=none ⇒ gives=none）",
      all(not (contract.produces(op) == "none" and contract.of(op)["gives"] != "none")
          for op in OPS_LIST),
      [op for op in OPS_LIST
       if contract.produces(op) == "none" and contract.of(op)["gives"] != "none"])
check("needs 里没有裸的 any 与具体类型并列",
      all(not ("any" in contract.needs_of(op) and len(contract.needs_of(op)) > 1)
          for op in OPS_LIST))
# 就地写者只能是那四个（多一个都说明 mode 写错了）
writers = sorted(op for op in OPS_LIST if contract.rewrites_input(op))
check("就地写者恰好是 tags/cover/remove-cover/rename",
      writers == ["cover", "remove-cover", "rename", "tags"], writers)
# convert/normalize 是"产出新文件但只读输入"，最容易写错
check("convert / normalize 的 mode 是 read（只写 outputs，不改输入）",
      all(contract.of(op)["mode"] == "read" for op in ("convert", "normalize")))
check("waveform / loudness-image 的 mode 是 read（同上）",
      all(contract.of(op)["mode"] == "read" for op in ("waveform", "loudness-image")))

print()
print("== 2. 矩阵 ==")
check(f"矩阵是 {len(OPS_LIST)}×{len(OPS_LIST)} 全格",
      all(len(MATRIX[a]) == len(OPS_LIST) for a in OPS_LIST))
cells = [(a, b, MATRIX[a][b]) for a in OPS_LIST for b in OPS_LIST]
bad = [(a, b, v) for a, b, v in cells if v not in VALID]
check("每格取值都在 {∥,⇉,⤫,⇥} 里", not bad, bad)
check("对角线是 ∥（同种操作连着做没有顺序约束）",
      all(MATRIX[o][o] == PARALLEL for o in OPS_LIST))
check("矩阵不对称是有意的（先 tags 后 rename ≠ 反过来）",
      MATRIX["probe"]["tags"] != MATRIX["tags"]["probe"],
      (MATRIX["probe"]["tags"], MATRIX["tags"]["probe"]))

print()
print("== 3. 抽样锚点（写死；变了就是有意变更，请改 ANCHORS） ==")
abad = [(a, b, w, relation(a, b)) for (a, b), w in ANCHORS.items() if relation(a, b) != w]
for a, b, w, g in abad:
    print(f"        {a} -> {b}: 期望 {w}，实际 {g}")
check(f"{len(ANCHORS)} 条锚点全部成立", not abad, f"{len(abad)} 条不符")

print()
print("== 4. 卡片可用性 ==")
# 唯一应当"整片不可选"的链尾：zip（交出 archive，没有 op 吃它）
blocked_tails = sorted({t for t in OPS_LIST for o in OPS_LIST if not availability(t, o)[0]})
check("只有 zip 之后全部不可选", blocked_tails == ["zip"], blocked_tails)
check("zip → 任意卡 都不可选",
      all(not availability("zip", o)[0] for o in OPS_LIST))
check("链为空时每张卡都能选（现有 op 没有 needs=upstream 的）",
      all(availability(None, o)[0] for o in OPS_LIST))
check("zip 可以当链的第一张（自身为第一项 → 打包源文件）",
      availability(None, "zip")[0])
# 硬禁与"可选但提示"的边界：图片喂给要音频的卡必须**可选**
ok, why = availability("waveform", "convert")
check("波形图 → 格式转换 可选，但要提示会回到原文件", ok and "回到原文件" in why, why)
ok, why = availability("extract-cover", "zip")
check("提取封面 → 打包 ZIP 可选且无需提示（zip 吃图片）", ok and not why, why)
ok, why = availability("zip", "tags")
check("打包 ZIP → 改标签 不可选，且原因是链已到尽头",
      (not ok) and ("最后一环" in why or "接不住" in why), why)
# 就地改写之后接得上（in_place 续点）
ok, why = availability("tags", "convert")
check("改标签 → 格式转换 可选且无需提示（in_place 续点）", ok and not why, why)
ok, why = availability("normalize", "convert")
check("标准化 → 格式转换 可选且无需提示（derived 续点）", ok and not why, why)
# 断链提示
ok, why = availability("verify", "normalize")
check("完整性校验 → 标准化 可选，提示会回到原文件",
      ok and "回到原文件" in why, why)

print()
print("== 5. 两个视图同源（矩阵的 ⇥ 与可用性不许打架） ==")
# 矩阵的 handoff_broken(a,b) 与可用性的 compatible(a,b) 必须**严格等值**：
# 一个是"两个 op 的先后"，一个是"链尾 + 下一张卡"，判据同源就不该分叉。
mismatch = []
for a in OPS_LIST:
    for b in OPS_LIST:
        if a == b:
            continue
        m = handoff_broken(a, b)
        c = compatible(a, b)
        if m != (not c):
            mismatch.append((a, b, m, c))
check("handoff_broken(a,b) ⇔ not compatible(a,b)", not mismatch, mismatch[:6])
# 反向**不成立**，而且这是有意的：`compatible` 说的是"这一步愿意收这种东西"，
# `availability` 说的是"界面让不让你选"。`zip` 之后每张卡都 compatible=True
# （它们都能收下压缩包），但都不该被选 —— 链已经到尽头了。
# 所以只能断言单向：不可选 + 接不住 才是矛盾（"嘴上说接得住却不让选"）。
mismatch2 = [(t, o) for t in OPS_LIST for o in OPS_LIST
             if not availability(t, o)[0] and (t, o) not in
             {(z, w) for z in OPS_LIST if z == "zip" for w in OPS_LIST}
             and compatible(t, o)]
check("不可选 ⇒ 接不住（zip 之后除外：那是终态，不是接不住）", not mismatch2, mismatch2[:6])

print()
print("== 6. 未知操作要炸得干净，不是抛异常 ==")
ok, why = availability("tags", "不存在的op")
check("链尾未知 → 不可选 + 说明", (not ok) and "未知" in why, why)
ok, why = availability(None, "不存在的op")
check("待选未知 → 不可选 + 说明", (not ok) and "未知" in why, why)

print()
print("== 7. 响应度报告（loudness-report）与格式表 ==")
# 报告是"交出文档"的旁路产物：它不产出音频、也不改格式流 —— 这两条一起
# 决定了它接在链上任何位置都不影响后面的转码判断（§3.9）。
check("loudness-report 的 produce 是 sidecar（交出文档，不是音频）",
      contract.produces("loudness-report") == "sidecar",
      contract.produces("loudness-report"))
check("loudness-report 的 gives/gives_formats 是 none（不参与格式流）",
      contract.of("loudness-report")["gives"] == "none"
      and contract.gives_formats("loudness-report") == "none",
      (contract.of("loudness-report")["gives"],
       contract.gives_formats("loudness-report")))
check("loudness-report 的 mode 是 read（只写 outputs，不改输入）",
      contract.of("loudness-report")["mode"] == "read")
check("loudness-report 能接在任何链尾后面（包括 zip？不：zip 后全禁）",
      all(availability(t, "loudness-report")[0]
          for t in OPS_LIST if t != "zip"))

# ---- `gives='none'` 的只读分析之后，卡片必须照旧可选 --------------------------
#
# 这里**曾经是一条空转断言**（写的是 `availability(None, 'loudness-report')`，
# 也就是"报告能不能当链的**第一张**"——它恒为真，跟"报告之后能不能接"毫无关系）。
# 于是真 bug 溜过去了：`gives='none'` 掉进"没有任何 op 接得住"的终态判定
# （`none` 不是任何卡 `needs` 里的类型，只有汇总类 `zip` 显式收它），
# 结果链尾是响度报告时**除打包以外全部置灰** —— 用户就是这么撞上的。
#
# 判据必须是"**在它后面**"：链尾 = 该 op，下一张卡能不能选。
_NONE_GIVES = [op for op in OPS_LIST if contract.of(op)["gives"] == "none"]
check("确实存在 gives='none' 的 op（否则下面几行是空转）",
      len(_NONE_GIVES) >= 4, _NONE_GIVES)
for _tail in _NONE_GIVES:
    _blocked = [o for o in OPS_LIST if not availability(_tail, o)[0]]
    # `zip` 自己是终态，不该被它后面的卡选（`_tail` 是 none 型时 zip 仍可选）
    check(f"链尾「{OPS[ _tail]['label']}」（gives=none）之后**所有卡都还能选**",
          not _blocked, _blocked)
check("gives='none' 的只读分析之后，格式转换仍然可选",
      availability("loudness-report", "convert")[0]
      and availability("verify", "convert")[0])
check("但真正的终态仍然全禁：打包 ZIP 之后一个都不能选",
      all(not availability("zip", o)[0] for o in OPS_LIST))
check("`none` 与 `archive` 的区别就是这条规则的要点："
      "前者是「没动那个音频文件」，后者是「没卡片接得住」",
      availability("loudness-report", "tags")[0] and not availability("zip", "tags")[0])
# ⚠ 这里**不要**断言"矩阵里 gives='none' → 别的 op 不是 ⤫/⇥"。
# `handoff_broken(probe, convert)` 确实是 True，而且是对的：它的定义是
# "b 会不会拿不到 a 的产物"（`⇥`，链上挂回落提示），**不是"禁止"**。
# 只读分析之后下一环当然拿不到产物（它压根没产物），所以提示是准确的；
# 该禁的只有 `archive`。上面"所有卡都还能选"那组才是这条 bug 的判据。
# 格式分类表：有损/无损的分界是"能不能复原"，不是"码率高低"
check("mp3/aac/m4a/ogg/opus/wma 判为有损",
      all(formats.is_lossy(f) for f in ("mp3", "aac", "m4a", "ogg", "opus", "wma")),
      [f for f in ("mp3", "aac", "m4a", "ogg", "opus", "wma")
       if not formats.is_lossy(f)])
check("flac/wav/aiff 判为无损",
      all(formats.is_lossless(f) for f in ("flac", "wav", "aiff")),
      [f for f in ("flac", "wav", "aiff") if not formats.is_lossless(f)])
# `alac` 是真实存在的无损编码，但**不是我们能转换到的目标**（不在白名单里），
# 所以对它必须回答"不知道"而不是"无损" —— 分类只覆盖白名单内的容器。
check("白名单外的容器（如 alac）判为未知，不硬猜成无损",
      not formats.is_lossless("alac") and not formats.is_lossy("alac")
      and formats.classify("alac") is None, formats.classify("alac"))
check("白名单恰好是转换目标的集合（分类与可选项不许分叉）",
      formats.AUDIO_FORMATS == set(formats.FORMAT_ARGS),
      formats.AUDIO_FORMATS ^ set(formats.FORMAT_ARGS))
check("无损集合由「白名单 − 有损」推导，不是手写的第二份名单",
      formats.LOSSLESS_FORMATS == formats.AUDIO_FORMATS - formats.LOSSY_FORMATS,
      formats.LOSSLESS_FORMATS)
check("tasks.FORMAT_ARGS 与 formats.FORMAT_ARGS 是同一份（别名，不是副本）",
      tasks.FORMAT_ARGS is formats.FORMAT_ARGS)
check("后缀带点 / 大小写都能认（`Convert` 传进来的就是 '.FLAC'）",
      formats.is_lossless(".FLAC") and formats.is_lossy(".Mp3"),
      (formats.classify(".FLAC"), formats.classify(".Mp3")))
check("未知后缀既不判有损也不判无损（不猜）",
      not formats.is_lossy("bin") and not formats.is_lossless("bin"),
      formats.classify("bin"))
check("有损与无损集合不相交", not (formats.LOSSY_FORMATS & formats.LOSSLESS_FORMATS))
check("tasks.LOSSLESS_FORMATS（码率校验用）与 formats 的口径一致",
      tasks.LOSSLESS_FORMATS == set(formats.LOSSLESS_FORMATS),
      tasks.LOSSLESS_FORMATS ^ set(formats.LOSSLESS_FORMATS))

print()
print(f"结果：{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
