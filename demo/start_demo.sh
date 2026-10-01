#!/usr/bin/env bash
# AudioEdition UI 演示版 —— 只需要 Python，不需要 ffmpeg
set -e
cd "$(dirname "$0")"
exec python3 serve.py "$@"
