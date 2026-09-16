#!/usr/bin/env bash
# 本地一键启动（后端 + 前端）
# 端口从项目根 .env 读取，不在这里硬编码
set -euo pipefail

# 脚本现在位于 scripts/，仓库根目录需向上一级解析
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# 加载 .env
ENV_FILE="$ROOT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

BACKEND_PORT="${BACKEND_PORT:-8180}"
FRONTEND_PORT="${FRONTEND_PORT:-3180}"

# 启动后端：**刻意只绑回环**。外部设备访问 API 走前端代理（rewrite 由 next 在服务端发起，
# 它打本机的 127.0.0.1 没问题），把后端暴露到局域网只会多一个无人看管的端口。
echo "启动后端服务 (127.0.0.1:$BACKEND_PORT)..."
cd "$ROOT_DIR/packages/backend"
BACKEND_PORT=$BACKEND_PORT PYTHONPATH=src python3 -m uvicorn app.main:app \
  --host 127.0.0.1 --port "$BACKEND_PORT" &
BACKEND_PID=$!
cd "$ROOT_DIR"

# 启动前端（BACKEND_PORT 传给 next.config.mjs 的 rewrite）。
# -H 0.0.0.0 是显式声明“开发页面要能从同网段其他机器打开”，别靠 next 的默认值维持这个行为。
echo "启动前端服务 (0.0.0.0:$FRONTEND_PORT)..."
cd "$ROOT_DIR/packages/frontend"
BACKEND_PORT=$BACKEND_PORT npm run dev -- -H 0.0.0.0 --port "$FRONTEND_PORT" &
FRONTEND_PID=$!
cd "$ROOT_DIR"

echo ""
echo "服务启动完成"
echo "  后端: http://127.0.0.1:$BACKEND_PORT  （仅本机；Swagger 在 /docs）"
echo "  前端: http://localhost:$FRONTEND_PORT"
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | cut -d' ' -f1 || true)"
[ -n "$LAN_IP" ] && echo "  同一局域网内其他机器可打开: http://$LAN_IP:$FRONTEND_PORT"
echo "  外部设备访问 /api 由前端代理转发到本机后端，无需把后端暴露出去。"
echo ""

# 等待用户中断
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" EXIT INT TERM
wait
