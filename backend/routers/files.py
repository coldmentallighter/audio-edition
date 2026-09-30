"""导入与文件本身：上传、列表、标签、封面、峰值、下载、批量删除。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import (APIRouter, Body, File, Form, HTTPException, Query,
                     UploadFile)
from fastapi.responses import FileResponse

from backend import audio, config, logs, queue as q_mod, store

router = APIRouter()

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


@router.post("/api/upload")
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

@router.get("/api/files")
def list_files(includeDeleted: bool = False) -> dict:
    rows = store.list_files(include_deleted=includeDeleted)
    return {"files": [f.as_dict() for f in rows], "count": len(rows)}


@router.get("/api/files/{fid}")
def get_file(fid: str) -> dict:
    f = store.get_file(fid)
    if not f:
        raise HTTPException(404, "文件不存在")
    d = f.as_dict()
    d["tasks"] = [t.as_dict() for t in store.list_tasks(file_id=fid, limit=50)]
    return d


@router.post("/api/files/{fid}/probe")
def probe_file(fid: str) -> dict:
    f = store.get_file(fid)
    if not f:
        raise HTTPException(404, "文件不存在")
    t = store.create_task("probe", file_id=fid)
    q_mod.queue_.submit(t.id)
    return {"taskId": t.id}


@router.get("/api/files/{fid}/peaks")
def file_peaks(fid: str, buckets: int = Query(1000, ge=64, le=20000),
               force: bool = False) -> dict:
    f = store.get_file(fid)
    if not f:
        raise HTTPException(404, "文件不存在")
    # 图片在库里是合法的（嵌封面要用），但它没有音频流，解码必然失败。
    # 这是**预期**情况，不是服务端故障：以前走 except 兜成 500，
    # 前端每刷新一次列表就重试一遍，日志里刷出上百条 500。
    if not config.is_audio(f.path):
        raise HTTPException(415, f"{f.path.suffix or '该文件'} 不是音频，没有波形可算")
    try:
        return audio.peaks(f.path, buckets=buckets, force=force)
    except Exception as e:
        # 解码不出来同样是"这份内容不是能解码的音频"，归 4xx
        raise HTTPException(415, f"{type(e).__name__}: {e}")


@router.get("/api/files/{fid}/tags")
def file_tags(fid: str) -> dict:
    f = store.get_file(fid)
    if not f:
        raise HTTPException(404, "文件不存在")
    return {"tags": audio.read_tags(f.path), "fields": audio.UI_FIELDS}


@router.put("/api/files/{fid}/tags")
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
        # 「这格式不支持标签」（AAC / 图片…）是**预期**情况 → 415；
        # 其余（metaflac 挂了、磁盘写失败…）才是 500。
        raise HTTPException(415 if r.unsupported else 500, r.error)
    store.set_file_info(fid, audio.probe(f.path).as_dict())
    return {"ok": True, "method": r.method, "written": r.written,
            "removed": r.removed, "reencoded": False,
            "tags": audio.read_tags(f.path)}


@router.delete("/api/files/{fid}")
def delete_file(fid: str, purge: bool = False, withDisk: bool = False) -> dict:
    f = store.get_file(fid)
    if not f:
        raise HTTPException(404, "文件不存在")
    if withDisk and f.path.exists():
        f.path.unlink(missing_ok=True)
        config.prune_empty_dirs(f.path.parent, config.UPLOADS)
    store.delete_file(fid, purge=purge)
    return {"ok": True, "purged": purge, "removedFromDisk": withDisk}


@router.post("/api/files/delete")
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


@router.get("/api/files/{fid}/download")
def download_file(fid: str):
    f = store.get_file(fid)
    if not f or not f.path.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(f.path, filename=f.name)


@router.get("/api/outputs/{rel:path}")
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


@router.post("/api/files/{fid}/reveal")
def reveal_file(fid: str, open: bool = True) -> dict:
    """在文件管理器里打开**工作副本所在目录**并选中它。

    只收 fid、**不收路径**：要打开的路径由后端从库里算（`FileRow.path` 已经过
    `safe_join`，只会落在 uploads/ 内），所以客户端没有办法让它去打开
    `C:\\Windows\\System32` 之类的地方。

    注意打开的是 **uploads/ 里的工作副本**，不是导入前的原文件 ——
    而 uploads/ 默认每次启动都会被清空（AE_FRESH=1），所以这一项的含义是
    "去取出这份副本"，不是"回到我原来那个文件"。

    `open=false` 只做解析与校验、返回绝对路径，不拉起任何进程
    （前端"复制路径"用它；测试也用它，免得在开发机上弹一屏资源管理器）。
    """
    f = store.get_file(fid)
    if not f:
        raise HTTPException(404, "文件不存在")
    p = f.path                       # safe_join 已收敛在 uploads/ 内
    if not p.exists():
        raise HTTPException(404, "工作副本已不在（uploads 每次启动会清空，"
                                 "或该文件已被删除）")
    revealed = False
    if open:
        revealed = _open_in_file_manager(p)
    return {"ok": True, "path": str(p), "opened": bool(open),
            "revealed": revealed, "kind": "working-copy"}


def _open_in_file_manager(p: Path) -> bool:
    """按平台拉起文件管理器并选中 p。返回是否真的支持"选中"。"""
    import subprocess
    import sys

    if sys.platform == "win32":
        # explorer 的老毛病：即使成功也返回退出码 1，**不能**拿退出码判断成败。
        #
        # 必须传"一整条命令行"（字符串），绝不能传列表：
        #   Popen(["explorer", '/select,"C:\\a b\\x.wav"'])
        # 列表会被 list2cmdline 重新转义，实际命令行变成
        #   explorer /select,\"C:\a b\x.wav\"
        # 反斜杠加引号是**字面字符**，explorer 解析不出路径，于是既没定位也没选中
        # （实测：只是又开了一个窗口，落在"文档"）。
        # 传字符串时 CreateProcess 收到原样命令行，引号只包住路径本身：
        #   explorer /select,"C:\a b\x.wav"
        # 实测在 ASCII/带空格/中文+空格 目录下都能定位并选中（0.3s~11s 采样稳定）。
        # 路径里的 `"` 不可能出现（config 的非法字符集含 `"`），所以无需再转义。
        subprocess.Popen(f'explorer /select,"{p}"')
        return True
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
        return True
    # Linux 没有通用的"选中"：退一步打开所在目录（xdg-open 可能也不存在）
    try:
        subprocess.Popen(["xdg-open", str(p.parent)])
        return False
    except FileNotFoundError:
        return False


@router.get("/api/files/{fid}/cover")
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


