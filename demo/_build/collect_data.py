"""从**真实后端**采集 UI 演示版所需的数据与资产。

演示版（demo/）不跑 ffmpeg、不跑 Python 后端，但要"看起来是真的"：
文件列表、卡片定义、每首歌的真实波形、真实内嵌封面、真实可播放的音频
—— 这些都由这个脚本从跑着的后端里 dump 出来。

用法（需要真实后端在 8765 上跑着，且可以随便清空它的 uploads/）：

    python demo/_build/collect_data.py

产物：
    demo/data/demo-data.js      # window.__AE_DEMO__ = {files, cards, ops, tags, peaks, logs}
    demo/assets/audio/<id>.<ext>
    demo/assets/covers/<id>.jpg|png

脚本是幂等的：每次都重新造一遍（用 --keep 可以不清空旧文件，一般不需要）。
"""
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zlib
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "http://127.0.0.1:8765"
ROOT = Path(__file__).resolve().parent.parent          # demo/
DEMO = ROOT
# 中间产物（生成的源音频、封面图）放到系统临时目录：
# 放 demo/_build/_work 会让演示包里多出 1MB 谁也用不上的草稿文件。
WORK = Path(tempfile.gettempdir()) / "ae_demo_work"

# ---------------------------------------------------------------- 样例设计
# 每项: (相对路径, 生成表达式, 编码参数, 标签, 是否嵌封面)
# aevalsrc 表达式都做了包络，波形各不相同 —— 列表上的波形才不会像复制粘贴。
TRACKS = [
    ("演示素材/夜航/01 夜航.flac",
     "0.55*sin(2*PI*220*t)*(0.25+0.75*abs(sin(2*PI*0.6*t)))",
     ["-c:a", "flac", "-compression_level", "5"],
     {"title": "夜航", "artist": "林昭", "album": "夜航", "tracknumber": "1"}, True),

    ("演示素材/夜航/02 潮汐.wav",
     "0.6*sin(2*PI*(180+120*t)*t)",
     ["-c:a", "pcm_s16le"],
     {"title": "潮汐", "artist": "林昭", "album": "夜航", "tracknumber": "2"}, False),

    ("演示素材/夜航/03 候鸟.mp3",
     None,   # 用 anoisesrc
     ["-c:a", "libmp3lame", "-b:a", "128k"],
     {"title": "候鸟", "artist": "林昭", "album": "夜航", "tracknumber": "3"}, True),

    ("演示素材/城市光/01 城市光.flac",
     "0.9*sin(2*PI*80*t)*exp(-4*abs(sin(2*PI*1.1*t)))",
     ["-c:a", "flac", "-compression_level", "8"],
     {"title": "城市光", "artist": "Syuenn", "album": "城市光", "tracknumber": "1"}, True),

    ("演示素材/城市光/02 天台.m4a",
     "0.5*sin(2*PI*330*t)*exp(-2*t)",
     ["-c:a", "aac", "-b:a", "128k"],
     {"title": "天台", "artist": "Syuenn", "album": "城市光", "tracknumber": "2"}, False),

    ("演示素材/城市光/03 末班车.ogg",
     "0.7*(sin(2*PI*110*t)+0.5*sin(2*PI*165*t))*exp(-1.2*t)",
     ["-c:a", "libvorbis", "-q:a", "4"],
     {"title": "末班车", "artist": "Syuenn", "album": "城市光", "tracknumber": "3"}, False),

    ("演示素材/单曲/试音 1kHz.flac",
     "0.85*sin(2*PI*1000*t)",
     ["-c:a", "flac"],
     {},                      # 故意不给标签：演示 title 回落到文件名
     False),

    ("演示素材/单曲/环境底噪.opus",
     "0.35*(sin(2*PI*90*t)+0.4*sin(2*PI*140*t))*abs(sin(2*PI*0.35*t))",
     ["-c:a", "libopus", "-b:a", "96k"],
     {"title": "环境底噪", "artist": "Various", "album": "素材库"}, False),
]

IMAGE_REL = "演示素材/封面素材/封面底图.png"
COVER_SEEDS = [((210, 71, 43), (255, 176, 59)),      # 红→橙
               ((32, 78, 143), (56, 189, 196)),      # 蓝→青
               ((24, 108, 84), (232, 223, 195))]     # 绿→米

DUR = "4"


# ------------------------------------------------------------------ 工具

def req(method, path, body=None, raw=None, ctype=None, base=BASE):
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if raw is not None:
        data = raw
        headers["Content-Type"] = ctype
    r = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode("utf-8", "replace")[:200]}


def get_bytes(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as resp:
        return resp.read()


def write_png(path, w, h, pixel):
    """最小 PNG 编码器（不依赖 Pillow）。pixel(x,y) -> (r,g,b)"""
    raw = bytearray()
    for y in range(h):
        raw.append(0)                       # filter type 0
        for x in range(w):
            raw += bytes(pixel(x, y))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


def make_cover(path, base_rgb, accent_rgb, size=420):
    """对角渐变底 + 一个大圆 + 两条斜带 —— 像抽象专辑封面，好认。"""
    br, bg, bb = base_rgb
    ar, ag, ab = accent_rgb
    cx, cy, rad = size * 0.62, size * 0.40, size * 0.24

    def px(x, y):
        t = (x + y) / (2 * size)
        r = int(br * (1 - 0.45 * t))
        g = int(bg * (1 - 0.45 * t))
        b = int(bb * (1 - 0.45 * t))
        d = math.hypot(x - cx, y - cy)
        if d < rad:
            k = 1 - d / rad
            r = int(r * (1 - k) + ar * k)
            g = int(g * (1 - k) + ag * k)
            b = int(b * (1 - k) + ab * k)
        # 底部斜带
        band = (x * 0.5 + y * 1.6) % (size * 0.42)
        if y > size * 0.72 and band < size * 0.085:
            r = int(r * 0.35 + ar * 0.65)
            g = int(g * 0.35 + ag * 0.65)
            b = int(b * 0.35 + ab * 0.65)
        return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))

    write_png(path, size, size, px)


def gen_audio(out: Path, expr, enc, dur=DUR):
    """表达式里**不能出现逗号，也不要用比较运算符**。

    逗号分隔滤镜链，`mod(t,0.4)` 会被从中间切开（"No option name near '4:s=44100'"）；
    转义成 `\\,` 又过不了表达式求值器（"Missing ')'"）。
    比较运算符 `>` 同样报 "Missing ')'"（实测 ffmpeg 9.0.2）。
    所以包络一律用 `abs()` / `exp()` 拼，不加逗号、不做比较：
      · 慢速起伏  `0.25+0.75*abs(sin(2*PI*0.6*t))`
      · 打击感    `exp(-4*abs(sin(2*PI*1.1*t)))`
    """
    if expr is None:
        src = ["-f", "lavfi", "-i", f"anoisesrc=d={dur}:c=pink:a=0.45"]
    else:
        assert "," not in expr, f"表达式不能含逗号: {expr}"
        assert ">" not in expr and "<" not in expr, f"表达式不能用比较运算符: {expr}"
        src = ["-f", "lavfi", "-i", f"aevalsrc={expr}:d={dur}:s=44100"]
    # libopus 只认 48k/24k/16k/12k/8k，喂 44100 直接拒绝
    ar = "48000" if "libopus" in enc else "44100"
    subprocess.run(["ffmpeg", "-y", "-v", "error", *src, "-ac", "1", "-ar", ar,
                    *enc, str(out)], check=True)


def multipart(paths, files):
    b = "----demo" + uuid.uuid4().hex
    out = bytearray()
    out += f"--{b}\r\nContent-Disposition: form-data; name=\"paths\"\r\n\r\n{json.dumps(paths)}\r\n".encode()
    for fn, content, mime in files:
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"files\";"
                f" filename=\"{fn}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
        out += content + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={b}"


def wait_all(timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        q = req("GET", "/api/tasks/queue")
        if not q.get("running") and not q.get("pending"):
            return True
        time.sleep(0.25)
    return False


# ------------------------------------------------------------------ 主流程

def scrub_health(h):
    """工具链的**绝对路径**（本机 WinGet 目录）不该跟着演示包分发：
    界面上的引擎条只显示工具名与版本号（`applyServerHealth` 只读 ok/version）。"""
    for v in (h.get("tools") or {}).values():
        if isinstance(v, dict):
            v.pop("path", None)
    srv = h.get("server")
    if isinstance(srv, dict):
        for k in ("root", "dir", "path", "projectRoot", "uploads", "outputs"):
            srv.pop(k, None)
    return h


def sanitize_queue(queue):
    """把任务里的 `result.command` 摘掉：那是本机绝对路径
    （...\\WinGet\\...\\metaflac.exe），既没被界面显示，又没必要跟着演示包分发。"""
    for k in ("running", "pending", "recent"):
        for t in queue.get(k) or []:
            r = t.get("result")
            if isinstance(r, dict):
                r.pop("command", None)
                r.pop("cmd", None)
    return queue


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    (DEMO / "data").mkdir(parents=True, exist_ok=True)
    # 资产目录先清空：不清的话上一轮 dump 的 <旧id>.flac / <旧id>.jpg 会留在包里，
    # 数出来"封面 8 张"而其实只有 4 张（实测踩过）。
    for sub in ("media", "covers", "outputs"):
        d = DEMO / "assets" / sub
        d.mkdir(parents=True, exist_ok=True)
        for p in d.glob("*"):
            p.unlink()
    old_audio_dir = DEMO / "assets" / "audio"
    if old_audio_dir.exists():                 # 早期版本用的目录名
        for p in old_audio_dir.glob("*"):
            p.unlink()

    h = req("GET", "/api/health")
    if not h.get("ok"):
        print("!! 后端没起来（%s），先 run.bat 再跑本脚本" % BASE)
        return 2

    # 0) 清空旧数据，保证每次 dump 一致
    old = req("GET", "/api/files?includeDeleted=true").get("files", [])
    if old:
        req("POST", "/api/files/delete",
            {"fileIds": [f["id"] for f in old], "purge": True, "withDisk": True})
        print(f"清掉旧文件 {len(old)} 个")

    # 1) 造源文件
    paths, files = [], []
    for i, (rel, expr, enc, tags, with_cover) in enumerate(TRACKS):
        src = WORK / Path(rel).name
        gen_audio(src, expr, enc)
        paths.append(rel)
        files.append((src.name, src.read_bytes(), "application/octet-stream"))
        print(f"生成 {rel}  ({src.stat().st_size // 1024} KB)")

    img = WORK / "封面底图.png"
    make_cover(img, *COVER_SEEDS[0])
    paths.append(IMAGE_REL)
    files.append((img.name, img.read_bytes(), "image/png"))

    # 2) 上传
    raw, ctype = multipart(paths, files)
    up = req("POST", "/api/upload", raw=raw, ctype=ctype)
    saved = up.get("saved", [])
    print("上传:", up.get("message"), "errors:", up.get("errors"))
    if len(saved) != len(TRACKS) + 1:
        print("!! 上传数量不对:", len(saved))
        return 2

    by_rel = {s["relPath"]: s for s in saved}
    img_row = by_rel[IMAGE_REL]

    # 3) 探测（并发 2，会排队）
    for s in saved:
        req("POST", f"/api/files/{s['id']}/probe")
    wait_all()

    # 4) 写标签 + 嵌封面
    cover_src_ids = []
    cover_i = 0
    for rel, expr, enc, tags, with_cover in TRACKS:
        row = by_rel[rel]
        if tags:
            r = req("PUT", f"/api/files/{row['id']}/tags",
                    {"tags": tags, "clearMissing": False})
            if r.get("_status"):
                print("  写标签失败", rel, r)
        if with_cover:
            cp = WORK / (Path(rel).stem + "-cover.png")
            make_cover(cp, *COVER_SEEDS[cover_i % len(COVER_SEEDS)])
            cover_i += 1
            # 封面图先上传进 uploads，再嵌入
            cpaths = [f"covers/{cp.name}"]
            craw, cctype = multipart(cpaths, [(cp.name, cp.read_bytes(), "image/png")])
            cup = req("POST", "/api/upload", raw=craw, ctype=cctype)
            cimg = cup["saved"][0]
            cover_src_ids.append(cimg["id"])
            req("POST", "/api/ops/cover",
                {"fileIds": [row["id"]], "imagePath": cimg["relPath"],
                 "pictureType": "Front Cover"})
            wait_all()
            print(f"  嵌入封面 {rel}")

    # 4.5) 封面**素材**用过就删掉：它们同样是合法的文件行，
    #      留着会让演示列表里多出 3 张 `covers/*.png`，看着像垃圾数据。
    if cover_src_ids:
        req("POST", "/api/files/delete",
            {"fileIds": cover_src_ids, "purge": True, "withDisk": True})
        print(f"清掉封面素材 {len(cover_src_ids)} 个")

    # 5) dump 文件列表（含真实 info）
    fl = req("GET", "/api/files")
    rows = fl.get("files", [])
    print(f"文件 {len(rows)} 个")

    # 6) 逐个 dump 标签、峰值、封面、媒体文件
    #    assets 映射直接写进 payload：mock.js 据此把 /api/... 的 URL 换成本地文件，
    #    不去猜扩展名（封面可能是 .jpg 也可能是 .png）。
    tags_map, peaks_map = {}, {}
    assets = {"media": {}, "covers": {}}
    for r in rows:
        fid = r["id"]
        t = req("GET", f"/api/files/{fid}/tags")
        tags_map[fid] = t.get("tags", {}) if not t.get("_status") else {}
        if r.get("kind") == "audio":
            p = req("GET", f"/api/files/{fid}/peaks?buckets=900")
            if not p.get("_status"):
                peaks_map[fid] = p["peaks"]
        ext = Path(r["name"]).suffix.lower()
        rel = f"assets/media/{fid}{ext}"
        try:
            (DEMO / rel).write_bytes(get_bytes(f"/api/files/{fid}/download"))
            assets["media"][fid] = rel
        except Exception as e:
            print("  媒体下载失败", r["name"], e)
        if (r.get("info") or {}).get("hasCover"):
            try:
                data = get_bytes(f"/api/files/{fid}/cover")
                ext2 = ".png" if data[:4] == b"\x89PNG" else ".jpg"
                crel = f"assets/covers/{fid}{ext2}"
                (DEMO / crel).write_bytes(data)
                assets["covers"][fid] = crel
            except Exception as e:
                print("  封面下载失败", r["name"], e)

    # 7) 卡片 / op 目录 / 快照 / 健康 / 队列快照 / 真实日志
    cards = req("GET", "/api/cards")
    ops = req("GET", "/api/ops")
    snaps = req("GET", "/api/snapshots")
    fields = req("GET", f"/api/files/{rows[0]['id']}/tags").get("fields", []) if rows else []
    queue = sanitize_queue(req("GET", "/api/tasks/queue"))
    logdump = req("GET", "/api/logs?limit=200")

    payload = {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "health": scrub_health({"ok": True, "tools": h.get("tools", {}),
                                "server": h.get("server", {})}),
        "files": rows,
        "cards": cards.get("cards", []),
        "categories": cards.get("categories", []),
        "builtinCount": cards.get("builtinCount", 0),
        "snapshots": snaps.get("snapshots", []),
        "snapDefaults": snaps.get("defaults", []),
        "ops": ops.get("ops", []),
        "paramTypes": ops.get("paramTypes", []),
        "icons": ops.get("icons", []),
        "tagFields": fields,
        "queue": queue,
        "logs": logdump.get("logs", []),
        "tags": tags_map,
        "peaks": peaks_map,
        "assets": assets,
        "outputSample": "assets/outputs/sample.png",
    }
    js = ("/* 由 demo/_build/collect_data.py 自动生成，请勿手改。\n"
          f"   采集时间 {payload['generatedAt']}，来自真实后端 {BASE}。\n"
          "   含：文件列表(真实 info)、卡片定义、op 目录、标签、真实波形峰值、\n"
          "   真实队列与日志快照。 */\n"
          "window.__AE_DEMO__ = " + json.dumps(payload, ensure_ascii=False) + ";\n")
    (DEMO / "data" / "demo-data.js").write_text(js, encoding="utf-8")

    print(f"\n卡片 {len(payload['cards'])} 张 / 分类 {len(payload['categories'])} 个 / "
          f"快照 {len(payload['snapshots'])} 个 / op {len(payload['ops'])} 个 / "
          f"字段 {len(payload['tagFields'])} 个")
    print(f"峰值 {len(peaks_map)} 份，封面 "
          f"{len(list((DEMO / 'assets' / 'covers').glob('*')))} 张，"
          f"日志 {len(payload['logs'])} 行，"
          f"队列 running/pending/recent = "
          f"{len(queue.get('running', []))}/{len(queue.get('pending', []))}/"
          f"{len(queue.get('recent', []))}")
    print(f"demo-data.js = {(DEMO / 'data' / 'demo-data.js').stat().st_size // 1024} KB")

    # 8) 给演示版造一份"产物"资产（队列里点下载用）
    make_cover(DEMO / "assets" / "outputs" / "sample.png", (56, 84, 138), (240, 196, 90), 320)
    print("产物样例 assets/outputs/sample.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
