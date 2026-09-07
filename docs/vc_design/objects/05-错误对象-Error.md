# 错误对象（Error）

## 描述
API 错误响应对象，用于返回操作失败时的错误信息。

## 属性
- `message`: 错误描述（字符串，必填）。
- `code`: 错误码（字符串，可选），用于程序识别错误类型。

## 来源
- openapi.yaml: `components/schemas/Error`

## 备注
当 API 返回 4xx/5xx 状态码时，响应体将包含此对象。前端应展示 `message` 给用户，并根据 `code` 进行特定处理（如参数校验失败、任务状态冲突等）。
