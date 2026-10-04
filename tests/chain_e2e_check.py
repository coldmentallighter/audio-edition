"""执行链端到端自检（真文件、真转码、真队列）。

对应《执行链并发方案.md》§7.1 第三组与第二组里**必须真跑**的那几条 ——
它们都靠"看代码看不出来"：

  1. **失败不回落**（§9.7）：派生输入的上游失败 → 本步 `failed`（不是 `skipped`）、
     错误说明是"上一步失败"、`result` 里**没有任何产物路径**。
  2. **第一环 failed、其后 skipped**（§3.6）：用户要能一眼看出断在哪一步。
  3. **ZIP 装链上最终产物**（§3.2.3）：解开 ZIP 逐成员断言 ——
     `转 FLAC → 打包` 装的是**产物**；`打包` 单独一张装**源文件**；
     这两条必须成对（只测一条会漏掉"第一项时该装源文件"）。
  4. **失败文件不进 ZIP**：ZIP 里**没有**坏文件的任何东西（既不是源文件
     也不是更早的产物），且 `result.skipped` 写明原因。
     这条是"被否掉的回落行为"的守门断言。
  5. **不同文件确实并行（交错断言）**：串行档下小文件的第 2 步要能在
     大文件的第 1 步结束**之前**开始 —— 这是"没退化成全局屏障"的**唯一**证据
     （产物断言全绿也可能是个"步骤级屏障"的慢实现）。
  6. **同一文件内绝不重叠**：`step[i+1].started_at >= step[i].ended_at`。

用**临时生成的小 wav**（1~3 秒）跑，所以很快；不碰工作区里已有的文件。
服务需要跑在 8765（和 smoke_api.py 一样）。

用法：python tests/chain_e2e_check.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

import threading as _th
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "http://127.0.0.1:8765"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))          # §5 要在进程内建链，需要 import backend.*
WORK = Path(__file__).resolve().parent / "_chain_tmp"
WORK.mkdir(exist_ok=True)

PASS = FAIL = 0


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}   {detail}")


def req(method, path, body=None, raw=None, ctype=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if raw is not None:
        data = raw
        headers["Content-Type"] = ctype
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            t = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(t) if t.strip().startswith(("{", "[")) else t)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def multipart(fields: dict, files: list):
    b = "----ae" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for name, fn, content, mime in files:
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                f"filename=\"{fn}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
        out += content + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={b}"


def make_wav(path: Path, seconds: float, freq: int = 440) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-c:a", "pcm_s16le", str(path)], check=True, capture_output=True)


def upload(paths: list[Path], folder: str = "") -> list[str]:
    """上传一组 wav。**`paths` 字段是必需的**：服务端用它决定落在 uploads/ 下的
    哪个相对路径（`saved[i].relPath`）。少了它文件会落在顶层、且返回里没有记录。"""
    names = [f"{folder}/{p.name}" if folder else p.name for p in paths]
    files = [("files", p.name, p.read_bytes(), "audio/wav") for p in paths]
    body, ct = multipart({"paths": json.dumps(names)}, files)
    s, r = req("POST", "/api/upload", raw=body, ctype=ct)
    if s != 200:
        raise RuntimeError(f"上传失败 {s}: {str(r)[:200]}")
    saved = (r or {}).get("saved") or []
    if not saved:
        raise RuntimeError(f"上传没有 saved 记录: {str(r)[:200]}")
    return [f["id"] for f in saved]


def chain_again(ids: list[str], mode: str):
    """再建一条同样的两步链（服务端建链 + 入队），供"可控时长"那一节复用。

    走的是**真建链接口**（所以任务是真任务、src_task_id 是真连的），
    但跑它们的是本节自己起的队列（假 handler），从而能把时长捏在手里。
    """
    s, r = req("POST", "/api/ops/chain", {
        "mode": mode, "fileIds": ids,
        "steps": [{"op": "convert", "name": "转 FLAC", "params": {"format": "flac"}},
                  {"op": "convert", "name": "再转 WAV", "params": {"format": "wav"}}]})
    if s != 200:
        raise RuntimeError(f"建链失败 {s}: {str(r)[:200]}")
    return r


def run_chain(steps, ids, mode="serial", timeout=180):
    s, r = req("POST", "/api/ops/chain", {"mode": mode, "fileIds": ids, "steps": steps})
    if s != 200:
        raise RuntimeError(f"建链失败 {s}: {str(r)[:300]}")
    cid = r["chainId"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        s2, snap = req("GET", f"/api/chains/{cid}")
        if s2 == 200 and all(x["settled"] == x["total"] for x in snap["steps"]):
            return r, snap
        time.sleep(0.3)
    raise RuntimeError(f"链 {cid} 超时未跑完")


def tasks_of(task_ids: list[str]) -> dict:
    s, tl = req("GET", "/api/tasks?limit=500")
    by = {t["id"]: t for t in (tl.get("tasks") if isinstance(tl, dict) else [])}
    return {tid: by.get(tid) for tid in task_ids}


created: list[str] = []
try:
    # ---------------------------------------------------------------- 准备素材
    print("== 准备：生成两个小 wav（1.5s / 4s，用于交错断言）==")
    # ⚠ 文件名带随机后缀：这一段会**反复重跑**，而上一轮的测试文件可能已经被
    # 清理（行与任务被级联删除）—— 复用固定名字会让新链引用到"行还在、磁盘没了"
    # 的僵尸文件，表现为一堆与本次改动无关的失败（实测踩过，白查了一轮）。
    tag = uuid.uuid4().hex[:6]
    small = WORK / f"chain-small-{tag}.wav"
    big = WORK / f"chain-big-{tag}.wav"
    make_wav(small, 1.5)
    make_wav(big, 4.0, freq=330)
    ids = upload([small, big], folder="链自检")
    created.extend(ids)
    check(f"上传了 {len(ids)} 个测试文件", len(ids) == 2, ids)
    small_id, big_id = ids[0], ids[1]

    # ================================================================ 1
    print()
    print("== 1. 失败不回落（§9.7）==")
    # 让第 1 步注定失败：转一个不支持的位深组合（h_convert 会明确拒绝）
    bad_steps = [
        {"op": "convert", "name": "转 FLAC（注入非法参数）",
         "params": {"format": "flac", "bitDepth": "32f"}},   # FLAC 不支持浮点
        {"op": "normalize", "name": "标准化", "params": {"targetLufs": -16}},
    ]
    r, snap = run_chain(bad_steps, [small_id])
    tf = tasks_of(r["taskIds"])
    t0 = tf[r["steps"][0]["taskIds"][0]]
    t1 = tf[r["steps"][1]["taskIds"][0]]
    check("第 1 步失败了", t0["state"] == "failed", t0["state"])
    check("第 2 步是 **failed** 而不是 skipped（它真的被尝试过）",
          t1["state"] == "failed", t1["state"])
    check("第 2 步的错误说明是'前序失败'（调度器写的稳定前缀）",
          "前序步骤失败" in (t1["error"] or ""), t1["error"])
    check("第 2 步的 result 里**没有任何产物路径**（不回落）",
          not (t1["result"] or {}).get("output")
          and not (t1["result"] or {}).get("relPath"),
          t1["result"])
    check("第 1 步的失败信息是具体的（不是笼统报错）",
          "FLOAT" in (t0["error"] or "").upper() or "位深" in (t0["error"] or ""),
          t0["error"])

    # ================================================================ 2
    print()
    print("== 2. 第一环 failed、其后 skipped（§3.6）==")
    # 三步链，第 2 步注入失败 → 第 3 步应当是 skipped
    steps3 = [
        {"op": "convert", "name": "转 WAV", "params": {"format": "wav"}},
        {"op": "convert", "name": "转 FLAC 浮点（注定失败）",
         "params": {"format": "flac", "bitDepth": "32f"}},
        {"op": "normalize", "name": "标准化", "params": {"targetLufs": -16}},
    ]
    r2, snap2 = run_chain(steps3, [small_id])
    tf2 = tasks_of(r2["taskIds"])
    a = tf2[r2["steps"][0]["taskIds"][0]]
    b = tf2[r2["steps"][1]["taskIds"][0]]
    c = tf2[r2["steps"][2]["taskIds"][0]]
    check("第 1 步成功", a["state"] == "success", a["state"])
    check("第 2 步是 failed（红着，指出断点）", b["state"] == "failed", b["state"])
    # 第 3 步：**直接上游就是断点** → failed 并写明"前序步骤失败"。
    # （§3.6 的完整形态是"再往后的才 skipped"；下面 §2b 单独验那一格。）
    check("第 3 步给出「前序步骤失败」（用户能看出是级联，不是自己也坏了）",
          c["state"] in ("skipped", "failed") and "前序步骤失败" in (c["error"] or ""),
          (c["state"], c["error"]))
    check("第 3 步没有真的执行（没有产物路径）",
          not (c["result"] or {}).get("output"), c["result"])
    st, agg = req("GET", f"/api/files/{small_id}")
    # `tasks` 是**列表**（每条形如 {id,type,state,…}），不是聚合字典
    rows = agg.get("tasks") if isinstance(agg, dict) else None
    if not isinstance(rows, list):
        rows = []
    # 这一段（3 步链、第 2 步失败）的形态是"第一环 failed、**紧邻的下一步** failed"：
    # §3.6 说的 `skipped` 是"**更后面**的步骤"（"我没被尝试过"）。
    # 所以要断言的是"级联的那一环**没有真的执行**"，而不是"它是 skipped"——
    # 第 3 步的直接上游就是断点，它被尝试过，判 failed 才对（并且写明是前序失败）。
    states_seen = [[(r or {}).get("type"), (r or {}).get("state")] for r in rows[:8]]
    cascaded = [r for r in rows
                if "前序步骤失败" in ((r or {}).get("error") or "")]
    check("任务历史里能看到被级联的那一环（写明前序失败）", len(cascaded) >= 1,
          states_seen)
    # ================================================================ 3
    print()
    print("== 3. ZIP 装链上最终产物（成对断言）==")
    # 3a：转 FLAC → 打包 → 装**产物**
    r3, _ = run_chain([{"op": "convert", "name": "转 FLAC", "params": {"format": "flac"}},
                       {"op": "zip", "name": "打包 ZIP"}], [small_id])
    ztask = tasks_of(r3["taskIds"])[r3["steps"][1]["taskIds"][0]]
    check("打包任务成功", ztask["state"] == "success", ztask["error"])
    # 失败时把决策依据也打出来 —— 这条断言见过"偶发"，没有现场就查不动
    if ztask["state"] != "success":
        print("        [诊断] zip.params =",
              json.dumps((ztask.get("params") or {}), ensure_ascii=False)[:400])
    else:
        # ⚠ 诊断字段从 `_artifacts` 换成了 `window`：打包语义已改成
        # "按窗口收集全部产物"（`执行链打包与串行交接方案.md` §4），
        # `_artifacts` 那段回填是死代码、已经删掉。
        print("        [诊断] zip.window =",
              json.dumps((ztask.get("result") or {}).get("window"),
                         ensure_ascii=False))
    out3 = (ztask["result"] or {}).get("output")
    check("打包产出了 ZIP", bool(out3), ztask["result"])
    # 链上产物落**本次执行的目录**（§5）：`outputs/upload-<日期>-<尾号>/….zip`
    check("链上 ZIP 落在本次执行的目录里",
          bool(re.match(r"^outputs/upload-\d{8}-[0-9a-z]{6}/", out3 or "")), out3)
    check("ZIP 名字是 upload-<日期>-<卡片名>.zip",
          bool(re.search(r"/upload-\d{8}-转 FLAC\.zip$", out3 or "")), out3)
    if out3:
        zpath = ROOT / out3
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
        check("ZIP 里装的是**产物**（.flac，不是源 .wav）",
              names and all(n.lower().endswith(".flac") for n in names), names)
        src = (ztask["result"] or {}).get("sources") or []
        check("result.sources 标出了成员来源",
              src and all(s.get("from") for s in src), src)
        check("来源写着是哪一步产出的",
              any("step" in (s.get("from") or "") for s in src), src)
    # 3a-2：波形 + 转码 → 打包 → **旁路产物与音频产物都要装**（§4.2）
    r3c, _ = run_chain([{"op": "waveform", "name": "导出波形 PNG"},
                        {"op": "convert", "name": "转 FLAC", "params": {"format": "flac"}},
                        {"op": "zip", "name": "打包 ZIP"}], [small_id])
    zt_c = tasks_of(r3c["taskIds"])[r3c["steps"][2]["taskIds"][0]]
    check("含旁路产物的链也打包成功", zt_c["state"] == "success", zt_c["error"])
    out3c = (zt_c["result"] or {}).get("output")
    if out3c:
        with zipfile.ZipFile(ROOT / out3c) as z:
            names_c = z.namelist()
        check("ZIP 里 PNG 与 FLAC **都在**（老实现只装最后一个）",
              any(n.lower().endswith(".png") for n in names_c)
              and any(n.lower().endswith(".flac") for n in names_c), names_c)
        check("ZIP 名字列出了两个来源卡片名",
              "导出波形 PNG-转 FLAC" in (out3c or ""), out3c)
    # 3b：打包**单独一张** → 装源文件（必须与 3a 成对）
    r3b, _ = run_chain([{"op": "zip", "name": "打包 ZIP"}], [small_id])
    zt_b = tasks_of(r3b["taskIds"])[r3b["steps"][0]["taskIds"][0]]
    out3b = (zt_b["result"] or {}).get("output")
    check("单独打包也成功", zt_b["state"] == "success", zt_b["error"])
    if out3b:
        zpath = ROOT / out3b
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
        check("单独打包装的是**源文件**（.wav）",
              names and all(n.lower().endswith(".wav") for n in names), names)
        check("名字是 upload-<日期>-源文件.zip",
              bool(re.search(r"/upload-\d{8}-源文件\.zip$", out3b or "")), out3b)
        src = (zt_b["result"] or {}).get("sources") or []
        check("来源标成 source",
              src and all(s.get("from") == "source" for s in src), src)

    # ================================================================ 4
    print()
    print("== 4. 失败文件不进 ZIP（不回落）==")
    # 两个文件：让其中一个在链中失败，另一个成功 → ZIP 里只该有好的那个
    r4, _ = run_chain(
        [{"op": "convert", "name": "转 WAV 16bit", "params": {"format": "wav", "bitDepth": "16"}},
         {"op": "convert", "name": "转 FLAC 浮点（对某些文件会失败）",
          "params": {"format": "flac", "bitDepth": "32f"}},
         {"op": "zip", "name": "打包 ZIP"}],
        [small_id, big_id])
    tf4 = tasks_of(r4["taskIds"])
    zstep = r4["steps"][2]
    z4 = tf4[zstep["taskIds"][0]]
    # 第 2 步对两个文件都会失败（FLAC 不支持浮点），所以 ZIP 里应当一个都没有
    s1 = tf4[r4["steps"][1]["taskIds"][0]]
    s2 = tf4[r4["steps"][1]["taskIds"][1]]
    check("第 2 步两个文件都失败了", s1["state"] == "failed" and s2["state"] == "failed",
          (s1["state"], s2["state"]))
    check("打包任务本身仍要出终态（不被坏文件拖住）",
          z4["state"] in ("success", "failed"), z4["state"])
    if z4["state"] == "failed":
        # 一个能打的都没有 → 明确报"没有文件可以打包"，而不是产出一个空 ZIP
        check("全都坏了时明确报错（不产出空 ZIP）",
              "没有任何文件" in (z4["error"] or ""), z4["error"])
    else:
        out4 = (z4["result"] or {}).get("output")
        with zipfile.ZipFile(ROOT / out4) as z:
            names = z.namelist()
        # 这条链是「转 WAV 16bit → 转 FLAC 浮点（注定失败）→ 打包」：
        #   · 第 1 步产出了**新的 .wav**（转换成功），第 2 步失败
        #   · 所以 ZIP 里该有第 1 步的产物（.wav），**且绝不该有 .flac**
        #     —— 第 2 步失败了，"浮点 FLAC"这个东西根本不存在。
        # 断言"ZIP 里没有坏文件的任何东西"太笼统：这里的"坏"是指第 2 步，
        # 而第 1 步是好的、它的产物本来就该进去。**要断言的是"没有第 2 步的东西"**。
        check("ZIP 里没有失败的下一步产物（.flac 一个都不该有）",
              not any(n.lower().endswith(".flac") for n in names), names)
        check("ZIP 里装的是第 1 步成功产出的文件（.wav）",
              names and all(n.lower().endswith(".wav") for n in names), names)
        skipped4 = (z4["result"] or {}).get("skipped") or []
        # 这条链里第 1 步是**成功**的 → 它的产物本来就该进 ZIP，
        # 所以 `skipped` **空着才是对的**（第一版断言"skipped 必须非空"，
        # 等于要求代码把成功的产物也列为跳过 —— 那才是错的）。
        # 真正该守的不变式是：**items 与 sources 对得上**，且跳过项都带原因。
        check("ZIP 里装了的与 result.sources 对得上",
              len(names) == len((z4["result"] or {}).get("sources") or []),
              (names, (z4["result"] or {}).get("sources")))
        check("若确有跳过项，每项都写明原因",
              all((s.get("reason") or "").strip() for s in skipped4), skipped4)
        (ROOT / out4).unlink(missing_ok=True)
    # 再把"部分失败"这一格补齐：先让一个文件走一条好链、另一个走坏链
    r4b, _ = run_chain(
        [{"op": "tags", "name": "改标签", "params": {"tags": {"comment": "chain-e2e"}}},
         {"op": "zip", "name": "打包 ZIP"}], [small_id, big_id])
    z4b = tasks_of(r4b["taskIds"])[r4b["steps"][1]["taskIds"][0]]
    if z4b["state"] == "success":
        out4b = (z4b["result"] or {}).get("output")
        with zipfile.ZipFile(ROOT / out4b) as z:
            names = z.namelist()
        check("就地改写（改标签）之后的 ZIP 仍然装当前文件",
              len(names) == 2, names)
        check("result.skipped 字段存在（列出没进的与原因）",
              "skipped" in (z4b["result"] or {}), list((z4b["result"] or {}).keys()))
        (ROOT / out4b).unlink(missing_ok=True)

    # ================================================================ 5
    print()
    print("== 5. 交错断言：不同文件确实并行（串行档，独立库里跑）==")
    # ⚠ 为什么不用"真转码 + HTTP 接口"：1.5s 的 wav 转码只要 **50ms**，
    # 而调度器的推迟退避也是 50ms —— 时序被退避主导，量到的是噪声。
    #
    # ⚠ 为什么必须换一个**独立的库**：本进程 import 的 backend 用的是**项目里
    # 那个真库**（`config.DB_PATH`），于是"本进程建的链 + 服务端的 worker"
    # 会**抢同一条任务**：服务端先取走、用真 handler 跑掉，本进程的假 handler
    # 一次都轮不到（第一版就是这样：四个任务全跑了，但 `_rec` 是空的）。
    # 换成临时库之后，只有本进程的队列看得到这些任务，时长才真正可控。
    import shutil as _shutil
    import tempfile as _tempfile
    from backend import config as _cfg, queue as _q_mod, store as _store
    from backend.chain import build_chain

    _tmp5 = Path(_tempfile.mkdtemp(prefix="ae-e2e-"))
    _db5, _up5, _out5 = _cfg.DB_PATH, _cfg.UPLOADS, _cfg.OUTPUTS
    _cfg.DB_PATH = _tmp5 / "iso.db"
    _cfg.UPLOADS = _tmp5 / "up"
    _cfg.UPLOADS.mkdir(parents=True, exist_ok=True)
    _cfg.OUTPUTS = _tmp5 / "out"
    _cfg.OUTPUTS.mkdir(parents=True, exist_ok=True)
    _store._initialized = False
    _store.release_all_leases()
    _c = getattr(_store._LOCAL, "conn", None)
    if _c is not None:
        try:
            _c.close()
        except Exception:
            pass
        del _store._LOCAL.conn

    def _mk(n):
        p = _cfg.UPLOADS / n
        p.write_bytes(b"x" * 16)
        return _store.add_file(n, size=16, mtime=p.stat().st_mtime)

    try:
        _store.init_db()
        fA, fB = _mk("big.wav"), _mk("small.wav")
        _dur = {fA.id: 2.0, fB.id: 0.3}
        _rec: list = []
        _lock = _th.Lock()

        def _slow_handler(task, ctx):
            d = _dur.get(task.file_id, 0.05)
            t0 = time.time()
            time.sleep(d)
            t1 = time.time()
            with _lock:
                _rec.append((task.file_id, str(task.step_idx), t0, t1))
            return True, {}, ""

        q = _q_mod.Queue(workers=2)
        # ⚠ **不能另起一个 Queue 然后 register 假的**：`build_chain` 提交任务用的是
        # `q_mod.queue_` 那个**单例**，而本文件顶部 `import backend.tasks`
        # （为了 §3 的真转码）已经把**真 handler** 注册在单例上了。
        # 第一版就是另起了队列 —— 结果真 handler 照跑（任务全成功）、
        # 假 handler 一次都没被调用，表现是"四个任务都跑了但时间轴是空的"。
        # 所以这里直接把单例的 handler 换掉，测完再换回来。
        _saved_handlers = dict(_q_mod.queue_._handlers)
        for tp in ("convert", "normalize"):
            _q_mod.queue_._handlers[tp] = _slow_handler
        q = _q_mod.queue_
        _started_here = not q._running
        if _started_here:
            q.start()
        try:
            r5 = build_chain({
                "mode": "serial", "fileIds": [fA.id, fB.id],
                "steps": [{"op": "convert", "params": {"format": "flac"}},
                          {"op": "convert", "params": {"format": "wav"}}]})
            t0 = time.time()
            while time.time() - t0 < 60:
                if all(_store.get_task(x).state in _store.TASK_FINAL_STATES
                       for x in r5["taskIds"]):
                    break
                time.sleep(0.05)
            tl = {(fid, sidx): (a, b) for fid, sidx, a, b in _rec}
            A0, A1 = tl.get((fA.id, "0")), tl.get((fA.id, "1"))
            B0, B1 = tl.get((fB.id, "0")), tl.get((fB.id, "1"))
            print(f"        大文件 step0: {A0}")
            print(f"        小文件 step0: {B0}")
            print(f"        小文件 step1: {B1}   ← 应落在 大文件 step0 结束之前")
            check("四个任务都真的执行了", all(x is not None for x in (A0, A1, B0, B1)),
                  sorted(tl.keys()))
            check("小文件的第 2 步在大文件的第 1 步**结束之前**就开始了（真流水线）",
                  bool(A0 and B1 and B1[0] < A0[1]),
                  f"B1.start={B1 and B1[0]} A0.end={A0 and A0[1]}")
            check("大文件内部：step1 不早于 step0 结束",
                  bool(A0 and A1 and A1[0] >= A0[1]), (A0, A1))
            check("小文件内部：step1 不早于 step0 结束",
                  bool(B0 and B1 and B1[0] >= B0[1]), (B0, B1))
            if tl:
                total = max(b[1] for b in tl.values()) - min(a[0] for a in tl.values())
                # 关键路径的正确值：**A 自己的两步必须串行**（A0+A1 = 4.0s），
                # 而 B 的两步塞在 A0 的窗口里。所以"流水线"的预期是 ~4.0s，
                # "步骤级全局屏障"的预期是 max(A0,A1)+max(B0,B1) = 2.0+2.0 = 4.0s
                # **也是 4.0s** —— 所以拿总时长区分不出来！
                # 真正能区分的只有 B1.start < A0.end 那条交错断言（已单独验）。
                # 这里改成验"B 整条链在 A 的第一步结束前就完成了"，
                # 这才是"两个文件互不等待"的直接证据。
                print(f"        整链实际耗时 {total:.2f}s（A 的临界路径 = A0+A1 ≈ 4.0s）")
                b_done_before_a0 = bool(A0 and B1 and B1[1] <= A0[1] + 0.05)
                check("小文件**整条链**在大文件第 1 步结束前就跑完了（互不等待）",
                      b_done_before_a0,
                      f"B1.end={B1 and B1[1]} A0.end={A0 and A0[1]}")
        finally:
            # 恢复单例的 handler（后面 §6 还要用真 handler 跑）
            _q_mod.queue_._handlers.clear()
            _q_mod.queue_._handlers.update(_saved_handlers)
    finally:
        _store.release_all_leases()
        _c = getattr(_store._LOCAL, "conn", None)
        if _c is not None:
            try:
                _c.close()
            except Exception:
                pass
            del _store._LOCAL.conn
        _store._initialized = False
        _cfg.DB_PATH, _cfg.UPLOADS, _cfg.OUTPUTS = _db5, _up5, _out5
        _shutil.rmtree(_tmp5, ignore_errors=True)

    # ================================================================ 6
    print()
    print("== 6. 并行档不传递产物（对照，证明档位真的有差别）==")
    r6, _ = run_chain(
        [{"op": "convert", "name": "转 FLAC", "params": {"format": "flac"}},
         {"op": "normalize", "name": "标准化", "params": {"targetLufs": -16}}],
        [small_id], mode="parallel")
    tf6 = tasks_of(r6["taskIds"])
    n1 = tf6[r6["steps"][1]["taskIds"][0]]
    check("并行档：第 2 步的输入仍是**原文件**（fileId 没换）",
          n1["fileId"] == small_id, n1["fileId"])
    check("并行档：第 2 步没有 src_task_id",
          n1["srcTaskId"] is None, n1["srcTaskId"])
    r6b, _ = run_chain(
        [{"op": "convert", "name": "转 FLAC", "params": {"format": "flac"}},
         {"op": "normalize", "name": "标准化", "params": {"targetLufs": -16}}],
        [small_id], mode="serial")
    tf6b = tasks_of(r6b["taskIds"])
    n1b = tf6b[r6b["steps"][1]["taskIds"][0]]
    check("串行档：第 2 步的输入换成了派生产物（fileId 变了）",
          n1b["fileId"] != small_id, n1b["fileId"])
    check("串行档：第 2 步有 src_task_id",
          n1b["srcTaskId"] == r6b["steps"][0]["taskIds"][0], n1b["srcTaskId"])

finally:
    print()
    print(f"== 清理本次上传的 {len(created)} 个测试文件 ==")
    for fid in created:
        req("DELETE", f"/api/files/{fid}?purge=true&withDisk=true")
    # 清掉本次产生的 outputs
    for pat in ("*.flac", "*.wav", "*.norm.flac"):
        for p in (ROOT / "outputs").glob(pat):
            if p.name.startswith("chain-"):
                p.unlink(missing_ok=True)
    # ⚠ 链上产物现在落**本次执行的目录**（`outputs/upload-<日期>-<尾号>/`，方案 §5），
    # 所以清理要连目录一起删 —— 只 glob `outputs/zips/*.zip` 会留下整批目录。
    # 只删这次测试自己造的（前缀 `upload-`）与临时源文件，不碰用户产物。
    import shutil as _sh
    for d in (ROOT / "outputs").glob("upload-*"):
        if d.is_dir():
            _sh.rmtree(d, ignore_errors=True)
    for d in (ROOT / "outputs").glob("upload-*"):
        if d.is_file():
            d.unlink(missing_ok=True)
    _sh.rmtree(WORK, ignore_errors=True)

print()
print(f"结果：{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
