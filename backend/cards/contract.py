"""每个 op 的"接触面"：它碰哪个文件、会不会改它、产出属于哪一类、吃和交哪种文件。

这是 `执行链并发方案.md` §2.1 那一张表的代码形态，也是**边界推导的唯一事实源**：
  · `boundary.compile_rules()` 用它推出两两关系矩阵（§2.3）
  · `boundary.availability()` 用 `needs`/`gives` 算"下一步能选什么卡"（§3.2.4）
  · 前端的卡片置灰也从 `GET /api/ops` 读到同一份字段（`catalog.py` 直接展开 spec）

字段含义（写错任何一个都会让矩阵或可用性判断错，测试里逐条盯着）：

  touch   : 碰哪些文件。`in` = uploads/ 里那个受管副本（或上游派生的产物），
            `out` = outputs/ 下新生成的文件。
  mode    : **会不会改变 `in` 指向的那个文件**。
              `write` —— 就地改写：跑完之后那个文件的内容/名字变了
                         （tags / cover / remove-cover / rename）
              `read`  —— 跑完 `in` 一字未改：probe / peaks / verify / loudness /
                         extract-cover（写 outputs） / waveform（写 outputs） /
                         loudness-image（写 outputs）
            ⚠ **`convert` / `normalize` 是 `read`**：它们往 outputs/ 写**新**文件，
            从不碰 `in`。这个区分是"规则 ① 互斥"的判据，写错的后果很具体：
               · 把它们当 write → `转 FLAC` 与 `转 WAV` 会被判成互斥（⤫），
                 而它们明明是两个纯读者，完全能并行，只是各自产出不同格式；
               · 也会让"同文件写者租约"把两个转换排成串行，白白慢一倍。
            "会不会产出新文件"由 `produce` 表达，不要用 `mode` 兼职。
  produce : 产出形态，决定**串行档的输入怎么解析**（§3.1.3）：
              in_place —— 改的就是当前那个文件 → 下一步仍用同一个 file_id
              derived  —— 产出新文件且链可以继续吃 → 下一步换文件（src_task_id）
              sidecar  —— 产出旁路文件（jpg/png/zip），**不是"这一步处理的音频"**，
                          所以**不参与输入解析**，但仍然能被 zip 装走
              none     —— 什么都不产出 → 下一步回到原文件
            `sidecar` 与 `none` 必须分开：`波形 → 打包` 装的是 PNG，
            而 `校验 → 打包` 装的是原音频，两者在旧表里都是 `none`，分不出来。
  obs     : handler **内部**读/改文件的哪一部分（whole / meta / audio / container）。
            · 用于"声明与代码一致性"的核对
            · **也参与规则 ①**：`whole` 表示整体重写，与任何就地改写互斥；
              两个只动标签字典的（meta）只需有序。见 `boundary.relation`
  needs   : 吃得动哪种文件，**可以多个**（audio / image / archive / any / upstream）。
            · `upstream` 表示"必须有上游产物，不能当链的第一张"（现有 op 一个都没有）
            · `any` 表示"谁都能喂"——**慎用**：它连"图片喂给要音频的卡"也一起放行，
              所以能用具体的就写具体的（`zip` 原来写 `any`，现已收紧成 audio+image）。
  gives   : 交出哪种文件（audio / image / archive / none）。
            `archive` 没有任何 op 的 needs 能吃 → **"打包 ZIP 是链尾"是类型系统的推论**，
            不是写死的特例（§3.2.3 四）。
            `none` 是"不交出任何东西"，**必须与 `produce` 一致**：
            `produce='none'` 的 op 一律 `gives='none'`（它没东西可交），
            否则会宣称"我交给你一份音频"，而下一步拿到的还是原文件。
  consumes: 这一步**依不依赖上一环递过来的东西**（默认 True）。
            `False` 只有两个：
              · `verify` —— 它给的是"这个文件完不完整"的**判定**，不看上一环产出
              · `zip`    —— 它自己去解算链上每个文件的最终产物（§3.2.3），不等人递
            有了它，`波形图 → 校验` 才不会被误判成"产物断链"：
            校验本来就不是在消费波形图，它只是在看那个音频文件。
  gives_formats:
            **这一步产出什么容器格式** —— 供建链时推导**格式流**用。
            返回值约定（`backend/chain.py` 的 `format_flow` 读它）：
              · `"same"`       —— 沿用输入的容器（`normalize` 产出 `.norm.<原后缀>`）
              · `"param:format"` —— 取 params 里的 `format`（`convert`）
              · `"image"` / `"archive"` / `"none"` —— 不是音频（不参与格式流）
            这一列是"**不允许有损→无损转码**"那条串行规则的前提：
            没有它就只能看输出扩展名去猜，而猜错的后果是静默放行。

**不允许存在 `zipless` 之类的特例字段**：边界必须能从上面五个字段推出来。
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------- 取值域

TOUCH = ("in", "out")
MODE = ("read", "write")
PRODUCE = ("in_place", "derived", "sidecar", "none")
NEED_ATOMS = ("audio", "image", "archive", "none", "any", "upstream")
GIVES = ("audio", "image", "archive", "none")
# `gives_formats` 的取值域：容器沿用 / 取参数 / 非音频产物
GIVES_FORMATS = ("same", "param:format", "image", "archive", "none")

# 给界面用的中文名（`availability()` 的文案要拼它们）
GIVE_LABEL = {"audio": "音频", "image": "图片", "archive": "压缩包", "none": "结果"}

# ---------------------------------------------------------------- 接触面表
#
# 一行一个 op。**这张表是新增 op 时必须同步改的地方**，
# 测试 tests/contract_check.py 会拿它和 handler 的真实行为对账。

CONTRACT: dict[str, dict[str, Any]] = {
    # ---- 只读、什么都不产出 ----
    # 这四个 `produce: none` **且 `gives: none`**：它们不会把"处理结果"交给下一步。
    # 别顺手把 `gives` 改成 `audio` 去绕开可用性问题 —— `gives` 说的是
    # "这一步**交出**什么"，而它们什么都没交出（下一步拿到的还是那份原文件）。
    # "下一步仍然有音频可用"这件事由 `availability()` 的 handoff 判定负责，
    # 不是靠这里撒谎（见 boundary._hands_something_off 的注释）。
    "probe":          {"touch": ["in"],        "mode": "read",  "obs": "meta",
                       "produce": "none",    "needs": ("audio",), "gives": "none", "gives_formats": "same"},
    "peaks":          {"touch": ["in"],        "mode": "read",  "obs": "audio",
                       "produce": "none",    "needs": ("audio",), "gives": "none", "gives_formats": "same"},
    "verify":         {"touch": ["in"],        "mode": "read",  "obs": "audio",
                       "produce": "none",    "needs": ("audio",), "gives": "none", "gives_formats": "same",
                       "consumes": False},
    "loudness":       {"touch": ["in"],        "mode": "read",  "obs": "audio",
                       "produce": "none",    "needs": ("audio",), "gives": "none", "gives_formats": "same"},

    # ---- 只读、产出旁路文件（sidecar：不参与输入解析，但能被 zip 装走）----
    "extract-cover":  {"touch": ["in", "out"], "mode": "read",  "obs": "container",
                       "produce": "sidecar", "needs": ("audio",), "gives": "image", "gives_formats": "image"},
    "waveform":       {"touch": ["in", "out"], "mode": "read",  "obs": "audio",
                       "produce": "sidecar", "needs": ("audio",), "gives": "image", "gives_formats": "image"},
    "loudness-image": {"touch": ["in", "out"], "mode": "read",  "obs": "audio",
                       "produce": "sidecar", "needs": ("audio",), "gives": "image", "gives_formats": "image"},
    # 响度报告：产出的是 .md，同样只读输入。`gives_formats="none"` 表示它
    # **不产出音频**，所以不参与"有损→无损"的格式流推导（下一步会回到原文件）。
    # `gives="none"` 与上面四个同理：它没交出音频（与原设计一致，别改）。
    "loudness-report": {"touch": ["in", "out"], "mode": "read", "obs": "audio",
                        "produce": "sidecar", "needs": ("audio",), "gives": "none",
                        "gives_formats": "none"},

    # ---- 就地改写（in_place：产物就是同一个 file_id 指向的文件）----
    "tags":           {"touch": ["in"],        "mode": "write", "obs": "meta",
                       "produce": "in_place", "needs": ("audio",), "gives": "audio", "gives_formats": "same"},
    "cover":          {"touch": ["in"],        "mode": "write", "obs": "container",
                       "produce": "in_place", "needs": ("audio",), "gives": "audio", "gives_formats": "same"},
    "remove-cover":   {"touch": ["in"],        "mode": "write", "obs": "container",
                       "produce": "in_place", "needs": ("audio",), "gives": "audio", "gives_formats": "same"},
    "rename":         {"touch": ["in"],        "mode": "write", "obs": "meta",
                       "produce": "in_place", "needs": ("audio",), "gives": "audio", "gives_formats": "same"},

    # ---- 产出新音频（derived：下一步吃这个新文件；但 mode 仍是 read）----
    "convert":        {"touch": ["in", "out"], "mode": "read",  "obs": "whole",
                       "produce": "derived",  "needs": ("audio",), "gives": "audio", "gives_formats": "param:format"},
    "normalize":      {"touch": ["in", "out"], "mode": "read",  "obs": "whole",
                       "produce": "derived",  "needs": ("audio",), "gives": "audio", "gives_formats": "same"},

    # ---- 汇总：吃音频或图片，交出压缩包（没人接得住 → 天生是链尾）----
    # needs 收紧成 audio+image 而不是 any：写成 any 会连"转 WAV → 嵌入封面"
    # 也一起放行（§3.9.2）。另外**显式收下 `none`** —— 它是"收全部文件"的汇总类，
    # 上一步有没有交出东西它都不在乎（它自己会解算链上每个文件的最终产物）。
    "zip":            {"touch": ["in"],        "mode": "read",  "obs": "whole",
                       "produce": "sidecar", "needs": ("audio", "image", "none"),
                       "gives": "archive", "gives_formats": "archive",   "consumes": False},
}

# ---------------------------------------------------------------- 助手

def of(op: str) -> dict[str, Any]:
    """取某个 op 的接触面；没有就抛 KeyError（早点炸比默默按默认值算好）。"""
    return CONTRACT[op]


def touches_input(op: str) -> bool:
    return "in" in CONTRACT[op]["touch"]


def rewrites_input(op: str) -> bool:
    """就地改写 `in` 指向的文件吗 —— **规则 ① 互斥的唯一判据**。

    只有 tags / cover / remove-cover / rename 这四个是 True。
    `convert`/`normalize` 虽然产出新文件，但从不碰 `in`，所以是 False。
    """
    return CONTRACT[op]["mode"] == "write" and "in" in CONTRACT[op]["touch"]


def produces(op: str) -> str:
    return CONTRACT[op]["produce"]


def obs_of(op: str) -> str:
    """handler 内部读/改文件的哪一部分。

    以前它只用于"声明与代码一致性"的核对，**现在规则 ① 真的用它**：
    `obs` 不同（如 `嵌入封面` 的 `container` vs `改标签` 的 `meta`）表示
    写的是不同区域，但都在往同一个文件里塞东西 → 互斥；
    只有两个都是 `meta`（`改标签` / `按标签重命名`）才退化成"有序"。
    少了这个区分，"改标签 → 按标签重命名"这条明显合理的链会被标成冲突。
    """
    return str(CONTRACT[op]["obs"])


def consumes_upstream(op: str) -> bool:
    """这一步依不依赖上一环递过来的产物。默认 True。

    `False` 的两个（`verify` / `zip`）不是"消费"上一环，而是**判定**或**收集**：
    少了这个事实，"波形图 → 完整性校验"会被误报成"产物断链"，
    而校验本来就没打算校验那张波形图。
    """
    return bool(CONTRACT[op].get("consumes", True))


def hands_off(op: str) -> bool:
    """这一步的产出能不能被下一步**当作音频输入**继续吃（规则 ③ 的判据）。

    只有 `derived` 能：它产出的是"这一步处理完的那个文件"。
    `in_place` 也能续（同一个 file_id），但那是"顺序"而不是"换文件"，另算。
    `sidecar`/`none` 不能 —— 下一步会回到原来的 file_id。
    """
    return produces(op) in ("derived", "in_place")


def yields_file(op: str) -> bool:
    """会不会产出可被 zip 装走的文件（derived / sidecar 都算）。"""
    return produces(op) in ("derived", "sidecar")


def needs_of(op: str) -> tuple[str, ...]:
    """标准化成 tuple：单个字符串也接受（写起来省事）。"""
    n = CONTRACT[op]["needs"]
    return (n,) if isinstance(n, str) else tuple(n)


def accepts(gives: str, op: str) -> bool:
    """链尾交出 `gives` 时，`op` 接不接得住。"""
    n = needs_of(op)
    if "any" in n:
        return True
    return gives in n


def first_ok(op: str) -> bool:
    """能不能当整条链的第一张（能吃源文件）。"""
    return "upstream" not in needs_of(op)


def gives_formats(op: str) -> str:
    """这一步产出什么容器格式 —— 建链时推导**格式流**用。

    见模块头部 `gives_formats` 那一段。未知 op 返回 `"none"`（保守：
    不参与格式流，而不是假装它产出无损音频）。
    """
    return str(CONTRACT.get(op, {}).get("gives_formats") or "none")


def validate_contract() -> list[str]:
    """自检：字段齐全、取值合法、没有漏登记的 op。

    **必须在导入 OPS 之后调用**（见 cards/__init__.py），
    否则漏登记一个 op 时前端只会"这张卡永远可选"，不报错。
    """
    from backend.cards.specs import OPS

    errs: list[str] = []
    for op in OPS:
        c = CONTRACT.get(op)
        if c is None:
            errs.append(f"{op}: 没有登记接触面（加 op 时必须同步 backend/cards/contract.py）")
            continue
        # 标量字段
        for key, allowed in (("mode", MODE), ("produce", PRODUCE), ("gives", GIVES),
                             ("gives_formats", GIVES_FORMATS)):
            if key not in c:
                errs.append(f"{op}: 缺字段 {key}")
            elif c[key] not in allowed:
                errs.append(f"{op}.{key}={c[key]!r} 不在 {allowed}")
        # `gives_formats` 与 `gives` 必须自洽：说好交图片就不能说产出音频容器。
        # （这两列分属"链能不能接"与"格式流怎么走"，很容易只改一处。）
        gf, gv = c.get("gives_formats"), c.get("gives")
        if (gf, gv) in (("image", "audio"), ("image", "none"), ("archive", "audio"),
                        ("param:format", "image"), ("param:format", "archive"),
                        ("param:format", "none")):
            errs.append(f"{op}: gives={gv!r} 与 gives_formats={gf!r} 互相矛盾")
        # touch 是**列表**（`['in','out']`），要逐个元素查，不能整个列表去比标量
        touch = c.get("touch")
        if not isinstance(touch, (list, tuple)):
            errs.append(f"{op}.touch 必须是列表，收到 {touch!r}")
        else:
            if not touch:
                errs.append(f"{op}.touch 不能为空")
            bad = [t for t in touch if t not in TOUCH]
            if bad:
                errs.append(f"{op}.touch 含非法值 {bad}（只能是 {TOUCH}）")
        if not needs_of(op):
            errs.append(f"{op}.needs 不能为空")
        badn = [x for x in needs_of(op) if x not in NEED_ATOMS]
        if badn:
            errs.append(f"{op}.needs 含非法值 {badn}")
        if "any" in needs_of(op) and len(needs_of(op)) > 1:
            errs.append(f"{op}.needs 里 any 不该和其他原子并列（any 已经是通配）")
        if not str(c.get("obs") or "").strip():
            errs.append(f"{op}: obs 不能为空")
    for op in CONTRACT:
        if op not in OPS:
            errs.append(f"{op}: 登记了接触面但 OPS 里没有这个 op")
    return errs
