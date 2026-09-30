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


def validate_card(card: dict, *, existing_ids: set[str] | None = None) -> dict:
    """校验并规范化一张卡片，返回干净的可存储对象。"""
    if not isinstance(card, dict):
        raise ValueError("卡片必须是对象")

    name = str(card.get("name") or "").strip()
    if not name:
        raise ValueError("卡片名称不能为空")
    if len(name) > 40:
        raise ValueError("卡片名称最长 40 字")

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
            params[key] = _check_value(spec, raw_params[key])
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


