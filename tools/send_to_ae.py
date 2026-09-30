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
    5. 打开浏览器；探测交给前端已有的流程
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
    env["AE_FRESH"] = "0"       # 关键：run.bat 不覆盖此变量，会一路传到后端
    env["AE_NO_BROWSER"] = "1"  # 由脚本负责开浏览器，避免开两个 tab
    env.setdefault("AE_HOST", "127.0.0.1")
    env.setdefault("AE_PORT", "8765")

    if os.name == "nt":
        bat = ROOT / "run.bat"
        if not bat.exists():
            print(f"找不到 {bat}", file=sys.stderr)
            return False
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

    for abs_path, _rel in files:
        filename = Path(abs_path).name
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(
            f'Content-Disposition: form-data; name="files"; '
            f'filename="{filename}"\r\n'.encode("utf-8")
        )
        parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
        parts.append(abs_path.read_bytes())
        parts.append(b"\r\n")

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

def browser_is_active(max_age=5.0):
    """页面若在最近 5 秒内打过心跳，就认为有活跃 tab。"""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=0.6) as r:
            d = json.loads(r.read())
        return bool(d.get("browser_active"))
    except Exception:
        return False

# ── 主流程 ────────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]
    if not args:
        return 0

    started_by_us = False
    if not service_alive():
        if not start_service():
            return 1
        if not wait_ready(timeout=60):
            print(
                "AudioEdition 未能在 60 秒内就绪。\n"
                "请查看刚弹出的 run.bat 窗口——错误会 pause 在那里。",
                file=sys.stderr,
            )
            return 1
        started_by_us = True

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

    if not browser_is_active():
        webbrowser.open(BASE)
    return 0


if __name__ == "__main__":
    sys.exit(main())