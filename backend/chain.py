"""执行链的建链：把一条链一次性地建成任务（方案 §3.2 / §3.2.2 / §3.2.4）。

为什么不复用 `submit_batch`：那是一次一批、批与批之间互不相干（这正是
"链接不起来"的根源，§1.1）。建链必须**一次建完**，因为：

  1. `src_task_id` 要指向"**同一文件的上一步任务**"，那需要先知道每一步的任务 id；
  2. `serial` 档的因果边必须在建链时就写死（§9.6：提交时绑定，不做运行时解算）；
  3. 链的整体状态（每步/每文件的进度）要有 `chain_id` 才查得到（§3.7）。
"""
from __future__ import annotations

import re
from typing import Any, Callable

from backend import config, store
from backend.cards import boundary, contract
from backend.cards.specs import OPS, task_params
from backend.formats import classify, is_lossless, is_lossy
from backend.queue import new_batch_id

# 链的模式（方案 §3.1.2）。**只有两档** —— 原来的"分段"档已删：
# 它唯一的卖点是"每步都回到原文件"，而那正是要修的 bug。
MODE_SERIAL = "serial"
MODE_PARALLEL = "parallel"
MODES = (MODE_SERIAL, MODE_PARALLEL)
DEFAULT_MODE = MODE_PARALLEL       # 不动存量行为（README 里那批已实测的回归都基于它）

# `zip` 这类"一次吃全部文件"的 op：只建**一条**任务，不 per-file 建。
# 判据用 `needs` 里有没有 `none`（"我收下所有东西，包括没产出文件的那些"），
# 而不是写死 op 名 —— 将来加"解压"之类的汇总类 op 时不用改这里。
def _is_aggregate(op: str) -> bool:
    return "none" in contract.needs_of(op)


class ChainError(ValueError):
    """建链被拒。`step_idx` 指出是第几步，前端据此把链上那一步标红。"""

    def __init__(self, message: str, step_idx: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.step_idx = step_idx


def _source_format(files: list) -> str | None:
    """这批源文件的容器名（取第一个；它们同后缀时才有意义）。

    ⚠ 作用域里混着**不同格式**的文件时，这条规则只能按"最常见/第一个"来判 ——
    也就是说它是**尽力而为**的：真正的判定在运行时（每个文件各自的格式流）。
    这里只用它挡住"用户明显会踩"的组合：一个 FLAC 源 + `转 MP3 → 转 FLAC`。

    拿不到后缀就返回 `None`（未知 → `check_format_flow` 不判，宁可放行不误拦）。
    """
    for f in files:
        suf = str(f.rel_path or "").rsplit(".", 1)
        if len(suf) == 2 and suf[1]:
            return suf[1].lower()
    return None


def _op_of_step(step: dict) -> str:
    """取这一步的 op。

    卡片被删过时前端仍会带 `op`（快照里有），所以**不接受"只给 cardId"** ——
    那会让"删了卡就跑不了"重新回来（§9.1.1 二）。
    """
    op = str(step.get("op") or "").strip()
    if not op:
        raise ChainError("这一步没有 op：卡片需要一个后端操作才能进链")
    return op


# ---------------------------------------------------------------- 格式流与"有损→无损"串行规则
#
# 需求：**串行模式下，不允许把有损格式转成无损格式** —— 那不是"升级"，
# 而是把一个已经丢了信息的文件包装成"无损"，是误导（听起来更专业、实际更差）。
#
# 关键点有三个，都不显然：
#   ① 判据是**目标格式**，不是源格式。链的**第一项**可能只是"把源文件转成 MP3"，
#      而源文件在 uploads/ 里可以是任何格式 —— 那条链不该被拦
#      （挡住它等于"用这个工具把音乐转成 MP3"都做不到）。
#   ② 判的是"**上一步产出的格式**"，不是"用户导入时的格式"：
#      `转 MP3 → 转 FLAC` 里第 2 步面对的就是一个 MP3（哪怕源文件是 FLAC）。
#   ③ 容器沿用型步骤（`标准化`/`改标签`/`嵌封面`/`按标签重命名`）不改变格式，
#      所以格式流要**穿过**它们继续往后传 —— 否则
#      `转 FLAC → 改标签 → 转 WAV` 会被误判。

def format_token(fmt: str | None) -> str:
    """容器名 → 有损/无损的类型 token。未知返回 `"?"`（**不猜**）。"""
    return classify(fmt) or "?"


def step_output_format(op: str, params: dict, incoming: str | None) -> str | None:
    """这一步跑完之后，文件是什么容器格式。→ `"mp3"` / `"flac"` / `None`（非音频或未知）。

    `incoming` 是这一步**输入端**的容器格式。
    """
    mode = contract.gives_formats(op)
    if mode == "same":
        return incoming                       # 只读 / 就地改写：格式不变
    if mode == "param:format":
        return str((params or {}).get("format") or incoming or "").lower() or None
    return None                               # image / archive：不是音频，不参与格式流


# ---------------------------------------------------------------- 码率 / 采样率

# 有损格式的"码率"参数（kbps）。前端卡片写的是 `128k` 这种带 k 的字符串。
_BITRATE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[kK]?\s*$")


def parse_bitrate_kbps(v: Any) -> float | None:
    """`"320k"` / `"320"` / `320` → `320.0`（kbps）。认不出返回 `None`（**不猜**）。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    m = _BITRATE_RE.match(str(v))
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    return n if n > 0 else None


def _step_bitrate_kbps(op: str, params: dict, incoming_kbps: float | None
                       ) -> float | None:
    """这一步跑完之后的有效码率（kbps）。

    `convert` 给了 `bitrate` 就用它；**没给就沿用输入的码率** ——
    这一点很重要：`转 MP3（不写码率）` 面对一个 128k 的 MP3 时，
    ffmpeg 会用 libmp3lame 的默认值，但那不是"用户显式要求升码率"，
    按"未知"处理更安全（宁可漏拦也不误拦，见 check_format_flow 的说明）。
    """
    if contract.gives_formats(op) != "param:format":
        return incoming_kbps
    return parse_bitrate_kbps((params or {}).get("bitrate"))


def _step_sample_rate(op: str, params: dict, incoming_hz: int | None) -> int | None:
    """这一步跑完之后的采样率（Hz）。只认显式的 `sampleRate` 参数。"""
    if contract.gives_formats(op) != "param:format":
        return incoming_hz
    raw = (params or {}).get("sampleRate")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


class SourceAudio:
    """这一批源文件的可见属性（用来给串行规则定基线）。

    ⚠ 作用域里混着不同参数的文件时，**只有一个值才认**，不一致就当未知。
    理由：几处不同的文件共用一个"源码率"必然是错的，而错的那一侧会**误拦**
    用户（例如一个 320k 和一个 128k 混着，按 128 判就会把本该放行的链拦下来）。
    """

    __slots__ = ("fmt", "kbps", "hz", "known")

    def __init__(self, fmt: str | None, kbps: float | None, hz: int | None,
                 known: bool = True) -> None:
        self.fmt = fmt
        self.kbps = kbps
        self.hz = hz
        self.known = known

    def __repr__(self) -> str:                   # pragma: no cover - 调试用
        return (f"SourceAudio(fmt={self.fmt!r}, kbps={self.kbps!r}, "
                f"hz={self.hz!r}, known={self.known})")


def source_audio(files: list) -> SourceAudio:
    """从库里那几行文件的 `info` 里取源属性。

    **不重新探测**：`info` 是导入/`probe` 时已经解析好的
    （`audio.ProbeInfo.as_dict()` 给了 `bitRate` / `sampleRate`）。
    没解析过（`info` 为空）就当作未知 —— 不要为了判规则去跑 ffprobe，
    那是建链路径，不该做重活。
    """
    fmt: str | None = None
    kbps: float | None = None
    hz: int | None = None
    seen_kbps: set[float] = set()
    seen_hz: set[int] = set()
    for f in files:
        suf = str(getattr(f, "rel_path", "") or "").rsplit(".", 1)
        if len(suf) == 2 and suf[1] and fmt is None:
            fmt = suf[1].lower()
        info = getattr(f, "info", None) or {}
        br = info.get("bitRate")
        sr = info.get("sampleRate")
        if isinstance(br, (int, float)) and br > 0:
            seen_kbps.add(round(float(br) / 1000.0, 3))
        if isinstance(sr, (int, float)) and sr > 0:
            seen_hz.add(int(sr))
    if len(seen_kbps) == 1:
        kbps = next(iter(seen_kbps))
    if len(seen_hz) == 1:
        hz = next(iter(seen_hz))
    return SourceAudio(fmt, kbps, hz)


def check_format_flow(ops: list[dict], source_fmt: str | None,
                      source: SourceAudio | None = None) -> None:
    """按链序推一遍格式流，把"有损→无损"的步骤挑出来。被拒时抛 `ChainError`。

    `ops` 是 `[{"op":..., "params":...}, …]`；`source_fmt` 是**源文件**的容器
    （来自 `uploads/` 里那个文件的后缀；没有就传 `None`，表示未知）。
    抛错的 `step_idx` 指向**那一步本身**，前端据此把链上那一格标红。

    ⚠⚠ **只判第 1 步之后的步骤**（`i > 0`）—— 这条规则的标题是"串行模式下
    不允许有损→无损"，它管的是**链上的转码选择**，不是"用户能不能把他手上的
    MP3 转成 FLAC"：

      · `转 FLAC`（源是 MP3）        —— **放行**。这是"我想塞进无损容器"，
        单步操作，产物不会假装比源更保真
      · `转 MP3 → 转 FLAC`           —— **拦下**。第 2 步把一个已经是 MP3 的
        *产物*再转成 FLAC，那才是"包装成无损"
      · `标准化 → 转 FLAC`（源 MP3） —— **拦下**。标准化不改格式，
        所以第 2 步面对的仍是那个 MP3（格式流会穿过 `same` 型步骤）

    源格式只用来推"第 0 步产出什么"，**它本身不构成违规**。
    第一版连第 0 步一起判，结果"把 MP3 转成 FLAC"这种正常用法被拦掉了。

    ⚠ "未知就不猜"**只覆盖源文件本身**，不覆盖链内部已经写死的格式流：

      · `未知源 → 转 FLAC`            —— 放行（第 0 步不判，源是什么都行）
      · `未知源 → 转 MP3 → 转 FLAC`   —— **拦下**。第 1 步的产出由它自己的
        `format` 参数决定，所以第 2 步的输入"是 MP3"是确定的，跟源无关。

    只有"输入未知**且**产出未知"时才不判 —— **误拦比漏拦更烦人**
    （用户没法自己绕过）。

    ⚠ **这条规则判的是"产物流动"，不是"用户勾了哪个档"。** 并行档下第 i+1 步
    拿到的是**原文件**，根本不存在"上一步的有损产物"这回事，所以同一条链
    (`转 MP3 → 转 FLAC`) 在并行档是合法的、在串行档才被拦 —— 这不是规则漏到
    并行，而是**档位就是在回答"产物流不流"**。因此拒绝文案里必须给出"切成并行"
    这条出路，否则用户会以为这个组合本身非法而卡死。
    """
    src = source or SourceAudio(source_fmt, None, None)
    current = source_fmt
    cur_kbps = src.kbps
    cur_hz = src.hz
    for i, st in enumerate(ops):
        op = _op_of_step(st)
        params = st.get("params") or {}
        nxt = step_output_format(op, params, current)
        nxt_kbps = _step_bitrate_kbps(op, params, cur_kbps)
        nxt_hz = _step_sample_rate(op, params, cur_hz)
        if i > 0 and current and nxt and is_lossy(current) and is_lossless(nxt):
            raise ChainError(
                f"不允许把有损格式转成无损：「{OPS[op]['label']}」的输入是 "
                f"{current.upper()}（有损，来自上一步），目标是 {nxt.upper()}（无损）。"
                f"有损文件再编码成无损**不会恢复任何信息**，只是把文件变大。"
                f"（想保留原格式就别在这条链里先转成有损；"
                f"或者**切到并行档** —— 那样这一步吃的是原文件，不是这个 "
                f"{current.upper()} 产物）",
                i)

        # ---- 有损 → 有损，但**码率/采样率反而调高** ----
        #
        # 与"有损→无损"同族：都是"不会恢复信息，只是把文件变大"。
        # 判据只看**能确定的**数字：输入码率或目标码率有一个不知道就不判
        # （`bitrate` 没写、源文件没 probe 过 —— 猜错会误拦，而用户绕不过去）。
        #
        # ⚠ 这条**和上面那条不一样，第 0 步也要判**：上面拦的是"链内部把产物
        # 包装成无损"，而这条拦的是"把一个已经是 128k 的文件重新编码成 320k" ——
        # 源文件本身是有损的时候，第 0 步就已经在浪费体积了。
        # 反过来 `源 128k MP3 → 转 MP3 96k`（降码率）是常见需求，放行。
        if current and nxt and is_lossy(current) and is_lossy(nxt):
            if cur_kbps and nxt_kbps and nxt_kbps > cur_kbps:
                raise ChainError(
                    f"不允许把有损格式的码率**调高**：「{OPS[op]['label']}」会把 "
                    f"{current.upper()} {_kbps_cn(cur_kbps)} 重新编码成 "
                    f"{nxt.upper()} {_kbps_cn(nxt_kbps)}。"
                    f"有损格式的每一次重编码都会再丢一层信息，"
                    f"调高码率**不会找回**丢掉的部分，只是文件更大。"
                    f"（想减小体积就往低调；想要更好的音质只能用原始无损源，"
                    f"或者在**并行档**下让这一步直接作用于原文件）",
                    i)
            if cur_hz and nxt_hz and nxt_hz > cur_hz:
                raise ChainError(
                    f"不允许把有损格式的采样率**调高**：「{OPS[op]['label']}」会把 "
                    f"{current.upper()} {cur_hz} Hz 重新编码成 {nxt_hz} Hz。"
                    f"上采样**不会增加**任何信息，只是让文件变大。"
                    f"（想降采样就往低调；或者在**并行档**下让这一步直接作用于原文件）",
                    i)

        if nxt:
            current = nxt
        if nxt_kbps:
            cur_kbps = nxt_kbps
        if nxt_hz:
            cur_hz = nxt_hz
        # `nxt is None`（图片/压缩包这类非音频产物）：下一步会回落读当前那个
        # 音频文件（§2.3 的 ⇥），格式没变 —— 所以 `current` 保持不变继续往后传。
        # 这里**不要**把 current 清空：那会让
        # `转 MP3 → 导出波形 → 转 FLAC` 里的最后一步漏过检查。


def _kbps_cn(kbps: float) -> str:
    """`320.0` → `"320 kbps"`。

    **一律取整**：源文件的码率来自 ffprobe 的容器平均码率，实测一个
    "128k" 的 MP3 会报 `132.244 kbps`（含容器开销）。把小数原样丢给用户
    只会让人困惑（"我的文件不是 128k 吗"），取整后 132 与 320 的对比仍然成立。
    """
    return f"{round(kbps):d} kbps"


def validate_chain(steps: list[dict], mode: str) -> None:
    """建链前的整体校验。**后端必须自己再算一遍**（§3.2.4 的实现要求）：

    前端那层置灰是**体验**，不是边界 —— 绕过页面直接 POST 就能构造出
    `打包 ZIP → 改标签`，所以这里用同一个 `boundary.availability` 兜底。

    与前端唯一的区别：**带提示的（可选但不推荐）在这里放行**，
    因为它们是合法用法（"先看一眼波形再处理音频"），只是链上要挂个提示。
    """
    if mode not in MODES:
        raise ChainError(f"模式只能是 {list(MODES)}，收到 {mode!r}")
    if not steps:
        raise ChainError("执行链为空")
    if len(steps) > config.MAX_CHAIN_STEPS:
        raise ChainError(f"一条链最多 {config.MAX_CHAIN_STEPS} 步，收到 {len(steps)}")

    tail: str | None = None
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ChainError("每一步必须是一个对象", i)
        op = _op_of_step(step)
        if op not in OPS:
            raise ChainError(f"未知的操作：{op}", i)
        ok, why = boundary.availability(tail, op)
        if not ok:
            raise ChainError(why, i)
        params = step.get("params")
        if params is not None and not isinstance(params, dict):
            raise ChainError("params 必须是对象", i)
        tail = op


def build_chain(payload: dict) -> dict:
    """建链并入库。返回 `{chainId, mode, steps, total, taskIds}`。

    `payload`:
        mode     "serial" | "parallel"（缺省 parallel）
        fileIds  作用域（**所有步骤共用**；"每步不同 scope"明确不做）
        steps    [{cardId?, name?, op, params}, …]，顺序即语义
    """
    mode = str(payload.get("mode") or DEFAULT_MODE).strip().lower()
    steps = payload.get("steps") or []
    if not isinstance(steps, list):
        raise ChainError("steps 必须是数组")
    validate_chain(steps, mode)

    ids = [str(x) for x in (payload.get("fileIds") or [])]
    files = [store.get_file(i) for i in ids]
    files = [f for f in files if f and f.state != "deleted"]
    if not files:
        raise ChainError("没有可操作的文件")
    if len(files) > config.MAX_BATCH_FILES:
        raise ChainError(f"单次最多 {config.MAX_BATCH_FILES} 个文件")

    # ---- 格式流检查（**只在串行档**）----
    # 串行档下第 i+1 步吃第 i 步的产物，所以"上一步产出了什么格式"是有意义的；
    # 并行档不传递产物（各步都作用在原文件上），因此那条规则**不适用** ——
    # 并行档是存量行为，不许因为新规则而改变它的可用组合。
    if mode == MODE_SERIAL:
        check_format_flow(steps, _source_format(files), source_audio(files))

    chain_id = store.new_id("ch_")
    per_step: list[dict[str, Any]] = []
    all_ids: list[str] = []
    n_steps = len(steps)
    # 每个文件"上一步的任务 id" —— 串行档靠它连因果边
    prev_of_file: dict[str, str] = {}

    for idx, step in enumerate(steps):
        op = _op_of_step(step)
        spec = OPS[op]
        type_ = str(spec.get("task") or op)
        # ⚠ 必须走 `task_params`，**不能**直接拷贝 step 里的 params：共用任务类型的
        # op 家族（响度三兄弟）靠服务端注入的 `_op` 分流，少了它
        # `loudness-report` / `loudness-image` 会退化成只算一遍并写缓存的 `loudness` ——
        # 任务仍然 `success`、进度 100，却什么都不产出（用户实测报的"报告不生成"）。
        params = task_params(op, step.get("params"))
        step_id = store.new_id("s_")
        batch_id = new_batch_id()
        made: list[tuple[str, str]] = []          # (file_id, task_id)

        if _is_aggregate(op):
            # 汇总类：只建一条任务，`params.fileIds` 带上全部文件。
            # 它**不吃上一环**（`consumes=False`），自己会去解算链上最终产物
            # （`h_zip` 的 `_zip_plan`），所以不需要 src_task_id。
            t = store.create_task(type_, params={**params, "fileIds": [f.id for f in files]},
                                  batch_id=batch_id, chain_id=chain_id,
                                  step_id=step_id, step_idx=idx, chain_steps=n_steps,
                                  chain_mode=mode)
            made.append(("", t.id))
        else:
            for f in files:
                src = prev_of_file.get(f.id) if mode == MODE_SERIAL else None
                t = store.create_task(type_, file_id=f.id, params=params,
                                      batch_id=batch_id, chain_id=chain_id,
                                      step_id=step_id, step_idx=idx,
                                      src_task_id=src, chain_steps=n_steps,
                                      chain_mode=mode)
                made.append((f.id, t.id))

        # 串行档：这一步每条任务就是**该文件**的"上一步"，供下一步连边
        prev_of_file = {fid: tid for fid, tid in made if fid}
        order = "按文件顺序" if mode == MODE_SERIAL else "各步独立"
        per_step.append({
            "stepIdx": idx,
            "stepId": step_id,
            "op": op,
            "name": str(step.get("name") or spec.get("label") or op),
            "type": type_,
            "batchId": batch_id,
            "taskIds": [tid for _, tid in made],
            "count": len(made),
            "aggregate": _is_aggregate(op),
        })
        all_ids.extend(tid for _, tid in made)

    # 全部建完之后一次入队：顺序稳定，日志读起来跟链一致
    from backend.queue import queue_ as _q
    for tid in all_ids:
        _q.submit(tid)

    return {
        "chainId": chain_id,
        "mode": mode,
        "steps": per_step,
        "taskIds": all_ids,
        "total": len(all_ids),
        # 前排提示：链上那些"能跑但接不上"的位置（§2.3 的 ⇥）
        "notes": chain_notes([s["op"] for s in per_step], mode),
    }


def chain_notes(ops: list[str], mode: str) -> list[dict]:
    """链上需要提示的位置。**只提示，不阻断**（§3.2.4）。

    两个判据，对应两种"你可能不是这个意思"：

      1. **并行档下的产物断链**（§2.3 的 `⇥`）：并行档不连因果边，
         所以第 i+1 步读的是**原文件**，不是第 i 步的产物。
         典型：`转 FLAC → 标准化` —— 用户以为标准化的是转出来的 FLAC，
         实际标准化的是原格式的音乐。**这条在串行档下自动消失**，
         所以文案里直接给"改成串行"这个出路。
      2. **写-写互斥**（`⤫`）：两步都改同一个文件且换顺序也救不了，
         只能说清"最终结果取决于执行顺序"。

    判据 1 用的是 `contract.produces(...) not in HANDOFF`，与 `_gate` 里
    "串行档才连边"是同一件事的两种视角 —— 串行档不提示，因为边真的连上了。
    """
    notes: list[dict] = []
    for i in range(len(ops) - 1):
        a, b = ops[i], ops[i + 1]
        why = ""
        if mode != MODE_SERIAL and _parallel_handoff_gap(a, b):
            la, lb = OPS[a]["label"], OPS[b]["label"]
            why = (f"并行档不传递产物：「{lb}」会作用在原文件上，"
                   f"不是「{la}」的产物（改成串行即可接上）")
        elif boundary.relation(a, b) == boundary.EXCLUSIVE:
            why = (f"「{OPS[a]['label']}」与「{OPS[b]['label']}」都会改这个文件，"
                   f"最终结果取决于执行顺序")
        if why:
            notes.append({"stepIdx": i, "text": why})
    return notes


def _parallel_handoff_gap(a: str, b: str) -> bool:
    """并行档下"第 i 步的产物喂不到第 i+1 步"，**而且这件事对用户有影响**吗。

    ⚠ **判据必须在"并行档"这个前提下成立** —— 第一版写成 mode-blind 的，
    结果 `转 FLAC → 标准化` 不提示了，而那恰恰是最该提示的一条。

    ⚠⚠ **第二版又错在"范围太宽"**（用户实测报的）：把
    `produce ∈ (sidecar, none)` 一律当断口，于是
    `导出波形 PNG → 响度分析报告` 也弹一条"会回到原文件"。
    那是**假警报**：只读分析/自绘图**根本没动那个音频文件**，
    下一步读到的本来就是完整原文件，**什么都没丢**。
    一条链上连着两张只读卡就刷一排警告，而每一条都在说一件不成问题的事 ——
    这种提示会训练用户忽略所有提示。

    所以判据只留"**真的丢了东西**"的两种：

      · **`a` 产出了新文件（`derived`）而并行档不会把它递下去** —— 这一条最要紧，
        也是最容易漏的：`转 FLAC → 标准化` 的类型完全相容（音频 → 要音频），
        所以任何"看类型对不对"的判据都会放它过去。但并行档下它是错的：
        标准化读的是**原文件**。用户的预期恰恰是"处理我刚转出来的那份"。
      · **`a` 交出的类型 `b` 真的要用（`consumes`），却接不住** ——
        例如 `导出波形 PNG → 批量改标签`（图片喂不给要音频的卡）。
        这时 `b` 拿到的也是原文件，而它要的东西确实没到手。

    反过来，**不再报警**的（都是"没丢东西"）：
      · `只读分析`（`produce=none`：探测/校验/响度总览图）→ 它什么都没留，下一步读原文件天经地义
      · `旁路产物`（`sidecar`）**而下一步并不需要那种文件** ——
        `导出波形 PNG → 响度分析报告`：报告要音频，源音频好好的在那儿
      · `打包 ZIP`（`consumes=False`）→ 它压根不吃上一环，`convert → zip` 不是断口

    ⚠ 不能靠 `contract.accepts(...)` 单判第一条：`accepts("audio", "normalize")`
    是 **True**（类型确实相容），而问题恰恰出在"类型相容但没人递"。
    """
    if not contract.consumes_upstream(b):
        return False                       # b 不吃上一环（校验判定 / 打包收集）
    # **唯一的断口：a 产出了新文件，而并行档不会把它递下去。**
    #
    # 其余一切（只读分析、旁路产物、`gives` 对不上）都**不报**，理由是
    # "用户并没有丢东西"：a 没动那个音频文件，或者 b 从源文件开始做也一样 ——
    # 两种情况下 b 拿到的都是完整原文件。之前拿 `accepts()` 兜底会把这些
    # 也判成断口（`none` 谁都不收、图片喂不给要音频的卡），于是
    # `探测 → 转换`、`波形 → 响度分析报告` 都刷警告，而它们各自都能正常跑完。
    return contract.produces(a) == "derived"
