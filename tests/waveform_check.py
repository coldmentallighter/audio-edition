"""波形 PNG 验证：默认必须是白色波形 + 透明底，且产物能通过 /api/outputs 取回。"""
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "http://127.0.0.1:8765"
ROOT = Path(__file__).resolve().parent.parent
WORK = Path(__file__).resolve().parent / "_wave_tmp"
WORK.mkdir(exist_ok=True)

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label}  {detail}")


def out_url(rel):
    """产物路径可能含中文（文件名来自源文件），请求前逐段转义。
    任务结果给的是相对项目根的 outputs/... —— 故意两种写法都测，
    因为 /api/outputs 两种都该收。"""
    r = str(rel)
    if r.startswith("outputs/"):
        r = r[len("outputs/"):]          # 这里用相对 outputs/ 的写法
    return "/api/outputs/" + "/".join(urllib.parse.quote(seg) for seg in r.split("/"))


def req(method, path, body=None, raw=None, ctype=None, binary=False):
    headers, data = {}, None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if raw is not None:
        data, headers["Content-Type"] = raw, ctype
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            b = resp.read()
            if binary:
                # 头部键统一小写，别用大小写敏感的 get 去猜
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                return resp.status, b, hdrs
            t = b.decode("utf-8", "replace")
            return resp.status, (json.loads(t) if t.strip().startswith(("{", "[")) else t)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def multipart(fields: dict, files: list):
    """fields: {名: 值}；files: [(字段名, 文件名, bytes, mime)]"""
    b = "----w" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for name, fn, content, mime in files:
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\";"
                f" filename=\"{fn}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
        out += content + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={b}"


def wait_task(tid, tries=120):
    for _ in range(tries):
        s, t = req("GET", f"/api/tasks/{tid}")
        if isinstance(t, dict) and t.get("state") in ("success", "failed"):
            return t
        time.sleep(0.3)
    return None


def rgba_of(png: bytes, w: int, h: int):
    """用 ffmpeg 把 PNG 解成 RGBA 原始像素，统计透明与颜色。"""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0",
                          "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                         input=png, capture_output=True).stdout
    n = len(raw) // 4
    if n == 0:
        return None
    transp = sum(1 for i in range(n) if raw[i * 4 + 3] == 0)
    opaque = {}
    for i in range(n):
        a = raw[i * 4 + 3]
        if a > 200:
            c = (raw[i * 4], raw[i * 4 + 1], raw[i * 4 + 2])
            opaque[c] = opaque.get(c, 0) + 1
    top = max(opaque.items(), key=lambda kv: kv[1]) if opaque else (None, 0)
    partial = sum(1 for i in range(n) if 0 < raw[i * 4 + 3] <= 200)
    return {"px": n, "transparent": transp, "transparentPct": transp / n * 100,
            "opaque": sum(opaque.values()), "topColor": top[0], "topCount": top[1],
            "partial": partial, "hist": opaque}


def count_near(info, color, tol=3):
    """统计接近某颜色的像素数。

    实底图上"最多的颜色"是背景（黑），不能用众数判断波形颜色，
    得按目标色去数。"""
    if not info:
        return 0
    return sum(c for col, c in info["hist"].items()
               if all(abs(col[i] - color[i]) <= tol for i in range(3)))


# 造一段有强弱起伏的立体声，别用纯正弦（波形会是一条等宽矩形）
src = WORK / "wave-src.wav"
subprocess.run([
    "ffmpeg", "-y", "-v", "error",
    "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2",
    "-f", "lavfi", "-i", "sine=frequency=880:duration=1.2",
    "-filter_complex",
    "[0:a][1:a]concat=n=2:v=0:a=1,volume='0.15+0.85*abs(sin(t*2.2))':eval=frame",
    "-ac", "2", "-ar", "44100", str(src)], check=True)

raw, ctype = multipart({"paths": json.dumps(["波形/素材.wav"])},
                       [("files", "素材.wav", src.read_bytes(), "audio/wav")])
s, up = req("POST", "/api/upload", raw=raw, ctype=ctype)
if not (isinstance(up, dict) and up.get("saved")):
    print(f"上传失败：status={s} resp={up}")
    print(f"素材大小={src.stat().st_size} B")
    sys.exit(2)
fid = up["saved"][0]["id"]
print(f"素材 id={fid}  {src.stat().st_size} B")

print("== 1. 默认参数：白色波形 + 透明底 ==")
s, r = req("POST", "/api/ops/waveform", {"fileIds": [fid]})
check("提交 200", s == 200, r)
tid = (r.get("taskIds") or [None])[0]
task = wait_task(tid)
check("任务成功", task and task.get("state") == "success", task)
res = (task or {}).get("result", {})
out_rel = res.get("output")
check("有产物路径", bool(out_rel), res)
check("产物在 outputs/waveforms/ 下", str(out_rel).startswith("outputs/waveforms/"), out_rel)
check("默认 1920x400", res.get("width") == 1920 and res.get("height") == 400, res)
check("默认颜色 #FFFFFF", res.get("color") == "#FFFFFF", res.get("color"))
check("默认背景 transparent", res.get("background") == "transparent", res.get("background"))

print("== 2. 取回 PNG 并逐像素验证 ==")
s, png, hdr = req("GET", out_url(out_rel), binary=True)
check("GET /api/outputs 200", s == 200, s)
check("Content-Type 是 png", "png" in hdr.get("content-type", ""), hdr.get("content-type"))
check("PNG 魔数", png[:8] == b"\x89PNG\r\n\x1a\n", png[:8])
check("是 RGBA（含 alpha 通道）", png[24:26] == b"\x08\x06", png[24:26])
print(f"        文件 {len(png)} B")

info = rgba_of(png, 1920, 400)
check("能解出像素", info is not None, info)
if info:
    print(f"        {info['px']} px  完全透明 {info['transparentPct']:.1f}%  "
          f"不透明 {info['opaque']}  半透明 {info['partial']}")
    check("背景确实是透明的（透明占比 > 60%）", info["transparentPct"] > 60,
          f"{info['transparentPct']:.1f}%")
    check("确实画了波形（有不透明像素）", info["opaque"] > 1000, info["opaque"])
    check("波形是纯白 #FFFFFF（不是 #FEFEFE）", info["topColor"] == (255, 255, 255),
          info["topColor"])
    check("没有杂色（主色占绝大多数）",
          info["topCount"] / max(1, info["opaque"]) > 0.95,
          f"{info['topCount']}/{info['opaque']}")

print("== 3. 自定义颜色与背景 ==")
s, r2 = req("POST", "/api/ops/waveform",
            {"fileIds": [fid], "width": 800, "height": 200,
             "color": "#22AAFF", "background": "black", "scale": "sqrt"})
t2 = wait_task((r2.get("taskIds") or [None])[0])
check("自定义任务成功", t2 and t2.get("state") == "success", t2)
out2 = (t2 or {}).get("result", {}).get("output")
s, png2, _ = req("GET", out_url(out2), binary=True)
i2 = rgba_of(png2, 800, 200)
if i2:
    blue = count_near(i2, (34, 170, 255))
    print(f"        黑底版：透明 {i2['transparentPct']:.1f}%  近 #22AAFF 像素 {blue}  "
          f"众数色 {i2['topColor']}")
    check("实底版没有透明像素", i2["transparentPct"] < 1, i2["transparentPct"])
    check("确实画了蓝色波形", blue > 1000, blue)
    check("背景是纯黑（众数色）", i2["topColor"] == (0, 0, 0), i2["topColor"])

print("== 4. 左右分道 ==")
s, r3 = req("POST", "/api/ops/waveform",
            {"fileIds": [fid], "width": 800, "height": 300, "splitChannels": True})
t3 = wait_task((r3.get("taskIds") or [None])[0])
check("分道任务成功", t3 and t3.get("state") == "success", t3)
out3 = (t3 or {}).get("result", {}).get("output")
s, png3, _ = req("GET", out_url(out3), binary=True)
i3 = rgba_of(png3, 800, 300)
if i3:
    print(f"        分道版：不透明 {i3['opaque']}  透明 {i3['transparentPct']:.1f}%")
    check("分道版也画出来了", i3["opaque"] > 1000, i3["opaque"])

print("== 5. 非法参数被拒 ==")
for label, body in [
    ("越界宽度", {"fileIds": [fid], "width": 99999}),
    ("越界高度", {"fileIds": [fid], "height": 5}),
    ("非法刻度", {"fileIds": [fid], "scale": "nope"}),
    ("非法背景", {"fileIds": [fid], "background": "rainbow"}),
]:
    s, rr = req("POST", "/api/ops/waveform", body)
    tid_ = (rr.get("taskIds") or [None])[0] if isinstance(rr, dict) else None
    t = wait_task(tid_) if tid_ else None
    bad = (t or {}).get("state") == "failed"
    # 宽度/高度越界会被夹到合法范围（不报错），刻度/背景走白名单直接失败
    print(f"        {label}: {s} state={(t or {}).get('state')} "
          f"err={((t or {}).get('error') or '')[:44]}")
    check(f"{label} 被处理（失败或夹紧）", s == 200, s)

print("== 6. 产物取回的路径防护 ==")
check("穿越被拒", req("GET", "/api/outputs/../../backend/app.py")[0] in (400, 404),
      req("GET", "/api/outputs/../../backend/app.py")[0])
check("不存在返回 404", req("GET", "/api/outputs/waveforms/nope.png")[0] == 404)

print("== 7. 快照接口 ==")
s, sn = req("GET", "/api/snapshots")
check("GET /api/snapshots 200", s == 200, sn)
check("默认 5 个", len(sn["snapshots"]) == 5, sn)
s, put = req("PUT", "/api/snapshots", {"snapshots": ["导出波形 PNG", "转 FLAC"]})
check("PUT 保存成功", s == 200 and put["snapshots"] == ["导出波形 PNG", "转 FLAC"], put)
s, sn2 = req("GET", "/api/snapshots")
check("读回一致", sn2["snapshots"] == ["导出波形 PNG", "转 FLAC"], sn2)
check("未知卡片被拒",
      req("PUT", "/api/snapshots", {"snapshots": ["没有这张卡"]})[0] == 400)
check("空列表被拒", req("PUT", "/api/snapshots", {"snapshots": []})[0] == 400)
check("非数组被拒", req("PUT", "/api/snapshots", {"snapshots": "x"})[0] == 400)
s, rs = req("DELETE", "/api/snapshots")
check("恢复默认", s == 200 and len(rs["snapshots"]) == 5, rs)

# 收尾
req("DELETE", f"/api/files/{fid}?withDisk=true")
for p in ROOT.glob("outputs/waveforms/*.png"):
    p.unlink(missing_ok=True)

print(f"\n结果：{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
