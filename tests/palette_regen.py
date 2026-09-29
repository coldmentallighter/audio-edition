"""t2/t3 第二色 ↔ 错误色对调 + t2 绿色降饱和：推导新令牌并做全量对比度验证。

保留原则：**已求解好的亮度台阶不动**，只换色相族；色相变了导致某条对比度不够时，
才在最小范围内微调 L（并打印出来），避免动到用户已经认可的层级关系。
"""
import colorsys
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb2hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02X}" for c in rgb)


def lum(rgb):
    def f(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (f(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    a = hex2rgb(a) if isinstance(a, str) else a
    b = hex2rgb(b) if isinstance(b, str) else b
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def hls(c):
    r, g, b = hex2rgb(c) if isinstance(c, str) else c
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360, s, l


def from_hls(h, s, l):
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360, l, s)
    return rgb2hex((r, g, b))


def desat(c, k):
    h, s, l = hls(c)
    return from_hls(h, s * k, l)


def at_hue_like(source_token, hue):
    """取 source_token 的 S/L，换成 hue —— 亮度台阶保持不变"""
    _, s, l = hls(source_token)
    return from_hls(hue, s, l), s, l


# ---------------------------------------------------------------- 改动后的源色板

GREEN_K = 0.64
VIS_FILL = 1.14      # 同级色块的最小区分度（§3.5 用的阈值）
NEW = {
    "t2": {"c1": desat("#B5E8B4", GREEN_K), "c2": "#FFE5B4", "err": "#FEC7FF",
           "ink": "#2D602D"},
    "t3": {"c1": "#D6C7FF", "c2": "#FFC5B4", "err": "#F0FFC7",
           "ink": "#554F65"},
}

# 当前 theme.css 里要用作"亮度模板"的令牌
TEMPLATE = {
    "t2": {
        "bg-app": "#F4F9F4", "bg-surface-alt": "#D4DBD4",
        "tint-primary": "#DEEFDE", "tint-accent": "#F2E3F2", "error-tint": "#DEE4EF",
        "dark-bg-app": "#121C11", "dark-bg-surface": "#1D2D1D",
        "dark-bg-surface-alt": "#2B402B", "dark-tint-primary": "#2C572B",
        "dark-tint-accent": "#562B57", "dark-error-tint": "#283650",
        "dark-fill-primary": "#99D897", "dark-fill-accent": "#D99FDB",
        "dark-ink-accent": "#E9B8EA", "dark-ink-error": "#B0C3E8",
    },
    "t3": {
        "bg-app": "#F5F4F9", "bg-surface-alt": "#D6D4DB",
        "tint-primary": "#E7E3F2", "tint-accent": "#E4EAD3", "error-tint": "#E3EFF2",
        "dark-bg-app": "#14111C", "dark-bg-surface": "#211D2D",
        "dark-bg-surface-alt": "#312B40", "dark-tint-primary": "#372B57",
        "dark-tint-accent": "#4B572B", "dark-error-tint": "#284750",
        "dark-fill-primary": "#A997D8", "dark-fill-accent": "#CBDB9F",
        "dark-ink-accent": "#DDEAB8", "dark-ink-error": "#B0DBE8",
    },
}

print("=" * 78)
print("1) 亮度模板（沿用现有令牌的 S/L，只换色相）")
print("=" * 78)
for t in ("t2", "t3"):
    print(f"\n--- {t} ---")
    for k, v in TEMPLATE[t].items():
        h, s, l = hls(v)
        print(f"  {k:20} {v}  H={h:6.1f}° S={s*100:5.1f}% L={l:.4f}")

# ---------------------------------------------------------------- 推导

def derive(t):
    src = NEW[t]
    tpl = TEMPLATE[t]
    h1, _, _ = hls(src["c1"])
    h2, _, _ = hls(src["c2"])
    he, _, _ = hls(src["err"])
    ink = src["ink"]

    # 错误 tint 族用哪个色相：错误色相本身；若与主题第一色撞（<30°）才取补色
    def hue_gap(a, b):
        d = abs((a - b) % 360)
        return min(d, 360 - d)
    err_hue = he if hue_gap(he, h1) >= 30 else (he + 180) % 360
    err_hue_rule = "错误色相" if err_hue == he else "错误色相补色"

    out = {"_meta": {"errHueRule": err_hue_rule,
                     "errHueGapToPrimary": round(hue_gap(he, h1), 1)}}

    # ---- 浅色 ----
    out["bg-app"] = desat(tpl["bg-app"], GREEN_K) if t == "t2" else tpl["bg-app"]
    out["bg-surface-alt"] = (desat(tpl["bg-surface-alt"], GREEN_K) if t == "t2"
                             else tpl["bg-surface-alt"])
    out["fill-primary"] = src["c1"]
    out["scroll-thumb"] = src["c1"]

    # --fill-accent：保留源 c2 原值。
    # 只在"在页底上几乎看不见"时才压暗（§3.5 的 VIS_FILL vs bg-app 规则）。
    # 刻意**不**为"第一色 vs 第二色亮度接近"去改：文档 §2 明确接受这一点
    # （t1 区分度 1.08，已发布的 t2 更是 1.02），理由是色相差足够时人眼能分开。
    # 真按亮度差硬压，t3 的珊瑚会被压到近黑，等于毁掉整块。
    fa = src["c2"]
    h_fa, s_fa, l_fa = hls(fa)
    step = 0
    while ratio(fa, out["bg-app"]) < VIS_FILL and ratio(ink, fa) >= 4.5 and step < 500:
        l_fa -= 0.002
        step += 1
        fa = from_hls(h_fa, s_fa, l_fa)
    out["fill-accent"] = fa
    out["fill-accent_nudged"] = step
    out["fill-accent_distinct"] = round(ratio(fa, src["c1"]), 3)

    # tint 族：同时满足 ink 可读(≥4.5) 与 vs bg-app 可见(≥1.13)，取最浅
    for slot, hue in (("tint-primary", h1), ("tint-accent", h2), ("error-tint", err_hue)):
        _, s, l = hls(tpl[slot])
        c = from_hls(hue, s, l)
        step = 0
        while (ratio(ink, c) < 4.5 or ratio(c, out["bg-app"]) < 1.13) and step < 500:
            l -= 0.002
            step += 1
            c = from_hls(hue, s, l)
        out[slot] = c
        out[slot + "_nudged"] = step

    # ---- error-solid：错误色相，降亮度到白字 ≥5.0 ----
    # 彩度要压到 45% 以内：源错误色是 S=100% 的粉彩，直接压暗会得到
    # #C400C8 这种刺眼的荧光块。文档里 t1 的 #9C3F73 也是 S≈43% 的闷调子。
    _, s_e, l_e = hls(src["err"])
    s_solid = min(s_e, 0.45)
    best = None
    l = l_e
    while l > 0.05:
        c = from_hls(he, s_solid, l)
        if ratio("#FFFFFF", c) >= 5.0:
            best = c
            break
        l -= 0.002
    out["error-solid"] = best

    # ---- 深色 ----
    out["dark-bg-app"] = desat(tpl["dark-bg-app"], GREEN_K) if t == "t2" else tpl["dark-bg-app"]
    out["dark-bg-surface"] = desat(tpl["dark-bg-surface"], GREEN_K) if t == "t2" else tpl["dark-bg-surface"]
    out["dark-bg-surface-alt"] = desat(tpl["dark-bg-surface-alt"], GREEN_K) if t == "t2" else tpl["dark-bg-surface-alt"]
    out["dark-tint-primary"] = (desat(tpl["dark-tint-primary"], GREEN_K) if t == "t2"
                                else tpl["dark-tint-primary"])

    # 深色 fill = 同色相 L≈0.72 的浅块
    _, _, lf = hls(tpl["dark-fill-primary"])
    out["dark-fill-primary"] = from_hls(h1, hls(tpl["dark-fill-primary"])[1] *
                                        (GREEN_K if t == "t2" else 1), lf)
    _, sf, _ = hls(tpl["dark-fill-accent"])
    out["dark-fill-accent"] = from_hls(h2, sf, lf)

    # 深色 tint = 同色相 L≈现有值
    for slot, hue, tslot in (("dark-tint-accent", h2, "dark-tint-accent"),
                             ("dark-error-tint", err_hue, "dark-error-tint")):
        _, s, l = hls(tpl[tslot])
        out[slot] = from_hls(hue, s, l)

    # 深色 ink-accent / ink-error = 对应色相的高亮浅色，需在深底上可读
    bg_dark = out["dark-bg-surface"]
    for slot, hue, tslot, target in (("dark-ink-accent", h2, "dark-ink-accent", 4.5),
                                     ("dark-ink-error", err_hue, "dark-ink-error", 4.5)):
        _, s, l = hls(tpl[tslot])
        c = from_hls(hue, s, l)
        step = 0
        while ratio(c, bg_dark) < target and step < 500:
            l += 0.002
            step += 1
            c = from_hls(hue, s, l)
        out[slot] = c
        out[slot + "_nudged"] = step

    return out


RESULT = {t: derive(t) for t in ("t2", "t3")}

print()
print("=" * 78)
print("2) 推导结果")
print("=" * 78)
for t, r in RESULT.items():
    print(f"\n--- {t}  (错误 tint 用{hls(NEW[t]['err'])[0]:.0f}° → {r['_meta']['errHueRule']}, "
          f"与主色相差 {r['_meta']['errHueGapToPrimary']}°) ---")
    for k in ("bg-app", "bg-surface-alt", "fill-primary", "fill-accent", "scroll-thumb",
              "tint-primary", "tint-accent", "error-tint", "error-solid",
              "dark-bg-app", "dark-bg-surface", "dark-bg-surface-alt",
              "dark-fill-primary", "dark-fill-accent", "dark-tint-primary",
              "dark-tint-accent", "dark-error-tint", "dark-ink-accent", "dark-ink-error"):
        v = r.get(k)
        h, s, l = hls(v)
        nud = r.get(k + "_nudged", 0)
        print(f"  {k:22} {v}  H={h:6.1f}° S={s*100:5.1f}% L={l:.4f}"
              + (f"   (压暗 {nud} 步)" if nud else ""))

# ---------------------------------------------------------------- 验证

print()
print("=" * 78)
print("3) 对比度验证（AA 门槛 4.5）")
print("=" * 78)

# 合并完整令牌表用于验证
FULL = {}
for t in ("t2", "t3"):
    r = RESULT[t]
    full = {
        "ink": NEW[t]["ink"], "on-fill": NEW[t]["ink"],
        "bg-app": r["bg-app"], "bg-surface": "#FFFFFF", "bg-surface-alt": r["bg-surface-alt"],
        "fill-primary": r["fill-primary"], "fill-accent": r["fill-accent"],
        "tint-primary": r["tint-primary"], "tint-accent": r["tint-accent"],
        "error-tint": r["error-tint"], "error-solid": r["error-solid"],
        "dark-ink": "#F2F6FA", "dark-on-fill": "#10171C",
        "dark-bg-app": r["dark-bg-app"], "dark-bg-surface": r["dark-bg-surface"],
        "dark-bg-surface-alt": r["dark-bg-surface-alt"],
        "dark-fill-primary": r["dark-fill-primary"], "dark-fill-accent": r["dark-fill-accent"],
        "dark-tint-primary": r["dark-tint-primary"], "dark-tint-accent": r["dark-tint-accent"],
        "dark-error-tint": r["dark-error-tint"],
        "dark-ink-accent": r["dark-ink-accent"], "dark-ink-error": r["dark-ink-error"],
    }
    FULL[t] = full

PAIRS = [
    ("ink / bg-app", "ink", "bg-app", 4.5),
    ("ink / bg-surface", "ink", "bg-surface", 4.5),
    ("ink / bg-surface-alt", "ink", "bg-surface-alt", 4.5),
    ("ink / tint-primary", "ink", "tint-primary", 4.5),
    ("ink / tint-accent", "ink", "tint-accent", 4.5),
    ("ink / error-tint", "ink", "error-tint", 4.5),
    ("on-fill / fill-primary", "on-fill", "fill-primary", 4.5),
    ("on-fill / fill-accent", "on-fill", "fill-accent", 4.5),
    ("white / error-solid", None, "error-solid", 4.5),
    ("dark ink / bg-app", "dark-ink", "dark-bg-app", 4.5),
    ("dark ink / bg-surface", "dark-ink", "dark-bg-surface", 4.5),
    ("dark ink / bg-surface-alt", "dark-ink", "dark-bg-surface-alt", 4.5),
    ("dark ink / tint-primary", "dark-ink", "dark-tint-primary", 4.5),
    ("dark ink / tint-accent", "dark-ink", "dark-tint-accent", 4.5),
    ("dark ink / error-tint", "dark-ink", "dark-error-tint", 4.5),
    ("dark on-fill / fill-primary", "dark-on-fill", "dark-fill-primary", 4.5),
    ("dark on-fill / fill-accent", "dark-on-fill", "dark-fill-accent", 4.5),
    ("dark ink-accent / bg-surface", "dark-ink-accent", "dark-bg-surface", 4.5),
    ("dark ink-error / bg-surface", "dark-ink-error", "dark-bg-surface", 4.5),
]

fails = []
for t in ("t2", "t3"):
    print(f"\n--- {t} ---")
    for label, fg, bg, need in PAIRS:
        if fg is None:
            a, b = "#FFFFFF", FULL[t][bg]
        else:
            a, b = FULL[t][fg], FULL[t][bg]
        v = ratio(a, b)
        flag = "✓" if v >= need else "✗"
        if v < need:
            fails.append((t, label, round(v, 2)))
        print(f"  {label:32} {v:6.2f}  {flag}")

print()
print("=" * 78)
print("4) 可见度（同级块面要能分开）")
print("=" * 78)
VIS = [
    ("bg-app / bg-surface", "bg-app", "bg-surface", 1.05),
    ("bg-surface / bg-surface-alt", "bg-surface", "bg-surface-alt", 1.30),
    ("bg-app / bg-surface-alt", "bg-app", "bg-surface-alt", 1.28),
    ("bg-app / tint-primary", "bg-app", "tint-primary", 1.13),
    ("bg-app / tint-accent", "bg-app", "tint-accent", 1.13),
    ("bg-app / fill-primary", "bg-app", "fill-primary", 1.14),
    ("bg-app / fill-accent", "bg-app", "fill-accent", 1.14),
    ("fill-primary vs fill-accent（仅记录）", "fill-primary", "fill-accent", 1.00),
]
for t in ("t2", "t3"):
    print(f"\n--- {t} ---")
    for label, a, b, need in VIS:
        v = ratio(FULL[t][a], FULL[t][b])
        flag = "✓" if v >= need else "✗"
        if v < need:
            fails.append((t, label, round(v, 2)))
        print(f"  {label:34} {v:6.3f}  {flag}")

print()
if fails:
    print(f"!! {len(fails)} 项未达标：")
    for f in fails:
        print("   ", f)
else:
    print("全部达标 ✓")
