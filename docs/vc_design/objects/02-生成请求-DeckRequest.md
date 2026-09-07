# 生成请求（DeckRequest）

## 描述
提交 PPT 生成任务的请求对象，包含标题、内容、引擎选择、参数等。这是前端向后端提交生成任务时使用的核心数据结构。

## 属性
- `title`: PPT 标题（字符串，可选），可从源材料提取或由 AI 自动生成。
- `content`: Markdown 内容（字符串，可选），草稿确认后必传；直出模式可空（用 source）。
- `content_style`: 内容风格（字符串，默认 "marketing_promo"），用于草稿重写和内容表达。
- `pages`: 目标页数（整数，可选，最小值1），可由 AI 决定或使用默认值。
- `agent`: 驱动引擎的 AI agent（枚举：claude, codex, hermes；默认 "claude"）。
- `engine`: 选择哪个引擎（字符串，必填），对应 GET /api/engines 返回的 name。
- `source`: 原始素材来源（对象，可选），优先于 content。
- `engine_params`: 引擎特定参数（对象，可选），与 GET /api/engines 返回的 params_schema 对齐。

## 来源
- openapi.yaml: `components/schemas/DeckRequest`
- engine.schema.json: `$defs/DeckContent`（通用层，与 DeckRequest 一致）

## 备注
此对象是业务流程的起点：用户通过前端提交 DeckRequest，后端创建 Job 并返回。支持两种模式：直出模式（提供 source）和草稿模式（提供 content）。
