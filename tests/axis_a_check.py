"""A 轴验证：预设卡不是"能提交"就算过，得产出**名字所说的那个东西**。

重点验三件新增能力：
  1. `convert.bitDepth` —— 16/24/32/32f 是否真的产出对应位深（含 FLAC 走 sample_fmt、
     AIFF 走大端、FLAC 拒绝浮点）
  2. `rename` 的 `{tracknumber:02}` 零填充，以及缺标签时的残渣清理
  3. `waveform` 的 4K / 手机壁纸尺寸没被静默夹到旧上限 2000
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
ROOT = Path(__file__).resolve().parent.parent
WORK = Path(__file__).resolve().parent / "_axis_tmp"
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
                return resp.status, b, {k.lower(): v for k, v in resp.headers.items()}
            t = b.decode("utf-8", "replace")
            return resp.status, (json.loads(t) if t.strip().startswith(("{", "[")) else t)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def multipart(fields, files):
    b = "----a" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for name, fn, content, mime in files:
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\";"
                f" filename=\"{fn}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
        out += content + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={b}"


def wait_task(tid, tries=200):
    for _ in range(tries):
        s, t = req("GET", f"/api/tasks/{tid}")
        if isinstance(t, dict) and t.get("state") in ("success", "failed"):
            return t
        time.sleep(0.25)
    return None


def run_op(op, params, fid):
    s, r = req("POST", f"/api/ops/{op}", {"fileIds": [fid], **params})
    if s != 200:
        return None, f"提交失败 {s} {str(r)[:80]}"
    tid = (r.get("taskIds") or [None])[0]
    return wait_task(tid), None


def out_file(task):
    rel = (task or {}).get("result", {}).get("output")
    return (ROOT / rel) if rel else None


# ---------------------------------------------------------------- 素材

src = WORK / "素材.wav"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                "-i", "sine=frequency=440:duration=0.6", "-ac", "2", "-ar", "44100",
                str(src)], check=True)
raw, ctype = multipart({"paths": json.dumps(["A轴/素材.wav"])},
                       [("files", "素材.wav", src.read_bytes(), "audio/wav")])
s, up = req("POST", "/api/upload", raw=raw, ctype=ctype)
fid = up["saved"][0]["id"]
print(f"素材 id={fid}\n")


def probe_out(p: Path, field="bits_per_raw_sample"):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                        "-show_entries", f"stream=codec_name,sample_fmt,{field}",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return r.stdout.strip()


print("== 1. convert.bitDepth 真的改位深 ==")
cases = [
    ("WAV 16bit",  {"format": "wav", "bitDepth": "16"},  "pcm_s16le", "s16"),
    ("WAV 24bit",  {"format": "wav", "bitDepth": "24"},  "pcm_s24le", "s32"),
    ("WAV 32bit",  {"format": "wav", "bitDepth": "32"},  "pcm_s32le", "s32"),
    ("WAV 32f",    {"format": "wav", "bitDepth": "32f"}, "pcm_f32le", "flt"),
    ("AIFF 24bit", {"format": "aiff", "bitDepth": "24"}, "pcm_s24be", "s32"),
    ("FLAC 16bit", {"format": "flac", "bitDepth": "16"}, "flac", "s16"),
    ("FLAC 24bit", {"format": "flac", "bitDepth": "24"}, "flac", "s32"),
]
for label, params, want_codec, want_fmt in cases:
    task, err = run_op("convert", params, fid)
    if err or not task or task["state"] != "success":
        check(label, False, err or f"state={(task or {}).get('state')} "
                                 f"{(task or {}).get('error', '')[:60]}")
        continue
    p = out_file(task)
    got = probe_out(p)
    check(f"{label} → {got}", got.startswith(want_codec) and want_fmt in got,
          f"期望含 {want_codec}/{want_fmt}")
    p.unlink(missing_ok=True)

# FLAC 不支持浮点：必须**明确报错**，不能静默产出别的位深
task, err = run_op("convert", {"format": "flac", "bitDepth": "32f"}, fid)
check("FLAC + 32f 被明确拒绝",
      task is not None and task["state"] == "failed" and "浮点" in (task.get("error") or ""),
      (task or {}).get("error"))
p = out_file(task) if task else None
if p:
    p.unlink(missing_ok=True)

# 非无损容器指定位深也要被拒（规格里 onlyIf 挡了，但接口可被直接调用）
task, err = run_op("convert", {"format": "mp3", "bitDepth": "16"}, fid)
check("MP3 + bitDepth 被明确拒绝",
      task is not None and task["state"] == "failed", (task or {}).get("error"))

print("\n== 2. rename 零填充与残渣清理 ==")
# 每个模板用**全新上传的一份副本**：重命名会改文件路径，
# 而"改回去"只能靠 {filename}（它取的是当前 stem，改不回原名），
# 于是用例之间会互相干扰、并发跑还会撞出"源文件不存在"。
tpl_cases = [
    ("{tracknumber:02} - {title}",                      "03 - 夜航西飞"),
    ("{artist} - {tracknumber:02} - {title}",           "陈婧霏 - 03 - 夜航西飞"),
    # album 没写 → 连续分隔符要被收掉，不能出现 "- -"
    ("{artist} - {album} - {tracknumber:02} - {title}", "陈婧霏 - 03 - 夜航西飞"),
    # genre 没写 → 空方括号要被去掉
    ("[{genre}] {artist} - {title}",                    "陈婧霏 - 夜航西飞"),
    ("{tracknumber} - {title}",                         "3 - 夜航西飞"),
]
for i, (pat, want) in enumerate(tpl_cases):
    raw, ctype = multipart({"paths": json.dumps([f"A轴/r{i}.wav"])},
                           [("files", f"r{i}.wav", src.read_bytes(), "audio/wav")])
    s, u = req("POST", "/api/upload", raw=raw, ctype=ctype)
    rid = u["saved"][0]["id"]
    # purge=true：软删除的行仍占着 rel_path，不清干净下一轮改名会撞唯一约束。
    # 用例 2/3 渲染出的目标名相同（album 缺失要被收掉），必须一案一清，
    # 否则第二个只会拿到 "-1" 后缀，断言就不再是"精确名字"了。
    try:
        req("PUT", f"/api/files/{rid}/tags",
            {"tags": {"title": "夜航西飞", "artist": "陈婧霏",
                      "tracknumber": "3", "date": "2024"}})
        time.sleep(0.2)
        task, err = run_op("rename", {"pattern": pat}, rid)
        if err:
            check(f"重命名 {pat}", False, err)
            continue
        got = (task or {}).get("result", {}).get("name")
        check(f"{pat} → {got}", got == want + ".wav",
              f"期望 {want}.wav，实际 {(task or {}).get('error') or got}")
    finally:
        req("DELETE", f"/api/files/{rid}?purge=true&withDisk=true")

print("\n== 3. waveform 尺寸没被静默夹紧 ==")
for label, w, h in [("4K", 3840, 2160), ("手机壁纸", 1170, 2532)]:
    task, err = run_op("waveform", {"width": w, "height": h, "color": "#FFFFFF",
                                    "background": "transparent"}, fid)
    if err or not task or task["state"] != "success":
        check(f"{label} {w}x{h}", False, err or (task or {}).get("error", "")[:70])
        continue
    res = task["result"]
    check(f"{label} 实际输出 {res.get('width')}x{res.get('height')}",
          res.get("width") == w and res.get("height") == h,
          f"期望 {w}x{h}")
    p = out_file(task)
    if p and p.exists():
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height,pix_fmt",
                            "-of", "csv=p=0", str(p)], capture_output=True, text=True)
        check(f"{label} PNG 实际像素 {r.stdout.strip()}", f"{w},{h}" in r.stdout, r.stdout)
        p.unlink(missing_ok=True)

print("\n== 4. 每个 op 的卡片参数都能被后端接受 ==")
s, cd = req("GET", "/api/cards")
ops_seen = {}
for c in cd["cards"]:
    if c["custom"] or c["op"] in ops_seen:
        continue
    ops_seen[c["op"]] = c
check(f"覆盖 {len(ops_seen)} 个 op", len(ops_seen) == 12, sorted(ops_seen))

# 收尾
req("DELETE", f"/api/files/{fid}?purge=true&withDisk=true")
for p in ROOT.glob("outputs/**/*"):
    if p.is_file():
        p.unlink(missing_ok=True)

print(f"\n结果：{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
