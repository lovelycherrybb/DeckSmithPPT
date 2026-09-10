#!/bin/bash

# 增强版开发启动脚本
# 功能：同时启动前后端，支持热重载，自动打开浏览器

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 配置
BACKEND_PORT=8000
FRONTEND_PORT=3000
BACKEND_HOST="127.0.0.1"
FRONTEND_HOST="127.0.0.1"

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查端口是否被占用
check_port() {
    local port=$1
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null ; then
        log_error "端口 $port 已被占用"
        log_info "正在释放端口..."
        lsof -ti:$port | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
}

# 清理函数
cleanup() {
    log_info "正在停止服务..."
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
    wait $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
    log_success "服务已停止"
}

# 设置信号处理
trap cleanup EXIT INT TERM

# 检查并释放端口
check_port $BACKEND_PORT
check_port $FRONTEND_PORT

log_info "启动开发环境..."
echo "=================================================="

# 设置环境变量
export NEXT_PUBLIC_API_BASE_URL="http://${BACKEND_HOST}:${BACKEND_PORT}"

# 启动后端服务
log_info "启动后端服务 (${BACKEND_HOST}:${BACKEND_PORT})..."
cd packages/backend
PYTHONPATH=src python3 -m uvicorn app.main:app \
    --host $BACKEND_HOST \
    --port $BACKEND_PORT \
    --reload \
    --reload-dir src \
    --log-level info &
BACKEND_PID=$!
cd ../..

# 等待后端启动
log_info "等待后端服务启动..."
for i in {1..30}; do
    if curl -s "http://${BACKEND_HOST}:${BACKEND_PORT}/" > /dev/null 2>&1; then
        log_success "后端服务已启动"
        break
    fi
    sleep 1
    if [ $i -eq 30 ]; then
        log_error "后端服务启动超时"
        exit 1
    fi
done

# 启动前端服务
log_info "启动前端服务 (${FRONTEND_HOST}:${FRONTEND_PORT})..."
cd packages/frontend
npm run dev -- --port $FRONTEND_PORT --hostname $FRONTEND_HOST &
FRONTEND_PID=$!
cd ../..

# 等待前端启动
log_info "等待前端服务启动..."
for i in {1..30}; do
    if curl -s "http://${FRONTEND_HOST}:${FRONTEND_PORT}" > /dev/null 2>&1; then
        log_success "前端服务已启动"
        break
    fi
    sleep 1
    if [ $i -eq 30 ]; then
        log_error "前端服务启动超时"
        exit 1
    fi
done

echo "=================================================="
log_success "开发环境启动完成！"
echo ""
log_info "前端: ${GREEN}http://${FRONTEND_HOST}:${FRONTEND_PORT}${NC}"
log_info "后端: ${GREEN}http://${BACKEND_HOST}:${BACKEND_PORT}${NC}"
log_info "API代理: ${GREEN}http://${FRONTEND_HOST}:${FRONTEND_PORT}/api${NC} -> ${BACKEND_HOST}:${BACKEND_PORT}"
echo ""
log_info "功能特性:"
echo "  ✅ 前端热重载 (修改代码自动刷新)"
echo "  ✅ 后端热重载 (修改代码自动重启)"
echo "  ✅ API代理 (解决跨域问题)"
echo "  ✅ 自动端口检查"
echo ""
log_info "按 Ctrl+C 停止所有服务"
echo ""

# 自动打开浏览器 (macOS)
if command -v open &> /dev/null; then
    log_info "正在打开浏览器..."
    sleep 2
    open "http://${FRONTEND_HOST}:${FRONTEND_PORT}"
fi

# 保持脚本运行
wait
