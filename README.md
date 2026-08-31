# DeckSmithPPT 2.0

内容 → 原生可编辑 PPTX 的生成工具。核心设计：**引擎可插拔 + 契约先行**。

## 目录导航

- `docs/research/` — 研究过程、实验笔记（草稿纸，可乱可过时）
- `docs/adr/` — 架构决策记录（选型定稿）
- `docs/contracts/` — 前后端/引擎契约（唯一事实来源，语言无关）
- `packages/engine/` — PPT 生成引擎（抽象层 + 各实现）
- `packages/backend/` — 后端服务（技术栈待定）
- `packages/frontend/` — 前端应用（技术栈待定）
- `packages/shared/` — 共享产物（仅代码生成，禁止手写）
- `lib/ppt-master/` — 上游依赖（只读，git submodule）
- `tests/` — 跨包测试（e2e / contract）

## 快速开始

（技术栈选型后补充，见 `docs/adr/`）

## 当前状态

- [x] 仓库骨架
- [ ] 技术栈选型
- [ ] 契约定义完善
- [ ] 引擎接入（ppt-master）
- [ ] 前后端实现
