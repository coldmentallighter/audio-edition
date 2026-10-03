"""卡片校验与命令预览。

validate_card 是**唯一**的入口校验：卡片来自请求体，不能假定它守规矩。
render_preview 交给后端渲染，保证"预览的命令"和"真正执行的命令"同源。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from backend.cards.builtin import CARD_CATS, CARD_ICONS
from backend.cards.specs import OPS
from backend.tasks import FORMAT_ARGS

# ---------------------------------------------------------------- 校验

def _applies(spec: dict, params: dict) -> bool:
    """参数的 onlyIf 条件是否成立（例如 compressionLevel 只在 format=flac 时有意义）。"""
    cond = spec.get("onlyIf")
    if not cond:
        return True
    cur = params.get(cond["key"])
    return ("" if cur is None else str(cur)) in [str(v) for v in cond["in"]]


def _check_value(spec: dict, value: Any) -> Any:
    """按规格把值收敛/校验；不合法直接抛 ValueError（带人能看懂的原因）。"""
    key, label, typ = spec["key"], spec["label"], spec["type"]

    if typ == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    if typ == "int":
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"「{label}」必须是整数，收到 {value!r}") from None
        if "min" in spec and n < spec["min"]:
            raise ValueError(f"「{label}」不能小于 {spec['min']}，收到 {n}")
        if "max" in spec and n > spec["max"]:
            raise ValueError(f"「{label}」不能大于 {spec['max']}，收到 {n}")
        return n

    if typ == "float":
        try:
            x = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"「{label}」必须是数字，收到 {value!r}") from None
        if "min" in spec and x < spec["min"]:
            raise ValueError(f"「{label}」不能小于 {spec['min']}，收到 {x}")
        if "max" in spec and x > spec["max"]:
            raise ValueError(f"「{label}」不能大于 {spec['max']}，收到 {x}")
        return x

    if typ == "enum":
        allowed = [o["value"] for o in spec.get("options", [])]
        s = "" if value is None else str(value)
        if s not in allowed:
            raise ValueError(f"「{label}」只能是 {allowed} 之一，收到 {value!r}")
        return s

    if typ == "tags":
        if value in (None, ""):
            return {}
        if isinstance(value, str):
            out: dict[str, str] = {}
            for line in value.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    raise ValueError(f"标签行必须是 key=value：{line!r}")
                k, _, v = line.partition("=")
                k = k.strip()
                if not k:
                    raise ValueError(f"标签名为空：{line!r}")
                out[k] = v.strip()
            return out
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items()}
        raise ValueError("标签必须是对象或 key=value 文本")

    # text
    s = "" if value is None else str(value)
    if spec.get("required") and not s.strip():
        raise ValueError(f"「{label}」不能为空")
    if len(s) > 500:
        raise ValueError(f"「{label}」太长了（上限 500 字符）")
    return s


def validate_card(card: dict, *, existing_ids: set[str] | None = None,
                  taken_names: set[str] | None = None,
                  builtin_names: set[str] = frozenset()) -> dict:
    """校验并规范化一张卡片，返回干净的可存储对象。

    `taken_names` 是**库里已经用掉的名字**（内置 + 自定义），由 `cards.store`
    算好传进来（它才知道自己那一份 `cards.json` 里有什么）；`builtin_names`
    只是为了让报错能说清"撞的是内置卡还是自定义卡"。两者缺省都不查重名 ——
    `validate_card` 也用在"只看单张卡合不合法"的场景（导入预检、冒烟测试），
    那些地方没有库上下文。
    """
    if not isinstance(card, dict):
        raise ValueError("卡片必须是对象")

    name = str(card.get("name") or "").strip()
    if not name:
        raise ValueError("卡片名称不能为空")
    if len(name) > 40:
        raise ValueError("卡片名称最长 40 字")

    # 卡片名必须**全局唯一**（内置 + 自定义）。
    #
    # 这个看着像"体验"问题，其实是硬约束：卡片库整套 UI 是**按名字**认卡的
    # （`cardHTML` 发的是 `data-card="${c.name}"`、`CARDS.find(c => c.name === …)`、
    # 快照条存的也是名字）。于是与内置卡同名的自定义卡在 `find` 里永远输给排在前面的
    # 内置卡（`all_cards()` = builtin + custom）—— 用户看到的是"我新建的卡被识别成
    # 内置卡"，点开只有「查看 / 另存为…」，既改不了也删不掉（实测）。
    # 更隐蔽的是同一个名字在不同代码路径解析到**不同的卡**：`find` 取第一个，
    # 而 `applyServerCards` 里 `new Map(CARDS.map(c => [c.name, c]))` 同名时后者覆盖前者。
    if taken_names and name in taken_names:
        kind = "内置卡片" if name in builtin_names else "自定义卡片"
        raise ValueError(
            f"已经有一张叫「{name}」的{kind}了，卡片名不能重复 —— "
            f"卡片库是按名字找卡的，重名会让其中一张点不开。请换个名字。")

    op = str(card.get("op") or "").strip()
    if op not in OPS:
        raise ValueError(f"未知的操作：{op!r}；可用：{', '.join(OPS)}")

    cat = str(card.get("cat") or OPS[op]["category"]).strip()
    if cat not in CARD_CATS:
        # 原来是静默回落成"自定义"：用户导入卡片时写错分类名不报错，
        # 卡片会莫名其妙跑到「自定义」段里，很难查。
        raise ValueError(f"未知分类：{cat!r}；可用：{', '.join(CARD_CATS)}")

    ico = str(card.get("ico") or OPS[op]["icon"]).strip()
    if ico not in CARD_ICONS:
        ico = OPS[op]["icon"]

    tier = str(card.get("tier") or "自定义").strip()[:8]
    desc = str(card.get("desc") or OPS[op]["desc"]).strip()[:120]

    raw_params = card.get("params") or {}
    if not isinstance(raw_params, dict):
        raise ValueError("params 必须是对象")

    specs = {p["key"]: p for p in OPS[op]["params"]}
    unknown = [k for k in raw_params if k not in specs]
    if unknown:
        raise ValueError(f"「{OPS[op]['label']}」不认识这些参数：{', '.join(unknown)}")

    params: dict[str, Any] = {}
    for key, spec in specs.items():
        if not _applies(spec, raw_params):
            continue
        if key in raw_params:
            value = raw_params[key]
            # **空字符串 = 未设置**。数值/枚举型参数允许"留空 = 自动"
            # （`loudness-image.highLufs` 就是：留空取 Integrated + LRA/2），
            # 而前端会把空串原样回传 —— 不特判就会被 `float("")` 判成
            # "必须是数字"而报错，用户看到的是一条莫名其妙的校验失败。
            # `text` 型不受影响（空文本是合法值）；`bool` 的 `""` 有意义（= False）。
            if (isinstance(value, str) and not value.strip()
                    and spec["type"] in ("int", "float", "enum")):
                continue
            params[key] = _check_value(spec, value)
        elif "default" in spec:
            params[key] = spec["default"]
    # 必填但没给值的，让这里统一报错
    for key, spec in specs.items():
        if not _applies(spec, params):
            continue
        if spec.get("required") and not str(params.get(key) or "").strip():
            raise ValueError(f"「{spec['label']}」是必填项")

    cid = str(card.get("id") or "").strip()
    if not cid:
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24] or "card"
        # 用 md5 而不是内置 hash()：hash 对 str 每进程随机化，
        # 同一张卡两次生成的 id 会不一样
        digest = hashlib.md5(f"{op}|{name}".encode("utf-8")).hexdigest()[:6]
        cid = f"c_{slug}_{digest}"
    if existing_ids and cid in existing_ids:
        raise ValueError(f"卡片 id 已存在：{cid}")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", cid):
        raise ValueError(f"卡片 id 只能含字母数字下划线连字符：{cid!r}")

    out = {"id": cid, "cat": cat, "name": name, "desc": desc, "tier": tier,
           "ico": ico, "op": op, "params": params, "custom": True}
    note = str(card.get("note") or "").strip()[:300]
    if note:
        out["note"] = note
    return out


# ---------------------------------------------------------------- 执行链步骤

def validate_step(raw: Any, *, idx: int = 0) -> dict:
    """校验并规范化**执行链上的一步**，返回干净的可存储对象。

    与 `validate_card` 的关键区别：**这里不碰卡片名唯一性，也不生成卡片 id**。
    链上的步骤是"一张卡的**快照**"（§9.1.1 二）—— 卡片可以被改名、被删，
    而这一步必须照旧能跑，所以它自包含 `op` + `params`。

    `name` **允许指向已不存在的卡片**（预设跨机器、或用户删过卡），
    这正是"快照"存在的意义，所以这里不校验它。
    """
    if not isinstance(raw, dict):
        raise ValueError("每一步必须是一个对象")

    op = str(raw.get("op") or "").strip()
    if op not in OPS:
        raise ValueError(f"未知的操作：{op!r}")

    spec = OPS[op]
    raw_params = raw.get("params")
    if raw_params is None:
        raw_params = {}
    if not isinstance(raw_params, dict):
        raise ValueError("params 必须是对象")

    specs = {p["key"]: p for p in spec["params"]}
    # 允许 `_` 开头的服务端内部键留在快照里吗？**不允许** —— 它们是路由注入的
    # （`routers/ops.py` 的 `_op`），留在预设里等于让用户能伪造服务端状态。
    unknown = [k for k in raw_params if k not in specs and not str(k).startswith("_")]
    if unknown:
        raise ValueError(f"「{spec['label']}」不认识这些参数：{', '.join(unknown)}")

    params: dict[str, Any] = {}
    for key, pspec in specs.items():
        if not _applies(pspec, raw_params):
            continue
        if key in raw_params:
            params[key] = _check_value(pspec, raw_params[key])
        elif "default" in pspec:
            params[key] = pspec["default"]
    for key, pspec in specs.items():
        if not _applies(pspec, params):
            continue
        if pspec.get("required") and not str(params.get(key) or "").strip():
            raise ValueError(f"「{pspec['label']}」是必填项")

    out: dict[str, Any] = {
        "name": str(raw.get("name") or spec["label"]).strip()[:60],
        "op": op,
        "params": params,
        # `ico` 进快照：卡片删了之后链上还要显示图标（§9.1.1 二）
        "ico": str(raw.get("ico") or spec["icon"]).strip()[:32],
    }
    # `cardId` / `custom` 决定"这一步是不是来自一张不在库里的卡"（§9.1.2 一），
    # 所以要保留；缺省按内置卡处理（内置卡永远在库里）。
    cid = str(raw.get("cardId") or "").strip()
    if cid:
        out["cardId"] = cid
    if raw.get("custom"):
        out["custom"] = True
    return out


def validate_steps(raw: Any, *, where: str = "") -> tuple[list[dict], list[str]]:
    """校验一串步骤 → `(干净的步骤, 逐条原因)`。

    **非法项不静默吞掉**（§3.8.2 七）：每一条失败都带上"第几步、哪张卡、为什么"，
    调用方据此决定是拒绝保存（保存路径）还是跳过该项（还原路径）。
    """
    if not isinstance(raw, list):
        raise ValueError("steps 必须是数组")
    if not raw:
        raise ValueError("执行链是空的，没有可保存的步骤")
    good: list[dict] = []
    why: list[str] = []
    for i, item in enumerate(raw):
        try:
            good.append(validate_step(item, idx=i))
        except ValueError as e:
            label = ""
            if isinstance(item, dict):
                label = str(item.get("name") or item.get("op") or "").strip()
            head = f"{where} 的第 {i + 1} 步" if where else f"第 {i + 1} 步"
            if label:
                head += f"「{label}」"
            why.append(f"{head}参数非法：{e}（该项已跳过）")
    return good, why


def render_preview(op: str, params: dict) -> str:
    """把参数代进模板，给用户看"这张卡等价于什么命令"。"""
    spec = OPS.get(op)
    if not spec:
        return ""
    tpl = spec.get("preview", "")

    # convert 的 {codec}/{extra} 不是参数名，必须在通用替换之前先展开，
    # 否则会被当成"未知占位符"替换成空串。
    if op == "convert":
        fmt = str(params.get("format") or "flac")
        codec = " ".join(FORMAT_ARGS.get(fmt, []))
        extra: list[str] = []
        if fmt == "flac" and params.get("compressionLevel") not in (None, ""):
            extra += ["-compression_level", str(params["compressionLevel"])]
        if params.get("bitrate"):
            extra += ["-b:a", str(params["bitrate"])]
        if params.get("sampleRate"):
            extra += ["-ar", str(params["sampleRate"])]
        if params.get("channels"):
            extra += ["-ac", str(params["channels"])]
        if params.get("keepTags", True):
            extra += ["-map_metadata", "0"]
        extra += ["-map", "0" if params.get("keepCover", True) else "0:a"]
        tpl = tpl.replace("{codec}", codec).replace("{extra}", " ".join(extra))

    def sub(m):
        k = m.group(1)
        v = params.get(k)
        return "" if v in (None, "") else str(v)

    out = re.sub(r"\{([a-zA-Z_]+)\}", sub, tpl)

    if op == "tag_edit":
        tags = params.get("tags") or {}
        if isinstance(tags, str):
            tags = {}
        bits = " ".join(f"--set-tag={k.upper()}={v}" for k, v in tags.items())
        out = out.replace("--set-tag=TITLE=... --remove-tag=...", bits or "--set-tag=KEY=VALUE")
    if op == "cover_embed":
        out = out.replace("<图片>", str(params.get("imagePath") or "<图片>"))

    return re.sub(r"\s{2,}", " ", out).strip()


