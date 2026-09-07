#!/bin/bash

# 启动后端服务
echo "启动后端服务..."
cd packages/backend
PYTHONPATH=src python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!
cd ../..

# 启动前端服务
echo "启动前端服务..."
cd packages/frontend
npm run dev -- --port 3000 &
FRONTEND_PID=$!
cd ../..

echo "服务启动完成"
echo "前端: http://localhost:3000"
echo "后端: http://localhost:8000"

# 等待用户中断
trap "kill $BACKEND_PID $FRONTEND_PID" EXIT
wait
