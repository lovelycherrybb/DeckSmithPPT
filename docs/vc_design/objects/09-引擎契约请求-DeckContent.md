# 引擎契约请求（DeckContent）

## 描述
引擎契约（engine.schema.json）中定义的通用层请求对象，与 openapi.yaml 中的 DeckRequest 保持一致。这是提交给引擎的生成请求。

## 属性
- `title`: PPT 标题（字符串，可选）。
- `content`: Markdown 内容（字符串，可选）。
- `pages`: 目标页数（整数，可选）。
- `engine`: 选择哪个引擎（字符串，必填）。
- `agent`: 驱动引擎的 AI agent（枚举：claude, codex, hermes；默认 "claude"）。
- `content_style`: 内容风格（字符串，默认 "marketing_promo"）。
- `source`: 原始素材来源（对象，可选）。
- `engine_params`: 引擎特定参数（对象，可选）。

## 来源
- engine.schema.json: `$defs/DeckContent`

## 备注
此对象与 DeckRequest 本质相同，但位于引擎契约层。引擎实现者应参考此定义来确保与通用层的兼容性。engine_params 的具体结构由各引擎的 params_schema 定义。
