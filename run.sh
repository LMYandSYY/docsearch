#!/usr/bin/env bash
# Mac / Linux 启动脚本：首次运行自动建虚拟环境并装依赖。
set -e
export PYTHONUTF8=1
cd "$(dirname "$0")"

# 精确检测 venv 可用（避免从 Windows 拷过来的 .venv 误判）
if [ ! -x ".venv/bin/python" ]; then
  echo "首次运行：创建虚拟环境并安装依赖（仅一次）…"
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

exec .venv/bin/python app.py
