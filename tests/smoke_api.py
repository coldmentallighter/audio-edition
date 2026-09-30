"""端到端冒烟：按前端真实调用顺序打一遍后端，确认页面拿到的都是真数据。

覆盖：/api/health -> /api/cards -> /api/files(空) -> 上传 -> /api/files(有)
      -> /api/ops/convert -> /api/tasks -> /api/logs -> 重试接口存在性
"""
import json
import re as _re
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid
import subprocess
import sys
from pathlib import Path

# 控制台按 UTF-8 输出，否则中文在 GBK 代码页下全是问号，看不出对错
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 1x1 透明 PNG（参数透传探测里给 imagePath 用）
_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6360000002000100ffff0300000600"
    "05570f4d0000000049454e44ae426082")

BASE = "http://127.0.0.1:8765"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import cards as cards_mod  # noqa: E402
from backend import config as config_mod  # noqa: E402
from backend import store as store_mod  # noqa: E402
# 项目根，不是 tests/ —— 之前写成 Path(__file__).parent，
# 结果 .cache/ 找错目录，而且「落点仍在 uploads 内」那条断言比的是
# tests/uploads（根本不存在），等于白测。这里必须是真正的受管目录。
ROOT = Path(__file__).resolve().parent.parent
WORK = Path(__file__).resolve().parent / "_smoke_tmp"
WORK.mkdir(exist_ok=True)
UP = (ROOT / "uploads").resolve()
CACHE = (ROOT / ".cache").resolve()
assert UP.is_dir(), f"uploads 目录不存在: {UP}（测试跑错位置了）"


def req(method, path, body=None, raw=None, ctype=None):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if raw is not None:
        data = raw
        headers["Content-Type"] = ctype
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            txt = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(txt) if txt.strip().startswith(("{", "[")) else txt)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def multipart(fields: dict, files: list):
    """files: [(fieldname, filename, bytes, mime)]"""
    b = "----ae" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for name, fn, content, mime in files:
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\";"
                f" filename=\"{fn}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
        out += content + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={b}"


ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label}  {detail}")


print("== 1. 健康与配置 ==")
s, h = req("GET", "/api/health")
check("GET /api/health 200", s == 200, h)
print(f"        {h}")

print("== 2. 卡片目录（前端 init 时拉一次）==")
s, c = req("GET", "/api/cards")
check("GET /api/cards 200", s == 200)
cards, cats, snaps = c["cards"], c["categories"], c["snapshots"]
check("卡片非空", len(cards) > 0, len(cards))
check("每张卡都有 op 与 ico", all(x.get("op") and x.get("ico") for x in cards),
      [x["name"] for x in cards if not x.get("op")])
check("卡片分类都落在 categories 里", all(x["cat"] in cats for x in cards),
      sorted({x["cat"] for x in cards} - set(cats)))
check("snapshots 都能在 cards 里找到", all(n in {x["name"] for x in cards} for n in snaps),
      [n for n in snaps if n not in {x["name"] for x in cards}])
print(f"        cards={len(cards)} cats={cats}")
print(f"        snaps={snaps}")

print("== 3. 首帧文件列表（此时应为空，前端显示空状态）==")
s, f0 = req("GET", "/api/files")
check("GET /api/files 200", s == 200)
print(f"        当前 {len(f0.get('files', f0) if isinstance(f0, dict) else f0)} 个文件（库内既有数据）")

print("== 4. 造一个真 WAV（400Hz，1.5s）并上传 ==")
wav = WORK / "smoke.wav"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                "-i", "sine=frequency=400:duration=1.5", "-ac", "2",
                "-ar", "44100", str(wav)], check=True)
raw, ctype = multipart({"paths": json.dumps(["冒烟测试/测试音.wav"])},
                       [("files", "测试音.wav", wav.read_bytes(), "audio/wav")])
s, up = req("POST", "/api/upload", raw=raw, ctype=ctype)
check("POST /api/upload 200", s == 200, up)
saved = (up or {}).get("saved", []) if isinstance(up, dict) else []
check("上传返回 saved 记录", len(saved) == 1, up)
fid = saved[0]["id"] if saved else None
if saved:
    check("服务端保存的中文文件名正确", saved[0]["name"] == "测试音.wav", saved[0]["name"])
    check("服务端保存的中文子目录正确", saved[0]["relPath"] == "冒烟测试/测试音.wav", saved[0]["relPath"])
print(f"        id={fid} relPath={saved[0]['relPath'] if saved else None}")

print("== 5. 文件列表里能看到它，且 name 中文未乱码 ==")
s, fl = req("GET", "/api/files")
files = fl["files"] if isinstance(fl, dict) else fl
mine = [x for x in files if x["id"] == fid]
check("列表含新文件", len(mine) == 1)
if mine:
    check("中文文件名正确", mine[0]["name"] == "测试音.wav", mine[0]["name"])
    print(f"        name={mine[0]['name']} state={mine[0]['state']} size={mine[0].get('size')}")

print("== 6. 触发一个真实 op（前端执行链走的就是这个）==")
s, r = req("POST", "/api/ops/peaks", {"fileIds": [fid], "buckets": 400})
check("POST /api/ops/peaks 200", s == 200, r)
check("返回任务总数", isinstance(r, dict) and r.get("total") == 1, r)
print(f"        {r}")

print("== 7. 队列快照（前端 API.tasks() → /api/tasks/queue）==")
state = None
q = {}
for _ in range(60):
    s, q = req("GET", "/api/tasks/queue")
    rows = (q.get("running", []) + q.get("pending", []) + q.get("recent", [])) if isinstance(q, dict) else []
    row = next((x for x in rows if x.get("fileId") == fid), None)
    if row:
        state = row.get("state")
        if state in ("success", "failed"):
            break
    time.sleep(0.4)
check("任务终态为 success", state == "success", state)
check("queue 有 counts", isinstance(q, dict) and "counts" in q, list(q) if isinstance(q, dict) else q)
print(f"        state={state} counts={(q or {}).get('counts')}")

print("== 8. 日志接口（前端 pollLogs 每 1.5s 拉一次）==")
s, lg = req("GET", "/api/logs")
items = lg["logs"] if isinstance(lg, dict) else lg
check("GET /api/logs 200", s == 200)
check("返回 lastSeq 供增量拉取", isinstance(lg, dict) and "lastSeq" in lg, list(lg) if isinstance(lg, dict) else lg)
check("队列生命周期有写日志", len(items) > 0, len(items))
need = {"seq", "time", "message", "kind"}
check("日志条目字段齐全（前端要 time/message/kind）",
      all(need <= set(e) for e in items), [sorted(set(e)) for e in items[:2]])
for e in items[-4:]:
    print(f"        #{e.get('seq')} [{e.get('kind','')}] {e.get('time','')} {e.get('message','')[:80]}")

print("== 8b. 增量拉取：since=lastSeq 不应重复返回旧日志 ==")
last = lg["lastSeq"]
s, lg2 = req("GET", f"/api/logs?since={last}")
check("增量结果为空（没有新日志）", len(lg2.get("logs", [])) == 0, lg2.get("logs"))

print("== 9. 峰值数据真的算出来了（前端波形要它）==")
s, pk = req("GET", f"/api/files/{fid}/peaks?buckets=400")
check("GET peaks 200", s == 200, str(pk)[:160])
if s == 200 and isinstance(pk, dict):
    d = pk.get("peaks") or []
    check("peaks 有点数", len(d) > 0, len(d))
    check("点数≈请求桶数", abs(len(d) - 400) <= 2, len(d))
    check("峰值在 0..1", all(0.0 <= x <= 1.0 for x in d), (min(d), max(d)) if d else None)
    check("不是全零（真解码了音频）", any(x > 0.01 for x in d))
    print(f"        points={len(d)} buckets={pk.get('buckets')} ch={pk.get('channels')} "
          f"min={min(d):.3f} max={max(d):.3f}")

    print("== 9b. 二次请求走缓存（速度应显著更快）==")
    t0 = time.perf_counter(); req("GET", f"/api/files/{fid}/peaks?buckets=400")
    t1 = time.perf_counter(); req("GET", f"/api/files/{fid}/peaks?buckets=400&force=true")
    t2 = time.perf_counter()
    print(f"        缓存 {1000*(t1-t0):.1f} ms  vs  强制重算 {1000*(t2-t1):.1f} ms")
    check("缓存确实更快", (t1 - t0) < (t2 - t1) * 0.6, f"{(t1-t0)*1000:.1f} vs {(t2-t1)*1000:.1f}")

print("== 10. 重试接口存在（前端失败任务的重试按钮）==")
s, rt = req("POST", f"/api/tasks/999999/retry")
check("不存在的任务返回 4xx 而不是 500", 400 <= s < 500, s)

print("== 11. 路径穿越：必须被限制在 uploads/ 之内 ==")
for bad in ["../evil.wav", "../../evil.wav", "a/../../evil.wav",
            "..\\evil.wav", "/abs/evil.wav", "C:/Windows/evil.wav"]:
    raw, ctype = multipart({"paths": json.dumps([bad])},
                           [("files", "evil.wav", wav.read_bytes(), "audio/wav")])
    s, ev = req("POST", "/api/upload", raw=raw, ctype=ctype)
    saved = (ev or {}).get("saved", []) if isinstance(ev, dict) else []
    if not saved:
        print(f"        拒绝  {bad!r:28} -> {s} {str(ev)[:70]}")
        check(f"穿越被拒 {bad}", 400 <= s < 600, f"{s} {str(ev)[:80]}")
        continue
    rp = saved[0]["relPath"]
    real = (UP / rp).resolve()
    # 必须真的落在 uploads/ 里：既要在 uploads 下，也不能是 uploads 本身
    inside = real != UP and UP in real.parents
    check(f"落点仍在 uploads 内 {bad}", inside, f"relPath={rp} real={real}")
    check(f"磁盘上确实存在 {bad}", real.exists(), str(real))
    print(f"        收敛  {bad!r:28} -> relPath={rp!r}")

print("== 12. 非法扩展名：不落盘，且给出原因 ==")
raw, ctype = multipart({"paths": json.dumps(["x.txt"])},
                       [("files", "x.txt", b"not audio", "text/plain")])
s, ev = req("POST", "/api/upload", raw=raw, ctype=ctype)
skipped = (ev or {}).get("skipped", []) if isinstance(ev, dict) else []
check("txt 未落盘", not (ev or {}).get("saved"), ev)
check("txt 出现在 skipped 且带原因", len(skipped) == 1 and skipped[0].get("reason"), ev)
print(f"        {s} {skipped}")

print("== 13. 软删除后重新导入：必须复活（否则拖回来也看不见）==")
raw, ctype = multipart({"paths": json.dumps(["回收/回魂.wav"])},
                       [("files", "回魂.wav", wav.read_bytes(), "audio/wav")])
s, up = req("POST", "/api/upload", raw=raw, ctype=ctype)
zid = up["saved"][0]["id"]
req("DELETE", f"/api/files/{zid}")
s, fl = req("GET", "/api/files")
check("软删除后不在列表里",
      zid not in {x["id"] for x in (fl["files"] if isinstance(fl, dict) else fl)})
raw, ctype = multipart({"paths": json.dumps(["回收/回魂.wav"])},
                       [("files", "回魂.wav", wav.read_bytes(), "audio/wav")])
s, up2 = req("POST", "/api/upload", raw=raw, ctype=ctype)
zid2 = up2["saved"][0]["id"]
s, fl = req("GET", "/api/files")
check("重新导入后回到列表里",
      zid2 in {x["id"] for x in (fl["files"] if isinstance(fl, dict) else fl)},
      f"uploaded id={zid2}")
req("DELETE", f"/api/files/{zid2}?withDisk=true")

print("== 14. 封面：嵌入 → 显示 → 缓存 → 移除 ==" )
# 造一个真 FLAC 和一张真图；FLAC 走 metaflac，不重编码
flac = WORK / "cover-source.flac"
png = WORK / "cover.png"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                "-i", "sine=frequency=330:duration=1.0", "-ac", "2", "-ar", "44100",
                str(flac)], check=True)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                "-i", "color=c=#3A7BD5:s=300x300:d=1", "-frames:v", "1", str(png)],
               check=True)
check("造出 FLAC", flac.exists() and flac.stat().st_size > 0)
check("造出 PNG", png.exists() and png.stat().st_size > 0)

raw, ctype = multipart({"paths": json.dumps(["封面测试/有封面.flac", "covers/cover.png"])},
                       [("files", "有封面.flac", flac.read_bytes(), "audio/flac"),
                        ("files", "cover.png", png.read_bytes(), "image/png")])
s, up = req("POST", "/api/upload", raw=raw, ctype=ctype)
cfid = next((x["id"] for x in up["saved"] if x["name"].endswith(".flac")), None)
cimg = next((x for x in up["saved"] if x["name"].endswith(".png")), None)
check("上传音频与封面图", cfid is not None and cimg is not None, up)

check("嵌入前 GET cover 应为 404", req("GET", f"/api/files/{cfid}/cover")[0] == 404)

s, r = req("POST", "/api/ops/cover",
           {"fileIds": [cfid], "imagePath": cimg["relPath"], "pictureType": "Front Cover"})
check("POST /api/ops/cover 200", s == 200, r)


def wait_task(file_id, tries=80):
    for _ in range(tries):
        s, q = req("GET", "/api/tasks/queue")
        rows = (q.get("running", []) + q.get("pending", []) + q.get("recent", [])) if isinstance(q, dict) else []
        row = next((x for x in rows if x.get("fileId") == file_id), None)
        if row and row.get("state") in ("success", "failed"):
            return row
        time.sleep(0.25)
    return None


row = wait_task(cfid)
check("嵌封面任务成功", row and row.get("state") == "success", row)

s, fl = req("GET", "/api/files")
row_f = next((x for x in (fl["files"] if isinstance(fl, dict) else fl) if x["id"] == cfid), None)
check("列表里 hasCover 为 true", row_f and row_f.get("info", {}).get("hasCover"), row_f and row_f.get("info"))

s, cov = req("GET", f"/api/files/{cfid}/cover")
check("GET cover 200", s == 200, str(cov)[:120])

# FileResponse 在 req() 里被当文本读了，这里直接取原始字节
with urllib.request.urlopen(f"{BASE}/api/files/{cfid}/cover", timeout=30) as resp:
    body = resp.read()
    ctype_hdr = resp.headers.get("content-type", "")
check("封面是图片且有内容", ctype_hdr.startswith("image/") and len(body) > 100,
      f"{ctype_hdr} {len(body)}B")
check("是 JPEG 魔数", body[:2] == b"\xff\xd8", body[:4])
print(f"        封面 {ctype_hdr} {len(body)} B")

# 缓存：别用耗时判断（20ms 量级噪声太大），直接看落盘的缓存文件与命中路径
cjpg = CACHE / f"cover-{cfid}.jpg"
ckey = CACHE / f"cover-{cfid}.key"
check("抽出的封面落进 .cache", cjpg.exists() and cjpg.stat().st_size > 100, str(cjpg))
check("写了 mtime+size 缓存戳", ckey.exists() and ckey.read_text().strip(), str(ckey))
t0 = time.perf_counter()
with urllib.request.urlopen(f"{BASE}/api/files/{cfid}/cover", timeout=30) as resp:
    body2 = resp.read()
t1 = time.perf_counter()
check("二次请求字节完全一致", body2 == body)
print(f"        命中缓存 {1000*(t1-t0):.1f} ms")
with urllib.request.urlopen(f"{BASE}/api/files/{cfid}/cover?refresh=true", timeout=30) as resp:
    check("refresh=true 仍能返回", resp.status == 200 and len(resp.read()) > 100)

s, r = req("POST", "/api/ops/remove-cover", {"fileIds": [cfid]})
check("POST remove-cover 200", s == 200, r)
row = wait_task(cfid)
check("移除封面任务成功", row and row.get("state") == "success", row)
check("移除后 GET cover 回到 404", req("GET", f"/api/files/{cfid}/cover")[0] == 404)

print("== 15. 批量删除（前端批量栏的「删除」）==")
bids = []
for i in range(3):
    raw, ctype = multipart({"paths": json.dumps([f"批删/b{i}.wav"])},
                           [("files", f"b{i}.wav", wav.read_bytes(), "audio/wav")])
    s, u = req("POST", "/api/upload", raw=raw, ctype=ctype)
    bids.append(u["saved"][0]["id"])
check("造出 3 个待删文件", len(bids) == 3)
paths = [(UP / x["relPath"]) for x in
         (req("GET", "/api/files")[1]["files"]) if x["id"] in bids]
before_names = {x["name"] for x in req("GET", "/api/files")[1]["files"]}
check("3 个都在列表里", all(f"b{i}.wav" in before_names for i in range(3)), before_names)

s, r = req("POST", "/api/files/delete", {"fileIds": bids, "withDisk": True})
check("POST /api/files/delete 200", s == 200, r)
check("删除计数为 3", r.get("count") == 3, r)
check("没有失败项", not r.get("failed"), r.get("failed"))
check("释放字节数 > 0", (r.get("freedBytes") or 0) > 0, r.get("freedBytes"))
after = {x["id"] for x in req("GET", "/api/files")[1]["files"]}
check("列表里已消失", not (set(bids) & after), after & set(bids))
check("磁盘上也删了", not any(p.exists() for p in paths), [str(p) for p in paths])
# 删文件不带走目录：空目录会一直留在 uploads/ 里
check("空目录被一起收掉", not (UP / "批删").exists(), str(UP / "批删"))
check("返回 prunedDirs", r.get("prunedDirs", 0) >= 1, r.get("prunedDirs"))

s, r = req("POST", "/api/files/delete", {"fileIds": []})
check("空 fileIds 返回 400", s == 400, s)
s, r = req("POST", "/api/files/delete", {"fileIds": ["f_nope"]})
check("不存在的 id 记入 failed 而不是 500", s == 200 and r.get("failed"), r)

print("== 16. 功能卡片：内置 + 自定义 CRUD ==")
s, ops = req("GET", "/api/ops")
check("GET /api/ops 200", s == 200)
specs = {o["op"]: o for o in ops["ops"]}
check("每个 op 标了任务类型，且都是已注册类型",
      all(o.get("task") in store_mod.TASK_TYPES for o in ops["ops"]),
      [o["op"] for o in ops["ops"] if o.get("task") not in store_mod.TASK_TYPES])
# 注意：这里比的是 op 的 `task` 字段，**不是 op 本身**。
# 早先这条写成 `set(specs) <= set(TASK_TYPES)`，等于把"op 名 = 任务类型名"
# 这个错误假设固化成了断言 —— 它一路全绿，而真实路由名对不上，
# 于是「提取封面」「删除封面」必然 404。别再改回去。
check("每个已注册任务类型都有对应的 op 规格",
      set(store_mod.TASK_TYPES) <= {o.get("task") for o in ops["ops"]},
      sorted(set(store_mod.TASK_TYPES) - {o.get("task") for o in ops["ops"]}))
check("每个参数都有 label 与 desc",
      all(p.get("label") and p.get("desc")
          for o in ops["ops"] for p in o["params"]),
      [(o["op"], p.get("key")) for o in ops["ops"] for p in o["params"]
       if not (p.get("label") and p.get("desc"))])
check("每个操作都有 desc 与 preview",
      all(o.get("desc") and o.get("preview") for o in ops["ops"]),
      [o["op"] for o in ops["ops"] if not (o.get("desc") and o.get("preview"))])
check("枚举参数都带 options",
      all(p.get("options") for o in ops["ops"] for p in o["params"]
          if p["type"] == "enum"),
      [(o["op"], p["key"]) for o in ops["ops"] for p in o["params"]
       if p["type"] == "enum" and not p.get("options")])
check("数值参数都带 min/max",
      all(p.get("min") is not None and p.get("max") is not None
          for o in ops["ops"] for p in o["params"]
          if p["type"] in ("int", "float")),
      [(o["op"], p["key"]) for o in ops["ops"] for p in o["params"]
       if p["type"] in ("int", "float") and (p.get("min") is None or p.get("max") is None)])

# ---- 最关键的一条：OPS 的 key 必须就是真实路由名 ----------------------------
# 前端执行卡片走的是 POST /api/ops/<op>（api.js 的 API.op 直接拼路径）。
# 之前 OPS 用的是任务类型名（tag_edit / cover_extract …），
# 于是「提取封面」「删除封面」两张内置卡必然 404 —— 而当时所有测试用的都是
# 路由名，三处全对，唯独后端目录表错了，所以全绿。
# 这里直接向 openapi 要路由清单来比，杜绝"两边各写一套名字"。
routes = set()
try:
    with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=10) as resp:
        spec_json = json.loads(resp.read().decode("utf-8"))
    routes = {p.split("/api/ops/", 1)[1] for p in spec_json["paths"]
              if p.startswith("/api/ops/") and "{" not in p}
except Exception as e:                                   # noqa: BLE001
    check("能读到 openapi.json 取路由清单", False, e)

check("OPS 的每个 key 都是真实存在的 /api/ops 路由",
      set(specs) <= routes, sorted(set(specs) - routes))
check("/api/ops 的每条路由都有参数规格",
      routes <= set(specs), sorted(routes - set(specs)))
check("每个 op 都标了对应的任务类型",
      all(o.get("task") in store_mod.TASK_TYPES for o in ops["ops"]),
      [o["op"] for o in ops["ops"] if o.get("task") not in store_mod.TASK_TYPES])

# 逐条真的 POST 一次，确认不是 404（有真实文件时才能真正跑，这里只验可达）
_probe_fid = None
try:
    _s, _fl = req("GET", "/api/files")
    _files = _fl["files"] if isinstance(_fl, dict) else _fl
    if _files:
        _probe_fid = _files[0]["id"]
except Exception:                                        # noqa: BLE001
    pass

check("有文件可用于 op 可达性探测", _probe_fid is not None)
if _probe_fid:
    for o in ops["ops"]:
        s, r = req("POST", f"/api/ops/{o['op']}", {"fileIds": [_probe_fid]})
        check(f"POST /api/ops/{o['op']} 不是 404", s != 404, f"{s} {str(r)[:60]}")

# ---- 内置卡片与 OPS / 前端的交叉校验 ---------------------------------------
_ck, _cd = req("GET", "/api/cards")
check("GET /api/cards 200", _ck == 200, _cd)
check("每张内置卡片的 op 都在 OPS 里",
      all(c["op"] in specs for c in _cd["cards"] if not c["custom"]),
      [(c["name"], c["op"]) for c in _cd["cards"]
       if not c["custom"] and c["op"] not in specs])
_cov = {c["op"] for c in _cd["cards"] if not c["custom"]}
check("每个操作都至少有一张内置卡（否则用户没入口）",
      set(specs) <= _cov, sorted(set(specs) - _cov))

# 每张内置卡片的**参数**都要能过验证器 —— 这是 A 轴铺卡后最该守住的东西：
# 卡片参数受白名单约束，写错一个枚举值/越界数字，用户点下去就报错。
_bad_cards = []
for c in _cd["cards"]:
    if c["custom"]:
        continue
    try:
        cards_mod.validate_card({**c, "id": ""})
    except ValueError as e:
        _bad_cards.append(f"{c['name']}: {e}")
check(f"{len(_cd['cards'])} 张内置卡片全部通过参数校验", not _bad_cards, _bad_cards[:5])

_ids = [c["id"] for c in _cd["cards"] if not c["custom"]]
check("内置卡片 id 唯一", len(set(_ids)) == len(_ids))
_names = [c["name"] for c in _cd["cards"] if not c["custom"]]
check("内置卡片名唯一", len(set(_names)) == len(_names))
check("内置卡片 id 都是规范 slug（ASCII）",
      all(_re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", i) for i in _ids),
      [i for i in _ids if not _re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", i)][:5])

# ---- 每个声明的参数都必须真的到达任务 -------------------------------------
# 这条是为 `bitDepth` 那次漏掉的：OPS 里有它、h_convert 认它、卡片配了它，
# 但 op_convert 自己又列了一遍参数名单并把它丢掉 —— 全程静默，产出还是默认位深。
# 只要还允许"路由层重新列一遍参数"就会再犯，所以这里直接断言到底。
_s, _fl = req("GET", "/api/files")
_files = _fl["files"] if isinstance(_fl, dict) else _fl
_pfid = _files[0]["id"] if _files else None
check("有文件可用于参数透传探测", _pfid is not None)

_png_raw, _png_ctype = multipart({"paths": json.dumps(["probe/art.png"])},
                                 [("files", "art.png", _PNG_1PX, "image/png")])
_s, _img = req("POST", "/api/upload", raw=_png_raw, ctype=_png_ctype)
_imgrel = (_img["saved"][0]["relPath"]
           if isinstance(_img, dict) and _img.get("saved") else "")


def _sample(spec):
    """按参数类型造一个合法的样本值"""
    t = spec["type"]
    if t == "enum":
        return spec["options"][0]["value"]
    if t == "int":
        return spec.get("default", spec.get("min", 1))
    if t == "float":
        return spec.get("default", spec.get("min", 1.0))
    if t == "bool":
        return True
    if t == "tags":
        return {"album": "probe"}
    if spec["key"] == "imagePath":
        return _imgrel
    return spec.get("placeholder") or spec.get("default") or "probe"


if _pfid:
    dropped = []
    total_params = 0
    for o in ops["ops"]:
        for p in o["params"]:
            total_params += 1
            val = _sample(p)
            if p["key"] == "imagePath" and not _imgrel:
                continue
            body = {"fileIds": [_pfid], p["key"]: val}
            for rq in o["params"]:                 # 补上必填项
                if rq.get("required") and rq["key"] not in body:
                    body[rq["key"]] = _sample(rq)
            # cover 的 imagePath 现在不是 required（改成执行时选图了），
            # 但 op_cover 仍要求"至少给 imagePath 或 mapping"，不给就 400。
            if o["op"] == "cover" and _imgrel and "imagePath" not in body:
                body["imagePath"] = _imgrel
            s, r = req("POST", f"/api/ops/{o['op']}", body)
            if s != 200:
                dropped.append(f"{o['op']}.{p['key']} 提交失败 {s}")
                continue
            tid = (r.get("taskIds") or [None])[0]
            if not tid:
                continue
            _s2, t2 = req("GET", f"/api/tasks/{tid}")
            if p["key"] not in ((t2 or {}).get("params") or {}):
                dropped.append(f"{o['op']}.{p['key']} 被路由层丢掉")
    check(f"{total_params} 个声明参数全部透传到任务", not dropped, dropped[:6])

# 清掉探测用的图片
if _imgrel:
    _s, _allf = req("GET", "/api/files")
    for f in (_allf["files"] if isinstance(_allf, dict) else _allf):
        if str(f.get("relPath", "")).startswith("probe/"):
            req("DELETE", f"/api/files/{f['id']}?withDisk=true")

# 前端**不该**再留卡片兜底表：它是漂移源（曾经 13 vs 15），
# 而且断网时显示一排点下去执行不了的卡片 = 假数据。
_appjs = (ROOT / "app.js").read_text(encoding="utf-8")
# 别写 `const CARDS = \[(.*?)\n\];`：现在它是一行的 `[];`，
# 非贪婪会一路吃到后面任意一个 `\n];`，把无关代码吞进来当成"内容非空"。
check("前端卡片表是空的（不再有本地兜底目录）", "const CARDS = [];" in _appjs)
check("前端快照表是空的", "const SNAPS = [];" in _appjs)
check("app.js 里没有写死的卡片条目", "{ cat:" not in _appjs)
check("断网时清空卡片与快照",
      "CARDS.length = 0" in _appjs and "SNAPS.length = 0" in _appjs)

s, cd = req("GET", "/api/cards")
check("内置卡片都在 /api/cards 里", cd["builtinCount"] == len(cards_mod.BUILTIN_CARDS), cd["builtinCount"])
check("内置卡片标记 custom=false", all(not c["custom"] for c in cd["cards"]))
check("每张卡都带 op 与 params", all(c.get("op") for c in cd["cards"]))

# 命令预览
s, pv = req("GET", "/api/cards/ops/convert/preview?params=" +
            urllib.parse.quote(json.dumps({"format": "mp3", "bitrate": "320k"})))
check("预览含 libmp3lame 与码率", "libmp3lame" in pv["command"] and "320k" in pv["command"], pv)
s, pv2 = req("GET", "/api/cards/ops/normalize/preview?params=" +
             urllib.parse.quote(json.dumps({"targetLufs": -14})))
check("预览代入 targetLufs", "I=-14" in pv2["command"], pv2)
check("未知操作预览 404", req("GET", "/api/cards/ops/nope/preview")[0] == 404)

# 新建自定义卡片
s, made = req("POST", "/api/cards", {
    "name": "冒烟-自定义MP3", "op": "convert", "cat": "自定义",
    "desc": "测试用", "params": {"format": "mp3", "bitrate": "320k"}})
check("POST /api/cards 200", s == 200, made)
new_id = made["card"]["id"] if s == 200 else None
check("新卡片 custom=true", made["card"]["custom"] is True)
check("无损格式不带 bitrate 默认值",
      "bitrate" not in req("POST", "/api/cards",
                           {"name": "冒烟-自定义FLAC", "op": "convert",
                            "params": {"format": "flac"}})[1]["card"]["params"])
flac_card_id = None

s, cd2 = req("GET", "/api/cards")
check("列表里能看到新卡片", any(c["name"] == "冒烟-自定义MP3" for c in cd2["cards"]))
check("内置数不变", cd2["builtinCount"] == len(cards_mod.BUILTIN_CARDS))
check("卡片总数 = 内置 + 自定义",
      len(cd2["cards"]) == cd2["builtinCount"] + sum(1 for c in cd2["cards"] if c["custom"]))

# 改名
s, upd = req("PUT", f"/api/cards/{new_id}",
             {"name": "冒烟-改名后", "op": "convert", "params": {"format": "mp3"}})
check("PUT /api/cards 200", s == 200, upd)
check("名字已改", upd["card"]["name"] == "冒烟-改名后")
s, cd3 = req("GET", "/api/cards")
check("旧名消失、新名出现",
      not any(c["name"] == "冒烟-自定义MP3" for c in cd3["cards"])
      and any(c["name"] == "冒烟-改名后" for c in cd3["cards"]))

# 内置卡片不许改/删
bid = next(c["id"] for c in cd3["cards"] if not c["custom"])
check("改内置卡返回 400", req("PUT", f"/api/cards/{bid}", {"name": "x", "op": "probe"})[0] == 400)
check("删内置卡返回 400", req("DELETE", f"/api/cards/{bid}")[0] == 400)
check("删不存在的卡 404", req("DELETE", "/api/cards/c_nope")[0] == 404)

# 非法输入
for label, body in [
    ("未知操作", {"name": "x", "op": "nope"}),
    ("未知参数", {"name": "x", "op": "convert", "params": {"bogus": 1}}),
    ("非法枚举", {"name": "x", "op": "convert", "params": {"format": "exe"}}),
    ("整数越界", {"name": "x", "op": "convert", "params": {"format": "flac", "compressionLevel": 99}}),
    ("浮点越界", {"name": "x", "op": "normalize", "params": {"targetLufs": -99}}),
    ("缺名称", {"op": "convert"}),
    ("缺必填", {"name": "x", "op": "cover_embed", "params": {}}),
]:
    s, e = req("POST", "/api/cards", body)
    check(f"{label} 被拒", s == 400, f"{s} {str(e)[:70]}")

# 导出 / 导入
s, ex = req("GET", "/api/cards/export")
check("导出含自定义卡片", s == 200 and len(ex["cards"]) >= 1, ex.get("count"))
s, im = req("POST", "/api/cards/import", {"cards": ex["cards"]})
check("重复导入被跳过（不覆盖）", im["count"] == 0 and im["skippedCount"] >= 1, im)
s, im2 = req("POST", "/api/cards/import", {"cards": []})
check("空导入返回 400", s == 400)

# 删掉冒烟建的卡
s, cd4 = req("GET", "/api/cards")
junk_cards = [c for c in cd4["cards"] if c["custom"] and c["name"].startswith("冒烟-")]
for c in junk_cards:
    req("DELETE", f"/api/cards/{c['id']}")
s, cd5 = req("GET", "/api/cards")
check("自定义卡片已清空", all(not c["custom"] for c in cd5["cards"]), len(cd5["cards"]))
check("清理后只剩内置卡片", len(cd5["cards"]) == len(cards_mod.BUILTIN_CARDS), len(cd5["cards"]))

# 真跑一次：自定义卡片的参数必须原样进到任务里
print("== 17. 自定义卡片真跑一次（参数要真的传到执行层）==")
raw, ctype = multipart({"paths": json.dumps(["卡片执行/源.wav"])},
                       [("files", "源.wav", wav.read_bytes(), "audio/wav")])
s, up = req("POST", "/api/upload", raw=raw, ctype=ctype)
src_id = up["saved"][0]["id"]

s, made = req("POST", "/api/cards", {
    "name": "冒烟-执行用卡", "op": "convert",
    "params": {"format": "flac", "compressionLevel": 8}})
check("建卡成功", s == 200, made)
exec_card = made["card"]
check("卡参数保留了 compressionLevel=8",
      exec_card["params"].get("compressionLevel") == 8, exec_card["params"])

# 前端「立即执行」就是这一步：把卡片 params 原样 POST 给 /api/ops/<op>
s, submitted = req("POST", "/api/ops/convert",
                   {"fileIds": [src_id], **exec_card["params"]})
check("提交执行 200", s == 200, submitted)
task_id = (submitted.get("taskIds") or [None])[0]
row = None
for _ in range(80):
    s, t = req("GET", f"/api/tasks/{task_id}")
    if isinstance(t, dict) and t.get("state") in ("success", "failed"):
        row = t
        break
    time.sleep(0.25)
check("任务成功", row and row.get("state") == "success", row)
check("任务参数里带着卡片的 compressionLevel=8",
      row and row.get("params", {}).get("compressionLevel") == 8,
      row and row.get("params"))
out_file = (row or {}).get("result", {}).get("output")
check("真的产出了文件", bool(out_file) and (ROOT / out_file).exists(), out_file)
print(f"        产出 {out_file}")

# 收尾：删卡、删文件、删产物
req("DELETE", f"/api/cards/{exec_card['id']}")
req("DELETE", f"/api/files/{src_id}?withDisk=true")
if out_file:
    (ROOT / out_file).unlink(missing_ok=True)

# ============================================================ 18. 标签读写矩阵
# 这一节是为一个"成功了但什么都没写"的 bug 加的：
# WAV / AIFF 的标签是 ID3 帧，而 mutagen 没有 EasyWAVE/EasyAIFF，
# 于是 `f['title'] = [...]` 抛 TypeError，又被 `except Exception: pass` 吞掉 ——
# PUT /tags 返回 200、written=0、文件一个字节没变。AAC 更是压根不支持标签。
# 逐格式写→读回比对，才能挡住这类静默失败。
print("== 18. 标签读写矩阵（每格式写入后必须读得回来）==")
TAG_CASES = [("flac", "flac", 44100), ("mp3", "libmp3lame", 44100), ("wav", "pcm_s24le", 44100),
             ("aiff", "pcm_s16be", 44100), ("m4a", "aac", 44100), ("ogg", "libvorbis", 44100),
             ("opus", "libopus", 48000), ("wma", "wmav2", 44100)]
WANT = {"title": "夜航西飞", "artist": "陈婧霏", "tracknumber": "3"}
tag_ids = []
for ext, codec, sr in TAG_CASES:
    p = WORK / f"tag.{ext}"
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=0.3", "-ac", "2",
                        "-ar", str(sr), "-c:a", codec, str(p)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"        跳过 {ext}（本机 ffmpeg 造不出来）")
        continue
    raw, ctype = multipart({"paths": json.dumps([f"标签/{ext}/t.{ext}"])},
                           [("files", f"t.{ext}", p.read_bytes(), f"audio/{ext}")])
    s, u = req("POST", "/api/upload", raw=raw, ctype=ctype)
    if not (isinstance(u, dict) and u.get("saved")):
        check(f"{ext} 上传", False, u)
        continue
    tid = u["saved"][0]["id"]
    tag_ids.append(tid)
    s, w = req("PUT", f"/api/files/{tid}/tags", {"tags": WANT})
    check(f"{ext} 写标签 200", s == 200, f"{s} {str(w)[:60]}")
    s, got = req("GET", f"/api/files/{tid}/tags")
    tags = got.get("tags", {}) if isinstance(got, dict) else {}
    check(f"{ext} 标签读得回来", all(tags.get(k) == v for k, v in WANT.items()), tags)

# AAC 不支持标签：必须明确失败，不能假装成功
p = WORK / "tag.aac"
if subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                   "-i", "sine=frequency=440:duration=0.3", "-ar", "44100",
                   "-c:a", "aac", str(p)], capture_output=True).returncode == 0:
    raw, ctype = multipart({"paths": json.dumps(["标签/aac/t.aac"])},
                           [("files", "t.aac", p.read_bytes(), "audio/aac")])
    s, u = req("POST", "/api/upload", raw=raw, ctype=ctype)
    if isinstance(u, dict) and u.get("saved"):
        aid = u["saved"][0]["id"]
        tag_ids.append(aid)
        s, w = req("PUT", f"/api/files/{aid}/tags", {"tags": WANT})
        # 断言**具体状态码**，不能写 s >= 400 —— 那样 500 也能过。
        # 这个洞真的漏过一次：AAC 写标签走了 HTTPException(500)，
        # "格式不支持"被当成了服务端故障，日志里看着像后端崩了。
        check("AAC 写标签明确失败，且是 415 而不是 500",
              s == 415, f"{s} {str(w)[:80]}")

# 非音频文件没有波形：/peaks 必须是 4xx，不能是 500
# （库里允许放图片，前端以前对**每个**文件都拉 peaks，图片必然解码失败，
#   于是每刷新一次列表就刷一串 500 —— 实测日志里 100+ 条）
raw, ctype = multipart({"paths": json.dumps(["冒烟测试/波形/图.png"])},
                       [("files", "图.png", _PNG_1PX, "image/png")])
s, u = req("POST", "/api/upload", raw=raw, ctype=ctype)
if isinstance(u, dict) and u.get("saved"):
    pid = u["saved"][0]["id"]
    _s, fl = req("GET", "/api/files")
    row = next((x for x in (fl.get("files") or []) if x["id"] == pid), None)
    check("图片在列表里被标成 kind=image（前端靠它跳过波形）",
          row is not None and row.get("kind") == "image", row and row.get("kind"))
    s, body = req("GET", f"/api/files/{pid}/peaks?buckets=100")
    check("图片请求 /peaks 返回 415 而不是 500", s == 415, f"{s} {str(body)[:100]}")
    req("DELETE", f"/api/files/{pid}?withDisk=true")

# ---- 「显示所在目录」：只收 fid，路径由后端算，且必须落在 uploads/ 内 ----
# 一律用 open=false：真拉起 explorer 会在开发机上弹一屏资源管理器。
print("\n== 显示所在目录（/reveal） ==")
s, fl = req("GET", "/api/files")
_files = fl.get("files") or []
if _files:
    _rid = _files[0]["id"]
    s, r = req("POST", f"/api/files/{_rid}/reveal?open=false")
    check("reveal 返回 200", s == 200, f"{s} {str(r)[:80]}")
    _p = (r or {}).get("path", "")
    check("返回的是**绝对**路径", bool(_p) and Path(_p).is_absolute(), _p)
    try:
        _inside = config_mod.UPLOADS.resolve() in Path(_p).resolve().parents
    except Exception:
        _inside = False
    check("路径落在 uploads/ 内（越界拿不到）", _inside, _p)
    check("open=false 时不拉起任何进程", (r or {}).get("opened") is False, r)
    check("明确标出这是工作副本，不是原文件",
          (r or {}).get("kind") == "working-copy", (r or {}).get("kind"))

    # 软删除之后工作副本还在（withDisk=false），reveal 应当仍然可用
    req("DELETE", f"/api/files/{_rid}?withDisk=false")
    s, _ = req("POST", f"/api/files/{_rid}/reveal?open=false")
    check("软删除后仍能 reveal（行还在、文件也还在）", s == 200, s)
    # 真删掉磁盘上那份之后必须明确报错，而不是打开一个空目录
    req("DELETE", f"/api/files/{_rid}?withDisk=true")
    s, _ = req("POST", f"/api/files/{_rid}/reveal?open=false")
    check("工作副本没了 → 404", s == 404, s)

# 用 ASCII 假 id：URL 里放中文会让 urllib 直接抛异常（要自己转义），
# 这条只是想验 404，没必要牵扯编码。
s, _ = req("POST", "/api/files/f_nope/reveal?open=false")
check("不存在的文件 → 404", s == 404, s)

for tid in tag_ids:
    req("DELETE", f"/api/files/{tid}?withDisk=true")

print(f"\n结果：{ok} passed, {fail} failed")

# ---- 清理：把本次上传的测试文件全部删掉，不污染用户的库 ----
print("== 清理本次上传的测试文件 ==")
s, fl = req("GET", "/api/files")
files = fl["files"] if isinstance(fl, dict) else fl
junk = [x for x in files if x["name"] in ("测试音.wav", "evil.wav", "有封面.flac", "cover.png")
        or x["name"].startswith(("evil-", "b0", "b1", "b2"))
        or x.get("relPath", "").startswith(("冒烟测试/", "abs/", "C_/", "a/", "封面测试/", "covers/", "批删/"))]
for x in junk:
    # withDisk=true：DELETE 默认只从库里摘掉、保留磁盘原文件（软删除），
    # 测试垃圾要连磁盘一起清，否则 uploads/ 会越堆越多。
    req("DELETE", f"/api/files/{x['id']}?withDisk=true")
print(f"        删除 {len(junk)} 个：{[x['name'] for x in junk]}")
req("DELETE", "/api/logs")
print("        已清空日志缓冲")

s, fl = req("GET", "/api/files")
left = len(fl["files"] if isinstance(fl, dict) else fl)
print(f"        剩余 {left} 个文件（应为库内原有数据）")

sys.exit(1 if fail else 0)
