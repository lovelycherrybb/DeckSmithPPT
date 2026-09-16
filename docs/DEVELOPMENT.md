# DeckSmithPPT 2.0 开发指南

## 快速开始

### 1. 启动开发环境

```bash
./scripts/dev.sh          # 一条命令起后端 + 前端
# 端口取自仓库根 .env（BACKEND_PORT / FRONTEND_PORT），未设时用脚本默认值
```

### 2. 访问应用（`scripts/dev.sh` 的实际端口）

- **前端**：http://localhost:3180 （`FRONTEND_PORT` 默认 3180）
- **后端 API**：http://localhost:8180 （`BACKEND_PORT` 默认 8180，Swagger 在 `/docs`）
- **API 代理**：`3180/api/*` → `127.0.0.1:$BACKEND_PORT/api/*`

⚠️ 代理目标**取决于你怎么起的前端**：`next.config.mjs` 里 `BACKEND_PORT` 的兜底默认是
**8080**，而 `dev.sh` 显式传的是 **8180**。手工 `npm run dev -- --port 3180` 起的前端会打到
8080，`dev.sh` 起的打到 8180 —— 两者若同时在跑，界面看起来“一切正常但任务列表不一致”，
其实是接了不同后端、读了不同库。起服务前先 `lsof -nP -iTCP -sTCP:LISTEN | grep -E '3180|8080|8180'`
看一眼，别凭记忆。

### 3. 监听地址与“对外可达”的正确测法

先记一条方法论：**别在 mac 上用本机局域网 IP 自连来判断对外可达**（hairpin 路径会被防火墙按
另一套规则处理）。同一条 `curl http://<本机IP>:3180/` 先后给出过 200 与超时，据此下结论必错。
正确做法是从同网段的**另一台机器**打，例如验证机：

```bash
ssh -i ~/.ssh/<验证机密钥文件名> <用户名>@<验证机IP> \
  'curl -s -m 5 -o /dev/null -w "%{http_code}\n" http://<mac的局域网IP>:3180/'
```

实测结论（2026-09-16，从虚拟机打 mac）：

- **容器 web（`:3000`）对外可达 → HTTP 200**。compose 里 `"${WEB_PORT:-3000}:3000"` 不加地址前缀时，
  Docker 默认就发布在 `0.0.0.0`，无需改成 `0.0.0.0:3000:3000`。
- **dev 前端（`:3180`）从外部连不上**：进程确实监听 `*:3180`，`/opt/homebrew/Cellar/node/…/bin/node`
  也在防火墙白名单里，但入站仍被丢 —— 头号嫌疑是 macOS 对**新启动进程**的入站审批未通过（防火墙
  已启用、block-all 关、自动允许已签名软件开）。此项未结案，别当成“配置写错了”。

dev 的后端仍刻意只绑 `127.0.0.1`：`/api/*` 的 rewrite 由 Next 在**服务端**发起，外部设备只要能打开
前端页面，API 就通 —— 不需要把后端暴露出去。`dev.sh` 里也显式写了前端 `-H 0.0.0.0`，不靠上游默认值。

### 环境变量补充

- **模型凭证不由后端代码读取。** 实测：`packages/backend/src` 与 `packages/engine` 里没有任何一处读
  `ANTHROPIC_*`；生成动作是拉起 agent CLI 子进程完成的，凭证由该 CLI 按自身规则加载
  （本机即 `~/.claude/settings.json` 的 `env` 块）。所以 dev 形态能否出片取决于**主机上的 CLI 配置**，
  不需要往仓库根 `.env` 里塞密钥；容器形态则由 compose 的 `env_file: deploy/.env` 把变量注入容器，
  与主机 CLI 配置互不影响。不同 token、不同 CLI 的接入方式各异，由使用者自行配置，此处不规定写法。
- 仓库根 `.env` 供 `config._load_dotenv()` 读取，只放运行参数（`BACKEND_PORT`、`FRONTEND_PORT`、
  `DECKSMITH_JOBS_ROOT`、`DATABASE_URL` 等）。

## 开发功能特性

### ✅ 热重载 (Hot Reload)

#### 前端热重载
- **Next.js Fast Refresh**: 修改React组件后，浏览器会自动刷新
- **样式热更新**: 修改CSS/Tailwind样式时，页面会立即更新
- **状态保持**: 修改组件时，React状态会保持不变

#### 后端热重载

**没有**。`scripts/dev.sh` 起的命令是 `python3 -m uvicorn app.main:app --host 127.0.0.1 --port …`，
**不带 `--reload`**：改了 `packages/backend/src/` 下的 Python 代码，进程不会重启；而且模块 import 有缓存，
必须手动杀掉重起才生效。历史上因此误判过“代码改了没反应”。要自动重启请自行加 `--reload`，
但注意它会把引擎拉起的子进程一并卷入重启，跑真出片时别开。

### ✅ API代理

前端通过相对路径访问后端，无需跨域配置：

```javascript
const response = await fetch('/api/jobs');   // 代理到 http://127.0.0.1:$BACKEND_PORT/api/jobs
```

目标端口由 `BACKEND_PORT` 决定（见上文警告：兜底默认 8080 与 `dev.sh` 的 8180 不一致）。

### ✅ 开发环境配置

#### 环境变量

- `BACKEND_HOST` / `BACKEND_PORT`：前端 rewrite 的代理目标（仅构建期与启动期求值，改完必须重启前端）
- `BACKEND_PORT` / `FRONTEND_PORT`：`dev.sh` 起服务时用的端口
- `DECKSMITH_JOBS_ROOT` / `DATABASE_URL`：后端产物与库的落点，未设时为
  `outputs/jobs` 与 `outputs/decksmith.db`

#### 脚本实际做了什么（也仅这些）

`dev.sh` 只做四件事：读根 `.env` → 起后端 → 起前端 → 退出时 `trap` 杀掉这两个子进程。
**没有**端口冲突检测、没有自动释放端口、没有健康检查、不会自动打开浏览器。

## 开发工作流

### 前端开发

1. 修改 `packages/frontend/src/` 下的文件
2. 保存后浏览器自动刷新
3. 查看效果并调试

### 后端开发

1. 修改 `packages/backend/src/` 下的文件
2. **手动**重启后端（`dev.sh` 起的进程 Ctrl-C 整组重启；单独起的按 PID kill 后重跑）
3. 用 `curl http://127.0.0.1:<后端端口>/docs` 确认新路由已注册

### 全栈开发

1. `./scripts/dev.sh` 起两端
2. 前端改动 → 浏览器自动刷新（Fast Refresh）
3. 后端改动 → **不会**自动重启，需自己重起后端进程

## 调试技巧

### 前端调试
- 使用浏览器开发者工具
- React Developer Tools 扩展
- 控制台查看网络请求（尤其看 `_next/static/chunks/*` 有没有 404——见下文“构建冲突”一节）

### 后端调试
- 查看终端日志输出
- 使用 curl 测试 API
- Python 调试器（如 pdb）

### API调试
- 访问 http://localhost:8180/docs 查看 Swagger 文档（端口以你实际启动值为准）

## 常见问题

### Q: 端口被占用怎么办？
A: 脚本**不会**自动检测或释放端口，自己确认再决定杀谁：

```bash
lsof -nP -iTCP -sTCP:LISTEN | grep -E ':(3180|8080|8180|3000)'
```

3000 是容器（`deploy/`）那套的映射端口，别顺手杀掉——它和本机 dev 是两码事。

### Q: 页面只显示“加载中...”、控制台却没有任何报错？
A: 先按下一节排查 `.next` 被生产构建踩坏的情况；其次确认前端代理指向的后端真的在跑（`curl 127.0.0.1:<后端>/api/engines`）。

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

⚠️ **跑着 `next dev` 时不要 `npm run build`**，原因见下一节。确实要验构建产物，就先停 dev，
或给 `next.config.mjs` 临时指定另一个 `distDir` 输出到别处。

## 本机三套服务与数据落点

同一份代码在这台机器上可以同时存在三套互不相干的运行实例，**各有各的库和产物目录**，
串了台就会出现“我刚才的任务怎么不见了”：

| 入口 | 是什么 | 数据落点 |
|---|---|---|
| `http://localhost:3180` | 主机 `next dev` 前端，代理到主机的 uvicorn（8080 或 8180，取决于怎么起的） | 仓库内 `outputs/jobs` 与 `outputs/decksmith.db` |
| `http://localhost:3000` | OrbStack 里的 compose 栈（`decksmith/web` + `decksmith/app`，版本由 `deploy/.env` 的 `IMAGE_TAG` 决定） | `deploy/data/{db,jobs,home,decks}` |
| `http://<验证机IP>:3000` | arm64 验证机上的同一套 compose 栈（Debian 12，独立 Linux、独立 dockerd） | 那台机器上自己的 `~/delivery/deploy/data` |

两条容易踩的口径：

1. **镜像不跨机器同步**。本机 `docker images` 里那几张 `decksmith/*` 只存在于 OrbStack 的虚拟磁盘；
   验证机上的同名镜像是 `docker load` 交付包 tar 进去的独立副本。
2. **`dist/` 与 `deploy/data/` 都不入库**（`.gitignore`），所以“仓库里看不到镜像和产物”是设计如此。

## 已知陷阱：`next build` 把在跑的 `next dev` 踩坏

2026-09-16 实测。dev 进程 9-14 18:36 起，9-15 11:00 有人跑过一次 `next build`（`.next/BUILD_ID`
的落盘时间就是它），此后那个仍在跑的 dev 服务打不开任何页面。

**症状**：页面永久停在首屏那句「加载中...」，浏览器标签一直在转圈，**控制台没有 JS 报错**，
后端日志里一条浏览器请求都没有。

**根因链**：生产构建把 `.next` 整棵目录按生产产物改写，dev 模式要现发的客户端入口就不存在了，
dev 服务对它们返回 404；而 404 正文的 MIME 是 `text/plain`，Chrome 严格 MIME 检查**直接拒绝执行脚本**
（`Refused to execute script … main-app.js`），React 因此从不挂载——留在页面上的那句「加载中...」
其实是服务端渲染的懒加载兜底，不是卡在网络或数据上。

**一句话自查**（任一条中即是本坑，不是后端问题）：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3180/_next/static/chunks/main-app.js
#   404 → 就是它
ls packages/frontend/.next/BUILD_ID      #   存在 = 有人在这里跑过生产构建
```

或打开浏览器 Network 面板，看 `_next/static/chunks/main-app.js`、`app/layout.js`、
`app-pages-internals.js`、`css/app/layout.css` 是否集体 404。

**修复**（三步，顺序不能换；别用 `rm`，`.next` 挪废纸篓即可，反正能重建）：

```bash
pgrep -fl "next dev --port 3180"          # 先确认要杀的 PID
kill <dev PID> <next-server PID>
mv packages/frontend/.next ~/.Trash/fe-next-stale-$(date +%s)
cd packages/frontend && npm run dev -- --port 3180
```

只重启不清 `.next` 可能残留症状：dev 会沿用被生产构建改写过的产物清单。

**同一族的历史案例**：两个 `next dev` 实例共用一棵 `.next` 会互毁缓存，表现为灵异 500 / ECONNRESET。
起 dev 前先 `pgrep -fl "next dev"` 确认只有一个。

**另一条相邻的坑**：重启 dev 之后，**旧标签页**的 HMR 连接与资源指纹都已失效，仍会显示改动前的界面。
复验必须新开标签或硬刷新，别拿旧标签的观感当结论。

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
