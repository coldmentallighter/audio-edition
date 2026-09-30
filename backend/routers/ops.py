"""12 个 op 的提交入口：POST /api/ops/<route>。

路径用的是**路由名**（如 cover / extract-cover），不是任务类型名 ——
卡片里的 op 字段必须与之一致，否则卡片执行会 404。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from backend import queue as q_mod, store, tasks

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


# ================================================================ 进度推送（SSE）
