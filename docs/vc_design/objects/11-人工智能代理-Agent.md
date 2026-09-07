# 人工智能代理（Agent）

## 描述
驱动 PPT 生成引擎的 AI 代理枚举类型，定义了可用的 AI 代理选项。

## 枚举值
- `claude`: Claude Code（默认）
- `codex`: Codex CLI
- `hermes`: Hermes

## 来源
- openapi.yaml: `DeckRequest.agent` 属性定义
- engine.schema.json: `DeckContent.agent` 属性定义

## 备注
Agent 是 DeckRequest 中的一个属性，用于指定使用哪个 AI 代理来驱动引擎。不同代理可能具有不同的能力、成本或性能特征。此枚举支持未来扩展新的 AI 代理。
