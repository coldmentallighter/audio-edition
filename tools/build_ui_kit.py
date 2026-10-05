"""把 `ui/` 下的样式表按**加载顺序**拼成单文件，产出 `ui-kit/`。

为什么要单独一份打包产物
------------------------
`ui/*.css` 是**应用在用的源**（`index.html` 按顺序引 10 个 `<link>`），
改完刷新就生效，没有构建步骤。但想把这套样式**拿走给别处用**时，10 个文件 +
"顺序不能改"这条口头约定很容易传丢，于是另出一份单文件：拿一个文件即可，
顺序已经烘死在里面。

⚠ 这是**生成物**，手改会在下次运行本脚本时被覆盖。
   应用**不读**它（应用仍走 `ui/` 的 10 个文件），所以"改了源但忘了重新打包"
   不会影响应用本身 —— 但会让 `ui-kit/` 悄悄过期，因此
   `tests/ui_check.py` 会逐字节校验它和源是否一致（同 `demo/app.js` 那个教训：
   拷贝不同步会一路绿灯）。

顺序的**唯一事实来源**就是这个模块的 `ORDER`：`ui_check.py` 直接 import 它，
所以不需要第三份清单。

用法：
    python tools/build_ui_kit.py          # 重新生成 ui-kit/audioedition-ui.css
    python tools/build_ui_kit.py --check  # 只校验是否同步（不写文件）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "ui"
KIT = ROOT / "ui-kit"
BUNDLE_NAME = "audioedition-ui.css"

#: 规范加载顺序。**顺序有语义**（后面的覆盖前面的同特异性规则），别为了"看起来整齐"重排。
#: 与 index.html / _snapdrag.html 里的 <link> 顺序必须一致（ui_check.py 会核对）。
ORDER = [
    "theme.css",     # 1. 令牌层，必须第一
    "base.css",      # 2. 重置 + 全局 + 视图过渡
    "common.css",    # 3. 基础件：按钮 / 输入 / 徽章 / 色点
    "header.css",
    "layout.css",
    "filecard.css",
    "drawer.css",
    "cardlib.css",
    "overlays.css",
    "editor.css",    # 10. 最后：它会覆盖前面的同特异性规则
]

BANNER = """\
/* ============================================================================
   AudioEdition UI Kit —— 单文件构建产物，**不要手改**

   由 `tools/build_ui_kit.py` 把 `ui/` 下的 10 个样式表按下面的顺序拼接而成。
   **顺序不能改**：层叠结果依赖它（`editor.css` 最后一个加载，会覆盖前面同特异性
   的规则；`.iconpick` 那个类名有两个主人，等等 —— 见 README.md「加载顺序」）。

     1. ui/theme.css        令牌层：6 套主题 × 21 个设计令牌（必须第一）
     2. ui/base.css         契约 + 重置 + 滚动条 + 视图过渡
     3. ui/common.css       基础件：按钮 / 输入 / 徽章 / 主题色点
     4. ui/header.css       顶栏 + 执行链 + 音量 + 分段开关 + 空状态
     5. ui/layout.css       侧栏 + 工作区 + 选择条 / 批量栏 + 任务与日志
     6. ui/filecard.css     文件卡片：波形 / 传输 / 元数据 / 封面 / 状态列
     7. ui/drawer.css       抽屉 + 快照条 + 卡片库网格 + 预设与链编辑
     8. ui/cardlib.css      卡片库：基础 / 光标跟随 / 点击反馈 / 对勾 + 搜索
     9. ui/overlays.css     拖拽导入 / 上传进度 / 右键菜单 / 通知 / 模态
    10. ui/editor.css       卡片编辑器 + 响应式

   重新生成：python tools/build_ui_kit.py
   同步校验：python tests/ui_check.py        （逐字节比对，源改了忘打包会红）

   用法：`<html data-theme="t1">` 浅色 / `<html data-theme="t1" data-mode="dark">` 深色，
   然后 `<link rel="stylesheet" href="audioedition-ui.css">` 一个文件就够。
   完整契约与踩坑清单见同目录 README.md；可视化样例见同目录 gallery.html。

   ⚠ 刻意**不做压缩**：这套样式的价值有一大半在注释里（每条规则为什么这么写、
     哪个坑踩过），压缩会把它们全部删掉，还要为此引入工具链。
   ============================================================================ */

"""

#: 每个源文件前的分隔注释，方便在这一个文件里定位（注释不影响层叠）
SEP = "/* ==================== ui/{name} ==================== */\n"


def chunks() -> list[tuple[str, str]]:
    """按 ORDER 读出每个源文件的内容（已保证以单个 \\n 结尾）。"""
    out = []
    for name in ORDER:
        p = SRC / name
        if not p.is_file():
            raise FileNotFoundError(f"缺少源文件：{p}")
        text = p.read_text(encoding="utf-8")
        # 统一成"恰好一个结尾换行"：源文件末尾若没有换行，直接拼会把下一个文件的
        # 第一行接到注释/规则尾巴上 —— 那会写出一个语法坏掉的包。
        out.append((name, text.rstrip("\n") + "\n"))
    return out


def render() -> str:
    """返回这份单文件的完整文本（`ui_check.py` 用它做同步校验）。"""
    body = "".join(SEP.format(name=n) + text for n, text in chunks())
    return BANNER + body


def build() -> Path:
    KIT.mkdir(parents=True, exist_ok=True)
    target = KIT / BUNDLE_NAME
    text = render()
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    target = KIT / BUNDLE_NAME
    text = render()

    if check_only:
        if not target.is_file():
            print(f"FAIL  {target.relative_to(ROOT)} 不存在，先跑 python tools/build_ui_kit.py")
            return 1
        if target.read_text(encoding="utf-8") != text:
            print(f"FAIL  {target.relative_to(ROOT)} 与 ui/ 下的源不一致，重新跑一次本脚本")
            return 1
        print(f"OK    {target.relative_to(ROOT)} 与 {len(ORDER)} 个源文件一致（{len(text)} 字符）")
        return 0

    old = target.read_text(encoding="utf-8") if target.is_file() else ""
    target = build()
    rel = target.relative_to(ROOT)
    if old == text:
        print(f"未变化  {rel}（{len(text)} 字符，{len(ORDER)} 个源文件）")
    else:
        print(f"已生成  {rel}（{len(text)} 字符，{len(ORDER)} 个源文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
