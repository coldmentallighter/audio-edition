"""Locate every distinct ink blob in a render, to see where things actually landed.

If the render is missing it is produced first, so this works from a clean checkout --
a diagnostic that only runs after someone else already ran it is not a diagnostic.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / ".cache" / "_look"
# a missing output dir must not crash a verification run
OUT.mkdir(parents=True, exist_ok=True)


def ensure_render(name: str, scale: float) -> Path:
    p = OUT / name
    if p.exists():
        return p
    subprocess.run([sys.executable,
                    str(Path(__file__).resolve().parent / "render_reference.py"),
                    str(scale)], check=True)
    return p


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "cq_faithful.png"
    s = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
    im = Image.open(ensure_render(name, s)).convert("L")
    W, H = im.size
    px = im.load()
    dark = {(x, y) for y in range(H) for x in range(W) if px[x, y] < 150}
    print(f"{name}: {W}x{H}, {len(dark)} dark px")

    seen: set[tuple[int, int]] = set()
    blobs = []
    for p in dark:
        if p in seen:
            continue
        stack = [p]
        seen.add(p)
        mnx = mxx = p[0]
        mny = mxy = p[1]
        n = 0
        while stack:
            x, y = stack.pop()
            n += 1
            mnx = min(mnx, x); mxx = max(mxx, x)
            mny = min(mny, y); mxy = max(mxy, y)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    q = (x + dx, y + dy)
                    if q in dark and q not in seen:
                        seen.add(q)
                        stack.append(q)
        if n >= 20:
            blobs.append((n, mnx, mny, mxx, mxy))
    blobs.sort(key=lambda b: (b[2], b[1]))
    print(f"{len(blobs)} blobs with >=20 px")
    print(f"{'px':>7} {'x0':>7} {'y0':>7} {'x1':>7} {'y1':>7}   raw(x0,y0)-(x1,y1)")
    for n, x0, y0, x1, y1 in blobs[:40]:
        print(f"{n:>7} {x0:>7} {y0:>7} {x1:>7} {y1:>7}   "
              f"({x0 / s:.0f},{y0 / s:.0f})-({x1 / s:.0f},{y1 / s:.0f})")


if __name__ == "__main__":
    main()
