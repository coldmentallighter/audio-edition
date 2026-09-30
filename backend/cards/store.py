"""自定义卡片与快照的持久化（cards.json）。

内置卡片是只读的，用户改出来的卡片全在这里。写入用"临时文件 + 原子替换"，
避免写一半把用户配置弄丢。启动时**不清空**这个文件（见 config.fresh_start）。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from backend import config
from backend.cards.builtin import BUILTIN_CARDS, SNAPS_DEFAULT
from backend.cards.validate import validate_card

CARDS_JSON = config.ROOT / "cards.json"

_lock = threading.Lock()

# ---------------------------------------------------------------- 存储

def _read_custom() -> list[dict]:
    if not CARDS_JSON.exists():
        return []
    try:
        data = json.loads(CARDS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("cards") if isinstance(data, dict) else data
    return [c for c in (items or []) if isinstance(c, dict)]


def _read_file() -> dict:
    """读整份配置（cards + snapshots）。"""
    if not CARDS_JSON.exists():
        return {"version": 1, "cards": [], "snapshots": []}
    try:
        data = json.loads(CARDS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "cards": [], "snapshots": []}
    if isinstance(data, list):                       # 兼容早期只有数组的格式
        return {"version": 1, "cards": data, "snapshots": []}
    data.setdefault("cards", [])
    data.setdefault("snapshots", [])
    data.setdefault("version", 1)
    return data


def _write_file(payload: dict) -> None:
    tmp = CARDS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CARDS_JSON)          # 原子替换，别写一半把用户配置弄丢


def _write_custom(items: list[dict]) -> None:
    data = _read_file()
    data["cards"] = items
    _write_file(data)


# ---------------------------------------------------------------- 快照

SNAP_COUNT = 5


def snapshots() -> list[str]:
    """抽屉顶部那排快照。用户拖拽改过就用他的，否则用默认。

    要过滤掉已经不存在的卡片名：卡片是能被删的，快照里留个找不到的洞
    会让前端渲染出一个空白格子。
    """
    raw = [n for n in (_read_file().get("snapshots") or []) if isinstance(n, str)]
    if not raw:
        return list(SNAPS_DEFAULT)
    valid = {c["name"] for c in all_cards()}
    kept = [n for n in raw if n in valid]
    return kept or list(SNAPS_DEFAULT)


def set_snapshots(names: list) -> list[str]:
    """存快照。只接受"存在的卡片名"，其余丢弃 —— 卡片被删了快照不该留个洞。"""
    valid = {c["name"] for c in all_cards()}
    clean: list[str] = []
    for n in names or []:
        n = str(n).strip()
        if not n or n in clean:
            continue
        if n not in valid:
            raise ValueError(f"没有这张卡片：{n}")
        clean.append(n)
    if not clean:
        raise ValueError("快照不能为空")
    if len(clean) > 12:
        raise ValueError("快照最多 12 个")
    with _lock:
        data = _read_file()
        data["snapshots"] = clean
        _write_file(data)
    return clean


def builtin() -> list[dict]:
    return [dict(c, custom=False) for c in BUILTIN_CARDS]


def custom() -> list[dict]:
    return [dict(c, custom=True) for c in _read_custom()]


def all_cards() -> list[dict]:
    return builtin() + custom()


def custom_ids() -> set[str]:
    return {c.get("id") for c in _read_custom()}


def add(card: dict) -> dict:
    ids = custom_ids() | {c["id"] for c in BUILTIN_CARDS}
    clean = validate_card(card, existing_ids=ids)
    with _lock:
        items = _read_custom()
        items.append(clean)
        _write_custom(items)
    return clean


def update(cid: str, card: dict) -> dict:
    if cid in {c["id"] for c in BUILTIN_CARDS}:
        raise ValueError("内置卡片不能直接改，请用「另存为新卡片」")
    with _lock:
        items = _read_custom()
        idx = next((i for i, c in enumerate(items) if c.get("id") == cid), None)
        if idx is None:
            raise KeyError(cid)
        # id 不允许改：任务历史/执行链里可能出现它
        clean = validate_card({**card, "id": cid})
        items[idx] = clean
        _write_custom(items)
    return clean


def remove(cid: str) -> bool:
    if cid in {c["id"] for c in BUILTIN_CARDS}:
        raise ValueError("内置卡片不能删除")
    with _lock:
        items = _read_custom()
        left = [c for c in items if c.get("id") != cid]
        if len(left) == len(items):
            return False
        _write_custom(left)
    return True


def reset() -> int:
    """清空自定义卡片（测试用）。"""
    with _lock:
        n = len(_read_custom())
        data = _read_file()
        data["cards"] = []
        _write_file(data)
    return n


def reset_snapshots() -> list[str]:
    """恢复默认快照。"""
    with _lock:
        data = _read_file()
        data["snapshots"] = []
        _write_file(data)
    return list(SNAPS_DEFAULT)
