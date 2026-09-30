"""验证「显示所在目录」在 Windows 上拼出的命令行**形式**是对的。

为什么单独有这个脚本：`explorer` **成功也返回退出码 1**，而参数拼错时它
既不报错也不返回失败 —— 只是默默打开一个无关窗口（实测落在「文档」）。
于是退出码、HTTP 状态、`{"ok":true,"revealed":true}` 全是绿的，
**只有命令行本身能证伪**。所以这里把 `Popen` 拦下来，直接看它收到什么。

这个脚本**不会真的弹出资源管理器**（`Popen` 被替换掉了），可以随便跑。

行为层面的取证（真的定位+选中）由 COM 探针做，那个会弹窗，所以不进测试套件；
脚本末尾记了结论与做法，改这段代码前先看一眼。
"""
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config                       # noqa: E402
from backend.routers import files as files_mod   # noqa: E402

PASSED = FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}   {detail}")


def capture(target, platform):
    """替换 subprocess.Popen / sys.platform，拦下调用参数。

    返回 (calls, 函数返回值)。calls 里每项是传给 Popen 的 (args, kwargs)。
    """
    calls = []
    real_popen, real_platform = subprocess.Popen, sys.platform

    def fake(*a, **k):
        calls.append((a, k))
        return None

    subprocess.Popen = fake
    sys.platform = platform
    try:
        ret = files_mod._open_in_file_manager(target)
    finally:
        subprocess.Popen = real_popen
        sys.platform = real_platform
    return calls, ret


PATHS = [
    Path(r"C:\Users\x\AppData\Local\Temp\uploads\plain.wav"),
    Path(r"C:\Users\x\AppData\Local\Temp\uploads\with space\out file.wav"),
    Path(r"C:\Users\x\AppData\Local\Temp\uploads\中文 目录\中文 名称 测试.wav"),
]

print("== Windows：命令行形式（这是本次修的那个 bug）==")
for p in PATHS:
    label = p.name
    calls, ret = capture(p, "win32")
    check(f"只拉起一个进程 [{label}]", len(calls) == 1, f"calls={len(calls)}")
    if not calls:
        continue
    a, k = calls[0]
    cmd = a[0] if a else None

    # 1) 必须是**一整条命令行字符串**。传列表会被 list2cmdline 重新转义，
    #    引号被写成 \" 交给 explorer，它解析不出路径。
    check(f"Popen 收到字符串而不是列表 [{label}]", isinstance(cmd, str), f"type={type(cmd).__name__}")
    # 2) 精确形式：引号只包住路径，/select, 后面紧跟引号
    check(f"命令行精确等于 explorer /select,\"<path>\" [{label}]",
          cmd == f'explorer /select,"{p}"', f"got={cmd!r}")
    # 3) 路径里的空格不能被当成参数分隔（靠这层引号）
    check(f"引号恰好 2 个、只包路径 [{label}]",
          isinstance(cmd, str) and cmd.count('"') == 2 and f'"{p}"' in cmd, f"got={cmd!r}")
    # 4) 绝不能出现反斜杠转义的引号 —— 这就是坏掉的那一版的指纹
    check(f"不含转义引号 \\\" [{label}]", isinstance(cmd, str) and '\\"' not in cmd, f"got={cmd!r}")
    # 5) 不能用 shell=True（会让引号再被 cmd.exe 处理一遍）
    check(f"没有 shell=True [{label}]", not k.get("shell"), f"kwargs={k}")
    check(f"返回 True（该平台支持「选中」）[{label}]", ret is True, f"ret={ret}")

print("\n== 负对照：坏掉的那一版到底错在哪 ==")
# 先把"坏"的形态构造出来，证明上面的断言有区分力 ——
# 否则这个脚本可能只是在断言一句恒真的废话。
bad = subprocess.list2cmdline(["explorer", '/select,"C:\\a b\\x.wav"'])
good = 'explorer /select,"C:\\a b\\x.wav"'
check("前提：列表形式确实会被转义出 \\\"（所以\"形式\"是可证伪的）",
      '\\"' in bad, f"list2cmdline={bad!r}")
check("前提：两种形式拼出的命令行确实不同（断言不是恒真）",
      bad != good, f"bad={bad!r} good={good!r}")
check("列表形式解析后 path 带上了字面引号（explorer 就是因此找不到路径）",
      bad.split("/select,", 1)[1].startswith('\\"'), f"tail={bad.split('/select,', 1)[1]!r}")

print("\n== 其它平台（不能被 Windows 分支带偏）==")
calls, ret = capture(PATHS[1], "darwin")
check("darwin 用 open -R 且参数走列表（这里没有引号解析问题）",
      len(calls) == 1 and list(calls[0][0][0]) == ["open", "-R", str(PATHS[1])],
      f"calls={calls}")
check("darwin 返回 True", ret is True, f"ret={ret}")

calls, ret = capture(PATHS[1], "linux")
check("linux 没有通用「选中」，退化为打开所在目录",
      len(calls) == 1 and list(calls[0][0][0]) == ["xdg-open", str(PATHS[1].parent)],
      f"calls={calls}")
check("linux 明确返回 False（前端据此提示「本平台不支持选中」）", ret is False, f"ret={ret}")

print("\n== 前提：文件名里不可能有引号（所以不做二次转义是安全的）==")
sanitized = config.sanitize_name('a"b.wav')
check("sanitize_name 会把 \" 换成 _（config._ILLEGAL 含 \"）",
      '"' not in sanitized, f"sanitized={sanitized!r}")
check("sanitize_relpath 同样清掉 \"",
      '"' not in str(config.sanitize_relpath('d"ir/f"ile.wav')),
      f"{config.sanitize_relpath('d\"ir/f\"ile.wav')!s}")

print(f"\n结果：{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
