"""功能卡片目录（包）。

按职责拆成四个模块，**对外 API 与拆分前的 backend/cards.py 完全一致** ——
app.py 与 tests 里所有 `cards.xxx` 调用都不用改：

    specs     参数规格（OPS / PARAM_TYPES / PICTURE_TYPES）
    builtin   71 张内置卡片
    validate  卡片校验 + 步骤校验 + 命令预览
    store     自定义卡片 / 快照 / 预设的持久化

依赖是单向的：specs -> builtin -> validate -> store，没有循环。
"""
from backend.cards.specs import OPS, PARAM_TYPES, PICTURE_TYPES
from backend.cards.builtin import BUILTIN_CARDS, CARD_CATS, CARD_ICONS, SNAPS_DEFAULT
from backend.cards.builtin import NO_CARD_OPS  # noqa: F401
from backend.cards.validate import validate_card, validate_step, validate_steps, render_preview
from backend.cards.store import (
    CARDS_JSON,
    PRESET_MAX,
    add,
    add_preset,
    all_cards,
    builtin,
    custom,
    custom_ids,
    get_preset,
    next_preset_name,
    presets,
    remove,
    remove_preset,
    reset,
    reset_presets,
    reset_snapshots,
    set_snapshots,
    snapshots,
    update,
    update_preset,
)

__all__ = [
    "OPS", "PARAM_TYPES", "PICTURE_TYPES",
    "BUILTIN_CARDS", "CARD_CATS", "CARD_ICONS", "SNAPS_DEFAULT",
    "validate_card", "validate_step", "validate_steps", "render_preview",
    "CARDS_JSON", "PRESET_MAX",
    "add", "all_cards", "builtin", "custom", "custom_ids",
    "remove", "reset", "reset_snapshots", "set_snapshots", "snapshots", "update",
    "presets", "get_preset", "add_preset", "update_preset", "remove_preset",
    "reset_presets", "next_preset_name",
]
