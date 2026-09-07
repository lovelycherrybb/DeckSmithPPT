# VC 设计阶段对象列表

本目录包含基于 `docs/contracts/` 契约反推出的 VC 设计阶段应设计的对象。每个对象以 Markdown 文件形式记录，文件名格式为 `序号-中文名-英文名.md`。

## 对象列表

1. [01-引擎信息-EngineInfo.md](01-引擎信息-EngineInfo.md) - 引擎注册信息，包含参数说明书
2. [02-生成请求-DeckRequest.md](02-生成请求-DeckRequest.md) - 提交 PPT 生成任务的请求对象
3. [03-生成任务-Job.md](03-生成任务-Job.md) - PPT 生成任务的核心实体
4. [04-任务状态-JobStatus.md](04-任务状态-JobStatus.md) - 任务状态枚举类型
5. [05-错误对象-Error.md](05-错误对象-Error.md) - API 错误响应对象
6. [06-ppt_master引擎参数-ppt_master.md](06-ppt_master引擎参数-ppt_master.md) - ppt_master 引擎的特定参数
7. [07-保存草稿请求-SaveDraftRequest.md](07-保存草稿请求-SaveDraftRequest.md) - 保存草稿的请求对象
8. [08-上传响应-UploadResponse.md](08-上传响应-UploadResponse.md) - 文件上传成功后的响应对象
9. [09-引擎契约请求-DeckContent.md](09-引擎契约请求-DeckContent.md) - 引擎契约中的通用层请求对象
10. [10-素材来源-Source.md](10-素材来源-Source.md) - 原始素材来源的值对象
11. [11-人工智能代理-Agent.md](11-人工智能代理-Agent.md) - 驱动引擎的 AI 代理枚举

## 设计依据

这些对象从以下契约文件中提取：
- `docs/contracts/openapi.yaml` - 前后端 API 契约
- `docs/contracts/engine.schema.json` - 引擎契约

## 使用说明

每个对象文件包含：
- 对象描述
- 属性列表（名称、类型、是否必填、说明）
- 来源（对应的契约定义）
- 备注（设计考虑、使用场景等）

这些对象可作为 VC 设计阶段的基础，用于进一步的领域建模、数据库设计、API 实现等。
