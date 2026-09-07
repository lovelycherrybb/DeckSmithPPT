# ppt_master 引擎参数（ppt_master）

## 描述
ppt_master 引擎在 engine_params 中支持的参数集合。这些参数定义了 PPT 的视觉风格、叙事模式、输出尺寸等特性。

## 属性
- `source`: 原始素材来源（对象，可选），优先于 content。
- `style`: 视觉风格（枚举，18种），例如 "swiss-minimal", "soft-rounded", "glassmorphism" 等。
- `mode`: 叙事模式（枚举，默认 "auto"），例如 "pyramid", "narrative", "instructional" 等。
- `format`: 画布/输出尺寸（枚举，默认 "ppt169"），例如 "ppt43", "xiaohongshu", "a4" 等。
- `notes`: 是否生成演讲备注（布尔值，默认 false）。
- `animation`: 动画设置（枚举，默认 "none"），"none" 或 "auto"。

## 来源
- openapi.yaml: `components/schemas/ppt_master`
- engine.schema.json: `$defs/ppt_master`

## 备注
此对象是引擎参数层的示例，展示了引擎可插拔设计：每个引擎可定义自己的参数模式（params_schema），前端根据此模式动态渲染表单。ppt_master 是当前唯一实现的引擎，未来可添加其他引擎。
