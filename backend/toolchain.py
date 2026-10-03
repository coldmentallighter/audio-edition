"""命令行工具探测。

设计要点：
  · metaflac 没有可用的 --version，所以健康检查用「真实干跑」而不是版本号
  · 所有探测结果缓存，避免每个请求都 fork 一次子进程
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

TIMEOUT = 10


@dataclass
class Tool:
    name: str
    path: Optional[str] = None
    version: str = ""
    ok: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "version": self.version,
            "ok": self.ok,
            "note": self.note,
        }


def _run(args: list[str]) -> tuple[int, str, str]:
    """永远用参数列表调用，绝不拼 shell 字符串。"""
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        p = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
            creationflags=flags,
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


@dataclass
class Toolchain:
    tools: dict[str, Tool] = field(default_factory=dict)
    _probed: bool = False

    # ---------- 探测 ----------

    def probe(self, force: bool = False) -> "Toolchain":
        if self._probed and not force:
            return self
        self.tools = {
            "ffmpeg": self._probe_ffmpeg(),
            "ffprobe": self._probe_version_flag("ffprobe", "-version"),
            "flac": self._probe_version_flag("flac", "--version"),
            "metaflac": self._probe_metaflac(),
        }
        self._probed = True
        return self

    def _probe_version_flag(self, name: str, flag: str) -> Tool:
        path = shutil.which(name)
        if not path:
            return Tool(name, None, "", False, f"未在 PATH 找到 {name}")
        code, out, err = _run([path, flag])
        text = (out or err).strip().splitlines()
        version = text[0].strip() if text else ""
        # flac 的 --version 把版本写在 stderr，退出码非 0 也算可用
        ok = bool(version) and "not found" not in version.lower()
        return Tool(name, path, version, ok, "" if ok else "无法读取版本")

    def _probe_ffmpeg(self) -> Tool:
        return self._probe_version_flag("ffmpeg", "-version")

    def _probe_metaflac(self) -> Tool:
        """metaflac 没有 --version / -h：只能用真实文件干跑来确认可用。

        做法：生成一个 0.1s 的极小 FLAC 到临时目录，读它的标签。
        失败则退化为「命令存在但未验证」。
        """
        path = shutil.which("metaflac")
        if not path:
            return Tool("metaflac", None, "", False, "未在 PATH 找到 metaflac")
        ff = shutil.which("ffmpeg")
        if not ff:
            return Tool("metaflac", path, "", True, "命令存在；无 ffmpeg 无法干跑验证")

        import tempfile

        # ignore_cleanup_errors：临时目录**删不掉**不该拖垮启动。
        # 实测在受限环境（Windows 沙箱 / 杀软锁文件）下，TemporaryDirectory.__exit__
        # 里的 rmtree 会抛 PermissionError，而这个异常是在 lifespan 里冒出来的 ——
        # 结果是"探一下 metaflac"导致整个服务起不来，用户只看到一句拒绝访问。
        # 探测的目的是"确认工具可用"，与"临时文件清理成功"无关，所以清理失败就放着。
        with tempfile.TemporaryDirectory(prefix="ae-probe-", ignore_cleanup_errors=True) as td:
            f = Path(td) / "probe.flac"
            code, _, _ = _run([
                ff, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=0.1",
                "-c:a", "flac", str(f),
            ])
            if code != 0 or not f.exists():
                return Tool("metaflac", path, "", True, "命令存在；ffmpeg 干跑失败，未验证")
            rc, out, err = _run([path, "--show-md5sum", str(f)])
            if rc == 0:
                return Tool("metaflac", path, "1.5.x (干跑验证通过)", True, "")
            return Tool("metaflac", path, "", False, f"干跑失败: {(err or out).strip()[:80]}")

    # ---------- 读取 ----------

    def get(self, name: str) -> Tool:
        self.probe()
        return self.tools.get(name, Tool(name, None, "", False, "未知工具"))

    def path_of(self, name: str) -> Optional[str]:
        t = self.get(name)
        return t.path if t.ok else None

    def require(self, name: str) -> str:
        p = self.path_of(name)
        if not p:
            t = self.get(name)
            raise ToolchainError(f"{name} 不可用：{t.note or '未安装'}")
        return p

    def as_dict(self) -> dict:
        self.probe()
        return {k: v.as_dict() for k, v in self.tools.items()}

    @property
    def all_ok(self) -> bool:
        self.probe()
        return all(t.ok for t in self.tools.values())


class ToolchainError(RuntimeError):
    pass


# 全局单例
toolchain = Toolchain()


if __name__ == "__main__":
    tc = toolchain.probe(force=True)
    for name, t in tc.as_dict().items():
        mark = "OK  " if t["ok"] else "FAIL"
        print(f"[{mark}] {name:<9} {t['version'][:60]}")
        if t["note"]:
            print(f"          note: {t['note']}")
        print(f"          path: {t['path']}")
    print()
    print("全部可用" if tc.all_ok else "存在不可用工具")
