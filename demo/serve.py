"""UI 演示版的静态服务器 —— 只用 Python 标准库，不需要 FastAPI / ffmpeg / 任何依赖。

    python demo/serve.py                 # 起服务 + 自动打开浏览器
    python demo/serve.py --port 8900     # 换端口
    python demo/serve.py --no-browser    # 不自动开浏览器

为什么还要个服务器（不能双击 index.html 吗）：
  · 双击（file://）其实**能跑** —— 所有 /api 都是 mock.js 在浏览器里拦掉的，
    音频/封面用的是相对路径。但 file:// 下部分浏览器会限制 <audio> 的范围请求，
    用 http://127.0.0.1 更接近正式环境，所以推荐这条路。

这个服务器**只发静态文件**，不实现任何 /api：
  页面的每个 /api 请求都被 mock.js 拦住了；如果这里看到 "未拦截的 /api 请求"，
  说明 mock.js 漏了一个接口 —— 那条日志就是给这种情况留的。
"""
import argparse
import http.server
import socket
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 演示包里要发的类型（音频要能直接喂给 <audio>）
EXTRA_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".aiff": "audio/aiff",
    ".webp": "image/webp",
}


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      **EXTRA_TYPES}

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def end_headers(self):
        # 演示版会被反复重生成，别让浏览器拿旧副本骗人
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        path = self.path or ""
        if "/api/" in path:
            # 走到这里说明 mock.js 没拦住这个接口 —— 必须显眼
            sys.stderr.write(f"[demo] !! 未拦截的 /api 请求: {path}\n")
            sys.stderr.flush()
            return
        if path.endswith((".html", "/")):
            super().log_message(fmt, *args)


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def pick_port(start: int, tries: int = 12) -> int:
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise SystemExit(f"端口 {start}-{start + tries - 1} 都被占用了")


def main() -> int:
    ap = argparse.ArgumentParser(description="AudioEdition UI 演示版静态服务器")
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()

    if not (ROOT / "index.html").exists():
        print("!! demo/index.html 不存在，先跑： python demo/_build/make_demo.py")
        return 2
    if not (ROOT / "data" / "demo-data.js").exists():
        print("!! demo/data/demo-data.js 不存在，先跑： python demo/_build/collect_data.py")
        return 2

    port = pick_port(a.port)
    url = f"http://{a.host}:{port}/index.html"
    httpd = Server((a.host, port), Handler)

    print("=" * 62)
    print("  AudioEdition · UI 演示版")
    print(f"  {url}")
    print("  内置样例数据，不会真的读写音频文件；Ctrl+C 结束")
    print("=" * 62)
    if port != a.port:
        print(f"（{a.port} 被占用，改用 {port}）")

    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
