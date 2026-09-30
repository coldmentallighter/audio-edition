"""功能卡片目录（包）。

按职责拆成四个模块，**对外 API 与拆分前的 backend/cards.py 完全一致** ——
app.py 与 tests 里所有 `cards.xxx` 调用都不用改：

    specs     参数规格（OPS / PARAM_TYPES / PICTURE_TYPES）
    builtin   69 张内置卡片
    validate  卡片校验 + 命令预览
    store     自定义卡片与快照的持久化

依赖是单向的：specs -> builtin -> validate -> store，没有循环。
"""
from backend.cards.specs import OPS, PARAM_TYPES, PICTURE_TYPES
from backend.cards.builtin import BUILTIN_CARDS, CARD_CATS, CARD_ICONS, SNAPS_DEFAULT
from backend.cards.validate import validate_card, render_preview
from backend.cards.store import (
    CARDS_JSON,
    add,
    all_cards,
    builtin,
    custom,
    custom_ids,
    remove,
    reset,
    reset_snapshots,
    set_snapshots,
    snapshots,
    update,
)

__all__ = [
    "OPS", "PARAM_TYPES", "PICTURE_TYPES",
    "BUILTIN_CARDS", "CARD_CATS", "CARD_ICONS", "SNAPS_DEFAULT",
    "validate_card", "render_preview",
    "CARDS_JSON", "add", "all_cards", "builtin", "custom", "custom_ids",
    "remove", "reset", "reset_snapshots", "set_snapshots", "snapshots", "update",
]
