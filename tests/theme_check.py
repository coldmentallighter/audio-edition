"""直接读 theme.css，验证 6 套主题的全部对比度与可见度。

和 palette_regen.py 的区别：那个是"从源色板推导"，这个是"校验落盘的文件"。
改了 theme.css 之后跑这个，确保没有手滑写错某个值。
"""
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "ui" / "theme.css").read_text(encoding="utf-8")


def hex2rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def parse_color(v):
    """支持 #RRGGBB 与 rgb(r g b / a)"""
    v = v.strip()
    if v.startswith("#"):
        return hex2rgb(v), 1.0
    m = re.match(r"rgb\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*(?:/\s*([\d.]+))?\s*\)", v)
    if m:
        r, g, b = (float(m.group(i)) / 255 for i in (1, 2, 3))
        a = float(m.group(4)) if m.group(4) else 1.0
        return (r, g, b), a
    raise ValueError(f"看不懂的颜色: {v!r}")


def lum(rgb):
    def f(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (f(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def blend(fg, fa, bg):
    return tuple(fg[i] * fa + bg[i] * (1 - fa) for i in range(3))


def ratio(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------- 解析

BLOCKS = {}
for m in re.finditer(r':root\[data-theme="(t\d)"\](?:\[data-mode="dark"\])?\s*\{(.*?)\n\}',
                     CSS, re.S):
    sel_start = m.start()
    header = CSS[max(0, sel_start - 200):sel_start]
    dark = '[data-mode="dark"]' in CSS[sel_start:sel_start + 60]
    theme = m.group(1)
    toks = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", m.group(2)))
    BLOCKS[(theme, "dark" if dark else "light")] = toks
    # 顺带确认标签页注释里的名称
    _ = header

print(f"解析到 {len(BLOCKS)} 套主题：{sorted(BLOCKS)}")

REQUIRED = ["--ink", "--ink-accent", "--ink-error", "--text-muted", "--text-disabled",
            "--bg-app", "--bg-surface", "--bg-surface-alt", "--fill-primary",
            "--fill-accent", "--on-fill", "--tint-primary", "--tint-accent",
            "--error-tint", "--error-solid", "--border", "--border-strong",
            "--shadow-sm", "--shadow-md", "--scroll-track", "--scroll-thumb"]

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print(f"  FAIL  {label}  {detail}")


print()
print("== 令牌齐全性 ==")
for key, toks in sorted(BLOCKS.items()):
    missing = [t for t in REQUIRED if t not in toks]
    check(f"{key[0]}/{key[1]} 21 个令牌齐全", not missing, missing)
    extra = [t for t in toks if t not in REQUIRED]
    check(f"{key[0]}/{key[1]} 没有多余令牌", not extra, extra)

print("== 对比度（AA 4.5） ==")
PAIRS = [
    ("ink/bg-app", "--ink", "--bg-app"),
    ("ink/bg-surface", "--ink", "--bg-surface"),
    ("ink/bg-surface-alt", "--ink", "--bg-surface-alt"),
    ("ink/tint-primary", "--ink", "--tint-primary"),
    ("ink/tint-accent", "--ink", "--tint-accent"),
    ("ink/error-tint", "--ink", "--error-tint"),
    ("on-fill/fill-primary", "--on-fill", "--fill-primary"),
    ("on-fill/fill-accent", "--on-fill", "--fill-accent"),
]
worst = (99, None)
for key, toks in sorted(BLOCKS.items()):
    row = []
    for label, fg, bg in PAIRS:
        f, fa = parse_color(toks[fg])
        b, ba = parse_color(toks[bg])
        if fa < 1:
            f = blend(f, fa, b)
        v = ratio(f, b)
        if v < worst[0]:
            worst = (v, f"{key[0]}/{key[1]} {label}")
        check(f"{key[0]}/{key[1]} {label}", v >= 4.5, f"{v:.2f}")
        row.append(f"{v:5.2f}")
    # error-solid 上的白字
    es, _ = parse_color(toks["--error-solid"])
    v = ratio((1, 1, 1), es)
    if v < worst[0]:
        worst = (v, f"{key[0]}/{key[1]} white/error-solid")
    check(f"{key[0]}/{key[1]} white/error-solid", v >= 4.5, f"{v:.2f}")
    print(f"  {key[0]}/{key[1]:5}  " + " ".join(row) + f"  白/错误实心 {v:5.2f}")

print("== 可见度（层级要能分开） ==")
# 文档 §3.4 的层级区分度表只针对**浅色**；深色走的是另一套台阶
# （bg-app 0.090 / surface 0.145 / surface-alt 0.210 的 HSL 亮度），
# 换算成相对亮度比大约是 1.26–1.28，所以深色单独放宽。
VIS_LIGHT = [
    ("bg-app/bg-surface", "--bg-app", "--bg-surface", 1.05),
    ("bg-surface/bg-surface-alt", "--bg-surface", "--bg-surface-alt", 1.30),
    ("bg-app/bg-surface-alt", "--bg-app", "--bg-surface-alt", 1.28),
    ("bg-app/tint-primary", "--bg-app", "--tint-primary", 1.12),
    ("bg-app/tint-accent", "--bg-app", "--tint-accent", 1.12),
]
VIS_DARK = [
    # 深色台阶实测就是 1.13–1.19（三套一致，含未改动的 t1），
    # 这里只作为"别塌掉"的回归下限，不是设计目标
    ("bg-app/bg-surface", "--bg-app", "--bg-surface", 1.10),
    ("bg-surface/bg-surface-alt", "--bg-surface", "--bg-surface-alt", 1.18),
]
print(f"  {'主题':10} {'app/surf':>9} {'surf/alt':>9} {'app/alt':>8} "
      f"{'app/tint1':>10} {'app/tint2':>10} {'app/errtint':>12}")
for key, toks in sorted(BLOCKS.items()):
    theme, mode = key
    pairs = VIS_LIGHT if mode == "light" else VIS_DARK
    for label, a, b, need in pairs:
        ca, _ = parse_color(toks[a])
        cb, _ = parse_color(toks[b])
        v = ratio(ca, cb)
        check(f"{theme}/{mode} {label}", v >= need, f"{v:.3f} < {need}")
    a1, _ = parse_color(toks["--bg-app"]); s1, _ = parse_color(toks["--bg-surface"])
    s2, _ = parse_color(toks["--bg-surface-alt"])
    t1, _ = parse_color(toks["--tint-primary"]); t2_, _ = parse_color(toks["--tint-accent"])
    e1, _ = parse_color(toks["--error-tint"])
    print(f"  {theme}/{mode:5} {ratio(a1, s1):9.3f} {ratio(s1, s2):9.3f} "
          f"{ratio(a1, s2):8.3f} {ratio(a1, t1):10.3f} {ratio(a1, t2_):10.3f} "
          f"{ratio(a1, e1):12.3f}")

print("== 语义抽查：错误色不该和本套第一色撞 ==")
for key, toks in sorted(BLOCKS.items()):
    c1, _ = parse_color(toks["--fill-primary"])
    es, _ = parse_color(toks["--error-solid"])
    v = ratio(c1, es)
    check(f"{key[0]}/{key[1]} 错误实心 vs 第一色 能分开", v >= 1.5, f"{v:.2f}")
    print(f"  {key[0]}/{key[1]:5}  {toks['--fill-primary']} vs {toks['--error-solid']}"
          f"   亮度比 {v:.2f}")

print()
print(f"结果：{ok} passed, {fail} failed")
print(f"全站最低对比度：{worst[0]:.2f}  （{worst[1]}）")
sys.exit(1 if fail else 0)
