"""AudioEdition 本地后端（FastAPI）。

启动：python -m backend.app   或   run.bat / run.sh
"""
from __future__ import annotations

import json
import shutil
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend import audio, cards as cards_mod, config, logs, queue as q_mod, store, tasks
from backend.toolchain import ToolchainError, toolchain

# 屏蔽第三方库的弃用噪音，保持控制台干净（本地工具，用户要看得清自己的日志）
warnings.filterwarnings("ignore", category=DeprecationWarning)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动/关闭钩子（替代已弃用的 on_event）。"""
    config.ensure_dirs()

    # 「每次打开都是空页面」：先把上一轮的工作区清干净，再建表。
    # 必须在任何请求进来之前做完，否则页面首帧可能读到上一轮的文件。
    # 导入是复制进 uploads/ 的，清掉的只是工作副本。
    if config.FRESH_ON_START:
        wiped = config.wipe_workspace()
        config.reset_db()
        total = sum(wiped.values())
        if total:
            print(f"[startup] 清空工作区 uploads={wiped['uploads']} "
                  f"outputs={wiped['outputs']} cache={wiped['cache']}")
        else:
            print("[startup] 工作区已是空的")

    store.init_db()
    stale = store.reset_stale_running()
    if stale:
        print(f"[startup] 清理中断任务 {stale} 条")
    tasks.register_all(q_mod.queue_)
    q_mod.queue_.start()
    tc = toolchain.probe()
    for name, t in tc.as_dict().items():
        mark = "OK  " if t["ok"] else "FAIL"
        print(f"[toolchain] [{mark}] {name:<9} {(t['version'] or t['note'])[:60]}")
    print(f"[startup] 就绪 → http://{config.HOST}:{config.PORT}")
    try:
        yield
    finally:
        q_mod.queue_.stop()


app = FastAPI(title="AudioEdition", version="0.1.0", docs_url="/api/docs",
              lifespan=lifespan)

# 仅本机使用，放开同源限制便于 file:// 调试
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================================================================ 基础

@app.get("/api/health")
def health() -> dict:
    tc = toolchain.probe()
    return {
        "ok": tc.all_ok,
        "tools": tc.as_dict(),
        "server": config.SERVER.as_dict(),
        "queue": q_mod.queue_.stats(),
    }


@app.get("/api/config")
def get_config() -> dict:
    return config.SERVER.as_dict()


# ================================================================ 日志

@app.get("/api/logs")
def get_logs(since: int = 0, limit: int = Query(500, ge=1, le=2000)) -> dict:
    """增量拉取运行日志（前端日志面板的真实来源）。"""
    items = logs.tail(since=since, limit=limit)
    return {
        "logs": items,
        "lastSeq": items[-1]["seq"] if items else since,
        "total": logs.count(),
    }


@app.delete("/api/logs")
def clear_logs() -> dict:
    return {"cleared": logs.clear()}


# ================================================================ 功能卡片

# 卡片目录、参数规格、自定义卡片存取都在 backend/cards.py。
# 前端不再写死任何卡片：内置 + 自定义都由 /api/cards 统一给出。


@app.get("/api/ops")
def get_ops() -> dict:
    """操作目录：每个 op 认哪些参数、取值范围、默认值、说明与等价命令模板。

    前端用它渲染「卡片编辑器」的参数表单，用户不用去翻源码猜参数。
    """
    return {
        "ops": [
            {"op": op, **{k: v for k, v in spec.items()}}
            for op, spec in cards_mod.OPS.items()
        ],
        "paramTypes": list(cards_mod.PARAM_TYPES),
        "icons": cards_mod.CARD_ICONS,
        "categories": cards_mod.CARD_CATS,
    }


@app.get("/api/cards")
def get_cards() -> dict:
    """内置 + 自定义卡片。custom=true 的那批可以编辑/删除。"""
    return {
        "cards": cards_mod.all_cards(),
        "categories": cards_mod.CARD_CATS,
        "snapshots": cards_mod.snapshots(),
        "builtinCount": len(cards_mod.BUILTIN_CARDS),
    }


@app.get("/api/snapshots")
def get_snapshots() -> dict:
    """抽屉顶部那排快照（卡片名列表）。"""
    return {"snapshots": cards_mod.snapshots(), "defaults": cards_mod.SNAPS_DEFAULT}


@app.put("/api/snapshots")
def put_snapshots(payload: dict = Body(...)) -> dict:
    """保存快照顺序/内容（拖拽替换后调用）。"""
    names = payload.get("snapshots")
    if not isinstance(names, list):
        raise HTTPException(400, "snapshots 必须是数组")
    try:
        clean = cards_mod.set_snapshots(names)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    logs.log(f"⇄ 快照已更新：{' / '.join(clean)}", kind="ok")
    return {"ok": True, "snapshots": clean}


@app.delete("/api/snapshots")
def reset_snapshots() -> dict:
    """恢复默认快照。"""
    return {"ok": True, "snapshots": cards_mod.reset_snapshots()}


@app.get("/api/cards/ops/{op}/preview")
def preview_card(op: str, params: str = "{}") -> dict:
    """按参数渲染等价命令，给编辑器实时预览用。"""
    if op not in cards_mod.OPS:
        raise HTTPException(404, f"未知的操作: {op}")
    try:
        parsed = json.loads(params) if params else {}
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"params 不是合法 JSON: {e}") from None
    if not isinstance(parsed, dict):
        raise HTTPException(400, "params 必须是对象")
    return {"op": op, "command": cards_mod.render_preview(op, parsed)}


@app.post("/api/cards")
def create_card(payload: dict = Body(...)) -> dict:
    """新建自定义卡片。"""
    try:
        card = cards_mod.add(payload)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    logs.log(f"✚ 新建卡片「{card['name']}」· {card['op']}", kind="ok")
    return {"ok": True, "card": card}


@app.put("/api/cards/{cid}")
def update_card(cid: str, payload: dict = Body(...)) -> dict:
    """改自定义卡片。内置卡片不允许原地改（用「另存为新卡片」）。"""
    try:
        card = cards_mod.update(cid, payload)
    except KeyError:
        raise HTTPException(404, f"卡片不存在: {cid}") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    logs.log(f"✎ 修改卡片「{card['name']}」", kind="ok")
    return {"ok": True, "card": card}


@app.delete("/api/cards/{cid}")
def delete_card(cid: str) -> dict:
    try:
        ok = cards_mod.remove(cid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if not ok:
        raise HTTPException(404, f"卡片不存在: {cid}")
    logs.log(f"🗑 删除卡片 {cid}", kind="warn")
    return {"ok": True}


@app.get("/api/cards/export")
def export_cards() -> dict:
    """导出全部自定义卡片（可再导入）。"""
    return {"version": 1, "cards": cards_mod.custom()}


@app.post("/api/cards/import")
def import_cards(payload: dict = Body(...)) -> dict:
    """导入自定义卡片；同名/同 id 的直接跳过，不覆盖已有配置。"""
    items = payload.get("cards") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "cards 必须是非空数组")
    have = cards_mod.custom_ids() | {c["id"] for c in cards_mod.BUILTIN_CARDS}
    added, skipped = [], []
    for raw in items:
        try:
            card = cards_mod.validate_card(raw)
        except ValueError as e:
            skipped.append({"name": (raw or {}).get("name"), "reason": str(e)})
            continue
        if card["id"] in have:
            skipped.append({"name": card["name"], "reason": "id 已存在"})
            continue
        try:
            added.append(cards_mod.add(card))
            have.add(card["id"])
        except ValueError as e:
            skipped.append({"name": card["name"], "reason": str(e)})
    if added:
        logs.log(f"⇩ 导入卡片 {len(added)} 张", kind="ok")
    return {"ok": True, "added": added, "skipped": skipped,
            "count": len(added), "skippedCount": len(skipped)}


# ================================================================ 上传

def _save_upload(upload: UploadFile, rel: str) -> dict:
    """保存一个上传文件到 uploads/<rel>，返回登记信息。"""
    if not rel:
        rel = upload.filename or "untitled"
    # safe_join 会拒绝越界，并逐段清洗
    try:
        dest = config.safe_join(config.UPLOADS, rel)
    except ValueError as e:
        raise HTTPException(400, str(e))

    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with dest.open("wb") as fh:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > config.MAX_UPLOAD_BYTES:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"文件超过上限 {config.MAX_UPLOAD_BYTES // (1 << 20)}MB")
            fh.write(chunk)

    st = dest.stat()
    row = store.add_file(
        str(dest.relative_to(config.UPLOADS.resolve())).replace("\\", "/"),
        size=st.st_size, mtime=st.st_mtime,
    )
    return {
        "id": row.id, "name": row.name, "relPath": row.rel_path,
        "size": row.size, "state": row.state,
        "kind": "audio" if config.is_audio(dest) else ("image" if config.is_image(dest) else "other"),
    }


@app.post("/api/upload")
async def upload(
    files: list[UploadFile] = File(...),
    paths: str = Form("[]"),                # 与 files 一一对应的相对路径（文件夹拖入时用）
) -> dict:
    """多文件/文件夹上传。

    浏览器侧：拖入文件夹时读取 webkitRelativePath 放进 paths，
    服务端按此重建目录结构。所有路径都经 safe_join 收敛，越界即拒。
    """
    if len(files) > config.MAX_BATCH_FILES:
        raise HTTPException(413, f"单次最多 {config.MAX_BATCH_FILES} 个文件")
    try:
        rel_list: list[str] = json.loads(paths or "[]")
    except json.JSONDecodeError:
        rel_list = []
    if len(rel_list) != len(files):
        rel_list = [f.filename or f"file{i}" for i, f in enumerate(files)]

    saved, skipped, errors = [], [], []
    for up, rel in zip(files, rel_list):
        ext = Path(rel).suffix.lower()
        if ext not in config.AUDIO_EXT and ext not in config.IMAGE_EXT:
            skipped.append({"name": rel, "reason": f"扩展名不在白名单: {ext or '(无)'}"})
            continue
        try:
            saved.append(_save_upload(up, rel))
        except HTTPException as e:
            errors.append({"name": rel, "reason": str(e.detail)})
        except Exception as e:
            errors.append({"name": rel, "reason": f"{type(e).__name__}: {e}"})

    return {
        "saved": saved,
        "skipped": skipped,
        "errors": errors,
        "count": len(saved),
        "message": f"导入 {len(saved)} 个"
                   + (f"，跳过 {len(skipped)}" if skipped else "")
                   + (f"，失败 {len(errors)}" if errors else ""),
    }


# ================================================================ 文件

@app.get("/api/files")
def list_files(includeDeleted: bool = False) -> dict:
    rows = store.list_files(include_deleted=includeDeleted)
    return {"files": [f.as_dict() for f in rows], "count": len(rows)}


@app.get("/api/files/{fid}")
def get_file(fid: str) -> dict:
    f = store.get_file(fid)
    if not f:
        raise HTTPException(404, "文件不存在")
    d = f.as_dict()
    d["tasks"] = [t.as_dict() for t in store.list_tasks(file_id=fid, limit=50)]
    return d


@app.post("/api/files/{fid}/probe")
def probe_file(fid: str) -> dict:
    f = store.get_file(fid)
    if not f:
        raise HTTPException(404, "文件不存在")
    t = store.create_task("probe", file_id=fid)
    q_mod.queue_.submit(t.id)
    return {"taskId": t.id}


@app.get("/api/files/{fid}/peaks")
def file_peaks(fid: str, buckets: int = Query(1000, ge=64, le=20000),
               force: bool = False) -> dict:
    f = store.get_file(fid)
    if not f:
        raise HTTPException(404, "文件不存在")
    try:
        return audio.peaks(f.path, buckets=buckets, force=force)
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")


@app.get("/api/files/{fid}/tags")
def file_tags(fid: str) -> dict:
    f = store.get_file(fid)
    if not f:
        raise HTTPException(404, "文件不存在")
    return {"tags": audio.read_tags(f.path), "fields": audio.UI_FIELDS}


@app.put("/api/files/{fid}/tags")
def put_tags(fid: str, payload: dict = Body(...)) -> dict:
    """写标签（不重新编码）。同步执行——metaflac 很快。"""
    f = store.get_file(fid)
    if not f:
        raise HTTPException(404, "文件不存在")
    tags = payload.get("tags") or {}
    if not isinstance(tags, dict):
        raise HTTPException(400, "tags 必须是对象")
    clear = bool(payload.get("clearMissing", False))
    r = audio.write_tags(f.path, tags, clear_missing=clear)
    if not r.ok:
        raise HTTPException(500, r.error)
    store.set_file_info(fid, audio.probe(f.path).as_dict())
    return {"ok": True, "method": r.method, "written": r.written,
            "removed": r.removed, "reencoded": False,
            "tags": audio.read_tags(f.path)}


@app.delete("/api/files/{fid}")
def delete_file(fid: str, purge: bool = False, withDisk: bool = False) -> dict:
    f = store.get_file(fid)
    if not f:
        raise HTTPException(404, "文件不存在")
    if withDisk and f.path.exists():
        f.path.unlink(missing_ok=True)
        config.prune_empty_dirs(f.path.parent, config.UPLOADS)
    store.delete_file(fid, purge=purge)
    return {"ok": True, "purged": purge, "removedFromDisk": withDisk}


@app.post("/api/files/delete")
def delete_files(payload: dict = Body(...)) -> dict:
    """批量删除（前端批量栏的「删除」）。

    逐条容错：某个文件失败不该让整批回滚，所以返回 deleted / failed 两份明细。
    """
    ids = payload.get("fileIds") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "fileIds 不能为空")
    if len(ids) > config.MAX_BATCH_FILES:
        raise HTTPException(400, f"单批最多 {config.MAX_BATCH_FILES} 个文件")
    with_disk = bool(payload.get("withDisk", True))
    purge = bool(payload.get("purge", False))

    deleted: list[dict] = []
    failed: list[dict] = []
    freed = 0
    pruned = 0
    for fid in ids:
        try:
            f = store.get_file(fid)
            if not f:
                failed.append({"id": fid, "error": "文件不存在"})
                continue
            size = f.size or 0
            if with_disk and f.path.exists():
                f.path.unlink(missing_ok=True)
                pruned += config.prune_empty_dirs(f.path.parent, config.UPLOADS)
            store.delete_file(fid, purge=purge)
            deleted.append({"id": fid, "name": f.name})
            freed += size
        except Exception as e:                       # noqa: BLE001 — 单条失败不拖垮整批
            failed.append({"id": fid, "error": f"{type(e).__name__}: {e}"})

    logs.log(f"🗑 批量删除 {len(deleted)} 个文件"
             + (f"，{len(failed)} 个失败" if failed else ""),
             kind="warn" if failed else "")
    return {"ok": not failed, "deleted": deleted, "failed": failed,
            "count": len(deleted), "freedBytes": freed, "withDisk": with_disk,
            "prunedDirs": pruned}


@app.get("/api/files/{fid}/download")
def download_file(fid: str):
    f = store.get_file(fid)
    if not f or not f.path.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(f.path, filename=f.name)


@app.get("/api/outputs/{rel:path}")
def download_output(rel: str, inline: bool = True):
    """取回 outputs/ 下的产物（波形 PNG、转换结果、ZIP…）。

    以前没有任何出口，生成的东西只能自己去文件夹里翻。
    任务结果里的 output 是【相对项目根】的（outputs/waveforms/x.png），
    而这里的 rel 是【相对 outputs/】的，两种写法都收，免得调用方各错各的。
    路径走 safe_join 收敛到 OUTPUTS 内，杜绝 ../ 穿越。
    """
    rel = str(rel or "").replace("\\", "/").lstrip("/")
    if rel.startswith("outputs/"):
        rel = rel[len("outputs/"):]
    try:
        p = config.safe_join(config.OUTPUTS, rel)
    except ValueError:
        raise HTTPException(400, "非法路径") from None
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "产物不存在")
    # 图片默认 inline（能直接在浏览器里看），其余强制下载
    disp = "inline" if inline else "attachment"
    return FileResponse(p, filename=p.name,
                        content_disposition_type=disp)


@app.get("/api/files/{fid}/cover")
def get_cover(fid: str, refresh: bool = False):
    """返回内嵌封面（jpg）。没有则 404。

    每张卡片都要显示封面，而这个接口每次调用都会起一个 ffmpeg 进程抽图，
    所以按「源文件 mtime + 大小」缓存结果：文件没变就直接回缓存。
    """
    f = store.get_file(fid)
    if not f or not f.path.exists():
        raise HTTPException(404, "文件不存在")
    try:
        st = f.path.stat()
        # 用纳秒：嵌入与移除可能在同一秒内完成，int(mtime) 会撞车
        key = f"{st.st_mtime_ns}-{st.st_size}"
    except OSError:
        key = "0-0"
    tmp, stamp = audio.cover_cache_paths(fid)
    if (not refresh and tmp.exists() and stamp.exists()
            and stamp.read_text(encoding="utf-8", errors="ignore").strip() == key):
        return FileResponse(tmp, media_type="image/jpeg")
    if tmp.exists():
        tmp.unlink(missing_ok=True)
    if not audio.extract_cover(f.path, tmp):
        stamp.unlink(missing_ok=True)
        raise HTTPException(404, "该文件没有内嵌封面")
    stamp.write_text(key, encoding="utf-8")
    return FileResponse(tmp, media_type="image/jpeg")


# ================================================================ 任务

@app.get("/api/tasks")
def list_tasks(fileId: Optional[str] = None, batchId: Optional[str] = None,
               states: Optional[str] = None, limit: int = Query(100, ge=1, le=1000)) -> dict:
    st = states.split(",") if states else None
    rows = store.list_tasks(file_id=fileId, batch_id=batchId, states=st, limit=limit)
    return {"tasks": [t.as_dict() for t in rows]}


@app.get("/api/tasks/queue")
def task_queue() -> dict:
    return store.queue_snapshot()


@app.get("/api/tasks/{tid}")
def get_task(tid: str) -> dict:
    t = store.get_task(tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    return t.as_dict()


@app.post("/api/tasks/{tid}/cancel")
def cancel_task(tid: str) -> dict:
    t = store.get_task(tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    q_mod.queue_.cancel(tid)
    return {"ok": True}


@app.post("/api/tasks/{tid}/retry")
def retry_task(tid: str) -> dict:
    nid = q_mod.retry_task(tid)
    if not nid:
        raise HTTPException(400, "只有失败或已取消的任务可以重试")
    return {"ok": True, "taskId": nid}


# ================================================================ 操作

def _require_files(ids: list[str]) -> list[store.FileRow]:
    out = []
    for i in ids:
        f = store.get_file(i)
        if f and f.state != "deleted":
            out.append(f)
    if not out:
        raise HTTPException(400, "没有可操作的文件")
    return out


@app.post("/api/ops/probe")
def op_probe(payload: dict = Body(...)) -> dict:
    files = _require_files(payload.get("fileIds") or [])
    res = q_mod.submit_batch("probe", [f.id for f in files], lambda _fid: {})
    return res


@app.post("/api/ops/peaks")
def op_peaks(payload: dict = Body(...)) -> dict:
    files = _require_files(payload.get("fileIds") or [])
    params = {k: v for k, v in payload.items() if k != "fileIds"}
    res = q_mod.submit_batch("peaks", [f.id for f in files], lambda _fid: params)
    return res


@app.post("/api/ops/convert")
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


@app.post("/api/ops/tags")
def op_tags(payload: dict = Body(...)) -> dict:
    """批量改标签。tags 会套用到每个文件。"""
    files = _require_files(payload.get("fileIds") or [])
    params = {k: v for k, v in payload.items() if k != "fileIds"}
    res = q_mod.submit_batch("tag_edit", [f.id for f in files], lambda _fid: params)
    return res


@app.post("/api/ops/cover")
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


@app.post("/api/ops/normalize")
def op_normalize(payload: dict = Body(...)) -> dict:
    files = _require_files(payload.get("fileIds") or [])
    # 同样原样透传：h_normalize 自己会校验范围
    params = {k: v for k, v in payload.items() if k != "fileIds"}
    res = q_mod.submit_batch("normalize", [f.id for f in files], lambda _fid: params)
    return res


@app.post("/api/ops/rename")
def op_rename(payload: dict = Body(...)) -> dict:
    files = _require_files(payload.get("fileIds") or [])
    pattern = str(payload.get("pattern") or "{tracknumber} - {title}")
    res = q_mod.submit_batch("rename", [f.id for f in files],
                             lambda _fid: {"pattern": pattern})
    return res


@app.post("/api/ops/waveform")
def op_waveform(payload: dict = Body(...)) -> dict:
    """导出波形 PNG（默认白色波形 + 透明底）。"""
    files = _require_files(payload.get("fileIds") or [])
    params = {k: v for k, v in payload.items() if k != "fileIds"}
    res = q_mod.submit_batch("waveform", [f.id for f in files], lambda _fid: params)
    return res


@app.post("/api/ops/verify")
def op_verify(payload: dict = Body(...)) -> dict:
    files = _require_files(payload.get("fileIds") or [])
    res = q_mod.submit_batch("verify", [f.id for f in files], lambda _fid: {})
    return res


@app.post("/api/ops/zip")
def op_zip(payload: dict = Body(...)) -> dict:
    """打包 ZIP：只建一个汇总任务，不 per-file 建。"""
    files = _require_files(payload.get("fileIds") or [])
    t = store.create_task("zip", params={"fileIds": [f.id for f in files]})
    q_mod.queue_.submit(t.id)
    return {"taskId": t.id, "count": len(files)}


@app.post("/api/ops/extract-cover")
def op_extract_cover(payload: dict = Body(...)) -> dict:
    files = _require_files(payload.get("fileIds") or [])
    res = q_mod.submit_batch("cover_extract", [f.id for f in files], lambda _fid: {})
    return res


@app.post("/api/ops/remove-cover")
def op_remove_cover(payload: dict = Body(...)) -> dict:
    files = _require_files(payload.get("fileIds") or [])
    res = q_mod.submit_batch("cover_remove", [f.id for f in files], lambda _fid: {})
    return res


# ================================================================ 进度推送（SSE）

@app.get("/api/events")
async def events(request: Request, interval: float = Query(1.0, ge=0.2, le=5.0)):
    """SSE：周期性推送队列与文件状态快照。

    比 WebSocket 简单，本地单机足够（需求 §3）。
    """
    async def gen():
        last = ""
        while True:
            if await request.is_disconnected():
                break
            snap = {
                "queue": store.queue_snapshot(limit=30),
                "files": [
                    {"id": f.id, "state": f.state, "tasks": f.task_summary}
                    for f in store.list_files(limit=500)
                ],
            }
            payload = json.dumps(snap, ensure_ascii=False)
            if payload != last:                 # 无变化不重复推
                last = payload
                yield f"data: {payload}\n\n"
            else:
                yield ": keepalive\n\n"
            import asyncio
            await asyncio.sleep(interval)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ================================================================ 静态前端

class NoCacheStatic(StaticFiles):
    """静态资源强制每次回源校验。

    StaticFiles 只发 ETag / Last-Modified，**不发 Cache-Control**，
    浏览器于是按"启发式缓存"处理（新鲜期≈文件年龄的 10%）——
    改完 CSS 刷新页面，可能拿到的还是旧文件，表现为"我改了但界面没变"。
    本地工具不存在带宽问题，直接 no-cache（仍然走 304，开销极小）。
    """

    def file_response(self, *args, **kwargs):        # type: ignore[override]
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


WEB = config.ROOT
if (WEB / "index.html").exists():
    app.mount("/", NoCacheStatic(directory=str(WEB), html=True), name="web")


# ================================================================ 入口

def main() -> None:
    import uvicorn
    config.ensure_dirs()
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
