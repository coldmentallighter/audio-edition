"""生成 UI 演示版的骨架：把前端的真实文件拷进 demo/，并注入两段脚本。

演示版跑的是**同一份** index.html / app.js / api.js / css（不 fork、不改逻辑），
只在最前面插进：
    data/demo-data.js   内置样例数据（由 collect_data.py 采集）
    mock.js             假后端：拦 fetch / XHR / EventSource / 媒体 src
所以「演示版看到的界面」就是真界面 —— 不会出现"演示版会、正式版不会"的偏差。

用法：
    python demo/_build/make_demo.py

改完前端（app.js / css / index.html）要重跑一次，否则 demo/ 里是旧副本。
"""
import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent.parent      # 仓库根
DEMO = ROOT / "demo"

COPY_FILES = ["app.js", "api.js", "theme.css"]
COPY_DIRS = ["css"]

# 注入顺序：数据 → 假后端 → 真前端（app.js / api.js 本来就是页面里的）
INJECT_BEFORE = '<script src="app.js">'
INJECT = ('<!-- 演示版注入：内置样例数据 + 假后端（见 demo/README.md） -->\n'
          '<script src="data/demo-data.js"></script>\n'
          '<script src="mock.js"></script>\n')

BADGE = """
<!-- 演示版角标：说明这是内置样例、不会真的动文件 -->
<div class="demo-badge" id="demoBadge">
  <strong>UI 演示版</strong>
  <span id="demoBadgeText">内置样例数据 · 操作不会真的读写音频文件</span>
  <button type="button" id="demoBadgeClose" aria-label="隐藏">×</button>
</div>
"""


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main():
    DEMO.mkdir(exist_ok=True)
    prov = {"generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"), "sources": {}}

    for name in COPY_FILES:
        src, dst = ROOT / name, DEMO / name
        shutil.copyfile(src, dst)
        prov["sources"][name] = {"sha256_16": sha(src), "bytes": src.stat().st_size}

    for d in COPY_DIRS:
        out = DEMO / d
        if out.exists():
            shutil.rmtree(out)
        out.mkdir()
        for f in sorted((ROOT / d).glob("*.css")):
            shutil.copyfile(f, out / f.name)
        prov["sources"][f"{d}/"] = {
            f.name: sha(f) for f in sorted((ROOT / d).glob("*.css"))}

    html = (ROOT / "index.html").read_text(encoding="utf-8")
    if INJECT_BEFORE not in html:
        print("!! index.html 里找不到 <script src=\"app.js\">，注入点变了")
        return 2
    html = html.replace(INJECT_BEFORE, INJECT + INJECT_BEFORE)

    # 演示版自己的样式（角标）+ 真样式
    html = html.replace('</head>',
                        '<link rel="stylesheet" href="demo.css">\n</head>')
    html = html.replace('</body>', BADGE + "</body>")
    (DEMO / "index.html").write_text(html, encoding="utf-8")
    prov["sources"]["index.html"] = {"sha256_16": sha(ROOT / "index.html"),
                                     "note": "注入 demo-data.js / mock.js / demo.css / 角标"}

    # 校验：真样式表一个都不能少，顺序也不能变（拆分后的层叠依赖顺序）
    order = re.findall(r'href="(css/[^"]+|theme\.css)"', html)
    prov["cssOrder"] = order
    need = ["theme.css"] + [f"css/{n}.css" for n in
                            ["base", "common", "header", "layout", "filecard",
                             "drawer", "cardlib", "overlays", "editor"]]
    if order != need:
        print("!! 样式表顺序/内容与预期不一致：", order)
        return 2

    (DEMO / "_build" / "provenance.json").write_text(
        json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")

    missing = [f for f in ("data/demo-data.js", "mock.js", "demo.css")
               if not (DEMO / f).exists()]
    print(f"demo/index.html 已生成；样式表 {len(order)} 个，顺序校验通过")
    print("provenance.json 记录了对源文件的 sha256_16")
    if missing:
        print("!! 还缺：", "、".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
