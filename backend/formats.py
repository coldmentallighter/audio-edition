"""音频格式的**事实源**：白名单、有损/无损分类、ffmpeg 编码参数。

为什么要单独一个模块：这些事实有**四个毫不相干的消费方**，
而它们分处不同层，直接互相 import 会成环：

  · `backend/tasks.py`       —— 编码参数白名单（`FORMAT_ARGS` 原来写在这里）
  · `backend/cards/specs.py` —— 卡片编辑器里"码率只对有损格式显示"的 `onlyIf`
  · `backend/cards/validate.py` —— 卡片校验要报"用的是哪个编码器"
  · `backend/chain.py`       —— **新增**的串行规则：有损→无损的转码要被挡住

所以放在这个不依赖任何人的小模块里，四边都 import 它。
（`tasks.py` 里保留 `FORMAT_ARGS` 这个名字只是给老代码用的别名，
内容由这里生成 —— 加格式只改这一处。）

------------------------------------------------------------------ 为什么 on-disk 要分开

`LOSSY_FORMATS` 与 `AUDIO_FORMATS` 是**两个不同的问题**，混起来写过一次，症状很隐蔽：

  · `AUDIO_FORMATS` —— "我们能转换到哪些容器"（白名单，用户的输入边界）
  · `LOSSY_FORMATS` —— "哪些容器转过去会丢信息"（物理事实）

早期版本把分类写成"不在 LOSSY 里的就是在 `FORMAT_ARGS` 里的，一律算无损"，
于是 `classify("bin")` 回答 `"lossless"` —— 一个**未知格式被静默当成无损**。
后果不是崩溃而是规则静默失效：链上 `神秘.bin → 转 MP3 → 转 FLAC` 会以
"bin 是无损"为前提放行。现在未知一律 `None`，调用方必须自己决定怎么办
（`chain.check_format_flow` 的选择是"不猜"）。
"""
from __future__ import annotations

# 我们认得的音频容器 = 用户能选的转换目标 = `FORMAT_ARGS` 的键。
# 加一个新的无损容器（比如 `alac`）只改这里 + 下面的 `FORMAT_ARGS`。
AUDIO_FORMATS: frozenset[str] = frozenset({
    "flac", "wav", "aiff",              # 无损
    "mp3", "aac", "m4a", "ogg", "opus", "wma",   # 有损
})

# 有损编码：解码再编码**不可逆**。转成这些格式会丢信息。
LOSSY_FORMATS: frozenset[str] = frozenset({
    "mp3", "aac", "m4a", "ogg", "opus", "wma",
})

# 无损 = 在我们认得的容器里、且不属于有损集合。**由上面两个集合推导**，
# 不手写第二份名单（手写就会出现"加了新容器忘了加进无损"的静默失效）。
LOSSLESS_FORMATS: frozenset[str] = AUDIO_FORMATS - LOSSY_FORMATS

# 目标格式 → ffmpeg 编码参数白名单（需求 §4.8：参数必须来自白名单）。
# `tasks.FORMAT_ARGS` 是这个 dict 的别名，别再抄一份。
FORMAT_ARGS: dict[str, list[str]] = {
    "flac": ["-c:a", "flac"],
    "wav":  ["-c:a", "pcm_s24le"],
    "mp3":  ["-c:a", "libmp3lame"],
    "aac":  ["-c:a", "aac"],
    "m4a":  ["-c:a", "aac"],
    "ogg":  ["-c:a", "libvorbis"],
    "opus": ["-c:a", "libopus"],
    "aiff": ["-c:a", "pcm_s16be"],
    "wma":  ["-c:a", "wmav2"],
}

# 给界面用的短标签（卡片置灰的文案要拼它）
CLASS_LABEL = {"lossy": "有损", "lossless": "无损"}


def clean(fmt: str | None) -> str:
    """归一化：去空白、去前导点、转小写。`".FLAC"` → `"flac"`，`None` → `""`。

    前导点必须去掉：`Path.suffix` 给的是 `".flac"`，而卡片 `format` 参数
    给的是 `"flac"`，两个来源都进这个模块，不归一化就会互相认不出。
    """
    return str(fmt or "").strip().lower().lstrip(".")


def classify(fmt: str | None) -> str | None:
    """`"mp3"` / `".MP3"` → `"lossy"`；`"flac"` → `"lossless"`；空/未知 → `None`。

    返回 `None` 表示"不知道"（格式是空的，或者不是我们认得的容器），
    调用方应当**当作未知处理**，而不是默认成无损 —— 默认成无损会让
    "有损→无损"这类规则对未知格式**静默放行**（见模块头那段说明）。
    """
    f = clean(fmt)
    if not f or f not in AUDIO_FORMATS:
        return None
    return "lossy" if f in LOSSY_FORMATS else "lossless"


def is_lossy(fmt: str | None) -> bool:
    return classify(fmt) == "lossy"


def is_lossless(fmt: str | None) -> bool:
    return classify(fmt) == "lossless"


def is_known(fmt: str | None) -> bool:
    """是不是我们认得的音频容器（`None`/空/`.bin` 都算不认得）。"""
    return classify(fmt) is not None
