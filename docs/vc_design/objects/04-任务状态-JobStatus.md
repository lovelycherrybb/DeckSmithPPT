# 任务状态（JobStatus）

## 描述
任务状态的枚举类型，定义了 PPT 生成任务生命周期中所有可能的状态。

## 枚举值
- `running`: 运行中，任务正在生成。
- `draft`: 草稿就绪待确认，用户可查看/修改草稿。
- `timeout`: 超时，任务未能在规定时间内完成。
- `failed`: 失败，任务生成过程中出现错误。
- `completed`: 完成，PPT 文件已生成。

## 来源
- openapi.yaml: `components/schemas/JobStatus`
- engine.schema.json: `$defs/JobStatus`

## 备注
状态流转规则：
1. 提交任务后进入 `running` 或 `draft`（取决于引擎是否支持草稿）。
2. `draft` 状态下，用户可保存草稿（PATCH /api/jobs/{job_id}/draft）或确认生成（POST /api/jobs/{job_id}/confirm）。
3. 确认后进入 `running`。
4. `running` 状态可转为 `completed`、`failed` 或 `timeout`。
5. 终态（completed/failed/timeout）不再变化。
