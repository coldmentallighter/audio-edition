"""把 `backend/loudness_svg.py` 画出来的整页渲染成图，供人眼核对。

    python tests/dsh-wheel/render_svg_preview.py            # 用 uploads 里章节最多的文件
    python tests/dsh-wheel/render_svg_preview.py --file X   # 指定文件
    python tests/dsh-wheel/render_svg_preview.py --theme t2 --mode dark
    python tests/dsh-wheel/render_svg_preview.py --png      # 顺便让 harness 截图（见提示）

`--theme` / `--mode` 就是老板执行链时的那两个值（`data-theme` / `data-mode`），
默认 `t1`/`light`。**六套都看一眼**是这一步的意义 —— 颜色现在是硬编码进文件的，
页面的主题换不了它，只有渲染时那个值算数。

产物落在 `.cache/_look/`：
  * `loudness.svg`      —— 真正的导出物
  * `loudness.html`     —— 套一层 HTML（截图工具只吃本地 .html）

⚠ 本机的 `chrome-headless-shell.exe` 被沙箱的命名管道限制挡着（`platform_channel.cc:108
拒绝访问`），所以**仓库自己的测试栅格化不了**。截图这一步由 harness 的视觉工具做
（沙箱外）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from backend import audio                                             # noqa: E402
from backend import chart_layout as L                                 # noqa: E402
from backend import loudness_svg                                      # noqa: E402
from backend import theme as theme_mod                                 # noqa: E402


def _size_text(n: int) -> str:
    for unit, div in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def _algorithm() -> str:
    """「测量算法」那一格的值。

    卡只有 200pt 宽，`ebur128 / FFmpeg <版本>` 放不下（会被截），所以取短写法 ——
    与 `tasks._algorithm_text()` 保持一致，否则预览看到的和线上出的不是一回事。
    """
    import re                                                          # noqa: PLC0415
    from backend.toolchain import toolchain                            # noqa: PLC0415
    try:
        raw = getattr(toolchain.get("ffmpeg"), "version", "") or ""
    except Exception:                                                  # noqa: BLE001
        raw = ""
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", raw)
    return f"ebur128 {m.group(1)}" if m else "ebur128"


def pick_file(explicit: str | None) -> Path:
    if explicit:
        return (ROOT / explicit).resolve() if not Path(explicit).is_absolute() \
            else Path(explicit)
    up = ROOT / "uploads"
    if up.is_dir():
        cands = [p for p in sorted(up.iterdir()) if p.is_file()]
        if cands:
            return cands[0].resolve()
    raise SystemExit("uploads/ 里没有素材，用 --file 指定一个")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--marker-file", help="marker 从哪个文件读（默认同 --file）")
    ap.add_argument("--out", default=str(ROOT / ".cache" / "_look" / "loudness.svg"))
    ap.add_argument("--theme", default=theme_mod.DEFAULT_THEME,
                    help="t1 / t2 / t3（= 页面的 data-theme）")
    ap.add_argument("--mode", default=theme_mod.DEFAULT_MODE,
                    help="light / dark（= 页面的 data-mode）")
    a = ap.parse_args()
    theme, mode = theme_mod.normalize(a.theme, a.mode)

    src = pick_file(a.file)
    mfile = Path(a.marker_file).resolve() if a.marker_file else src

    data = audio.loudness_timeline(src)
    info = audio.probe(src)
    marks = audio.chapters(mfile)
    print(f"source      : {src.name}")
    print(f"duration    : {data.get('duration'):.1f}s  frames={len(data.get('t') or [])}")
    print(f"summary     : {data.get('summary')}")
    print(f"markers     : {len(marks)}  {marks[:3]}")

    meta = {
        "title": (info.tags.get("title") or "").strip(),
        "artist": (info.tags.get("artist") or "").strip(),
        "album": (info.tags.get("album") or "").strip(),
        "track": (info.tags.get("track") or "").strip(),
        "duration": info.duration,
        "channels": info.channels,
        "sampleRate": info.sample_rate,
        "bits": info.bits,
        "sizeText": _size_text(info.size),
        "algorithm": _algorithm(),
    }
    print(f"algorithm   : {meta['algorithm']}")
    print(f"theme       : {theme}/{mode}")

    svg = loudness_svg.render_loudness_svg(data, title=src.name, meta=meta,
                                           markers=marks,
                                           patterns=(data.get("summary") or {})
                                           .get("drpOccurrences") or [],
                                           theme=theme, mode=mode)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    html = out.with_suffix(".html")
    # ⚠ 套壳那层的白底必须跟图的画布一致，否则深色主题的截图边上会是一圈白
    page_bg = loudness_svg.chart_palette(theme, mode)["bg"]
    html.write_text(
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        f"<title>{src.name}</title>"
        f"<style>html,body{{margin:0;background:{page_bg}}}"
        "svg{display:block;width:1100px;height:auto}</style></head><body>\n"
        + svg + "\n</body></html>\n", encoding="utf-8")
    print(f"\nwrote {out}  ({len(svg)} bytes)")
    print(f"wrote {html}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
