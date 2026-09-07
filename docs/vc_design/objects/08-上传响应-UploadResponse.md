# 上传响应（UploadResponse）

## 描述
文件上传成功后的响应对象，包含上传标识、原始文件名、服务器存储路径和文件大小。

## 属性
- `upload_id`: 上传标识（UUID 字符串，必填）。
- `filename`: 原始文件名（字符串，必填）。
- `path`: 服务器存储路径（字符串，必填），格式为 "jobs_root/uploads/<upload_id>/<filename>"，可作为 source.value 提交。
- `size`: 文件大小（整数，字节数）。

## 来源
- openapi.yaml: `components/schemas/UploadResponse`
- API端点: `POST /api/upload`

## 备注
上传流程：用户通过前端上传本地文件（docx/pdf 等），后端存储到服务器并返回此对象。前端可将 `path` 作为 DeckRequest.source.value 提交，实现基于本地文件的 PPT 生成。
