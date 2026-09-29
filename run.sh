#!/usr/bin/env bash
# ============================================================
#  AudioEdition 一键启动（Linux / macOS）
#  用法：./run.sh        或   AE_PORT=9000 ./run.sh
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

echo
echo " ============================================"
echo "  AudioEdition | 本地音频工具箱"
echo " ============================================"
echo

# ---- 1. Python ----
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo " [错误] 未找到 python3，请先安装 Python 3.10+"
  exit 1
fi
echo " [1/4] $($PY --version)"

# ---- 2. 工具链 ----
MISSING=0
for t in ffmpeg ffprobe flac metaflac; do
  if command -v "$t" >/dev/null 2>&1; then
    echo " [2/4] $t 已就绪"
  else
    echo " [警告] 未找到 $t"
    MISSING=1
  fi
done
if [ "$MISSING" = "1" ]; then
  echo
  echo " [提示] 缺少工具仍可启动，但相关功能会报错。"
  echo "        Debian/Ubuntu: sudo apt install ffmpeg flac"
  echo "        macOS:         brew install ffmpeg flac"
  echo
fi

# ---- 3. 依赖 ----
if ! $PY -c "import fastapi, uvicorn, mutagen, multipart" >/dev/null 2>&1; then
  echo " [3/4] 缺少依赖，正在安装..."
  $PY -m pip install --disable-pip-version-check -q \
      fastapi "uvicorn[standard]" mutagen python-multipart
else
  echo " [3/4] 依赖已满足"
fi

# ---- 4. 启动 ----
AE_HOST="${AE_HOST:-127.0.0.1}"
AE_PORT="${AE_PORT:-8765}"
export AE_HOST AE_PORT

# 先查端口：别等 uvicorn 打完启动横幅才报 10048，那时浏览器可能已经开了
port_pid() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -n1
  elif command -v ss >/dev/null 2>&1; then
    ss -lptnH "sport = :$1" 2>/dev/null | grep -o 'pid=[0-9]*' | head -n1 | cut -d= -f2
  fi
}

# 返回 0 = 端口空闲。lsof / ss 都没有时用 Python 真 bind 一次，
# 因为 run.sh 本来就要求有 Python，这条兜底在所有平台都成立。
port_free() {
  if command -v lsof >/dev/null 2>&1; then
    ! lsof -nP -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1
  elif command -v ss >/dev/null 2>&1; then
    ! ss -lptnH "sport = :$1" 2>/dev/null | grep -q .
  else
    $PY - "$1" "$AE_HOST" >/dev/null 2>&1 <<'PYEOF'
import socket, sys
host, port = sys.argv[2], int(sys.argv[1])
s = socket.socket()
# 与 uvicorn 一致地设 SO_REUSEADDR：避免把 TIME_WAIT 误判成"被占用"
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind((host, port))
except OSError:
    sys.exit(1)
finally:
    s.close()
PYEOF
  fi
}

if ! port_free "$AE_PORT"; then
  BUSY_PID="$(port_pid "$AE_PORT" || true)"
  echo " [错误] 端口 ${AE_PORT} 已被占用，服务无法启动"
  if [ -n "$BUSY_PID" ]; then
    echo "        占用者：$(ps -p "$BUSY_PID" -o comm= 2>/dev/null || echo 未知进程) (PID ${BUSY_PID})"
  fi
  echo
  echo " 两种解决办法，任选其一："
  echo
  echo "   A. 换个端口启动"
  echo "        AE_PORT=9000 ./run.sh"
  echo
  if [ -n "$BUSY_PID" ]; then
    echo "   B. 结束占用端口的进程"
    echo "        kill ${BUSY_PID}"
  else
    echo "   B. 找出并结束占用端口的进程"
    echo "        lsof -iTCP:${AE_PORT} -sTCP:LISTEN     # 或  ss -lptn 'sport = :${AE_PORT}'"
  fi
  echo
  echo "  提示：如果那是上一次没关干净的服务窗口，直接关掉它即可。"
  exit 1
fi

echo " [4/4] 启动服务  http://${AE_HOST}:${AE_PORT}"
echo
echo " 按 Ctrl+C 停止"
echo

# 轮询健康检查，服务真的答上了才开浏览器（失败就不开，免得指向别人的服务）
(
  URL="http://${AE_HOST}:${AE_PORT}"
  for _ in $(seq 1 40); do
    if command -v curl >/dev/null 2>&1; then
      code="$(curl -s -o /dev/null -m 1 -w '%{http_code}' "${URL}/api/health" 2>/dev/null || echo 000)"
    else
      code="$($PY - "$URL" <<'EOF' 2>/dev/null || echo 000
import sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1] + "/api/health", timeout=1) as r:
        print(r.status)
except Exception:
    print(0)
EOF
)"
    fi
    if [ "$code" = "200" ]; then
      if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
      elif command -v open     >/dev/null 2>&1; then open "$URL"     >/dev/null 2>&1 || true
      fi
      break
    fi
    sleep 0.4
  done
) &

exec $PY -m backend.app
