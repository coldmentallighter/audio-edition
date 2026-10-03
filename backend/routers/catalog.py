"""操作目录（/api/ops）、卡片 CRUD、快照。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Body, HTTPException

from backend import cards as cards_mod, logs

router = APIRouter()

@router.get("/api/ops")
def get_ops() -> dict:
    """操作目录：每个 op 认哪些参数、取值范围、默认值、说明与等价命令模板。

    前端用它渲染「卡片编辑器」的参数表单，用户不用去翻源码猜参数。
    """
    return {
        "ops": [
            {"op": op, **{k: v for k, v in spec.items()}}
            for op, spec in cards_mod.OPS.items()
        ],
        "paramTypes": list(cards_mod.PARAM_TYPES),
        "icons": cards_mod.CARD_ICONS,
        "categories": cards_mod.CARD_CATS,
    }


@router.get("/api/cards")
def get_cards() -> dict:
    """内置 + 自定义卡片。custom=true 的那批可以编辑/删除。"""
    return {
        "cards": cards_mod.all_cards(),
        "categories": cards_mod.CARD_CATS,
        "snapshots": cards_mod.snapshots(),
        "builtinCount": len(cards_mod.BUILTIN_CARDS),
    }


@router.get("/api/snapshots")
def get_snapshots() -> dict:
    """抽屉顶部那排快照（卡片名列表）。"""
    return {"snapshots": cards_mod.snapshots(), "defaults": cards_mod.SNAPS_DEFAULT}


@router.put("/api/snapshots")
def put_snapshots(payload: dict = Body(...)) -> dict:
    """保存快照顺序/内容（拖拽替换后调用）。"""
    names = payload.get("snapshots")
    if not isinstance(names, list):
        raise HTTPException(400, "snapshots 必须是数组")
    try:
        clean = cards_mod.set_snapshots(names)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    logs.log(f"⇄ 快照已更新：{' / '.join(clean)}", kind="ok")
    return {"ok": True, "snapshots": clean}


@router.delete("/api/snapshots")
def reset_snapshots() -> dict:
    """恢复默认快照。"""
    return {"ok": True, "snapshots": cards_mod.reset_snapshots()}


# ================================================================ 预设（§3.8.2）
#
# 三个端点与 snapshots 同形（GET / POST / PUT / DELETE），前端照抄接线。
#
# 预设是**一条链的快照**，不是"一张卡"：
#   · `steps` 存 `{name, op, params, ico, cardId?, custom?}` —— 自包含，
#     卡片被改名/删除都不影响它（§9.1.1 二 / §3.8.2 二）
#   · `mode` 必须存：同一条链在串行/并行下结果完全不同（§3.8.2 二）
#   · `icons: null` = 自动推导，数组 = 用户手选（§3.8.2 三）

@router.get("/api/presets")
def get_presets() -> dict:
    """所有预设 + **下一条的默认名**。

    默认名由后端算（`next_preset_name`）而不是前端：前端算就要把
    "取 max+1 不复用空洞"这条规则再写一遍，而两处漂移的表现是
    "存了两条同名的预设"（后端会因为重名拒绝，用户只会看到一个 400）。
    """
    items = cards_mod.presets()
    return {
        "presets": items,
        "nextName": cards_mod.next_preset_name(items),
        "max": cards_mod.PRESET_MAX,
    }


@router.post("/api/presets")
def create_preset(payload: dict = Body(...)) -> dict:
    """新建预设。

    ⚠ **非法步骤在这里就拒掉**（400 + 逐条原因），不像"还原"那样跳过 ——
    保存是用户当下看着链点的，链上有非法步骤说明前端已经坏了，
    存进去只会让问题延后到某天点开预设时（§3.8.2 七）。
    """
    try:
        steps, why = cards_mod.validate_steps(payload.get("steps"),
                                              where=str(payload.get("name") or "预设"))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if why:
        raise HTTPException(400, "；".join(why))
    try:
        item = cards_mod.add_preset({
            "name": payload.get("name"),
            "desc": payload.get("desc"),
            "mode": payload.get("mode"),
            "steps": steps,
            "icons": payload.get("icons"),
        })
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    logs.log(f"✚ 保存预设「{item['name']}」· {len(item['steps'])} 步 · "
             f"{'串行' if item['mode'] == 'serial' else '并行'}", kind="ok")
    return {"ok": True, "preset": item}


@router.put("/api/presets/{pid}")
def update_preset(pid: str, payload: dict = Body(...)) -> dict:
    """改预设（重命名 / 改描述 / 改档位 / 改步骤 / 改图标）。"""
    patch: dict = {}
    for key in ("name", "desc", "mode", "icons"):
        if key in payload:
            patch[key] = payload[key]
    if "steps" in payload:
        try:
            steps, why = cards_mod.validate_steps(
                payload.get("steps"),
                where=str(payload.get("name") or "预设"))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        if why:
            raise HTTPException(400, "；".join(why))
        patch["steps"] = steps
    try:
        item = cards_mod.update_preset(pid, patch)
    except KeyError:
        raise HTTPException(404, f"预设不存在: {pid}") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    logs.log(f"✎ 修改预设「{item['name']}」", kind="ok")
    return {"ok": True, "preset": item}


@router.delete("/api/presets/{pid}")
def delete_preset(pid: str) -> dict:
    try:
        ok = cards_mod.remove_preset(pid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if not ok:
        raise HTTPException(404, f"预设不存在: {pid}")
    logs.log(f"🗑 删除预设 {pid}", kind="warn")
    return {"ok": True}


@router.delete("/api/presets")
def reset_presets() -> dict:
    """清空预设（测试用；**不动自定义卡片**）。"""
    n = cards_mod.reset_presets()
    return {"ok": True, "removed": n}


@router.get("/api/cards/ops/{op}/preview")
def preview_card(op: str, params: str = "{}") -> dict:
    """按参数渲染等价命令，给编辑器实时预览用。"""
    if op not in cards_mod.OPS:
        raise HTTPException(404, f"未知的操作: {op}")
    try:
        parsed = json.loads(params) if params else {}
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"params 不是合法 JSON: {e}") from None
    if not isinstance(parsed, dict):
        raise HTTPException(400, "params 必须是对象")
    return {"op": op, "command": cards_mod.render_preview(op, parsed)}


@router.post("/api/cards")
def create_card(payload: dict = Body(...)) -> dict:
    """新建自定义卡片。"""
    try:
        card = cards_mod.add(payload)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    logs.log(f"✚ 新建卡片「{card['name']}」· {card['op']}", kind="ok")
    return {"ok": True, "card": card}


@router.put("/api/cards/{cid}")
def update_card(cid: str, payload: dict = Body(...)) -> dict:
    """改自定义卡片。内置卡片不允许原地改（用「另存为新卡片」）。"""
    try:
        card = cards_mod.update(cid, payload)
    except KeyError:
        raise HTTPException(404, f"卡片不存在: {cid}") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    logs.log(f"✎ 修改卡片「{card['name']}」", kind="ok")
    return {"ok": True, "card": card}


@router.delete("/api/cards/{cid}")
def delete_card(cid: str) -> dict:
    try:
        ok = cards_mod.remove(cid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if not ok:
        raise HTTPException(404, f"卡片不存在: {cid}")
    logs.log(f"🗑 删除卡片 {cid}", kind="warn")
    return {"ok": True}


@router.get("/api/cards/export")
def export_cards() -> dict:
    """导出全部自定义卡片（可再导入）。"""
    return {"version": 1, "cards": cards_mod.custom()}


@router.post("/api/cards/import")
def import_cards(payload: dict = Body(...)) -> dict:
    """导入自定义卡片；同名/同 id 的直接跳过，不覆盖已有配置。"""
    items = payload.get("cards") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "cards 必须是非空数组")
    have = cards_mod.custom_ids() | {c["id"] for c in cards_mod.BUILTIN_CARDS}
    added, skipped = [], []
    for raw in items:
        try:
            card = cards_mod.validate_card(raw)
        except ValueError as e:
            skipped.append({"name": (raw or {}).get("name"), "reason": str(e)})
            continue
        if card["id"] in have:
            skipped.append({"name": card["name"], "reason": "id 已存在"})
            continue
        try:
            added.append(cards_mod.add(card))
            have.add(card["id"])
        except ValueError as e:
            skipped.append({"name": card["name"], "reason": str(e)})
    if added:
        logs.log(f"⇩ 导入卡片 {len(added)} 张", kind="ok")
    return {"ok": True, "added": added, "skipped": skipped,
            "count": len(added), "skippedCount": len(skipped)}


# ================================================================ 上传
