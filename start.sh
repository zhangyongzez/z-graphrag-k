#!/usr/bin/env bash
# =============================================================================
# z-graphrag-k 一键启动（Linux / macOS）
# 用法：./start.sh [--port 8000] [--no-browser] [--reload]
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  echo "[kgrag] 首次运行，创建虚拟环境..."
  python3 -m venv .venv
  PY=".venv/bin/python"
  echo "[kgrag] 安装依赖（可能需要几分钟）..."
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -e .
else
  echo "[错误] 未找到 python3，请先安装 Python 3.11+"
  exit 1
fi

exec "$PY" start.py "$@"
