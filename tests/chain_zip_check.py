"""执行链**打包语义**自检（`执行链打包与串行交接方案.md` §4 / §5）。

用户口径（原文）：

> 不论在哪种执行链逻辑下都应当是对**所有终产物**打包（串行对应所有文件执行到
> 打包前一步骤产生的产物，并行对应的是执行链上在此之前直到上一个打包操作的
> 所有的非 zip 产物）…… 每一次用户点击执行链时最终产生的产物不能合并到一起。

对应到实现：

  · 窗口 = 「上一个打包步骤（不含）」→「本次打包步骤（不含）」
  · **串行**：每个文件取窗口内**最后一个音频产物** + 窗口内**全部旁路产物**
  · **并行**：窗口内**全部**非 zip 产物（`转 FLAC → 转 MP3 → 打包` 两个都装）
  · 一个打包步骤 = 一个 zip（所以一条链可以出多个 zip）
  · ZIP 名 `upload-<YYYYMMDD>-<来源卡片名…>.zip`；产物落 `outputs/<本次执行的目录>/`

以前的行为是"**每个文件只装最后一个产物**"（`chain_derived_artifact` 的
`ORDER BY step_idx DESC LIMIT 1`）—— `波形 → 响度报告 → 打包` 里 PNG 被静默丢掉。

真队列 + 真 ffmpeg + 临时工作区（不碰工作区文件，也不需要服务）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend import chain, config, store                          # noqa: E402
from backend import queue as q_mod                                # noqa: E402
from backend import tasks as tasks_mod                            # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


_tmp = Path(tempfile.mkdtemp(prefix="ae-zip-"))
_orig = (config.ROOT, config.DB_PATH, config.UPLOADS, config.OUTPUTS, config.CACHE)
config.ROOT = _tmp
config.DB_PATH = _tmp / "t.db"
config.UPLOADS = _tmp / "up"
config.OUTPUTS = _tmp / "out"
config.CACHE = _tmp / "cache"
for _d in (config.UPLOADS, config.OUTPUTS, config.CACHE):
    _d.mkdir(parents=True, exist_ok=True)


def _reset_store() -> None:
    store._initialized = False
    store.release_all_leases()
    c = getattr(store._LOCAL, "conn", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
        del store._LOCAL.conn


def _wait(tids: list[str], timeout: float = 240.0) -> list:
    end = time.time() + timeout
    while time.time() < end:
        rows = [store.get_task(t) for t in tids]
        if rows and all(r and r.state in ("success", "failed", "cancelled", "skipped")
                        for r in rows):
            return rows
        time.sleep(0.15)
    return [store.get_task(t) for t in tids]


def _run(steps: list[dict], mode: str, fids: list[str]) -> list:
    r = chain.build_chain({"mode": mode, "fileIds": fids, "steps": steps})
    return _wait(r["taskIds"])


def _zip_names(task) -> list[str]:
    """任务产物 zip 里的成员名。"""
    rel = (task.result or {}).get("relPath")
    assert rel, task.result
    with zipfile.ZipFile(config.OUTPUTS / rel) as z:
        return sorted(z.namelist())


def _zip_rel(task) -> str:
    return str((task.result or {}).get("relPath") or "")


try:
    _reset_store()
    store.init_db()
    src = config.UPLOADS / "song.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=0.6",
                    "-ac", "2", "-ar", "44100", str(src)], check=True)
    f = store.add_file("song.wav", size=src.stat().st_size, mtime=src.stat().st_mtime)
    tasks_mod.register_all(q_mod.queue_)
    q_mod.queue_.start()

    # ---------------------------------------------------------------- 1
    print("== 1. 单卡打包：装源文件，名字是 `upload-<日期>-源文件.zip` ==")
    rows = _run([{"op": "zip", "name": "打包 ZIP"}], "serial", [f.id])
    z = rows[0]
    check("打包任务成功", z.state == "success", (z.state, z.error))
    check("装的是源文件", _zip_names(z) == ["song.wav"], _zip_names(z))
    name = Path(_zip_rel(z)).name
    check("命名是 upload-<YYYYMMDD>-源文件.zip",
          bool(re.fullmatch(r"upload-\d{8}-源文件\.zip", name)), name)
    check("落点是**本次执行的目录**（不是 outputs/zips/）",
          bool(re.match(r"upload-\d{8}-[0-9a-z]{6}/", _zip_rel(z))), _zip_rel(z))
    check("result.window 说明装的是哪一段",
          (z.result or {}).get("window") == {"from": 0, "to": 0},
          (z.result or {}).get("window"))

    # ---------------------------------------------------------------- 2
    print()
    print("== 2. 串行 `转 FLAC → 打包`：装产物（不是源文件）==")
    rows = _run([{"op": "convert", "name": "转 FLAC", "params": {"format": "flac"}},
                 {"op": "zip", "name": "打包 ZIP"}], "serial", [f.id])
    cv, zp = rows
    check("两步都成功", all(r.state == "success" for r in rows),
          [(r.step_idx, r.state, (r.error or "")[:60]) for r in rows])
    check("ZIP 里是转换产物", _zip_names(zp) == ["song.flac"], _zip_names(zp))
    check("命名带上了来源卡片名：upload-<日期>-转 FLAC.zip",
          bool(re.fullmatch(r"upload-\d{8}-转 FLAC\.zip", Path(_zip_rel(zp)).name)),
          Path(_zip_rel(zp)).name)
    check("sources 里记着来自第几步",
          any(s.get("from", "").startswith("step1:") for s in
              (zp.result or {}).get("sources") or []),
          (zp.result or {}).get("sources"))

    # ---------------------------------------------------------------- 3
    print()
    print("== 3. `波形 → 转 FLAC → 打包`：旁路产物与音频产物**都要装** ==")
    # 这是"所有终产物"的核心用例：老实现只装最后一个（FLAC），PNG 静默丢失。
    rows = _run([{"op": "waveform", "name": "导出波形 PNG"},
                 {"op": "convert", "name": "转 FLAC", "params": {"format": "flac"}},
                 {"op": "zip", "name": "打包 ZIP"}], "serial", [f.id])
    z3 = rows[-1]
    check("三步都成功", all(r.state == "success" for r in rows),
          [(r.step_idx, r.state, (r.error or "")[:60]) for r in rows])
    names = _zip_names(z3)
    check("ZIP 里既有波形 PNG 又有 FLAC（旁路产物不再被丢）",
          any(n.endswith(".png") for n in names) and any(n.endswith(".flac") for n in names),
          names)
    check("命名列出两个来源卡片名",
          Path(_zip_rel(z3)).name ==
          f"upload-{config.run_date(store.chain_started_at(z3.chain_id))}-导出波形 PNG-转 FLAC.zip",
          Path(_zip_rel(z3)).name)

    # ---------------------------------------------------------------- 4
    print()
    print("== 4. 串行 `转 FLAC → 打包 → 转 MP3 → 打包`：**两个 zip**，窗口不重叠 ==")
    rows = _run([{"op": "convert", "name": "转 FLAC", "params": {"format": "flac"}},
                 {"op": "zip", "name": "打包 ZIP"},
                 {"op": "convert", "name": "转 MP3", "params": {"format": "mp3",
                                                                "bitrate": "128k"}},
                 {"op": "zip", "name": "打包 ZIP"}], "serial", [f.id])
    check("四步都成功", all(r.state == "success" for r in rows),
          [(r.step_idx, r.state, (r.error or "")[:70]) for r in rows])
    z_a, z_b = rows[1], rows[3]
    check("两个打包步骤各出一个 zip", z_a.id != z_b.id and _zip_rel(z_a) != _zip_rel(z_b))
    check("第一个包装的是 FLAC", _zip_names(z_a) == ["song.flac"], _zip_names(z_a))
    check("第二个包装的是 MP3（不是把 FLAC 再装一遍）",
          all(n.endswith(".mp3") for n in _zip_names(z_b)), _zip_names(z_b))
    check("两个 zip 在同一目录（同一次执行）",
          Path(_zip_rel(z_a)).parent == Path(_zip_rel(z_b)).parent,
          (_zip_rel(z_a), _zip_rel(z_b)))
    check("第一个 zip 的窗口到第 2 步为止",
          (z_a.result or {}).get("window") == {"from": 0, "to": 1},
          (z_a.result or {}).get("window"))
    check("第二个 zip 的窗口从第 3 步开始",
          (z_b.result or {}).get("window") == {"from": 2, "to": 3},
          (z_b.result or {}).get("window"))
    check("第二个 zip 的名字只列它自己窗口里的卡片名",
          Path(_zip_rel(z_b)).name.endswith("-转 MP3.zip"), Path(_zip_rel(z_b)).name)

    # ---------------------------------------------------------------- 5
    print()
    print("== 5. 并行 `转 FLAC → 转 MP3 → 打包`：**两个音频产物都装** ==")
    # 并行档各步都作用于源文件，两个产物互不取代 —— 与串行（只装最后一个）不同。
    rows = _run([{"op": "convert", "name": "转 FLAC", "params": {"format": "flac"}},
                 {"op": "convert", "name": "转 MP3", "params": {"format": "mp3",
                                                                "bitrate": "128k"}},
                 {"op": "zip", "name": "打包 ZIP"}], "parallel", [f.id])
    z5 = rows[-1]
    check("三步都成功", all(r.state == "success" for r in rows),
          [(r.step_idx, r.state, (r.error or "")[:60]) for r in rows])
    names5 = _zip_names(z5)
    check("并行档：FLAC 与 MP3 都在包里",
          any(n.endswith(".flac") for n in names5) and any(n.endswith(".mp3") for n in names5),
          names5)

    # ---------------------------------------------------------------- 6
    print()
    print("== 6. 窗口屏障：并行 `波形 → 探测 → 打包` 不许抢跑 ==")
    # 老屏障只等"紧邻的前一步"（探测），波形那条线可能还没跑完 ——
    # 于是包里静默少一个 PNG，还不报错。
    rows = _run([{"op": "waveform", "name": "导出波形 PNG"},
                 {"op": "probe", "name": "重新探测"},
                 {"op": "zip", "name": "打包 ZIP"}], "parallel", [f.id])
    wf, pb, z6 = rows
    check("三步都成功", all(r.state == "success" for r in rows),
          [(r.step_idx, r.state, (r.error or "")[:60]) for r in rows])
    ends = [r.ended_at or 0 for r in (wf, pb)]
    check("打包在**窗口内全部步骤**结束之后才开始（不抢跑）",
          (z6.started_at or 0) >= max(ends) - 0.001,
          ((z6.started_at or 0), ends))
    check("因此波形 PNG 真的在包里",
          any(n.endswith(".png") for n in _zip_names(z6)), _zip_names(z6))

    # ---------------------------------------------------------------- 7
    print()
    print("== 7. 失败不回落：`转 FLAC(必失败) → 打包` ==")
    # FLAC + 32 位浮点是被后端明确拒绝的组合（`axis_a_check` 也用它造失败）
    rows = _run([{"op": "convert", "name": "转 FLAC",
                  "params": {"format": "flac", "bitDepth": "32f"}},
                 {"op": "zip", "name": "打包 ZIP"}], "serial", [f.id])
    cv7, zp7 = rows
    check("转换确实失败了（用例前提）", cv7.state == "failed", cv7.state)
    check("打包任务也失败（没有任何文件可打）", zp7.state == "failed", zp7.state)
    check("报错说清了「没有可打包的」而不是产出一个空包",
          "可以打包" in (zp7.error or ""), (zp7.error or "")[:120])

    # ---------------------------------------------------------------- 8
    print()
    print("== 8. 每次执行一个目录：两条链的产物不混 ==")
    rows_a = _run([{"op": "convert", "name": "转 FLAC", "params": {"format": "flac"}},
                   {"op": "zip", "name": "打包 ZIP"}], "serial", [f.id])
    rows_b = _run([{"op": "convert", "name": "转 FLAC", "params": {"format": "flac"}},
                   {"op": "zip", "name": "打包 ZIP"}], "serial", [f.id])
    dir_a = Path(_zip_rel(rows_a[-1])).parent.as_posix()
    dir_b = Path(_zip_rel(rows_b[-1])).parent.as_posix()
    check("两次执行的打包产物落在**不同目录**", dir_a != dir_b, (dir_a, dir_b))
    check("两个目录名都带链 id 尾号（可区分）",
          dir_a != dir_b and dir_a.startswith("upload-") and dir_b.startswith("upload-"),
          (dir_a, dir_b))
    # 非链任务仍然落在老的 outputs/zips/（存量行为不变）
    t_solo = store.create_task("zip", params={"fileIds": [f.id]})
    q_mod.queue_.submit(t_solo.id)
    solo = _wait([t_solo.id])[0]
    check("非链打包仍在 outputs/zips/（存量行为不变）",
          _zip_rel(solo).startswith("zips/"), _zip_rel(solo))

finally:
    q_mod.queue_.stop()
    _reset_store()
    config.ROOT, config.DB_PATH, config.UPLOADS, config.OUTPUTS, config.CACHE = _orig
    shutil.rmtree(_tmp, ignore_errors=True)

print()
print(f"结果：{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
