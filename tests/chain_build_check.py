"""P0-4 自检：建链（`POST /api/ops/chain` 的内核 `chain.build_chain`）。

对应《执行链并发方案.md》§3.2 / §3.2.2 / §3.2.4 / §9.6 / §9.15：

  · **一步建成**：链上每一步的任务在提交那一刻就都在库里（含 src_task_id）
  · **serial 才连因果边**：并行档的 src_task_id 必须为空 —— 这是"存量行为
    零变化"的根据（非链任务与并行档都走老路径）
  · **每条 src_task_id 指向同一文件的上一步**，绝不跨文件（串行档的
    "每个文件一条自己的流水线"全靠这条）
  · **后端必须自己校验可用性**：绕过前端直接 POST 也不能构造出
    `打包 ZIP → 改标签`（§3.2.4 的实现要求）
  · **汇总类只建一条任务**（zip 不 per-file 建）
  · **400 要指出第几步**（前端据此标红）
  · 链的模式/作用域/步数上限都挡住

用独立临时库，不碰工作区的 audioedition.db，也不真跑队列（只看建出来的任务）。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend import chain, config, store                            # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


_tmp = Path(tempfile.mkdtemp(prefix="ae-p04-"))
_orig_db, _orig_up, _orig_out = config.DB_PATH, config.UPLOADS, config.OUTPUTS
config.DB_PATH = _tmp / "t.db"
config.UPLOADS = _tmp / "up"
config.UPLOADS.mkdir(parents=True, exist_ok=True)
config.OUTPUTS = _tmp / "out"
config.OUTPUTS.mkdir(parents=True, exist_ok=True)


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


def _mkfile(name: str) -> store.FileRow:
    p = config.UPLOADS / name
    p.write_bytes(b"x" * 16)
    return store.add_file(name, size=16, mtime=p.stat().st_mtime)


def _mkfile_info(name: str, *, bitrate: int | None = None,
                 sample_rate: int | None = None) -> store.FileRow:
    """带 `info` 的源文件 —— 码率/采样率规则要靠它定基线。

    **不跑真 ffprobe**：那是建链路径不该做的事，而规则读的就是库里这一行
    （`source_audio()` 只读 `info`）。直接写库最贴近真实数据形态。
    """
    f = _mkfile(name)
    info: dict = {}
    if bitrate:
        info["bitRate"] = bitrate
    if sample_rate:
        info["sampleRate"] = sample_rate
    if info:
        store.set_file_info(f.id, info)
    return store.get_file(f.id)


def _reject(payload: dict) -> tuple[bool, str, int | None]:
    """建链被拒时返回 (True, 原因, stepIdx)；成功则 (False, "", None)。"""
    try:
        chain.build_chain(payload)
        return False, "", None
    except chain.ChainError as e:
        return True, e.message, e.step_idx


try:
    _reset_store()
    store.init_db()
    f1 = _mkfile("one.flac")
    f2 = _mkfile("two.flac")
    ids = [f1.id, f2.id]

    # ---------------------------------------------------------------- 1
    print("== 1. 两步链，一次建成（parallel）==")
    r = chain.build_chain({
        "mode": "parallel",
        "fileIds": ids,
        "steps": [{"op": "convert", "params": {"format": "flac"}, "name": "转 FLAC"},
                  {"op": "normalize", "params": {"targetLufs": -16}, "name": "标准化"}],
    })
    check("返回 chainId", bool(r.get("chainId")), r.get("chainId"))
    check("返回 2 步", len(r["steps"]) == 2, [s["stepIdx"] for s in r["steps"]])
    check("任务总数 = 2 步 × 2 文件", r["total"] == 4, r["total"])
    check("每步有自己的 batchId",
          len({s["batchId"] for s in r["steps"]}) == 2,
          [s["batchId"] for s in r["steps"]])
    check("每步有独立的 stepId",
          len({s["stepId"] for s in r["steps"]}) == 2)
    tasks = [store.get_task(t) for t in r["taskIds"]]
    check("任务都落库了（一步建成，不是跑一步建一步）",
          all(t is not None for t in tasks))
    check("都带 chain_id", all(t.chain_id == r["chainId"] for t in tasks))
    check("step_idx 是 0/1", {t.step_idx for t in tasks} == {0, 1})
    check("并行档：src_task_id 全为空（存量行为零变化）",
          all(t.src_task_id is None for t in tasks),
          [(t.type, t.src_task_id) for t in tasks if t.src_task_id])

    # ---------------------------------------------------------------- 2
    print()
    print("== 2. serial 档：因果边按文件连，绝不跨文件 ==")
    r2 = chain.build_chain({
        "mode": "serial",
        "fileIds": ids,
        "steps": [{"op": "convert", "params": {"format": "flac"}},
                  {"op": "normalize", "params": {}},
                  {"op": "tags", "params": {"tags": {"title": "x"}}}],
    })
    by_step: dict[int, dict[str, str]] = {}
    for tid in r2["taskIds"]:
        t = store.get_task(tid)
        by_step.setdefault(t.step_idx, {})[t.file_id] = t.id
    check("3 步 × 2 文件 = 6 条任务", r2["total"] == 6, r2["total"])
    # 第 0 步无上游
    check("第 1 步没有上游",
          all(store.get_task(t).src_task_id is None for t in r2["taskIds"]
              if store.get_task(t).step_idx == 0))
    # 第 1 步的每条 = 同文件第 0 步
    ok = True
    for fid, tid in by_step[1].items():
        if store.get_task(tid).src_task_id != by_step[0].get(fid):
            ok = False
    check("第 2 步的上游 = **同一文件**的第 1 步", ok,
          {fid: (store.get_task(t).src_task_id, by_step[0].get(fid))
           for fid, t in by_step[1].items()})
    ok2 = all(store.get_task(t).src_task_id == by_step[1].get(fid)
              for fid, t in by_step[2].items())
    check("第 3 步同理（链是逐跳连的）", ok2)
    # 跨文件检查：上游任务的 file_id 必须与下游一致
    cross = [(store.get_task(t).file_id,
              store.get_task(store.get_task(t).src_task_id).file_id)
             for t in r2["taskIds"] if store.get_task(t).src_task_id]
    check("没有任何一条边跨文件", all(a == b for a, b in cross),
          [x for x in cross if x[0] != x[1]])
    check("并行档的链没有被串行档影响（两条链互不干扰）",
          all(store.get_task(t).src_task_id is None for t in r["taskIds"]))

    # ---------------------------------------------------------------- 3
    print()
    print("== 3. 缺省模式是 parallel（不改存量行为）==")
    r3 = chain.build_chain({"fileIds": [f1.id],
                            "steps": [{"op": "probe"}]})
    check("不传 mode → parallel", r3["mode"] == "parallel", r3["mode"])
    check("parallel 下没有 src_task_id",
          store.get_task(r3["taskIds"][0]).src_task_id is None)

    # ---------------------------------------------------------------- 4
    print()
    print("== 4. 后端自己校验可用性（绕过前端也拦得住）==")
    # ⚠ 这一段原来钉的是"打包 ZIP 之后接任何东西都被拒"。**P1 起不再是拒**：
    # `zip` 是 `mode=read` + `consumes=False`，它什么都没改动，后面的步骤
    # 照旧作用于当前文件；而且一条链可以有多个打包步骤（每个收集"上一个
    # 打包步骤之后"的产物，见 `执行链打包与串行交接方案.md` §4）。
    # 所以这里改成断言"放行 **且** 挂上提示"。
    bad, why, idx = _reject({"mode": "parallel", "fileIds": ids,
                             "steps": [{"op": "zip"}, {"op": "tags"}]})
    check("打包 ZIP → 改标签 放行（不再是终态）", not bad, why)
    r_zt = chain.build_chain({"mode": "parallel", "fileIds": ids,
                              "steps": [{"op": "zip"}, {"op": "tags"}]})
    check("但它必须挂一条提示：压缩包不会被递下去",
          any("压缩包" in n["text"] and n["stepIdx"] == 0 for n in r_zt["notes"]),
          r_zt["notes"])
    bad, why, idx = _reject({"mode": "serial", "fileIds": ids,
                             "steps": [{"op": "convert"}, {"op": "zip"},
                                       {"op": "convert"}, {"op": "zip"}]})
    check("一条链两个打包步骤 放行（多 zip 是合法用法）", not bad, why)
    # 真正的拒绝仍然存在：未知 op / 非法模式 / 空链（见 §6）
    # 合法组合必须放行
    bad, why, idx = _reject({"mode": "serial", "fileIds": ids,
                             "steps": [{"op": "tags"}, {"op": "convert"}, {"op": "zip"}]})
    check("改标签 → 转 FLAC → 打包 放行", not bad, why)
    bad, why, idx = _reject({"mode": "serial", "fileIds": ids,
                            "steps": [{"op": "convert"}, {"op": "normalize"}]})
    check("转 FLAC → 标准化 放行", not bad, why)
    # zip 单独一张也要放行（自身为第一项 → 打包源文件）
    bad, why, idx = _reject({"mode": "parallel", "fileIds": ids,
                             "steps": [{"op": "zip"}]})
    check("打包 ZIP 单独一张放行", not bad, why)

    # ---------------------------------------------------------------- 5
    print()
    print("== 5. 汇总类（zip）只建一条任务 ==")
    r5 = chain.build_chain({"mode": "parallel", "fileIds": ids,
                            "steps": [{"op": "convert"}, {"op": "zip"}]})
    zstep = r5["steps"][1]
    check("zip 这一步只建 1 条任务", zstep["count"] == 1, zstep["count"])
    check("zip 这一步被标成 aggregate", zstep["aggregate"] is True)
    ztask = store.get_task(zstep["taskIds"][0])
    check("zip 任务没有 file_id（它是全批汇总）", ztask.file_id is None, ztask.file_id)
    check("zip 的 params 里带着全部文件 id",
          sorted(ztask.params.get("fileIds") or []) == sorted(ids),
          ztask.params.get("fileIds"))
    cstep = r5["steps"][0]
    check("普通 op 仍然是 per-file 建（2 文件 = 2 条）", cstep["count"] == 2, cstep["count"])

    # ---------------------------------------------------------------- 6
    print()
    print("== 6. 参数与模式校验 ==")
    bad, why, idx = _reject({"mode": "segment", "fileIds": ids,
                             "steps": [{"op": "probe"}]})
    check("非法模式被拒（'分段'档已删，只认 serial/parallel）", bad, why)
    bad, why, idx = _reject({"mode": "parallel", "fileIds": ids, "steps": []})
    check("空链被拒", bad, why)
    bad, why, idx = _reject({"mode": "parallel", "fileIds": ids,
                             "steps": [{"op": "不存在的op"}]})
    check("未知 op 被拒", bad, why)
    # 拒绝要指出是**第几步**（前端据此把链上那一步标红，而不是笼统报错）。
    # 用"最后一个非法"来钉：idx 必须是 1，不能恒为 0。
    bad, why, idx = _reject({"mode": "parallel", "fileIds": ids,
                             "steps": [{"op": "tags"}, {"op": "不存在的op"}]})
    check("拒绝时指出是第几步（不是恒为 0）", bad and idx == 1, (bad, idx, why))
    bad, why, idx = _reject({"mode": "parallel", "fileIds": ids,
                             "steps": [{"params": {}}]})
    check("没有 op 的步骤被拒（卡片删了也要带 op 快照）", bad, why)
    bad, why, idx = _reject({"mode": "parallel", "fileIds": ids,
                             "steps": [{"op": "convert", "params": "不是对象"}]})
    check("params 不是对象被拒", bad, why)
    bad, why, idx = _reject({"mode": "parallel", "fileIds": ["f_不存在"],
                             "steps": [{"op": "probe"}]})
    check("没有可用文件被拒", bad, why)
    too_many = [{"op": "probe"}] * (config.MAX_CHAIN_STEPS + 1)
    bad, why, idx = _reject({"mode": "parallel", "fileIds": ids, "steps": too_many})
    check(f"超过 {config.MAX_CHAIN_STEPS} 步被拒", bad, why)
    # 刚好等于上限要放行
    edge = [{"op": "probe"}] * config.MAX_CHAIN_STEPS
    bad, why, idx = _reject({"mode": "parallel", "fileIds": [f1.id], "steps": edge})
    check("刚好等于上限放行", not bad, why)

    # ---------------------------------------------------------------- 6b
    print()
    print("== 6b. 串行档：不允许「有损 → 无损」（格式流规则）==")
    # 这条规则只看**链上的转码选择**，所以源文件后缀要参与推流。
    fmp3 = _mkfile("lossy.mp3")
    ids_mp3 = [fmp3.id]
    ids_flac = [f1.id]

    def _flow(tag: str, file_ids: list[str], steps: list[dict],
              want_ok: bool) -> None:
        bad, why, idx = _reject({"mode": "serial", "fileIds": file_ids,
                                 "steps": steps})
        check(f"{tag} —— {'放行' if want_ok else '拦下'}", bad != want_ok,
              why or "（本该被拒但放行了）")

    # —— 放行的那一半 ——
    _flow("转 MP3（第 1 步）", ids_flac, [{"op": "convert", "params": {"format": "mp3"}}], True)
    _flow("转 FLAC → 转 MP3（无损掉到有损，允许）", ids_flac,
          [{"op": "convert", "params": {"format": "flac"}},
           {"op": "convert", "params": {"format": "mp3"}}], True)
    _flow("转 FLAC → 转 WAV（无损到无损）", ids_flac,
          [{"op": "convert", "params": {"format": "flac"}},
           {"op": "convert", "params": {"format": "wav"}}], True)
    _flow("转 MP3 → 转 Opus（有损到有损）", ids_flac,
          [{"op": "convert", "params": {"format": "mp3"}},
           {"op": "convert", "params": {"format": "opus"}}], True)
    _flow("源 MP3 单步转 FLAC（塞进无损容器，**第 1 步不判**）", ids_mp3,
          [{"op": "convert", "params": {"format": "flac"}}], True)
    _flow("源 MP3 → 标准化（不转码）", ids_mp3, [{"op": "normalize", "params": {}}], True)

    # —— 拦下的那一半 ——
    _flow("转 MP3 → 转 FLAC（第 2 步把 MP3 产物转无损）", ids_flac,
          [{"op": "convert", "params": {"format": "mp3"}},
           {"op": "convert", "params": {"format": "flac"}}], False)
    _flow("转 MP3 → 改标签 → 转 WAV（`same` 型步骤不挡格式流）", ids_flac,
          [{"op": "convert", "params": {"format": "mp3"}},
           {"op": "tags", "params": {"tags": {"title": "x"}}},
           {"op": "convert", "params": {"format": "wav"}}], False)
    _flow("源 MP3 → 标准化 → 转 WAV（格式流穿过标准化）", ids_mp3,
          [{"op": "normalize", "params": {}},
           {"op": "convert", "params": {"format": "wav"}}], False)
    # 源格式未知（`.bin`）时的边界：**只放过第 1 步**。
    #
    # 第 1 步的产出由它自己的 `format` 参数决定（转 MP3 → 已知是 MP3），
    # 所以第 2 步的"输入是有损"是**确定的**，不需要知道链从哪儿开始。
    # 换句话说："未知就不猜"只覆盖**源文件本身**，不覆盖链内部已经写死的
    # 格式流 —— 单步 `未知源 → 转 FLAC` 放行（不看源），但
    # `未知源 → 转 MP3 → 转 FLAC` 拦下（第 2 步的输入板上钉钉是 MP3）。
    fbin = _mkfile("mystery.bin")
    _flow("未知源 → 转 MP3 → 转 FLAC（第 2 步输入已确定是 MP3，拦下）",
          [fbin.id],
          [{"op": "convert", "params": {"format": "mp3"}},
           {"op": "convert", "params": {"format": "flac"}}], False)
    _flow("未知源 → 转 FLAC（单步，不回头看源，放行）", [fbin.id],
          [{"op": "convert", "params": {"format": "flac"}}], True)
    # 反向对照：源**已知无损**、链上第 1 步才转 MP3 —— 同样拦下。
    # 这一条证明拦的是"链内部的格式流"（MP3 产物 → 无损），**与源是什么无关**。
    _flow("已知无损源 → 转 MP3 → 转 FLAC（拦下，与源无关）", ids_flac,
          [{"op": "convert", "params": {"format": "mp3"}},
           {"op": "convert", "params": {"format": "flac"}}], False)
    # 拒绝时 stepIdx 指向**那一步本身**（前端据此标红）
    #
    # 下面这段是**档位边界**的钉子：`_same_steps` 是同一组步骤，
    # 只换 `mode`。串行拦、并行放 —— 证明这条规则判的是"产物流不流"，
    # 不是"有没有勾串行"以外的任何东西，也证明了它**没有漏到并行档**。
    _same_steps = [{"op": "convert", "params": {"format": "mp3"}},
                   {"op": "convert", "params": {"format": "flac"}}]
    bad, why, idx = _reject({"mode": "serial", "fileIds": ids_flac,
                             "steps": _same_steps})
    check("拒绝时 stepIdx 指向违规的那一步", bad and idx == 1, idx)
    check("拒绝的原因是人话（说清'不恢复信息'）", "信息" in why or "变大" in why, why)
    # 拒绝文案必须给出**出路**：并行档下这一步吃的是原文件，这条链是合法的。
    # 不写这句，用户会以为"MP3 → FLAC"这个组合本身被禁了，于是卡死。
    check("拒绝文案指出可以切到并行档（给出出路，不是只说'不行'）",
          "并行" in why, why)
    bad, why, idx = _reject({"mode": "parallel", "fileIds": ids_flac,
                             "steps": _same_steps})
    check("**同一组步骤**换并行档就放行（规则没漏到并行）", not bad, why)

    # ---------------------------------------------------------------- 6c
    print()
    print("== 6c. 串行档：不允许「有损→有损但码率/采样率调高」==")
    # 这条与 6b **判据不同**：6b 管"链内部把产物包装成无损"（只判第 1 步之后），
    # 这条管"把一个已经是 128k 的文件重新编码成 320k" —— **第 0 步也要判**。
    f_mp3_128 = _mkfile_info("src128.mp3", bitrate=128_000, sample_rate=44100)
    f_mp3_320 = _mkfile_info("src320.mp3", bitrate=320_000, sample_rate=44100)
    f_low_hz = _mkfile_info("src8k.mp3", bitrate=96_000, sample_rate=8000)
    f_nobr = _mkfile("src_nobr.mp3")          # 没 probe 过 → 码率未知

    def _conv(fmt, **params):
        p = {"format": fmt}
        p.update(params)
        return {"op": "convert", "params": p}

    def _ser(fid, steps, want_ok, tag):
        bad, why, idx = _reject({"mode": "serial", "fileIds": [fid],
                                 "steps": steps})
        check(f"{tag} —— {'放行' if want_ok else '拦下'}", bad != want_ok,
              why or "（本该被拒但放行了）")
        return why

    # 第 0 步就该拦：源文件本身是有损的
    why = _ser(f_mp3_128.id, [_conv("mp3", bitrate="320k")], False,
               "源 MP3 128k → 转 MP3 320（第 1 步就升码率）")
    # ⚠ 别写 `"无损" not in why`：`"有损"` 里**不含**"无损"，但反过来
    # `str.replace("有损","")` 之后剩下的串里仍可能出现"无损"（"不会找回"），
    # 这种断言很脆。这里只钉"说的是码率这件事"。
    check("拒绝文案说清了是码率问题", "码率" in why and "kbps" in why, why)
    check("拒绝文案给出出路（并行档）", "并行" in why, why)
    check("拒绝文案里的数字取了整（源码率来自容器平均，会有小数）",
          "132" not in why or " " in why, why)
    _ser(f_mp3_128.id, [_conv("mp3", bitrate="128k")], True,
         "源 MP3 128k → 转 MP3 128k（同码率）")
    _ser(f_mp3_128.id, [_conv("opus", bitrate="96k")], True,
         "源 MP3 128k → 转 Opus 96k（降码率）")
    _ser(f_mp3_128.id, [_conv("m4a", bitrate="256k")], False,
         "源 MP3 128k → 转 M4A 256k（换容器也拦）")
    _ser(f_mp3_320.id, [_conv("m4a", bitrate="256k")], True,
         "源 MP3 320k → 转 M4A 256k（320→256 放行）")
    # 无损源 → 有损：**不适用**（源不是有损）
    _ser(ids_flac[0], [_conv("mp3", bitrate="320k")], True,
         "源 FLAC（无损）→ 转 MP3 320（升码率规则不适用）")
    # 未知码率不猜：没 probe 过、或卡片没写 bitrate
    _ser(f_nobr.id, [_conv("mp3", bitrate="320k")], True,
         "源码率未知 → 转 MP3 320（不猜，放行）")
    _ser(f_mp3_128.id, [_conv("mp3")], True,
         "源 MP3 128k → 转 MP3（没写 bitrate，不猜，放行）")
    # 链内部：MP3 320 → MP3 128 放行；MP3 128 → MP3 320 拦
    _ser(ids_flac[0],
         [_conv("mp3", bitrate="320k"), _conv("mp3", bitrate="128k")], True,
         "转 MP3 320 → 转 MP3 128k（链内降码率）")
    _ser(ids_flac[0],
         [_conv("mp3", bitrate="128k"), _conv("mp3", bitrate="320k")], False,
         "转 MP3 128k → 转 MP3 320（链内升码率）")
    # 用户给的用例：**先转无损、再转有损**，完全合法
    _ser(ids_flac[0], [_conv("wav"), _conv("mp3", bitrate="320k")], True,
         "转 WAV → 转 MP3 320（用户给的用例，必须先放行）")
    # 采样率同族：上采样也拦
    why = _ser(f_low_hz.id, [_conv("mp3", bitrate="96k", sampleRate=48000)], False,
               "源 MP3 8k 采样率 → 重采样 48k（上采样）")
    check("拒绝文案说清了是采样率问题", "采样率" in why or "Hz" in why, why)
    _ser(f_low_hz.id, [_conv("mp3", bitrate="96k", sampleRate=8000)], True,
         "源 MP3 8k 采样率 → 8k（同采样率）")
    # 格式流也要穿过 `same` 型步骤：改标签不改变码率
    _ser(ids_flac[0],
         [_conv("mp3", bitrate="128k"),
          {"op": "tags", "params": {"tags": {"title": "x"}}},
          _conv("mp3", bitrate="320k")], False,
         "转 MP3 128k → 改标签 → 转 MP3 320（码率穿过改标签）")
    # 并行档不受这条规则约束
    bad, why, idx = _reject({"mode": "parallel", "fileIds": [f_mp3_128.id],
                             "steps": [_conv("mp3", bitrate="320k")]})
    check("并行档不判码率（同一条链放行）", not bad, why)

    # ---------------------------------------------------------------- 6c
    print()
    print("== 6c. 共用任务类型的 op 家族必须带服务端 `_op` ==")
    # 用户实测报的："响度分析报告不生成"。链路是：卡片在执行链里跑 → 建链时
    # 直接把 step 的 params 拷给任务 → `_op` 没注入 → `tasks.h_loudness` 三个
    # 分支一个都不命中 → 任务 `success`、进度 100，**却什么都不产出**
    # （报告/图都没有，也没有 error）。直连路由（单步）那条路一直是好的，
    # 所以现象是"同一个卡片，单跑行、进链就不出文件"。
    #
    # 判据从 `specs.OPS` 现算（见 `specs.SHARED_TASK_OPS`），不写死 op 名。
    from backend.cards.specs import SHARED_TASK_OPS                  # noqa: E402
    check("共用任务类型的家族就是响度三兄弟（判据从 specs 现算）",
          set(SHARED_TASK_OPS) == {"loudness", "loudness-image", "loudness-report"},
          sorted(SHARED_TASK_OPS))

    def _chain_params(op: str, params: dict | None = None) -> dict:
        p = chain.build_chain({"mode": "serial", "fileIds": [f1.id],
                               "steps": [{"op": op, "params": params or {}}]})
        return store.get_task(p["taskIds"][0]).params

    rep = _chain_params("loudness-report", {"detail": "full"})
    check("链上的 `loudness-report` 带上了 `_op`（报告不再静默退化）",
          rep.get("_op") == "loudness-report", rep)
    check("卡片自己的参数没被 `_op` 顶掉", rep.get("detail") == "full", rep)
    img = _chain_params("loudness-image", {"width": 2400})
    check("链上的 `loudness-image` 同样带 `_op`",
          img.get("_op") == "loudness-image", img)
    plain = _chain_params("loudness", {"force": True})
    check("家族里最朴素的 `loudness` 也带 `_op`（三兄弟一致）",
          plain.get("_op") == "loudness", plain)
    forged = _chain_params("loudness-report", {"_op": "loudness-image"})
    check("链上伪造的 `_op` 被服务端覆盖（客户端定不了产物类型）",
          forged.get("_op") == "loudness-report", forged)
    conv = _chain_params("convert", {"format": "flac"})
    check("不带 `_op` 的普通 op 不会被污染",
          "_op" not in conv and conv.get("format") == "flac", conv)

    # ---------------------------------------------------------------- 7
    print()
    print("== 7. 链上的提示（notes）：只提示不阻断 ==")
    par = chain.build_chain({"mode": "parallel", "fileIds": [f1.id],
                             "steps": [{"op": "convert"}, {"op": "normalize"}]})
    check("并行档：转换 → 标准化 给出'会回到原文件'的提示",
          any("原文件" in n["text"] for n in par["notes"]), par["notes"])
    ser = chain.build_chain({"mode": "serial", "fileIds": [f1.id],
                             "steps": [{"op": "convert"}, {"op": "normalize"}]})
    check("串行档：同一条链不再提示（产物真的接上了）",
          not any("原文件" in n["text"] for n in ser["notes"]), ser["notes"])
    ex = chain.build_chain({"mode": "parallel", "fileIds": [f1.id],
                            "steps": [{"op": "cover", "params": {"imagePath": "a.png"}},
                                      {"op": "remove-cover"}]})
    check("嵌封面 → 删封面 提示'两者互斥、结果取决于顺序'",
          any("互斥" in n["text"] or "取决于执行顺序" in n["text"] for n in ex["notes"]),
          ex["notes"])
    check("提示里带上 stepIdx", all("stepIdx" in n for n in ex["notes"]), ex["notes"])

    # ---- 假警报：只读分析 / 旁路产物**不该**报"会回到原文件" ----
    # 用户实测报的：`导出波形 PNG → 响度分析报告` 也弹一条警告。
    # 那是假警报 —— 原来把 `produce ∈ (sidecar, none)` 一律当断口，
    # 而这两类**根本没动那个音频文件**，下一步读到的就是完整原文件，什么都没丢。
    # 唯一该报的是"上一步产出了新文件（`derived`）而并行档不会递下去"。
    QUIET = [
        (["waveform", "loudness-report"], "波形图 → 响度分析报告（都是旁路）"),
        (["loudness-report", "loudness"], "响度报告 → 响度总览图（都是只读）"),
        (["probe", "convert"], "探测 → 转换（只读 → 有产出）"),
        (["verify", "normalize"], "完整性校验 → 标准化"),
        (["extract-cover", "zip"], "提取封面 → 打包（打包不吃上一环）"),
        (["convert", "zip"], "转换 → 打包（打包不吃上一环）"),
        (["loudness", "loudness-image"], "响度总览图 → 导出响度分析图"),
    ]
    for ops, tag in QUIET:
        p = chain.build_chain({"mode": "parallel", "fileIds": [f1.id],
                               "steps": [{"op": o} for o in ops]})
        check(f"**不报**假警报：{tag}", not p["notes"], p["notes"])
        # 反向对照：同一条链在**串行档**也不该报（产物真的接上了）
        s2 = chain.build_chain({"mode": "serial", "fileIds": [f1.id],
                                "steps": [{"op": o} for o in ops]})
        check(f"串行档同样不报：{tag}", not s2["notes"], s2["notes"])
    # 真正该报的那一条仍在（`derived` 的产物被并行档丢掉）
    still = chain.build_chain({"mode": "parallel", "fileIds": [f1.id],
                               "steps": [{"op": "convert"}, {"op": "normalize"}]})
    check("但 `derived` 的断口仍然报（转 FLAC → 标准化）",
          len(still["notes"]) == 1 and "原文件" in still["notes"][0]["text"],
          still["notes"])

    # ---------------------------------------------------------------- 8
    print()
    print("== 8. 链快照（前端状态点/角标的数据源）==")
    snap = store.chain_snapshot(r2["chainId"])
    check("快照能看到 3 步", len(snap["steps"]) == 3, len(snap["steps"]))
    check("快照能看到每个文件", len(snap["files"]) == 2, snap["files"])
    check("未开始时 settled=0",
          all(s["settled"] == 0 for s in snap["steps"]), snap["steps"])
    check("stepCount = 3", snap["stepCount"] == 3, snap["stepCount"])
    check("不存在的链返回空 steps（端点据此 404）",
          store.chain_snapshot("ch_不存在")["steps"] == [])

finally:
    _reset_store()
    config.DB_PATH, config.UPLOADS, config.OUTPUTS = _orig_db, _orig_up, _orig_out
    shutil.rmtree(_tmp, ignore_errors=True)

print()
print(f"结果：{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
