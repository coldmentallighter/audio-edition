"""卡片上那行「Loudness」的显示自检：**不许出现 `undefined LUFS`**。

用户实测报的：每张文件卡上都写着 `undefined LUFS`。两个原因，都要钉住：

  ① `applyServerFiles` 从来没把 `info.loudness` 读进 `FILES`，于是 `f.loudness`
     是 `undefined`，而模板是裸插值 `${f.loudness} LUFS` —— 直接把 `undefined`
     印到页面上。
  ② 就算读进来了，也得有"没测过"的表示：响度是**内容测量值**（要解码整条流），
     只有跑过响度分析 / 响度标准化才有。没测过必须显示 `—`，与同区域的
     `Bitdepth` 一致，而不是印一个 `NaN` 或 `undefined`。

这个脚本不连服务、不需要浏览器：它**从 `app.js` 里把真正那段表达式抠出来**，
交给 node 逐种输入跑一遍，断言渲染结果。抠不出来就直接红 —— 这样它不会
"抄一份期望值、两边各自漂移"（`app.js` 改了形状它就知道）。

用法：python tests/loudness_display_check.py      （需要 node，浏览器探针也要它）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


APP = ROOT / "app.js"
src = APP.read_text(encoding="utf-8")


def _balanced(text: str, start: int, open_ch: str, close_ch: str) -> str:
    """取 `text[start]`（必须是 `open_ch`）起到配对闭合符为止的**内容**（不含两端）。"""
    assert text[start] == open_ch, (start, text[start:start + 20])
    depth = 0
    for i in range(start, len(text)):
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
    raise AssertionError(f"从 {start} 起找不到配对的 {close_ch}")


def _mapping_expr() -> str:
    """`applyServerFiles` 里 `loudness: …` 那个值表达式。

    取到**行尾的 `,`** 为止（`? Number(info.loudness) : null,`）。
    形状改了（比如有人在里面塞了个逗号）就会抠出半截、node 那边直接报错 ——
    这个检查宁可红，也不要"抠错了还一路绿"。
    """
    anchor = "loudness: "
    i = src.find(anchor)
    if i < 0:
        raise AssertionError("app.js 里找不到 `loudness:` 的映射")
    j = i + len(anchor)
    end = src.find(",\n", j)
    if end < 0:
        raise AssertionError("`loudness:` 那个值找不到结尾的逗号")
    return src[j:end].strip()


def _template_expr() -> str:
    """卡片上 Loudness 那一格 `${…}` 里的表达式。"""
    anchor = 'class="kv__k">Loudness</span><span class="kv__v">'
    i = src.find(anchor)
    if i < 0:
        raise AssertionError("app.js 里找不到 Loudness 那一格")
    k = src.find("${", i)
    if k < 0:
        raise AssertionError("Loudness 那一格没有插值")
    return _balanced(src, k + 1, "{", "}")


try:
    print("== 1. app.js 里那段真代码能被抠出来（形状变了就红）==")
    mapping = _mapping_expr()
    template = _template_expr()
    check("抠到了 info → f.loudness 的映射表达式",
          "info.loudness" in mapping and "Number(" in mapping, mapping)
    check("抠到了卡片上那一格的渲染表达式",
          "LUFS" in template and "f.loudness" in template, template)
    check("不再是裸插值 `${f.loudness} LUFS`",
          "${f.loudness} LUFS" not in src)

    # 指纹：响度是"跑完任务才出现"的字段，不进指纹就不会重绘
    # （表现是"任务成功了，卡片上的数字要等下一次无关变更才刷新"）。
    check("数据指纹带上了 loudness",
          "info.loudness == null ? '' : info.loudness" in src)

    print()
    print("== 2. 把那段表达式交给 node 真跑（不需要浏览器）==")
    node = shutil.which("node")
    if not node:
        check("找得到 node", False, "没装 node（这个检查靠它跑那段表达式）")
        raise SystemExit(1)

    harness = f"""
const cases = [null, undefined, '', 'abc', -19.34, -5.7, 0, 12.5];
const out = cases.map((raw) => {{
  const info = {{ loudness: raw }};
  const f = {{}};
  f.loudness = {mapping};
  return {{
    input: String(raw),
    mapped: (f.loudness === null) ? 'null'
            : (Number.isNaN(f.loudness) ? 'NaN' : String(f.loudness)),
    text: ({template}),
    // 两个负对照，分别对应这条 bug 的两半：
    //   naiveNoMapping —— 旧代码压根没读 info.loudness，于是印 `undefined LUFS`
    //   naiveNoGuard   —— 读了但没兜底（Number(...) 直接拼），非数字就印 `NaN LUFS`
    naiveNoMapping: String(({{}}).loudness) + ' LUFS',
    naiveNoGuard: String(Number(info.loudness)) + ' LUFS',
  }};
}});
process.stdout.write(JSON.stringify(out));
"""
    tmp = ROOT / "tests" / "_loudness_display_tmp.js"
    tmp.write_text(harness, encoding="utf-8")
    try:
        r = subprocess.run([node, str(tmp)], capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
    finally:
        tmp.unlink(missing_ok=True)
    if r.returncode != 0:
        check("node 跑得通那段表达式", False, (r.stderr or "")[:300])
        raise SystemExit(1)
    rows = json.loads(r.stdout)
    by_input = {row["input"]: row for row in rows}

    check("没测过（null）→ 显示 `—`", by_input["null"]["text"] == "—",
          by_input["null"])
    check("undefined → 显示 `—`（这就是原来那个 `undefined LUFS`）",
          by_input["undefined"]["text"] == "—", by_input["undefined"])
    check("空串 → 显示 `—`", by_input[""]["text"] == "—", by_input[""])
    check("后端给了非数字 → 显示 `—` 而不是 `NaN LUFS`",
          by_input["abc"]["text"] == "—", by_input["abc"])
    check("真值保留一位小数（-19.34 → -19.3 LUFS）",
          by_input["-19.34"]["text"] == "-19.3 LUFS", by_input["-19.34"])
    check("-5.7 → `-5.7 LUFS`", by_input["-5.7"]["text"] == "-5.7 LUFS",
          by_input["-5.7"])
    check("0 也是合法响度（不能被当成「没测过」）",
          by_input["0"]["text"] == "0.0 LUFS", by_input["0"])
    check("正数同样保留一位（12.5 → 12.5 LUFS）",
          by_input["12.5"]["text"] == "12.5 LUFS", by_input["12.5"])
    check("任何输入都不许出现 `undefined`",
          all("undefined" not in row["text"] for row in rows),
          [row for row in rows if "undefined" in row["text"]])

    # 负对照：证明这条断言不是空转 —— 缺了映射 / 缺了兜底，**确实**会印出坏字符串。
    check("负对照：没读 info.loudness 就会印 `undefined LUFS`",
          by_input["undefined"]["naiveNoMapping"] == "undefined LUFS",
          by_input["undefined"]["naiveNoMapping"])
    check("负对照：读了但不兜底，非数字会印 `NaN LUFS`",
          by_input["abc"]["naiveNoGuard"] == "NaN LUFS",
          by_input["abc"]["naiveNoGuard"])

finally:
    pass

print()
print(f"结果：{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
