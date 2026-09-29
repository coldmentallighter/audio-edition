"""给浏览器探针铺数据：1 个带封面的 FLAC + 3 个普通 WAV。

只在本地自检时用；跑完请用 tests/smoke_api.py 的清理逻辑或手动删掉。
"""
import json
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "http://127.0.0.1:8765"
WORK = Path(__file__).resolve().parent / "_seed_tmp"
WORK.mkdir(exist_ok=True)


def req(method, path, body=None, raw=None, ctype=None):
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if raw is not None:
        data = raw
        headers["Content-Type"] = ctype
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=120) as resp:
        t = resp.read().decode("utf-8", "replace")
        return json.loads(t) if t.strip().startswith(("{", "[")) else t


def multipart(paths, files):
    b = "----seed" + uuid.uuid4().hex
    out = bytearray()
    out += f"--{b}\r\nContent-Disposition: form-data; name=\"paths\"\r\n\r\n{json.dumps(paths)}\r\n".encode()
    for fn, content, mime in files:
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"files\";"
                f" filename=\"{fn}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
        out += content + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={b}"


def wait_task(fid, tries=80):
    for _ in range(tries):
        q = req("GET", "/api/tasks/queue")
        rows = q.get("running", []) + q.get("pending", []) + q.get("recent", [])
        row = next((x for x in rows if x.get("fileId") == fid), None)
        if row and row.get("state") in ("success", "failed"):
            return row
        time.sleep(0.25)
    return None


# 带封面的 FLAC（封面用一张有明显颜色的图，便于确认真的显示了）
flac = WORK / "带封面.flac"
png = WORK / "art.png"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                "-i", "sine=frequency=440:duration=2", "-ac", "2", "-ar", "44100",
                str(flac)], check=True)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                "-i", "color=c=#D2472B:s=400x400:d=1", "-frames:v", "1", str(png)],
               check=True)

wavs = []
for i in range(3):
    p = WORK / f"待删{i}.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"sine=frequency={300 + i * 100}:duration=1", "-ac", "2",
                    "-ar", "44100", str(p)], check=True)
    wavs.append(p)

paths = ["演示/带封面.flac", "covers/art.png"] + [f"演示/待删{i}.wav" for i in range(3)]
files = [("带封面.flac", flac.read_bytes(), "audio/flac"),
         ("art.png", png.read_bytes(), "image/png")] + \
        [(f"待删{i}.wav", wavs[i].read_bytes(), "audio/wav") for i in range(3)]

raw, ctype = multipart(paths, files)
up = req("POST", "/api/upload", raw=raw, ctype=ctype)
saved = up["saved"]
fid = next(x["id"] for x in saved if x["name"].endswith(".flac"))
img = next(x for x in saved if x["name"].endswith(".png"))
delids = [x["id"] for x in saved if x["name"].startswith("待删")]

req("POST", "/api/ops/cover", {"fileIds": [fid], "imagePath": img["relPath"],
                              "pictureType": "Front Cover"})
row = wait_task(fid)
print(json.dumps({
    "coverFileId": fid,
    "coverState": row and row.get("state"),
    "deleteIds": delids,
    "total": len(saved),
}, ensure_ascii=False))
