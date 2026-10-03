"""12 个 op 的提交入口：POST /api/ops/<route>。

路径用的是**路由名**（如 cover / extract-cover），不是任务类型名 ——
卡片里的 op 字段必须与之一致，否则卡片执行会 404。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from backend import chain, queue as q_mod, store, tasks
from backend.cards.specs import task_params

router = APIRouter()

def _require_files(ids: list[str]) -> list[store.FileRow]:
    out = []
    for i in ids:
        f = store.get_file(i)
        if f and f.state != "deleted":
            out.append(f)
    if not out:
        raise HTTPException(400, "没有可操作的文件")
    return out


@router.post("/api/ops/probe")
def op_probe(payload: dict = Body(...)) -> dict:
    files = _require_files(payload.get("fileIds") or [])
    res = q_mod.submit_batch("probe", [f.id for f in files], lambda _fid: {})
    return res


@router.post("/api/ops/peaks")
def op_peaks(payload: dict = Body(...)) -> dict:
    files = _require_files(payload.get("fileIds") or [])
    params = {k: v for k, v in payload.items() if k != "fileIds"}
    res = q_mod.submit_batch("peaks", [f.id for f in files], lambda _fid: params)
    return res


@router.post("/api/ops/convert")
def op_convert(payload: dict = Body(...)) -> dict:
    """批量转换。所有参数在 h_convert 里做白名单校验。

    这里**必须原样透传** payload 里的参数，不能自己再列一遍名单 ——
    列一遍就等于多出一处需要在加参数时同步修改的地方，而漏改是**静默**的：
    `bitDepth` 加进 OPS 与 h_convert 之后，卡片配了它、handler 也认它，
    但被这一层悄悄丢掉，产出还是默认位深，谁都不报错。
    """
    files = _require_files(payload.get("fileIds") or [])
    fmt = str(payload.get("format") or "").lower().lstrip(".")
    if fmt not in tasks.FORMAT_ARGS:
        raise HTTPException(400, f"不支持的目标格式: {fmt}")
    params = {k: v for k, v in payload.items() if k != "fileIds"}
    params["format"] = fmt
    res = q_mod.submit_batch("convert", [f.id for f in files], lambda _fid: params)
    return res


@router.post("/api/ops/tags")
def op_tags(payload: dict = Body(...)) -> dict:
    """批量改标签。tags 会套用到每个文件。"""
    files = _require_files(payload.get("fileIds") or [])
    params = {k: v for k, v in payload.items() if k != "fileIds"}
    res = q_mod.submit_batch("tag_edit", [f.id for f in files], lambda _fid: params)
    return res


@router.post("/api/ops/cover")
def op_cover(payload: dict = Body(...)) -> dict:
    """批量嵌封面：统一封面或按文件分别指定。"""
    files = _require_files(payload.get("fileIds") or [])
    mapping: dict[str, str] = payload.get("mapping") or {}
    default = payload.get("imagePath") or ""
    ptype = str(payload.get("pictureType", "Front Cover"))

    def params_for(fid: str) -> dict:
        return {"imagePath": mapping.get(fid, default), "pictureType": ptype}

    if not default and not mapping:
        raise HTTPException(400, "必须提供 imagePath 或 mapping")
    res = q_mod.submit_batch("cover_embed", [f.id for f in files], params_for)
    return res


@router.post("/api/ops/normalize")
def op_normalize(payload: dict = Body(...)) -> dict:
    files = _require_files(payload.get("fileIds") or [])
    # 同样原样透传：h_normalize 自己会校验范围
    params = {k: v for k, v in payload.items() if k != "fileIds"}
    res = q_mod.submit_batch("normalize", [f.id for f in files], lambda _fid: params)
    return res


@router.post("/api/ops/rename")
def op_rename(payload: dict = Body(...)) -> dict:
    files = _require_files(payload.get("fileIds") or [])
    pattern = str(payload.get("pattern") or "{tracknumber} - {title}")
    res = q_mod.submit_batch("rename", [f.id for f in files],
                             lambda _fid: {"pattern": pattern})
    return res


@router.post("/api/ops/waveform")
def op_waveform(payload: dict = Body(...)) -> dict:
    """导出波形 PNG（默认白色波形 + 透明底）。"""
    files = _require_files(payload.get("fileIds") or [])
    params = {k: v for k, v in payload.items() if k != "fileIds"}
    res = q_mod.submit_batch("waveform", [f.id for f in files], lambda _fid: params)
    return res


@router.post("/api/ops/verify")
def op_verify(payload: dict = Body(...)) -> dict:
    files = _require_files(payload.get("fileIds") or [])
    res = q_mod.submit_batch("verify", [f.id for f in files], lambda _fid: {})
    return res


@router.post("/api/ops/zip")
def op_zip(payload: dict = Body(...)) -> dict:
    """打包 ZIP：只建一个汇总任务，不 per-file 建。"""
    files = _require_files(payload.get("fileIds") or [])
    t = store.create_task("zip", params={"fileIds": [f.id for f in files]})
    q_mod.queue_.submit(t.id)
    return {"taskId": t.id, "count": len(files)}


@router.post("/api/ops/extract-cover")
def op_extract_cover(payload: dict = Body(...)) -> dict:
    files = _require_files(payload.get("fileIds") or [])
    res = q_mod.submit_batch("cover_extract", [f.id for f in files], lambda _fid: {})
    return res


@router.post("/api/ops/remove-cover")
def op_remove_cover(payload: dict = Body(...)) -> dict:
    files = _require_files(payload.get("fileIds") or [])
    res = q_mod.submit_batch("cover_remove", [f.id for f in files], lambda _fid: {})
    return res


# ---------------------------------------------------------------- 响度分析
# 两个 op 共用 `loudness` 任务类型：`loudness` 只算并缓存（前端画 Canvas），
# `loudness-image` 另外导出一张 PNG。路由名里的连字符与任务类型的下划线
# 是两套命名，卡片里的 `op` 字段必须与**路由名**一致（否则卡片执行会 404）。

def _loudness_params(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k != "fileIds"}


def _submit_loudness(payload: dict, op: str) -> dict:
    """三个响度 op 共用一个 handler，靠 `params._op` 分流（handler 里读它）。

    ⚠ `_op` 由**服务端**塞进去，不接受客户端指定 —— 否则谁都能让
    `loudness` 这个卡片去写报告文件（`_op` 是内部约定，不是参数）。
    注入这件事统一在 `specs.task_params` 里做，**执行链那条建任务路径也要走它**
    （少了它链上的报告/图会静默退化成只算缓存，见 specs.task_params 的注释）。
    """
    files = _require_files(payload.get("fileIds") or [])
    params = task_params(op, _loudness_params(payload))
    return q_mod.submit_batch("loudness", [f.id for f in files], lambda _fid: params)


@router.post("/api/ops/loudness")
def op_loudness(payload: dict = Body(...)) -> dict:
    """响度总览图：算时间线 + 8 项指标，按文件缓存。"""
    return _submit_loudness(payload, "loudness")


@router.post("/api/ops/loudness-image")
def op_loudness_image(payload: dict = Body(...)) -> dict:
    """导出响度分析图（PNG）。**图的像素级复刻尚未实现**，见 tasks.h_loudness。"""
    return _submit_loudness(payload, "loudness-image")


@router.post("/api/ops/loudness-report")
def op_loudness_report(payload: dict = Body(...)) -> dict:
    """导出响度分析报告（Markdown），落 outputs/loudness/。"""
    return _submit_loudness(payload, "loudness-report")


# ================================================================ 执行链
#
# 一次提交整条链（方案 §3.2）。取代原来前端"每步一次 HTTP"的循环 ——
# 那种做法下 `await` 的只是"任务建好了"，步骤之间没有任何屏障，
# 而且后端拿到的每一批都互不相干，所以"链"根本接不起来（§1.1）。

@router.post("/api/ops/chain")
def op_chain(payload: dict = Body(...)) -> dict:
    """一次提交整条执行链。

    body:
        mode     "serial" | "parallel"（缺省 parallel）
        fileIds  作用域，**所有步骤共用**
        steps    [{cardId?, name?, op, params}, …]，顺序即语义

    `mode="serial"` 时会给每条任务写上 `src_task_id`（指向同一文件的上一步），
    于是调度器按"**每个文件自己的流水线**"推进：同一文件内严格有序、
    不同文件之间并行（§3.1.1）。

    400 的响应体里带 `stepIdx`，前端据此把链上那一步标红而不是笼统报错。
    """
    try:
        return chain.build_chain(payload)
    except chain.ChainError as e:
        raise HTTPException(400, {"message": e.message, "stepIdx": e.step_idx}) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/api/chains/{chain_id}")
def get_chain(chain_id: str) -> dict:
    """链的整体状态：每步进度 + 每个文件走到第几步（方案 §3.7 的状态点/角标）。"""
    snap = store.chain_snapshot(chain_id)
    if not snap["steps"]:
        raise HTTPException(404, "没有这条链")
    return snap


# ================================================================ 进度推送（SSE）
