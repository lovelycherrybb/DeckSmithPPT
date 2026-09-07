# 引擎信息（EngineInfo）

## 描述
引擎注册信息，包含引擎名称、显示名称、描述以及该引擎的参数说明书（JSON Schema）。前端使用此信息动态渲染引擎选择和参数表单。

## 属性
- `name`: 引擎标识（字符串，必填），对应 DeckRequest.engine 的值，例如 "ppt_master"。
- `display_name`: 用户可见的名称（字符串，必填），例如 "PPT Master"。
- `description`: 简短说明（字符串，可选）。
- `params_schema`: 该引擎 engine_params 的 JSON Schema（对象，必填），用于前端动态渲染表单控件。

## 来源
- openapi.yaml: `components/schemas/EngineInfo`
- API端点: `GET /api/engines` 返回此对象的数组。

## 备注
引擎可插拔设计的核心：每个引擎通过实现 PptEngine 协议并在 registry.py 注册，提供 EngineInfo 实例。
