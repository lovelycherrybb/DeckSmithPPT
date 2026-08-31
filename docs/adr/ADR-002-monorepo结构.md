# ADR-002: Monorepo 结构（packages/）

- 状态: 已接受
- 日期: 2026-08-25
- 决策者: 龚成

## 背景

技术栈未定（后端候选 Java/Python/Go，前端候选 Next.js）。
公司标准目录（01-验证/02-设计/03-开发...）偏向文档型项目，对多语言软件仓库不适用。

## 决策

1. 采用 `packages/` monorepo：engine / backend / frontend / shared。
2. 契约先行：`docs/contracts/`（openapi.yaml + *.schema.json）语言无关，
   各端类型由代码生成，禁止手写共享类型。
3. 测试分层：单测在包内，tests/e2e + tests/contract 在根。
4. 公司标准结构映射：验证→docs/research，设计→docs/adr + docs/contracts，开发→packages。

## 后果

- 正面：契约可并行开发；换后端语言不影响契约；单测隔离。
- 代价：多包构建需要统一脚本（scripts/）；初期目录比单仓复杂。
