# DeckSmithPPT 2.0

内容 → 原生可编辑 PPTX 的生成工具。核心设计：**引擎可插拔 + 契约先行**。

## 目录导航

- `docs/research/` — 研究过程、实验笔记（草稿纸，可乱可过时）
- `docs/adr/` — 架构决策记录（选型定稿）
- `docs/contracts/` — 前后端/引擎契约（唯一事实来源，语言无关）
- `docs/vc_design/` — VC设计文档（对象/状态/接口/流程/约束）
- `packages/engine/` — PPT 生成引擎（抽象层 + 各实现）
- `packages/backend/` — 后端服务（FastAPI + SQLAlchemy）
- `packages/frontend/` — 前端应用（Next.js + React + Ant Design）
- `packages/shared/` — 共享产物（仅代码生成，禁止手写）
- `lib/ppt-master/` — 上游依赖（只读，git submodule）
- `tests/` — 跨包测试（e2e / contract）

## 技术栈

- **后端**：Python + FastAPI + SQLAlchemy + SQLite
- **前端**：Next.js (App Router) + React + TypeScript + Ant Design
- **引擎**：可插拔架构，支持 ppt-master 等多种实现
- **契约**：OpenAPI 3.1 + JSON Schema

## 快速开始

```bash
# 启动后端
cd packages/backend
source ../../.venv/bin/activate
PYTHONPATH=src python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8080

# 启动前端（新终端）
cd packages/frontend
npm run dev -- --port 3080
```

访问：
- 前端：http://localhost:3080
- 后端 API：http://127.0.0.1:8080
- API 文档：http://127.0.0.1:8080/docs

## 当前状态

- [x] 仓库骨架
- [x] 技术栈选型（ADR-003）
- [x] 契约定义完善（openapi.yaml + engine.schema.json）
- [x] 引擎抽象层（PptEngine 协议）
- [x] 后端基础实现（FastAPI + 路由 + 数据库初始化）
- [x] 前端基础实现（Next.js + 页面结构）
- [ ] 引擎具体实现（ppt-master 接入）
- [ ] 前端完整页面
- [ ] e2e 测试

## 架构决策

详见 `docs/adr/`：
- ADR-001：引擎可插拔架构
- ADR-002：Monorepo 结构
- ADR-003：技术栈选型（FastAPI + Next.js + SQLAlchemy/SQLite）
