# DeckSmithPPT 2.0 部署指南

## 环境要求

- Node.js 18+
- Python 3.10+
- npm 或 yarn

## 快速启动

### 1. 安装依赖

```bash
# 安装前端依赖
cd packages/frontend
npm install

# 安装后端依赖
cd ../backend
pip install -r requirements.txt
```

### 2. 启动开发服务器

```bash
# 使用启动脚本
./start-dev.sh

# 或者手动启动

# 启动后端
cd packages/backend
PYTHONPATH=src python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 启动前端（新终端）
cd packages/frontend
npm run dev -- --port 3000
```

### 3. 访问应用

- 前端: http://localhost:3000
- 后端 API: http://localhost:8000
- API 文档: http://localhost:8000/docs

## 端口权限问题

如果遇到端口权限问题（`EPERM: operation not permitted`），可以尝试以下解决方案：

### 方案 1: 使用高位端口

```bash
# 使用 1024 以上的端口
npm run dev -- --port 3000
python3 -m uvicorn app.main:app --port 8000
```

### 方案 2: 修改系统权限（macOS）

```bash
# 允许非管理员用户使用低位端口
sudo sysctl net.inet.ip.portrange.reservedhigh=0
```

### 方案 3: 使用管理员权限

```bash
# 使用 sudo 启动服务
sudo npm run dev -- --port 3000
sudo python3 -m uvicorn app.main:app --port 8000
```

### 方案 4: 使用 Docker

```dockerfile
# Dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]
```

```bash
docker build -t decksmith-frontend .
docker run -p 3000:3000 decksmith-frontend
```

## 生产环境部署

### 1. 构建前端

```bash
cd packages/frontend
npm run build
```

### 2. 启动生产服务器

```bash
cd packages/frontend
npm start
```

### 3. 配置 Nginx（可选）

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }

    location /api {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }
}
```

## 环境变量

### 前端环境变量

创建 `packages/frontend/.env.local` 文件：

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

### 后端环境变量

创建 `packages/backend/.env` 文件：

```env
APP_ENV=development
DATABASE_URL=sqlite:///./decksmith.db
ENGINE=ppt_master
DECKSMITH_JOBS_ROOT=./outputs/jobs
```

## 故障排除

### 1. 端口被占用

```bash
# 查看端口占用
lsof -i :3000
lsof -i :8000

# 杀死占用进程
kill -9 <PID>
```

### 2. 依赖安装失败

```bash
# 清除缓存
npm cache clean --force
rm -rf node_modules package-lock.json
npm install
```

### 3. 数据库问题

```bash
# 删除数据库文件
rm -f outputs/decksmith.db

# 重新启动服务
```

## 监控和日志

### 前端日志

```bash
# 查看 Next.js 日志
npm run dev -- --verbose
```

### 后端日志

```bash
# 启用详细日志
python3 -m uvicorn app.main:app --log-level debug
```

## 性能优化

### 1. 启用压缩

```bash
# 在 next.config.js 中启用压缩
module.exports = {
  compress: true,
}
```

### 2. 使用 CDN

```bash
# 配置静态资源 CDN
NEXT_PUBLIC_CDN_URL=https://cdn.example.com
```

### 3. 数据库优化

```bash
# 使用 PostgreSQL 替代 SQLite
DATABASE_URL=postgresql://user:password@localhost/decksmith
```

## 安全建议

1. **使用 HTTPS**: 在生产环境中使用 SSL 证书
2. **环境变量**: 不要将敏感信息提交到代码库
3. **输入验证**: 确保所有用户输入都经过验证
4. **CORS 配置**: 限制允许的来源
5. **速率限制**: 实施 API 速率限制

## 更新和维护

### 更新依赖

```bash
# 更新前端依赖
cd packages/frontend
npm update

# 更新后端依赖
cd ../backend
pip install --upgrade -r requirements.txt
```

### 备份数据

```bash
# 备份数据库
cp outputs/decksmith.db outputs/decksmith.db.backup

# 备份生成文件
tar -czf outputs-backup.tar.gz outputs/
```

## 支持

如有问题，请查看：
1. 项目 README.md
2. API 文档: http://localhost:8000/docs
3. 设计文档: designs/decksmith-redesign/README.md
