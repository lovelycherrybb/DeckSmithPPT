# DeckSmithPPT 2.0 开发指南

## 快速开始

### 1. 启动开发环境

```bash
# 启动开发环境（后端 + 前端，端口读取根目录 .env）
./scripts/dev.sh
```

### 2. 访问应用

- **前端**: http://localhost:3000
- **后端API**: http://localhost:8000
- **API代理**: http://localhost:3000/api → http://localhost:8000/api

## 开发功能特性

### ✅ 热重载 (Hot Reload)

#### 前端热重载
- **Next.js Fast Refresh**: 修改React组件后，浏览器会自动刷新
- **样式热更新**: 修改CSS/Tailwind样式时，页面会立即更新
- **状态保持**: 修改组件时，React状态会保持不变

#### 后端热重载
- **uvicorn --reload**: 修改Python代码后，服务器会自动重启
- **文件监听**: 监听 `packages/backend/src/` 目录下的所有 `.py` 文件

### ✅ API代理

项目配置了API代理，前端可以通过相对路径访问后端API：

```javascript
// 前端代码示例
const response = await fetch('/api/jobs');  // 自动代理到 http://localhost:8000/api/jobs
```

无需配置跨域，无需知道后端端口。

### ✅ 开发环境配置

#### 环境变量
- `NEXT_PUBLIC_API_BASE_URL`: API基础URL（开发环境已配置为相对路径）
- `NODE_ENV`: 当前环境（development/production）

#### 自动化功能
- 端口冲突检测与自动释放
- 服务健康检查
- 自动打开浏览器（macOS）

## 开发工作流

### 前端开发

1. 修改 `packages/frontend/src/` 下的文件
2. 保存后浏览器自动刷新
3. 查看效果并调试

### 后端开发

1. 修改 `packages/backend/src/` 下的文件
2. 服务器自动重启
3. 测试API端点

### 全栈开发

1. 启动开发环境：`./scripts/dev.sh`
2. 前端代码修改 → 浏览器自动刷新
3. 后端代码修改 → 服务器自动重启
4. 无需手动重启任何服务

## 调试技巧

### 前端调试
- 使用浏览器开发者工具
- React Developer Tools 扩展
- 控制台查看网络请求

### 后端调试
- 查看终端日志输出
- 使用Postman或curl测试API
- Python调试器（如pdb）

### API调试
- 访问 http://localhost:8000/docs 查看Swagger文档
- 使用浏览器Network面板查看请求

## 常见问题

### Q: 端口被占用怎么办？
A: 脚本会自动检测并释放端口。如果仍有问题，手动运行：
```bash
lsof -ti:3000 | xargs kill -9
lsof -ti:8000 | xargs kill -9
```

### Q: 修改代码后没有效果？
A: 检查：
1. 文件保存是否成功
2. 控制台是否有错误
3. 是否修改了正确的文件

### Q: API请求失败？
A: 检查：
1. 后端服务是否运行
2. API路径是否正确
3. 网络面板查看具体错误

## 性能优化

### 开发环境优化
- 启用文件监听轮询（next.config.mjs）
- 使用相对路径API调用
- 禁用不必要的开发指示器

### 生产环境构建
```bash
# 构建生产版本
cd packages/frontend
npm run build

# 启动生产服务器
npm start
```

## 工具推荐

### VS Code扩展
- ES7+ React/Redux/React-Native snippets
- Tailwind CSS IntelliSense
- Python
- Pylance

### 浏览器扩展
- React Developer Tools
- Redux DevTools
- JSON Formatter

## 下一步

- 查看 `docs/contracts/` 了解API规范
- 阅读 `docs/adr/` 了解架构决策
- 查看 `README.md` 了解项目概述
