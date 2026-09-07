# 素材来源（Source）

## 描述
原始素材来源的值对象，用于指定 PPT 生成的输入材料。支持网页链接、上传文件路径和直接文本三种类型。

## 属性
- `type`: 来源类型（枚举，必填）：
  - `url`: 网页链接
  - `file_path`: 上传后的服务器路径
  - `text`: 直接文本
- `value`: 具体的值（字符串，必填），根据 type 分别为 URL、路径或文本内容。

## 来源
- openapi.yaml: 嵌套在 `DeckRequest.source` 和 `ppt_master.source` 中
- engine.schema.json: 嵌套在 `DeckContent.source` 和 `ppt_master.source` 中

## 备注
此值对象在多个地方重复出现，体现了领域模型的一致性。使用 source 时，其优先级高于 content（Markdown 内容）。典型用法：
1. 提供网页链接，后端抓取内容并生成 PPT。
2. 提供上传文件路径，后端解析文件内容。
3. 提供直接文本，后端直接使用。
