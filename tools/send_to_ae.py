"""
把资源管理器选中的文件/文件夹投递给正在运行的 AudioEdition。

用法：
    python send_to_ae.py <路径1> [<路径2> ...]

对应后端签名（backend/app.py）：
    POST /api/upload
        files: list[UploadFile]   # multipart 文件字段，可多个
        paths: str = Form("[]")   # JSON 数组，与 files 下标一一对应，决定落盘相对路径

行为：
    1. 服务没在跑就拉起（AE_FRESH=0，绝不清空工作区）
    2. 展开文件夹、按扩展名白名单预过滤
    3. 按字节/个数分批 POST，避免撞 MAX_BATCH_FILES=500 与内存
    4. 打印 saved / skipped / errors，失败不静默
    5. 打开浏览器
"""
import os
import sys
import json
import time
import uuid
import subprocess
import webbrowser
import urllib.request
import urllib.error
from pathlib import Path

# ── 与 backend/config.py 逐字一致（已确认） ─────────────────────────────
BASE = "http://127.0.0.1:8765"
HEALTH_URL = f"{BASE}/api/health"
UPLOAD_URL = f"{BASE}/api/upload"

AUDIO_EXT = {
    ".flac", ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif", ".wma",
}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
ALLOWED_EXT = AUDIO_EXT | IMAGE_EXT

MAX_BATCH_FILES = 500                    # = config.MAX_BATCH_FILES
BATCH_BYTES = 256 * 1024 * 1024          # 单批体积上限，防爆内存/超时
UPLOAD_TIMEOUT = 600                     # 单批请求超时（秒）

ROOT = Path(__file__).resolve().parent.parent  # audio-edition 根目录


# ── 服务探测与拉起 ────────────────────────────────────────────────────
def service_alive(timeout=0.6):
    try:
        urllib.request.urlopen(HEALTH_URL, timeout=timeout)
        return True
    except Exception:
        return False


def wait_ready(timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if service_alive():
            return True
        time.sleep(0.5)
    return False


def start_service():
    """通过项目根目录的 run.bat / run.sh 拉起服务。

    run.bat 自带：Python 与工具链检查、依赖安装、端口预检、
    健康检查成功后才开浏览器。出错会 pause 在窗口里，不会被吞。
    """
    env = os.environ.copy()
    env["AE_FRESH"] = "0"  # 关键：run.bat 不覆盖此变量，会一路传到后端
    env["AE_NO_BROWSER"] = "1"
    # run.bat 对 AE_HOST / AE_PORT 用的是 "if not defined"，
    # 所以这里 setdefault 只是补默认值，不会覆盖用户已设的。
    env.setdefault("AE_HOST", "127.0.0.1")
    env.setdefault("AE_PORT", "8765")

    if os.name == "nt":
        bat = ROOT / "run.bat"
        if not bat.exists():
            print(f"找不到 {bat}", file=sys.stderr)
            return False
        # CREATE_NEW_CONSOLE：给 run.bat 一个自己的窗口，
        # 用户能看到启动横幅、能 Ctrl+C 停、出错能看见 pause。
        subprocess.Popen(
            ["cmd", "/c", str(bat)],
            cwd=str(ROOT),
            env=env,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            close_fds=True,
        )
    else:
        sh = ROOT / "run.sh"
        if not sh.exists():
            print(f"找不到 {sh}", file=sys.stderr)
            return False
        subprocess.Popen(
            ["bash", str(sh)],
            cwd=str(ROOT),
            env=env,
            close_fds=True,
        )
    return True


# ── 收集与过滤 ────────────────────────────────────────────────────────
def collect(paths):
    """展开文件夹；返回 (files, skipped)。

    files  : list[(绝对路径, 相对路径 posix)]
             相对路径保留顶层文件夹名，用于重建目录结构。
    skipped: list[str]，扩展名不在白名单的文件名。
    """
    files, skipped = [], []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in p.rglob("*"):
                if not f.is_file():
                    continue
                rel = (Path(p.name) / f.relative_to(p)).as_posix()
                if f.suffix.lower() in ALLOWED_EXT:
                    files.append((f, rel))
                else:
                    skipped.append(rel)
        elif p.is_file():
            if p.suffix.lower() in ALLOWED_EXT:
                files.append((p, p.name))
            else:
                skipped.append(p.name)
    return files, skipped


def chunk(files):
    """按字节数 + 个数双限分批。files: list[(abs_path, rel_posix)]"""
    batch, size = [], 0
    for item in files:
        try:
            n = item[0].stat().st_size
        except OSError:
            n = 0
        if batch and (size + n > BATCH_BYTES or len(batch) >= MAX_BATCH_FILES):
            yield batch
            batch, size = [], 0
        batch.append(item)
        size += n
    if batch:
        yield batch


# ── 上传 ──────────────────────────────────────────────────────────────
def post_batch(files):
    """一次 POST。files: list[(abs_path, rel_posix)]，返回解析后的 JSON。"""
    boundary = "----AE" + uuid.uuid4().hex
    parts = []

    # 1) files 字段：每个文件一个 part
    for abs_path, _rel in files:
        # filename 用 basename（UTF-8），目录结构走 paths 字段；
        # 后端 fallback 时也不至于丢名字。
        filename = Path(abs_path).name
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(
            f'Content-Disposition: form-data; name="files"; '
            f'filename="{filename}"\r\n'.encode("utf-8")
        )
        parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
        parts.append(abs_path.read_bytes())
        parts.append(b"\r\n")

    # 2) paths 字段：JSON 数组，与 files 下标一一对应
    rel_list = [rel for _abs, rel in files]
    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(b'Content-Disposition: form-data; name="paths"\r\n\r\n')
    parts.append(json.dumps(rel_list, ensure_ascii=False).encode("utf-8"))
    parts.append(b"\r\n")

    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    req = urllib.request.Request(
        UPLOAD_URL,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=UPLOAD_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))

PROBE_TIMEOUT = 600   # 单个大无损文件算响度可能要几十秒，给宽

def _get_json(url, data=None, method="GET", timeout=30):
    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def list_unprobed_ids():
    """返回所有 info 为空的文件 id（= 尚未完成元数据/响度探测）。"""
    files = _get_json(f"{BASE}/api/files").get("files") or []
    return [
        f["id"] for f in files
        if f.get("id") and not f.get("info")
    ]

def probe_file(fid):
    """触发后端探测：元数据 + 响度。同步返回。"""
    return _get_json(f"{BASE}/api/files/{fid}/probe",
                     data=b"", method="POST", timeout=PROBE_TIMEOUT)
    
    
# ── 主流程 ────────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]
    if not args:
        return 0

    if not service_alive():
        if not start_service():
            return 1
        if not wait_ready(timeout=60):
            print(...)  # 原样保留
            return 1

    files, pre_skipped = collect(args)

    saved = skipped = errors = 0
    for batch in chunk(files):
        try:
            r = post_batch(batch)
        except urllib.error.HTTPError as e:
            detail = e.read()[:300]
            print(f"上传失败 HTTP {e.code}: {detail!r}", file=sys.stderr)
            errors += len(batch)
            continue
        except Exception as e:
            print(f"上传异常: {type(e).__name__}: {e}", file=sys.stderr)
            errors += len(batch)
            continue

        saved += r.get("count", 0)
        skipped += len(r.get("skipped", []))
        errors += len(r.get("errors", []))

        if r.get("skipped") or r.get("errors"):
            print(r.get("message", ""), file=sys.stderr)
            for it in r.get("skipped", []) + r.get("errors", []):
                print(f"  ! {it.get('name')}: {it.get('reason')}", file=sys.stderr)

    if pre_skipped:
        print(f"按扩展名预过滤 {len(pre_skipped)} 个（后端也不会收）：", file=sys.stderr)
        for name in pre_skipped[:10]:
            print(f"  - {name}", file=sys.stderr)
        if len(pre_skipped) > 10:
            print(f"  … 另 {len(pre_skipped) - 10} 个", file=sys.stderr)

    print(f"导入 {saved}，跳过 {skipped}，失败 {errors}")

    # ── 触发探测：元数据 + 响度 ────────────────────────────
    # 浏览器侧同样是在 GET /api/files 之后逐个 POST /api/files/{id}/probe
    try:
        targets = list_unprobed_ids()
    except Exception as e:
        print(f"拉取文件列表失败，跳过探测: {type(e).__name__}: {e}", file=sys.stderr)
        targets = []

    if targets:
        print(f"探测 {len(targets)} 个未解析文件的元数据/响度…")
        ok = bad = 0
        for fid in targets:
            try:
                probe_file(fid)
                ok += 1
            except urllib.error.HTTPError as e:
                print(f"  probe {fid} HTTP {e.code}: {e.read()[:200]!r}", file=sys.stderr)
                bad += 1
            except Exception as e:
                print(f"  probe {fid} {type(e).__name__}: {e}", file=sys.stderr)
                bad += 1
        print(f"探测完成：{ok} 成功，{bad} 失败")

    webbrowser.open(BASE)
    return 0


if __name__ == "__main__":
    sys.exit(main())