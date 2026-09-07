# 保存草稿请求（SaveDraftRequest）

## 描述
用户在草稿状态下编辑 Markdown 内容后，保存草稿的请求对象。此操作仅持久化内容，不改变任务状态（仍为 draft），也不触发正式生成。

## 属性
- `content`: 用户修改后的草稿 Markdown 内容（字符串，必填）。

## 来源
- openapi.yaml: `components/schemas/SaveDraftRequest`
- API端点: `PATCH /api/jobs/{job_id}/draft`

## 备注
此对象用于草稿编辑流程：用户可在 draft 状态下多次保存草稿（前端自动保存或手动保存），确认正式生成需调用 `POST /api/jobs/{job_id}/confirm`。
