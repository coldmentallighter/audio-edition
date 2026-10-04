"""钉住 `backend/tasks.py` 的 tag 取值：大小写不敏感 + 别名归一。

为什么值得一个独立文件：这三个函数是**纯的**（吃 dict 吐 str，没有 I/O），
失败方式是**静默的** —— `tags.get("track")` 在 FLAC（`TRACKNUMBER`）上返回空串，
卡片画成 `—`，谁也不会报错。实测踩过一次，所以钉住。

跑法：
    python tests/check_tasks_tags.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.tasks import _tag, _tag_for                    # noqa: E402


def check() -> bool:
    ok = True

    def t(name: str, cond: bool, extra: object = "") -> None:
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + name
              + (f"   {extra}" if extra else ""))
        ok = ok and cond

    # ---- 大小写：FLAC 的 Vorbis comment 是大写标准名 ----
    t("FLAC 的 TRACKNUMBER 命中 track",
      _tag_for({"TRACKNUMBER": "7"}, "track") == "7")
    t("小写的 track 也命中",
      _tag_for({"track": "7"}, "track") == "7")
    t("ID3 的 TRCK 也命中",
      _tag_for({"TRCK": "7"}, "track") == "7")
    t("TITLE / ARTIST / ALBUM 全大写也命中",
      _tag_for({"TITLE": "夜航西飞"}, "title") == "夜航西飞"
      and _tag_for({"ARTIST": "陈婧霏"}, "artist") == "陈婧霏"
      and _tag_for({"ALBUM": "潜水艇"}, "album") == "潜水艇")

    # ---- 取不到 ----
    t("空 dict 返回空串（不是 None）", _tag_for({}, "track") == "")
    t("值是空串时也返回空串",
      _tag_for({"TRACKNUMBER": ""}, "track") == "")
    t("值里的首尾空白被去掉",
      _tag_for({"TRACKNUMBER": "  7  "}, "track") == "7")
    t("值是非字符串（ffprobe 偶尔给 int）不炸",
      _tag_for({"TRACKNUMBER": 7}, "track") == "7")

    # ---- 补零那条路（`_render_pattern` 里 `{tracknumber:02}` 靠它） ----
    t("tracknumber 与 track 是同一个值的两种拼法",
      _tag_for({"TRACKNUMBER": "7"}, "tracknumber") == "7"
      and _tag_for({"track": "7"}, "tracknumber") == "7")

    # ---- 别名表里的键都要能查到 ----
    t("别名表里每个键都能通过自己的名字查到",
      all(_tag_for({k.upper(): "x"}, k) == "x"
          for k in ("track", "tracknumber", "disc", "discnumber", "date")))

    return ok


if __name__ == "__main__":
    print("invariants:")
    print("ALL PASS" if check() else "PROBLEMS")