"""自定义卡片、快照与**预设**的持久化（cards.json）。

内置卡片是只读的，用户改出来的卡片全在这里。写入用"临时文件 + 原子替换"，
避免写一半把用户配置弄丢。启动时**不清空**这个文件（见 config.fresh_start）。

三样东西共用一个文件（`cards` / `snapshots` / `presets`），这是**有意的**：
`AE_FRESH=0` 的保留语义、备份、迁移、原子替换只要写一遍。新开一个配置文件
就得把那些各写一遍，而它们每一样都可能写错。

⚠ **2026-10：老板问"用户的卡片怎么一直消失？是不是没实体保存？"**
是实体保存的（就是这个文件），但当时有**三条会静默把它清空**的路径，都已修掉：

1. **读失败会伪造一份空配置**（最危险的一条）。原来是
   `except (OSError, json.JSONDecodeError): return {"cards": [], ...}` ——
   而所有写路径都是"读 → 改一项 → 整份写回"。于是**任何一次读失败**（文件被另一个
   进程占着、或内容坏了）都会让下一次写（用户随便拖一下快照栏就够了）把
   `cards: []` **永久落盘**。没有报错、没有日志，用户只会看到"卡片又没了"。
   现在读不出来就**抛**（`CardsFileError`），写路径因此中断，原文件一个字节都不动。
2. **临时文件名是固定的**（`cards.json.tmp`）。两个写入者会写同一个临时文件，
   一个 `replace` 就可能把另一个**写了一半**的内容搬上去 —— 直接产出坏 JSON，
   再被第 1 条放大成"卡片全没了"。`audio.py` 早就在 ebur128 的元数据文件上栽过同一个
   跟头（那里的注释写着"中间文件名**每个任务唯一**"）。现在临时名带 pid + uuid。
3. **没有上一代备份**。现在替换前留一份 `cards.json.bak`（上一代的**好**文件），
   真出事能捞回来。

⚠ `_lock` 是**进程内**的锁。`cards.json` 在仓库根、被**所有**实例共用，所以
**同时只开一个实例**；临时名唯一 + 备份这两条是"万一开了两个"时的兜底，
不是"可以开两个"的许可。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from backend import config
from backend.cards.builtin import BUILTIN_CARDS, SNAPS_DEFAULT
from backend.cards.validate import validate_card

CARDS_JSON = config.ROOT / "cards.json"
#: 上一代的**好**文件（替换前留一份）。真出事能捞回来 —— 用户数据没有第二个来源。
CARDS_BAK = CARDS_JSON.with_name(CARDS_JSON.name + ".bak")

_lock = threading.Lock()

#: 读失败时重试几次、每次退避多少秒。Windows 上另一个进程正在 `replace` 的瞬间，
#: `read_text` 会抛一次 `PermissionError` —— 退避重试能把"偶发"和"真坏"分开。
_READ_RETRY = 4
_READ_BACKOFF = 0.03

_EMPTY = {"version": 1, "cards": [], "snapshots": [], "presets": []}


class CardsFileError(RuntimeError):
    """`cards.json` 读不出来（不是"没有"）。

    **绝不能**把它当成"没有自定义卡片"：那会让下一次写把用户的卡片永久清掉。
    """


# ---------------------------------------------------------------- 存储

def _read_file() -> dict:
    """读整份配置（cards + snapshots + presets）。

    * 文件**不存在** → 合法空配置（新装就是这个状态）；
    * 文件**存在但读不出来 / 不是合法 JSON** → **抛 `CardsFileError`**。

    第二条是 2026-10 修掉的真 bug：原来这里 `return {...空...}`，于是**任何一次读
    失败都会被下一次写固化成"用户没有卡片"**。读不出来必须让调用方失败，而不是
    替用户决定"你没有卡片"。
    """
    for attempt in range(_READ_RETRY):
        try:
            raw = CARDS_JSON.read_text(encoding="utf-8")
        except FileNotFoundError:
            return dict(_EMPTY)
        except OSError:
            time.sleep(_READ_BACKOFF * (attempt + 1))
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise CardsFileError(
                f"{CARDS_JSON.name} 不是合法 JSON（第 {e.lineno} 行）：{e.msg}。"
                f"文件已原样保留，没有被覆盖；上一代在 {CARDS_BAK.name}。") from e
        if isinstance(data, list):                   # 兼容早期只有数组的格式
            return {"version": 1, "cards": data, "snapshots": [], "presets": []}
        if not isinstance(data, dict):
            raise CardsFileError(f"{CARDS_JSON.name} 的顶层既不是对象也不是数组")
        data.setdefault("cards", [])
        data.setdefault("snapshots", [])
        data.setdefault("presets", [])
        data.setdefault("version", 1)
        return data
    raise CardsFileError(
        f"{CARDS_JSON} 读不出来（被别的进程占着？）。**没有**覆盖它 —— "
        f"卡片还在盘上，稍后再试一次。")


def _read_custom() -> list[dict]:
    items = _read_file().get("cards")
    return [c for c in (items or []) if isinstance(c, dict)]


def _write_file(payload: dict) -> None:
    """整份写回：唯一临时名 → 备份上一代 → 原子替换。

    ⚠ 临时名**必须唯一**（pid + uuid）。原来固定叫 `cards.json.tmp`，两个写入者
    （两个线程、或两个实例）会写同一个临时文件，一个 `replace` 就把另一个写了一半的
    内容搬上去 —— 产出坏 JSON，然后被上面 `_read_file` 那条旧逻辑放大成"卡片全丢"。
    """
    tmp = CARDS_JSON.with_name(f"{CARDS_JSON.name}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        # 备份**上一代**再替换。best-effort：备份失败不该挡住这次写。
        try:
            if CARDS_JSON.exists():
                shutil.copy2(CARDS_JSON, CARDS_BAK)
        except OSError:
            pass
        tmp.replace(CARDS_JSON)          # 原子替换，别写一半把用户配置弄丢
    finally:
        tmp.unlink(missing_ok=True)      # 失败路径别留垃圾（成功后它已经不存在了）


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


def _names(items: list[dict]) -> tuple[set[str], set[str]]:
    """→（库里已用掉的全部卡片名，其中的内置卡片名）。

    卡片名是**全局唯一**的（`validate_card` 据此拒绝重名），所以这份名字既要算
    内置的、也要算自定义的。第二个返回值只为让报错说清"撞的是内置还是自定义"。
    """
    builtin_names = {str(c.get("name") or "") for c in BUILTIN_CARDS}
    return builtin_names | {str(c.get("name") or "") for c in items}, builtin_names


def add(card: dict) -> dict:
    with _lock:
        # 判重必须**在锁里**：连点两次保存时，两个请求会同时读到"还没有同名卡"，
        # 于是双双通过 —— 落盘后就是两张同名的卡，而 UI 只找得到第一张。
        items = _read_custom()
        ids = {c.get("id") for c in items} | {c["id"] for c in BUILTIN_CARDS}
        names, builtin_names = _names(items)
        clean = validate_card(card, existing_ids=ids, taken_names=names,
                              builtin_names=builtin_names)
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
        # 改名不许撞到**别的**卡 —— 撞到自己不算：用户在编辑器里只改描述、
        # 名字没动时走的也是这条路径，不能因此报"重名"。
        names, builtin_names = _names([c for c in items if c.get("id") != cid])
        # id 不允许改：任务历史/执行链里可能出现它
        clean = validate_card({**card, "id": cid}, taken_names=names,
                              builtin_names=builtin_names)
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


# ---------------------------------------------------------------- 预设（§3.8.2）

# 预设能存几条。没有上限的话预设网格会变成一个没有出口的列表，
# 而它的用途是"常用的那几条链"，不是归档所有跑过的链。
PRESET_MAX = 60
# `预设_NN` 的编号提取。取 max+1 不复用空洞，理由见 next_preset_name。
_NAME_RE = re.compile(r"^预设_(\d+)$")


def _read_presets() -> list[dict]:
    return [p for p in (_read_file().get("presets") or []) if isinstance(p, dict)]


def _new_preset_id(existing: list[dict]) -> str:
    """`p_` + 8 位随机。碰撞就重来（预设最多几十条，几乎不会撞）。"""
    have = {p.get("id") for p in existing}
    for _ in range(50):
        pid = "p_" + uuid.uuid4().hex[:8]
        if pid not in have:
            return pid
    return "p_" + uuid.uuid4().hex[:12]


def next_preset_name(existing: list[dict] | None = None) -> str:
    """`预设_NN`，NN = 现有预设里**最大编号 + 1**（两位补零）。

    ⚠ **取 max+1，不是"最小空号"**：用户删掉 `预设_02` 之后再存，
    如果复用 02，用户会以为"这就是原来那个"—— 预设的内容完全不同。
    编号只增不复用是刻意的（§3.8.2 一）。
    """
    items = _read_presets() if existing is None else existing
    top = 0
    for p in items:
        m = _NAME_RE.match(str(p.get("name") or "").strip())
        if m:
            try:
                top = max(top, int(m.group(1)))
            except ValueError:
                pass
    return f"预设_{top + 1:02d}"


def presets() -> list[dict]:
    return [dict(p) for p in _read_presets()]


def get_preset(pid: str) -> dict | None:
    return next((dict(p) for p in _read_presets() if p.get("id") == pid), None)


def add_preset(preset: dict) -> dict:
    """新建预设。`steps` 必须已经是**校验过**的快照（调用方走 validate_steps）。"""
    with _lock:
        data = _read_file()
        items = [p for p in (data.get("presets") or []) if isinstance(p, dict)]
        if len(items) >= PRESET_MAX:
            raise ValueError(f"预设最多 {PRESET_MAX} 条，请先删掉一些")
        name = str(preset.get("name") or "").strip()
        if not name:
            raise ValueError("预设名称不能为空")
        if any(str(p.get("name") or "").strip() == name for p in items):
            raise ValueError(f"已经有一条叫「{name}」的预设了")
        item = {
            "id": preset.get("id") or _new_preset_id(items),
            "name": name[:40],
            "desc": str(preset.get("desc") or "").strip()[:120],
            # `mode` **必须存**：同一条链在串行/并行下结果完全不同（§3.8.2 二）
            "mode": "serial" if str(preset.get("mode")) == "serial" else "parallel",
            "steps": preset.get("steps") or [],
            # `icons: null` = 自动推导，不是"没有图标"（§3.8.2 三）
            "icons": preset.get("icons"),
            "createdAt": float(preset.get("createdAt") or time.time()),
        }
        items.append(item)
        data["presets"] = items
        _write_file(data)
    return dict(item)


def update_preset(pid: str, patch: dict) -> dict:
    """改预设。可改 name / desc / mode / steps / icons —— **不动 id 与 createdAt**。"""
    with _lock:
        data = _read_file()
        items = [p for p in (data.get("presets") or []) if isinstance(p, dict)]
        idx = next((i for i, p in enumerate(items) if p.get("id") == pid), None)
        if idx is None:
            raise KeyError(pid)
        cur = dict(items[idx])
        if "name" in patch:
            name = str(patch.get("name") or "").strip()
            if not name:
                raise ValueError("预设名称不能为空")
            if any(str(p.get("name") or "").strip() == name
                   for i, p in enumerate(items) if i != idx):
                raise ValueError(f"已经有一条叫「{name}」的预设了")
            cur["name"] = name[:40]
        if "desc" in patch:
            cur["desc"] = str(patch.get("desc") or "").strip()[:120]
        if "mode" in patch:
            cur["mode"] = ("serial" if str(patch.get("mode")) == "serial"
                           else "parallel")
        if "steps" in patch:
            cur["steps"] = patch.get("steps") or []
        if "icons" in patch:
            cur["icons"] = patch.get("icons")
        items[idx] = cur
        data["presets"] = items
        _write_file(data)
    return dict(cur)


def remove_preset(pid: str) -> bool:
    with _lock:
        data = _read_file()
        items = [p for p in (data.get("presets") or []) if isinstance(p, dict)]
        left = [p for p in items if p.get("id") != pid]
        if len(left) == len(items):
            return False
        data["presets"] = left
        _write_file(data)
    return True


def reset_presets() -> int:
    """清空预设（测试用）。→ 删掉了几条。"""
    with _lock:
        data = _read_file()
        n = len([p for p in (data.get("presets") or []) if isinstance(p, dict)])
        data["presets"] = []
        _write_file(data)
    return n
