#!/usr/bin/env bash
# 本地一键启动（后端 + 前端）
# 端口从项目根 .env 读取，不在这里硬编码
set -euo pipefail

# 加载 .env
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

BACKEND_PORT="${BACKEND_PORT:-8180}"
FRONTEND_PORT="${FRONTEND_PORT:-3180}"

# 启动后端
echo "启动后端服务 (端口 $BACKEND_PORT)..."
cd "$SCRIPT_DIR/packages/backend"
BACKEND_PORT=$BACKEND_PORT PYTHONPATH=src python3 -m uvicorn app.main:app \
  --host 127.0.0.1 --port "$BACKEND_PORT" &
BACKEND_PID=$!
cd "$SCRIPT_DIR"

# 启动前端（BACKEND_PORT 传给 next.config.mjs 的 rewrite）
echo "启动前端服务 (端口 $FRONTEND_PORT)..."
cd "$SCRIPT_DIR/packages/frontend"
BACKEND_PORT=$BACKEND_PORT npm run dev -- --port "$FRONTEND_PORT" &
FRONTEND_PID=$!
cd "$SCRIPT_DIR"

echo ""
echo "服务启动完成"
echo "  前端: http://localhost:$FRONTEND_PORT"
echo "  后端: http://localhost:$BACKEND_PORT"
echo ""

# 等待用户中断
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" EXIT INT TERM
wait
