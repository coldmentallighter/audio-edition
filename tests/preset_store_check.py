"""预设持久化与校验的纯函数/端点自检（§3.8.2 / §9.1.2）。

对应《执行链并发方案.md》§3.8.2：
  · `cards.json` 的 `presets` 字段（与 cards / snapshots 同一个文件、同一套原子写）
  · **`预设_NN` 取 max+1，不复用空洞**（删掉 02 之后再存是 03，不是 02）
  · `steps` 存**自包含快照**（`op` + `params` + `ico`），不是卡片名
  · `mode` 必须存下来（漏了等于"还原出一个不一样的链"）
  · `icons: null` = 自动推导，**不是**"没有图标"
  · 非法步骤**逐条报原因**（保存时直接拒，还原时跳过该项）

用独立临时目录，不碰工作区的 cards.json。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend import config                                            # noqa: E402
from backend.cards import store as cstore                             # noqa: E402
from backend.cards import validate as cvalidate                       # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


_tmp = Path(tempfile.mkdtemp(prefix="ae-preset-"))
_orig_json, _orig_root = cstore.CARDS_JSON, config.ROOT
cstore.CARDS_JSON = _tmp / "cards.json"
config.ROOT = _tmp


def _steps(*ops):
    """把 `("convert", {"format": "flac"})` 变成校验过的干净步骤。"""
    raw = [{"op": o, "params": p} for o, p in ops]
    good, why = cvalidate.validate_steps(raw, where="测试")
    assert not why, why
    return good


try:
    # ---------------------------------------------------------------- 1
    print("== 1. 名称：max+1，不复用空洞 ==")
    check("空库里下一条是 预设_01", cstore.next_preset_name() == "预设_01",
          cstore.next_preset_name())
    p1 = cstore.add_preset({"name": cstore.next_preset_name(), "mode": "serial",
                            "steps": _steps(("convert", {"format": "flac"}))})
    p2 = cstore.add_preset({"name": cstore.next_preset_name(), "mode": "parallel",
                            "steps": _steps(("tags", {"tags": {"title": "x"}}))})
    p3 = cstore.add_preset({"name": cstore.next_preset_name(), "mode": "serial",
                            "steps": _steps(("normalize", {}))})
    check("三条名字是 01/02/03",
          [x["name"] for x in cstore.presets()] == ["预设_01", "预设_02", "预设_03"],
          [x["name"] for x in cstore.presets()])
    cstore.remove_preset(p2["id"])
    check("删掉 02 之后下一条是 **03+1 = 预设_04**（不复用空洞）",
          cstore.next_preset_name() == "预设_04", cstore.next_preset_name())
    check("剩下 01/03", [x["name"] for x in cstore.presets()] == ["预设_01", "预设_03"])
    # 手动塞一个高位编号，max 要跟着它走
    cstore.add_preset({"name": "预设_17", "mode": "serial",
                       "steps": _steps(("probe", {}))})
    check("有 预设_17 时下一条是 预设_18（取 max 而不是计数）",
          cstore.next_preset_name() == "预设_18", cstore.next_preset_name())

    # ---------------------------------------------------------------- 2
    print()
    print("== 2. 步骤是自包含快照，不是卡片名 ==")
    body = json.loads(cstore.CARDS_JSON.read_text(encoding="utf-8"))
    st = body["presets"][0]["steps"][0]
    check("步骤存了 op", st.get("op") == "convert", st)
    check("步骤存了 params（含默认值收敛）",
          st.get("params", {}).get("format") == "flac", st.get("params"))
    check("步骤存了 ico（卡片删了链上还要显示图标）",
          st.get("ico") == "flac", st.get("ico"))
    check("步骤里**没有**卡片 id 兜底（不是按名字引用卡片）",
          "cardId" not in st, st)
    check("文件里就三个顶层键 + version",
          set(body) == {"version", "cards", "snapshots", "presets"}, sorted(body))

    # ---------------------------------------------------------------- 3
    print()
    print("== 3. mode 必须存下来（漏了等于还原出一个不一样的链）==")
    modes = {p["name"]: p["mode"] for p in cstore.presets()}
    check("串行/并行分别存对了",
          modes.get("预设_01") == "serial" and modes.get("预设_03") == "serial",
          modes)
    check("非法 mode 收敛成 parallel（不写进一个不存在的档位）",
          cstore.add_preset({"name": "怪档", "mode": "segment",
                             "steps": _steps(("probe", {}))})["mode"] == "parallel")

    # ---------------------------------------------------------------- 4
    print()
    print("== 4. icons：null = 自动推导，不是「没有图标」==")
    auto = cstore.add_preset({"name": "自动图标", "mode": "serial",
                              "steps": _steps(("convert", {"format": "flac"}))})
    check("不传 icons → null（自动）", auto["icons"] is None, auto["icons"])
    manual = cstore.add_preset({"name": "手选图标", "mode": "serial",
                                "icons": ["tag", "zip"],
                                "steps": _steps(("tags", {"tags": {}}))})
    check("传了 icons → 原样存数组", manual["icons"] == ["tag", "zip"],
          manual["icons"])
    check("改回 null（恢复自动）", 
          cstore.update_preset(manual["id"], {"icons": None})["icons"] is None)

    # ---------------------------------------------------------------- 5
    print()
    print("== 5. 重名拒绝、id 不动、createdAt 不动 ==")
    try:
        cstore.add_preset({"name": "预设_01", "mode": "serial",
                           "steps": _steps(("probe", {}))})
        check("重名被拒", False, "没报错")
    except ValueError as e:
        check("重名被拒", "已经有一条叫" in str(e), e)
    before = cstore.get_preset(p1["id"])
    after = cstore.update_preset(p1["id"], {"desc": "改过的描述"})
    check("update 不改 id", after["id"] == before["id"])
    check("update 不改 createdAt", after["createdAt"] == before["createdAt"])
    check("update 真的改了 desc", after["desc"] == "改过的描述", after["desc"])
    check("update 能改 mode",
          cstore.update_preset(p1["id"], {"mode": "serial"})["mode"] == "serial")
    check("update 能改 steps",
          len(cstore.update_preset(p1["id"], {"steps": _steps(("probe", {}))})["steps"]) == 1)
    try:
        cstore.update_preset("p_不存在", {"name": "x"})
        check("改不存在的预设 → KeyError", False, "没报错")
    except KeyError:
        check("改不存在的预设 → KeyError", True)
    check("删不存在的预设 → False", cstore.remove_preset("p_不存在") is False)

    # ---------------------------------------------------------------- 6
    print()
    print("== 6. 非法步骤逐条报原因（不静默吞掉）==")
    # ⚠ 挑非法用例要挑**无条件校验**的参数：`convert` 的 `compressionLevel`
    # 带 `onlyIf: format=flac`，而这一步没给 format，于是它被**合理地跳过**了
    # （不是漏校验）。用 `normalize.targetLufs`（恒有 min/max）才不会有歧义。
    good, why = cvalidate.validate_steps(
        [{"op": "convert", "params": {"format": "flac"}},
         {"op": "normalize", "params": {"targetLufs": 999}},
         {"op": "不存在的op"},
         {"op": "probe", "params": {}}],
        where="预设_09")
    check("合法的两条保留", [s["op"] for s in good] == ["convert", "probe"],
          [s["op"] for s in good])
    check("非法的一条一条报出来（共 2 条）", len(why) == 2, why)
    check("原因里带**第几步**", "第 2 步" in why[0] and "第 3 步" in why[1], why)
    check("原因里带**为什么**", "不能大于" in why[0], why[0])
    check("原因里带**预设名**（用户知道是哪条预设的问题）",
          all("预设_09" in w for w in why), why)
    check("原因里明说该项已跳过", all("已跳过" in w for w in why), why)
    # `onlyIf` 不成立的参数不参与校验 —— 这是**特性**不是漏校验：
    # `转 MP3` 的卡片里留着 `compressionLevel` 不该报错（§3.2.4 参数层的 onlyIf）
    ok_only, why_only = cvalidate.validate_steps(
        [{"op": "convert", "params": {"format": "mp3", "compressionLevel": 99}}])
    check("onlyIf 不成立的参数被跳过而不是报错",
          not why_only and "compressionLevel" not in ok_only[0]["params"],
          (why_only, ok_only[0]["params"] if ok_only else None))
    try:
        cvalidate.validate_steps([], where="空")
        check("空步骤被拒", False, "没报错")
    except ValueError as e:
        check("空步骤被拒", "空" in str(e) or "数组" in str(e), e)
    try:
        cvalidate.validate_steps("不是数组")
        check("非数组被拒", False, "没报错")
    except ValueError:
        check("非数组被拒", True)
    # 服务端内部键不许留在快照里（`_op` 是路由注入的）
    good2, why2 = cvalidate.validate_steps(
        [{"op": "loudness-report", "params": {"_op": "loudness-image",
                                              "detail": "full"}}])
    check("`_` 开头的服务端内部键被剔除，不当成非法参数报错",
          not why2 and good2 and "_op" not in good2[0]["params"],
          (why2, good2[0]["params"] if good2 else None))

    # ---------------------------------------------------------------- 7
    print()
    print("== 7. 未知卡片：`custom + cardId` 要保留下来（§9.1.2 一）==")
    good3, why3 = cvalidate.validate_steps(
        [{"op": "convert", "params": {"format": "flac"}, "name": "我的转码卡",
          "cardId": "c_已删除的卡_abc123", "custom": True, "ico": "mp3"}],
        where="预设_10")
    check("未知卡的步骤能存下来（名字不在库里也照样校验通过）",
          len(good3) == 1 and not why3, why3)
    check("保留了 cardId（还原时据此判断'这张卡不在库里'）",
          good3[0].get("cardId") == "c_已删除的卡_abc123", good3[0])
    check("保留了 custom 标记", good3[0].get("custom") is True, good3[0])
    check("保留了 ico 快照", good3[0].get("ico") == "mp3", good3[0])
    check("内置卡（不带 custom）不会被打上 custom",
          "custom" not in _steps(("probe", {}))[0])

    # ---------------------------------------------------------------- 8
    print()
    print("== 8. 上限与 reset ==")
    while len(cstore.presets()) < cstore.PRESET_MAX:
        i = len(cstore.presets())
        cstore.add_preset({"name": f"填充_{i}", "mode": "serial",
                           "steps": _steps(("probe", {}))})
    check(f"能存满 {cstore.PRESET_MAX} 条", len(cstore.presets()) == cstore.PRESET_MAX,
          len(cstore.presets()))
    try:
        cstore.add_preset({"name": "第 61 条", "mode": "serial",
                           "steps": _steps(("probe", {}))})
        check("超过上限被拒", False, "没报错")
    except ValueError as e:
        check("超过上限被拒", "最多" in str(e), e)
    n = cstore.reset_presets()
    check(f"reset 清掉 {n} 条", n == cstore.PRESET_MAX and cstore.presets() == [], n)

    # ---------------------------------------------------------------- 8b
    print()
    print("== 8b. 卡片名全局唯一（内置 + 自定义）==")
    # 用户实测踩的：新建的卡取了内置卡的名字，于是它**在 UI 里点不开** ——
    # 卡片库整套是按名字认卡的（`data-card="${c.name}"`、`CARDS.find(c => c.name === …)`、
    # 快照条存的也是名字），而 `all_cards()` 是 builtin + custom，`find` 取第一个，
    # 命中的永远是内置那张。表现就是"我新建的卡被识别成内置卡"，点开只有
    # 「查看 / 另存为…」，改不了也删不掉。名字唯一因此是**硬约束**，不是体验问题。
    from backend.cards.builtin import BUILTIN_CARDS                 # noqa: E402
    builtin_name = BUILTIN_CARDS[0]["name"]

    def _card(name, **kw):
        c = {"name": name, "op": "probe", "cat": "自定义", "params": {}}
        c.update(kw)
        return c

    try:
        cstore.add(_card(builtin_name))
        check("与内置卡重名被拒", False, "没报错")
    except ValueError as e:
        check("与内置卡重名被拒（内置卡在先，重名的新卡永远点不开）",
              "内置卡片" in str(e), e)
        check("报错给出出路（换个名字）", "换个名字" in str(e), e)

    ca = cstore.add(_card("测试卡 A"))
    check("正常名字能建", ca["name"] == "测试卡 A", ca)
    cstore.add(_card("测试卡 B"))
    try:
        cstore.add(_card("测试卡 A"))
        check("与自定义卡重名被拒", False, "没报错")
    except ValueError as e:
        check("与自定义卡重名被拒（报错区分内置/自定义）",
              "自定义卡片" in str(e), e)

    # 改卡：**撞自己不算重名**。编辑器里只改描述、名字没动时走的就是这条路径，
    # 在这里误报的话"保存"会变成一件做不了的事。
    same = cstore.update(ca["id"], {**ca, "desc": "只改了描述"})
    check("改卡时名字不动 → 不算重名", same["desc"] == "只改了描述", same)
    try:
        cstore.update(ca["id"], {**ca, "name": "测试卡 B"})
        check("改名撞到别的自定义卡被拒", False, "没报错")
    except ValueError as e:
        check("改名撞到别的自定义卡被拒", "已经有一张叫" in str(e), e)
    try:
        cstore.update(ca["id"], {**ca, "name": builtin_name})
        check("改名撞到内置卡被拒", False, "没报错")
    except ValueError as e:
        check("改名撞到内置卡被拒（报错说清是内置）", "内置卡片" in str(e), e)
    check("两次被拒之后库里还是那两张（没有半写进去）",
          sorted(c["name"] for c in cstore.custom()) == ["测试卡 A", "测试卡 B"],
          [c["name"] for c in cstore.custom()])

    # ---------------------------------------------------------------- 9
    print()
    print("== 9. 老 cards.json（没有 presets 字段）要能平滑读 ==")
    cstore.CARDS_JSON.write_text(json.dumps(
        {"version": 1, "cards": [], "snapshots": ["转 FLAC"]}, ensure_ascii=False),
        encoding="utf-8")
    check("读得出来（presets 缺省为空数组）", cstore.presets() == [])
    check("下一条名字从 01 开始", cstore.next_preset_name() == "预设_01")
    p = cstore.add_preset({"name": "预设_01", "mode": "serial",
                           "steps": _steps(("probe", {}))})
    check("加了一条之后 snapshots 没被冲掉",
          json.loads(cstore.CARDS_JSON.read_text(encoding="utf-8"))["snapshots"]
          == ["转 FLAC"],
          json.loads(cstore.CARDS_JSON.read_text(encoding="utf-8")).get("snapshots"))
    check("cards 也还在", "cards" in json.loads(
        cstore.CARDS_JSON.read_text(encoding="utf-8")))
    # 坏文件不能让整个预设功能炸掉
    cstore.CARDS_JSON.write_text("{ 这不是 json", encoding="utf-8")
    check("坏文件 → 当作空库，不抛异常", cstore.presets() == [])
    _ = p

finally:
    cstore.CARDS_JSON = _orig_json
    config.ROOT = _orig_root
    shutil.rmtree(_tmp, ignore_errors=True)

print()
print(f"结果：{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
