# 生成任务（Job）

## 描述
PPT 生成任务的核心实体，代表一个从提交到完成的完整生命周期。包含任务 ID、状态、结果路径、草稿内容等信息。

## 属性
- `job_id`: 任务唯一 ID（字符串，必填）。
- `status`: 任务状态（JobStatus 枚举，必填）。
- `title`: PPT 标题（字符串，可选），可从源材料提取或由 AI 自动生成。
- `pages`: 目标页数（整数，可选）。
- `engine`: 选择的引擎（字符串）。
- `agent`: 驱动引擎的 AI agent（字符串）。
- `content_style`: 内容风格（字符串）。
- `engine_params`: 提交/确认阶段使用的引擎参数（对象）。
- `result_path`: 生成的 PPTX 文件路径（字符串，completed 后非空）。
- `draft_path`: 草稿 Markdown 文本路径（字符串，draft 状态时有值）。
- `error`: 失败/超时原因（字符串，仅 failed/timeout 时有值）。
- `created_at`: 任务创建时间（ISO 8601 字符串）。
- `updated_at`: 任务最后更新时间（ISO 8601 字符串）。
- `draft_content`: 草稿 Markdown 内容（字符串，draft 状态时返回，供前端查看/编辑）。

## 来源
- openapi.yaml: `components/schemas/Job`
- engine.schema.json: `$defs/Job`

## 备注
Job 是领域模型的核心聚合根，管理任务的状态流转：提交 → 草稿(draft) → 确认 → 正式生成(running) → completed/failed/timeout。前端通过轮询或 WebSocket 获取状态更新。
