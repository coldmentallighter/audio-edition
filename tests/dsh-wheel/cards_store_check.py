"""`cards.json` 的持久化回归 —— 老板问"用户的卡片怎么一直消失？是不是没实体保存？"

    python tests/dsh-wheel/cards_store_check.py

**答案**：是实体保存的（`cards.json`，仓库根，gitignore）。但 2026-10 之前有
**三条会静默把它清空**的路径，这个文件把它们逐条钉住：

1. **读失败伪造空配置** —— 原来 `_read_file()` 读不出来就 `return {"cards": []...}`，
   而所有写路径都是"读 → 改一项 → 整份写回"。于是**任何一次读失败**都会让下一次写
   （用户随便拖一下快照栏就够了）把 `cards: []` **永久落盘**，没有报错、没有日志。
2. **固定临时文件名** —— `cards.json.tmp` 被两个写入者共用，一个 `replace` 可能把
   另一个写了一半的内容搬上去，产出坏 JSON，再被第 1 条放大成"卡片全丢"。
3. **没有上一代备份** —— 出事没法捞。

⚠ 这个测试**必须**指向临时目录：`preset_store_check.py` 已经示范了怎么隔离
（`cstore.CARDS_JSON = 临时路径`）。真实 `cards.json` 是用户数据，测试碰它就是
在制造同一类事故。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from backend import config                                            # noqa: E402
from backend.cards import store as cstore                             # noqa: E402

PASS = FAIL = 0

#: 隔离：整个测试都在临时目录里跑
_tmp = Path(tempfile.mkdtemp(prefix="ae-cards-"))
cstore.CARDS_JSON = _tmp / "cards.json"
cstore.CARDS_BAK = _tmp / "cards.json.bak"
config.ROOT = _tmp


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


def _disk() -> dict:
    return json.loads(cstore.CARDS_JSON.read_text(encoding="utf-8"))


def _card(name: str, op: str = "probe") -> dict:
    return {"name": name, "op": op, "cat": "自定义", "params": {}}


def main() -> int:
    print("== 1. 真的有实体（不是内存里的一张表）==")
    check("一开始没有文件 → 合法空", not cstore.CARDS_JSON.exists()
          and cstore.custom() == [])
    a = cstore.add(_card("回归-A"))
    check("add 之后**盘上**就有这张卡",
          cstore.CARDS_JSON.exists() and _disk()["cards"][0]["name"] == "回归-A",
          _disk())
    b = cstore.add(_card("回归-B"))
    check("第二次 add 追加，不覆盖第一张",
          [c["name"] for c in cstore.custom()] == ["回归-A", "回归-B"],
          [c["name"] for c in cstore.custom()])
    # "换个进程读" = 直接重新解析文件（不经过任何缓存）
    check("换一份全新的读取也看得到（文件就是唯一真相）",
          [c["name"] for c in _disk()["cards"]] == ["回归-A", "回归-B"])

    print()
    print("== 2. 快照 / 预设改一项，不许把卡片带走 ==")
    cstore.set_snapshots(["回归-A", "回归-B"])
    check("改快照后卡片还在", len(cstore.custom()) == 2, cstore.custom())
    cstore.add_preset({"name": "预设_01", "mode": "serial", "steps": []})
    check("加预设后卡片还在", len(cstore.custom()) == 2)
    cstore.reset_snapshots()
    cstore.reset_presets()
    check("重置快照+预设后卡片还在（两条 reset 都不碰 cards）",
          [c["name"] for c in cstore.custom()] == ["回归-A", "回归-B"],
          [c["name"] for c in cstore.custom()])

    print()
    print("== 3. 回归：读不出来时**必须失败**，不许伪造空配置 ==")
    # 这是老板"卡片一直消失"的根因：文件坏了 → 读成空 → 下一次写把空落盘。
    good = cstore.CARDS_JSON.read_text(encoding="utf-8")
    cstore.CARDS_JSON.write_text('{"version": 1, "cards": [{"name": "半截',
                                 encoding="utf-8")
    raised = False
    try:
        cstore.custom()
    except cstore.CardsFileError:
        raised = True
    check("坏 JSON 时读卡片抛 CardsFileError（不是返回空列表）", raised)
    still = cstore.CARDS_JSON.read_text(encoding="utf-8")
    check("坏文件**一个字节都没被动**", still.startswith('{"version": 1, "cards": [{"name": "半截'))
    # 写路径更要紧：快照拖一下就走这条路（原来这一下就把卡片清空了）
    raised = False
    try:
        cstore.set_snapshots(["回归-A"])
    except cstore.CardsFileError:
        raised = True
    check("KNOWN: 文件坏了时**改快照**也抛错，而不是把 cards 写成 []", raised)
    check("KNOWN: 所以卡片没有丢（原来这里会变成空的）",
          "半截" in cstore.CARDS_JSON.read_text(encoding="utf-8"))
    cstore.CARDS_JSON.write_text(good, encoding="utf-8")     # 修好，继续
    check("修好之后立刻读得回来", len(cstore.custom()) == 2)

    print()
    print("== 4. 回归：临时文件名必须唯一（不许共用 cards.json.tmp）==")
    tmp_prefix = cstore.CARDS_JSON.name
    leftovers = [p.name for p in _tmp.iterdir()
                 if p.name.endswith(".tmp")]
    check("写完之后临时文件不留垃圾", leftovers == [], leftovers)
    # 两个线程各自读-改-写：共用固定临时名时这里可能产出坏 JSON
    errs: list[Exception] = []

    def worker(tag: str) -> None:
        try:
            for i in range(12):
                cstore.add(_card(f"并发-{tag}-{i}"))
        except Exception as e:                                       # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=worker, args=(t,)) for t in ("x", "y")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("并发 add 不报错", not errs, errs[:2])
    parsed = None
    try:
        parsed = json.loads(cstore.CARDS_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        parsed = None
        check("并发写之后文件仍是合法 JSON", False, e)
    if parsed is not None:
        check("并发写之后文件仍是合法 JSON", True)
        names = [c["name"] for c in parsed["cards"]]
        check("并发 add 的 24 张一张不少（进程内锁 + 唯一临时名）",
              len(names) == 2 + 24, len(names))
    check("并发之后临时文件也不留垃圾",
          [p.name for p in _tmp.iterdir() if p.name.endswith(".tmp")] == [],
          [p.name for p in _tmp.iterdir() if p.name.endswith(".tmp")])

    print()
    print("== 5. 上一代备份（真出事能捞）==")
    n_before = _disk()["version"]
    check("备份文件存在", cstore.CARDS_BAK.exists(), cstore.CARDS_BAK)
    bak = json.loads(cstore.CARDS_BAK.read_text(encoding="utf-8"))
    check("备份是**上一代**（比当前少一次写）",
          len(bak["cards"]) < len(_disk()["cards"]),
          (len(bak["cards"]), len(_disk()["cards"])))
    check("备份也是合法 JSON（能直接拿回来用）", isinstance(bak.get("cards"), list))
    cstore.remove(cstore.custom()[-1]["id"])
    check("删一张之后剩下的还在", len(cstore.custom()) == 25, len(cstore.custom()))

    print()
    print(f"  临时目录：{_tmp}")
    print(f"结果：{PASS} passed, {FAIL} failed")
    shutil.rmtree(_tmp, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
