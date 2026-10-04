"""命令之间的界限：三条规则 → 两两关系矩阵 → 卡片可用性。

纯函数、无 IO。这是 `执行链并发方案.md` §2.2 / §2.3 / §3.2.4 的可执行形态：
  · `compile_rules()` 推出矩阵（文档 §2.3 那张表由它 dump，**不许手抄**）
  · `availability()` 算"链尾是某张卡时，另一张卡能不能选"

三条规则（§2.2）：

  规则 ① 同文件写者互斥
      同一文件上，任何两个"会**就地改**它"的步骤不能同时在跑。
      判据：`contract.rewrites_input(op)` —— `mode='write'` 且 `touch` 含 `in`。
      只有 tags / cover / remove-cover / rename 四个满足。
      ⚠ **`convert`/`normalize` 不算**：它们 mode 是 read（只往 outputs/ 写新文件），
      所以"转换 A → 转换 B"是两个纯读者，能并行，只是各自产出不同格式。

  规则 ② 链序 = 因果
      链上 i<j 且 j 会**就地改** i 读过的那个文件 ⇒ j 必须等 i 结束。
      落地形式是 `tasks.src_task_id` + `queue._gate`（按**文件**就绪，不是按步）。
      **矩阵里的 `⇉` 就是这条**；只有"写者 vs 接触 in 的任一方"才触发。

  规则 ③ 输入解析 / 产物断链
      上一步的 `produce` 不产出可续的东西（`sidecar`/`none`），
      而下一步还要吃文件 ⇒ "能跑，但会回到原文件"。**矩阵里的 `⇥`**。

矩阵取值：
  ∥  可并行       ⇉  同文件按链序串行       ⤫  写-写互斥       ⇥  产物接不上（回到原文件）
"""
from __future__ import annotations

from backend.cards import contract
from backend.cards.specs import OPS

PARALLEL = "∥"
ORDERED = "⇉"
EXCLUSIVE = "⤫"
BROKEN = "⇥"

# `produce` 里这两类不产出"可续的音频" → 下一步回到原文件（规则 ③）
NO_HANDOFF = ("none", "sidecar")


def relation(a: str, b: str) -> str:
    """链上**先 a 后 b** 时两者的关系。顺序有意义，所以矩阵不对称。

    判据的**方向很关键**（第一版就在这里写反过）：
      · 规则 ① 看"两个写者会不会写到同一个区域"
      · 规则 ② 看"**先跑的那个**是不是写了 `in`"——`probe → tags` 不需要顺序
        （probe 什么都没改，tags 等它没有意义），而 `tags → probe` 需要
        （probe 不能读到写了一半的标签）
      · 规则 ③ 看"a 的产出能不能被 b 当输入吃"

    `obs` 在规则 ① 里真的用上了：`obs='whole'`（整体重写）与谁都不能共存 ——
    `嵌封面` 要把图片作为一条新流写进容器、整个文件重写一遍，所以它和
    `改标签`/`按标签重命名` 是**互斥**（换顺序也救不了）；
    而 `改标签` 与 `按标签重命名` 都在动**同一个标签字典**，所以是**有序**。
    这一格如果只按"两个都是写者"一刀切成互斥，UI 上就会把一条用户明显
    想要的有序链（改标签 → 按标签重命名）标成冲突。
    """
    # 规则 ①：两个都就地改 in，但**写的不是同一块东西** → 写-写互斥。
    #
    # 判据用 `obs`，而 `meta` 是这里唯一"可合并"的取值：
    #   · `改标签` 与 `按标签重命名` 都是 `meta` → 都在动**同一个标签字典**，
    #     顺序说清楚就行（用户要的就是"先改标签、再按新标签改名"）。
    #     一刀切成互斥会把这件明显合理的事标成冲突。
    #   · `嵌封面`（`container`） vs `改标签`（`meta`）→ 一个动图片块/整容器、
    #     一个动标签字典，**写的地方不同，但都在往同一个文件里塞**：
    #     实测 `embed_cover` 对 FLAC 走 `metaflac --import-picture-from`、
    #     对 mp3/m4a 走 ffmpeg 重写容器（`audio.py:426-449`，`-c:a copy`），
    #     都不是"只动一小块"的原子操作 → 换顺序也救不了，判互斥。
    #   · `嵌入封面` vs `删除封面` → 都在动 PICTURE 块，互斥。
    if contract.rewrites_input(a) and contract.rewrites_input(b):
        obs_a, obs_b = contract.obs_of(a), contract.obs_of(b)
        if obs_a == obs_b == "meta":
            return ORDERED
        return EXCLUSIVE

    # 规则 ②：**先跑的 a 就地改了文件**，后跑的 b 又碰同一个文件 → b 必须等 a
    if contract.rewrites_input(a) and contract.touches_input(b):
        return ORDERED

    # 规则 ③：a 的产出喂不到 b，而 b 又碰同一个文件 → b 拿到的是**原文件**，
    # 不是 a 的结果。两个分支，覆盖两种"喂不到"：
    #   · b 干脆不吃上一环（consume=False：`校验` 是判定、`打包` 是收集）
    #   · b 吃，但 a 交出的类型它接不住（`提取封面` 交图片，`转换` 要音频）
    # 不算断链的例外：b 接得住 a 交出的类型，或 a 是就地改写（改动已落在同一 file_id）。
    if contract.touches_input(b) and not compatible(a, b):
        return BROKEN

    return PARALLEL


def compile_rules() -> dict[str, dict[str, str]]:
    """全矩阵：`out[a][b]` = 先 a 后 b 的关系。

    文档 §2.3 那张表必须由它 dump（`python -m backend.cards.boundary --dump-matrix`），
    **不许手抄一份**——手抄的迟早和代码分叉。

    对角线固定 `∥`：同一种操作连着做两次之间**不存在顺序约束**
    （两次 `probe` 之间没有因果）。注意 `⇥` 与 `∥` 在矩阵里是**两个维度**：
    `⇥` 说的是"后一步拿不到前一步的产物"，`∥` 说的是"两者没有先后约束"。
    一对 op 可以同时是两者（如 `probe → peaks`：能并行，但 peaks 看不到 probe 的产出，
    而 probe 本来也没有产出）。所以**判 `⇥` 与判 `⇉`/`⤫` 是并列的，不是互斥的**——
    只是这里为了画成一张表，`⇥` 优先显示。
    """
    ops = list(OPS)
    return {a: {b: (PARALLEL if a == b else relation(a, b)) for b in ops} for a in ops}


def handoff_broken(a: str, b: str) -> bool:
    """链上先 a 后 b 时，b 会不会拿不到 a 的产物（= UI 上要挂 `⇥`）。

    与 `relation()` 分开，因为 `⇥` 与"有没有顺序约束"是两个维度：
    `probe → peaks` 既没有顺序约束（∥），又确实喂不到东西（⇥）。
    """
    if a == b:
        return False
    return contract.touches_input(b) and not compatible(a, b)


# ---------------------------------------------------------------- 抽样锚点
#
# §7.1 要求把这几条**写死成断言**：改矩阵时若变了，测试会问"你是故意的吗"。
# 放在这里而不是测试文件里，是为了让"有意变更"必须改这个具名常量。

ANCHORS: dict[tuple[str, str], str] = {
    # 两个纯读者（都只写 outputs/）→ 能并行，但产物接不上：normalize 读的是原文件。
    # ⚠ 这一格**只表示"能并行"**；"接不上"这件事由 `availability` 那一层去说
    #   （链上标 ⇥），矩阵不再重复表达同一个意思 —— 否则同一个事实有两个来源。
    ("convert", "normalize"): PARALLEL,
    ("normalize", "convert"): PARALLEL,
    # rename 读标签，tag_edit 写标签 —— 先写者在前，后来者必须等
    ("tags", "rename"): ORDERED,
    ("rename", "tags"): ORDERED,
    # ⚠ 反过来不成立：probe 什么都没改，tags 等它毫无意义
    ("probe", "tags"): BROKEN,
    ("tags", "probe"): ORDERED,
    # 两个纯读者，怎么排都对
    ("peaks", "verify"): PARALLEL,
    # 提取封面交出的是**图片**，而转换要的是音频 → 转换会回到原文件。
    # （"能并行"和"接得上"是两件事：这里两者互不干扰，但产物喂不过去。）
    ("extract-cover", "convert"): BROKEN,
    ("extract-cover", "peaks"): BROKEN,
    # 都原地改封面 → 换顺序也救不了
    ("cover", "remove-cover"): EXCLUSIVE,
    # zip 是"收全部文件"的汇总类：它不吃上一环，所以上一个步骤产出什么都不算断链
    ("waveform", "zip"): PARALLEL,
    ("verify", "zip"): PARALLEL,
    # 但波形图喂不给"要音频"的步骤
    ("waveform", "tags"): BROKEN,
    # 就地改写之后再转换：有序，而且接得上（in_place 续点，§3.1.3）
    ("tags", "convert"): ORDERED,
}

# ---------------------------------------------------------------- 卡片可用性（§3.2.4）

def _hands_something_off(tail: str) -> bool:
    """上一步交出的东西能不能续给下一环。

    `produce='derived'`（产出新文件）与 `in_place'`（改动落在同一个 file_id 上）
    都能续；`none`（只读分析，什么都没留）与 `sidecar`（交出旁路文件，
    下一环照旧读原音频）都算"没交东西"。

    **这是 `gives='none'` 容易踩的坑**：它字面上像"没有可用产物"，但它真正的
    含义是"**没动那个音频文件**"—— 所以下一环照旧读原文件就行，绝不是"链到此为止"。
    `verify` / `probe` / `peaks` / `loudness` 也全是 `gives='none'`。
    """
    return contract.produces(tail) not in NO_HANDOFF


def compatible(tail: str, op: str) -> bool:
    """链尾交出/留下的东西，`op` 用得上吗（"要不要在链上挂回落提示"的判据）。

    **用得上**的情形：
      · `tail` 交出的类型 `op` 接得住（如 `zip` 接图片）
      · `tail` 产出了可续的东西（`derived`）—— 它把处理结果传下去了
      · `tail` 是**就地改写**（`in_place`）—— 改动已经落在同一个 file_id 上
      · `op` 本来就不吃上一环（`consumes=False`：`校验` 判定、`打包` 收集）——
        它不指望上一环递东西，所以"没递"不算问题
    其余（`none` / `sidecar`，且下一环确实在等一份音频）都算用不上。

    注意它与矩阵的 `⇥` 是**同一件事的两种视图**：矩阵给一对 op，
    这里给"链尾 + 下一张卡"。两处判据同源，所以不会互相打架。
    """
    if not contract.consumes_upstream(op):
        return True
    if contract.accepts(contract.of(tail)["gives"], op):
        return True
    return _hands_something_off(tail)


def availability(tail: str | None, op: str) -> tuple[bool, str]:
    """链尾是 `tail` 时，`op` 能不能选。→ `(可选?, 原因 / 提示文案)`。

    `tail=None` 表示链为空（`op` 要当第一张）。
    纯函数：前端从 `GET /api/ops` 拿同一份字段自己算，后端在建链时用同一函数兜底 ——
    写在浏览器里而不在后端兜底 = 绕过前端就绕过约束。

    三种状态（§3.2.4），**判据按这个顺序判，顺序本身就是语义**：
      1. 链尾交出的是**压缩包**（`gives='archive'`）→ **可选 + 提示**：
         压缩包不会被递给下一步，但 `zip` 什么都没改动（`mode=read`），
         后面的步骤照旧作用于当前文件；而且一条链本来就可以有多个打包步骤
      2. `tail` 留下的是原文件，而 `op` 要的正好是另一种大类（音频↔图片）
         → **可选但强提示**：用户很可能就是想"先看一眼波形/响度图，再处理音频"，
         禁用它等于把一条正常用法判成错误
      3. `tail` 留下的是原文件，大类相同（如 `校验 → 转换`）→ **可选 + 回落提示**
      4. 其余 → 可选，无提示

    ⚠ **这里不再有"硬禁"**（原来只有 `archive` 一条）。那条规则的前提是
    "ZIP 必须在链尾"，而打包语义已经改了：一个打包步骤收集的是**它自己那个窗口**
    里的产物（上一个打包步骤之后、它之前），所以 `转 FLAC → 打包 → 转 MP3 → 打包`
    是合法且有用的链。详见 `执行链打包与串行交接方案.md` §4。

    ⚠ 「有没有任何 op 吃这个类型」**不能**当硬禁判据：`提取封面 → 格式转换` 里
    convert 确实吃不下图片，但用户可以无视那张图、继续处理原文件。
    """
    if op not in OPS or op not in contract.CONTRACT:
        return False, f"未知的操作：{op}"
    label = OPS[op]["label"]

    # 链为空：能不能当第一张（needs 里有 upstream 的不能）
    if tail is None:
        if not contract.first_ok(op):
            return False, f"「{label}」需要一个上游产物作为输入，不能排在最前"
        return True, ""

    if tail not in OPS or tail not in contract.CONTRACT:
        return False, f"链尾是未知操作：{tail}"

    tail_label = OPS[tail]["label"]
    give = contract.of(tail)["gives"]
    give_cn = contract.GIVE_LABEL.get(give, give)
    needs = contract.needs_of(op)
    needs_cn = "/".join(contract.GIVE_LABEL.get(n, n) for n in needs)

    # 1) 压缩包：可选，但必须说清"它不会被递下去"
    if give == "archive":
        return True, (f"「{tail_label}」交出的是压缩包，它不会被递给下一步 —— "
                      f"后面的步骤仍然作用于当前文件")

    # 已经能续下去（上一步把处理结果交出来了）→ 无需提示
    if compatible(tail, op):
        return True, ""

    # 2) 大类错配：上一步交的是图片，这一步要的是音频 → 允许，但必须说清
    if give in ("audio", "image") and give not in needs:
        return True, (f"「{tail_label}」交出的是{give_cn}，"
                      f"「{label}」要的是{needs_cn}，这一步会回到原文件")
    # 3) 大类相同，只是上一步没把处理结果传下来
    return True, (f"「{tail_label}」不产出{needs_cn}，"
                  f"「{label}」这一步会回到原文件")


def unknown_reasons(tail: str | None, ops: list[str]) -> dict[str, str]:
    """给前端一次算一批：`{op: 原因}`，其中**只有不可选的**会被列出来。

    可选的但带提示的**不进这个字典**（它们不该被置灰）——
    提示文案由 `/api/ops` 的字段在前端另算，或者直接调 `availability` 拿第二项。
    """
    out: dict[str, str] = {}
    for op in ops:
        ok, why = availability(tail, op)
        if not ok:
            out[op] = why
    return out


# ---------------------------------------------------------------- CLI

def _dump_matrix() -> str:
    """把矩阵渲染成 markdown（粘进文档前请确认测试也是绿的）。"""
    ops = list(OPS)
    head = "| ↓先 \\ →后 | " + " | ".join(ops) + " |"
    sep = "|" + "---|" * (len(ops) + 1)
    rows = [head, sep]
    for a in ops:
        cells = " | ".join(relation(a, b) for b in ops)
        rows.append(f"| **{a}** | {cells} |")
    return "\n".join(rows)


if __name__ == "__main__":       # pragma: no cover
    import sys
    if "--dump-matrix" in sys.argv:
        print(_dump_matrix())
    elif "--dump-availability" in sys.argv:
        from backend.cards.boundary import unknown_reasons as _u
        ops = list(OPS)
        for tail in [None, *ops]:
            print(f"--- 链尾 = {tail} ---")
            for op in ops:
                ok, why = availability(tail, op)
                if not ok:
                    print(f"  ⛔ {op:<16} {why}")
                elif why:
                    print(f"  ⚠  {op:<16} {why}")
    else:
        print("用法: python -m backend.cards.boundary --dump-matrix | --dump-availability")
