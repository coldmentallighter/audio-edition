"""子进程执行器。

铁律（需求 §4.8）：**永远不拼 shell 字符串**，只用参数列表。
另外提供：取消、超时、stderr 摘要、进度解析。
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from backend import config

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

# 明确禁止出现在任何参数里的 shell 元字符（双保险：我们本来就不走 shell）
SHELL_METACHARS = set("|&;<>$`\n\r")


@dataclass
class Result:
    args: list[str]
    code: int
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.cancelled

    @property
    def stderr_summary(self) -> str:
        """给前端看的简短错误摘要（需求 §4.5：失败任务附 stderr 摘要）。"""
        text = (self.stderr or self.stdout or "").strip()
        if not text:
            return f"退出码 {self.code}"
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        # ffmpeg 的报错通常在最后几行
        tail = lines[-4:] if len(lines) > 4 else lines
        return " / ".join(tail)[:600]

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "ok": self.ok,
            "cancelled": self.cancelled,
            "duration": round(self.duration, 3),
            "stderr": self.stderr_summary,
        }


def check_args(args: Sequence[str]) -> None:
    """参数列表自检：不允许空参数、不允许元字符混入（防御性）。"""
    if not args:
        raise ValueError("空命令")
    for i, a in enumerate(args):
        if not isinstance(a, str):
            raise TypeError(f"第 {i} 个参数不是字符串: {a!r}")
        if "\x00" in a:
            raise ValueError(f"第 {i} 个参数含 NUL")


@dataclass
class Running:
    """一个正在运行的子进程句柄。"""
    proc: subprocess.Popen
    result: Result
    _cancel: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self._cancel.set()
        try:
            self.proc.kill()
        except Exception:
            pass

    def wait(self, timeout: float | None = None) -> Result:
        self.proc.wait(timeout=timeout)
        return self.result


def run(
    args: Sequence[str],
    *,
    timeout: float = config.TASK_TIMEOUT,
    cwd: Path | None = None,
    on_line: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Result:
    """同步执行，返回 Result。适合短命令（probe / metaflac）。"""
    check_args(args)
    t0 = time.time()
    proc = subprocess.Popen(
        list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
        creationflags=CREATE_NO_WINDOW,
        shell=False,                       # 显式关闭 shell
    )
    out_buf: list[str] = []
    err_buf: list[str] = []

    def pump(stream, buf: list[str]) -> None:
        try:
            for line in stream:
                buf.append(line)
                if on_line:
                    on_line(line.rstrip("\n"))
        except Exception:
            pass

    t_out = threading.Thread(target=pump, args=(proc.stdout, out_buf), daemon=True)
    t_err = threading.Thread(target=pump, args=(proc.stderr, err_buf), daemon=True)
    t_out.start()
    t_err.start()

    cancelled = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    finally:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
        t_out.join(timeout=2)
        t_err.join(timeout=2)

    return Result(
        args=list(args),
        code=proc.returncode if proc.returncode is not None else -1,
        stdout="".join(out_buf),
        stderr="".join(err_buf),
        duration=time.time() - t0,
        cancelled=cancelled,
    )


def spawn(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    on_line: Optional[Callable[[str], None]] = None,
) -> Running:
    """异步启动，返回句柄。适合长任务（转换 / 峰值计算）。"""
    check_args(args)
    proc = subprocess.Popen(
        list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,          # 合并，便于按进度解析
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
        creationflags=CREATE_NO_WINDOW,
        shell=False,
        bufsize=1,
    )
    res = Result(args=list(args), code=-1)

    def pump() -> None:
        try:
            for line in proc.stdout:                      # type: ignore[union-attr]
                if on_line:
                    on_line(line.rstrip("\n"))
        except Exception:
            pass

    threading.Thread(target=pump, daemon=True).start()
    return Running(proc=proc, result=res)


def run_bytes(
    args: Sequence[str],
    *,
    timeout: float = config.TASK_TIMEOUT,
    cwd: Path | None = None,
) -> tuple[int, bytes, str]:
    """执行并把 stdout 当【二进制】取回（用于解码 PCM）。

    不能用 run()：那里按 utf-8 解码，会把原始采样数据毁掉。
    返回 (退出码, stdout bytes, stderr 文本)。
    """
    check_args(args)
    try:
        p = subprocess.run(
            list(args),
            capture_output=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            creationflags=CREATE_NO_WINDOW,
            shell=False,
        )
        return p.returncode, p.stdout or b"", (p.stderr or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, b"", "timeout"
    except FileNotFoundError:
        return 127, b"", "not found"


def which_version(args: Iterable[str]) -> str:
    """取命令版本首行（用于 --version 类调用）。"""
    r = run(list(args), timeout=10)
    line = (r.stdout or r.stderr).strip().splitlines()
    return line[0].strip() if line else ""
