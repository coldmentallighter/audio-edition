"""组件库护栏：加载顺序 + 打包同步 + 未定义令牌审计。

`ui/` 的 10 个样式表是**一个整体**，层叠结果依赖它们的先后
（`editor.css` 最后一个加载，它会覆盖前面同特异性的规则 —— 见 `ui-kit/README.md` §2）。
`ui-kit/audioedition-ui.css` 是它们按同一顺序拼出来的**单文件打包产物**。
这个脚本守四件事：

  1. **顺序**：`index.html` / `_snapdrag.html` 声明的样式表必须与 `ui/` 下的文件
     集合**完全一致、顺序完全一致**。少一个、多一个、换个位置，都会静默改变层叠结果。
  2. **打包同步**：`ui-kit/audioedition-ui.css` 必须与当前源**逐字节**一致。
     它是生成物，源改了忘打包就会悄悄过期（同 `demo/app.js` 那个教训：
     拷贝不同步，自检会一路绿灯）。
  3. **令牌**：`var(--x)` 用到的自定义属性必须定义过（除非是 JS 在运行时写的），
     定义过的也不能没人用（除非是给后端 / 配色契约 / 打包产物保留的）。
  4. **行尾**：`ui/*.css` 与打包产物必须是 LF（`.gitattributes` 是 `eol=lf`）。

顺序的**唯一事实来源**是 `tools/build_ui_kit.py` 的 `ORDER`（打包脚本要用它），
这里直接 import，不另抄一份清单。

与 `theme_check.py` 的分工：那个查**色值与对比度**，这个查**结构、打包与引用**。

用法：python tests/ui_check.py
"""
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
KIT = ROOT / "ui-kit"

# 顺序的唯一事实来源在打包脚本里（它生成产物时要用），这里 import 复用
sys.path.insert(0, str(ROOT / "tools"))
import build_ui_kit as kit  # noqa: E402

ORDER = kit.ORDER
BUNDLE = kit.BUNDLE_NAME

#: `ui/` 里除样式表外允许存在的文件（打包产物与文档都在 ui-kit/）
UI_EXTRA_ALLOWED: set[str] = set()

#: `ui-kit/` 里允许存在的文件：单文件产物 + 文档 + 画廊
KIT_ALLOWED = {BUNDLE, "README.md", "gallery.html"}

#: 必须按 ORDER 引用 `ui/` 的页面（应用侧）。画廊引的是打包产物，单独查。
APP_PAGES = ["index.html", "_snapdrag.html"]

#: 运行时由 JS 写、或者写在别处（所以 CSS 里查不到定义）的自定义属性。
#: 每一项都要写清**谁写的** —— 加进来不是"消音"，是声明来源。
RUNTIME_VARS = {
    "--nx": "bindFollow() 写归一化指针 X（app.js）",
    "--ny": "bindFollow() 写归一化指针 Y（app.js）",
    "--mx": "bindFollow() 写指针像素 X（网点/光晕的遮罩原点）",
    "--my": "bindFollow() 写指针像素 Y",
    "--vt-d": "switchTheme() 写平扫斜线偏移（app.js）",
    "--vt-r": "switchTheme() 写径向半径（app.js）",
    "--drawer-left": "抽屉逻辑写左侧栏实际渲染宽度（app.js）",
}

#: 定义在 CSS 里、但被 CSS 之外消费（或按契约保留）的令牌。
#: 删它们之前先看这张表。
RESERVED_VARS = {
    "--ink-accent": "后端 backend/loudness_svg.py 消费（−23 参考线）",
    "--text-disabled": "配色契约保留位，见 theme_check.py 的 REQUIRED",
}

LINK = re.compile(r'<link[^>]+rel=["\']stylesheet["\'][^>]*href=["\']([^"\']+)["\']',
                  re.I)
#: 声明：行首 / `{` / `;` 之后跟 `--名字:`。
#: 不能写成 `(--[\w-]+)\s*:` —— 那会把 `.btn--primary:hover` 里的 `:hover` 当成声明。
DECL = re.compile(r"(?:^|[;{])\s*(--[\w-]+)\s*:", re.M)
USE = re.compile(r"var\(\s*(--[\w-]+)")
COMMENT = re.compile(r"/\*.*?\*/", re.S)

ok = 0
fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label}   {detail}")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def links_of(page: Path) -> list[str]:
    """页面声明的样式表，解析成相对仓库根的 posix 路径。"""
    out = []
    for h in LINK.findall(read(page)):
        clean = h.split("?")[0].split("#")[0]      # 画廊的 iframe 用 ?bare=1
        try:
            out.append((page.parent / clean).resolve()
                       .relative_to(ROOT.resolve()).as_posix())
        except ValueError:
            out.append(clean)                       # 仓库外，原样记下
    return out


# ============================================================ 1. 两边的文件集合

print("== 文件集合 ==")
ui_present = {p.name for p in UI.iterdir() if p.is_file()}
kit_present = {p.name for p in KIT.iterdir() if p.is_file()}

for name in ORDER:
    check(f"存在 ui/{name}", (UI / name).is_file())
for name in sorted(ui_present - set(ORDER) - UI_EXTRA_ALLOWED):
    check(f"ui/{name} 是预期外的文件", False,
          "样式表要加进 build_ui_kit.ORDER；文档/画廊应放 ui-kit/")
check(f"ui/ 恰好是 {len(ORDER)} 个样式表", ui_present == set(ORDER), sorted(ui_present))

for name in sorted(KIT_ALLOWED):
    check(f"存在 ui-kit/{name}", (KIT / name).is_file())
for name in sorted(kit_present - KIT_ALLOWED):
    check(f"ui-kit/{name} 是预期外的文件", False, "ui-kit/ 只放打包产物 + 文档 + 画廊")

# ============================================================ 2. 应用侧的加载顺序

print()
print("== 应用侧（按 ORDER 引 ui/）==")
want = [f"ui/{n}" for n in ORDER]
for rel in APP_PAGES:
    page = ROOT / rel
    if not page.is_file():
        check(f"{rel} 存在", False)
        continue
    resolved = links_of(page)
    check(f"{rel} 引用 {len(ORDER)} 个样式表", len(resolved) == len(ORDER),
          f"{len(resolved)} 个：{resolved}")
    check(f"{rel} 顺序与 build_ui_kit.ORDER 一致", resolved == want,
          "" if resolved == want else f"\n         实际 {resolved}\n         期望 {want}")

# ============================================================ 3. 打包产物

print()
print("== 打包产物 ui-kit/ ==")
bundle_path = KIT / BUNDLE
expected = kit.render()
if not bundle_path.is_file():
    check(f"{BUNDLE} 存在", False, "先跑 python tools/build_ui_kit.py")
else:
    actual = read(bundle_path)
    check(f"{BUNDLE} 与 ui/ 下的源**逐字节**一致（改了源要重跑打包）",
          actual == expected,
          f"文件 {len(actual)} 字符 vs 应为 {len(expected)} 字符；"
          f"跑 python tools/build_ui_kit.py")
    # 每个源文件都应能在产物里找到自己的分隔注释（顺序也就一起核对了）
    missing = [n for n in ORDER if f"ui/{n} ====" not in actual]
    check("产物里 10 个源文件的分隔注释齐全", not missing, missing)
    idx = [actual.find(f"ui/{n} ====") for n in ORDER]
    check("产物里的顺序与 ORDER 一致（分隔注释递增）",
          idx == sorted(idx) and -1 not in idx, idx)

gallery = KIT / "gallery.html"
if gallery.is_file():
    gl = links_of(gallery)
    check(f"画廊只引 1 个样式表（打包产物）", gl == [f"ui-kit/{BUNDLE}"],
          f"{gl}")
else:
    check("画廊存在", False)

# ============================================================ 4. 令牌审计

print()
print("== 令牌：用到的必须定义过 ==")
defined: dict[str, str] = {}
used: dict[str, set[str]] = {}
for name in ORDER:
    text = COMMENT.sub("", read(UI / name))
    for m in DECL.finditer(text):
        defined.setdefault(m.group(1), name)
    for m in USE.finditer(text):
        used.setdefault(m.group(1), set()).add(name)

undefined = sorted(set(used) - set(defined) - set(RUNTIME_VARS))
for name in undefined:
    check(f"{name} 有定义", False,
          f"用在 {','.join(sorted(used[name]))}，但全库没定义；"
          f"若是 JS 写的，加进 RUNTIME_VARS 并写明谁写")

print()
print("== 令牌：定义了的要有人用 ==")
unused = sorted(set(defined) - set(used) - set(RESERVED_VARS))
for name in unused:
    check(f"{name} 被使用", False,
          f"定义在 {defined[name]}，但没人 var() 它；"
          f"若是给后端/契约保留的，加进 RESERVED_VARS 并写明")

print()
print(f"== 令牌统计 ==\n  定义了 {len(defined)} 个，"
      f"用到 {len(used)} 个；运行时提供 {len(RUNTIME_VARS)} 个，"
      f"保留 {len(RESERVED_VARS)} 个")
check("没有未定义的引用（除运行时变量）", not undefined, undefined)
check("没有无人使用的定义（除保留令牌）", not unused, unused)

# ============================================================ 5. 行尾

print()
print("== 行尾（.gitattributes 要求 eol=lf） ==")
crlf = [n for n in ORDER if b"\r\n" in (UI / n).read_bytes()]
check("ui/*.css 都是 LF", not crlf, f"这些是 CRLF：{crlf}")
if bundle_path.is_file():
    check(f"{BUNDLE} 是 LF", b"\r\n" not in bundle_path.read_bytes())

# ============================================================ 汇总

print()
print(f"结果：{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
