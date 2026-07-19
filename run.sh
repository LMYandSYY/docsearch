#!/usr/bin/env bash
# Mac / Linux 启动脚本：首次运行自动建虚拟环境并装依赖。
set -e
export PYTHONUTF8=1
cd "$(dirname "$0")"

# 精确检测 venv 可用：有可执行权限，且解释器能真正运行
# （避免从别的机器/路径拷过来的 .venv：文件在、有 x 权限，但 shebang 指向失效路径）
if [ ! -x ".venv/bin/python" ] || ! .venv/bin/python -c 'import sys' >/dev/null 2>&1; then
  echo "首次运行：创建虚拟环境并安装依赖（仅一次）…"
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

exec .venv/bin/python app.py
