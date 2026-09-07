# DeckSmithPPT 2.0 文档目录

## 目录结构

### `docs/adr/` - 架构决策记录
记录项目中的重要技术决策和架构选择。每个 ADR 文件记录一个决策。

### `docs/contracts/` - 契约层
包含 API 定义、数据模型和接口规范。是前后端、各服务之间的契约。

### `docs/research/` - 研究文档
项目前期的调研、分析和验证文档。

### `docs/vc_design/` - VC 设计文档
项目的整体设计文档，采用 vibecoding 设计理念。包含：
- `objects/`: 对象定义和数据模型
- `states/`: 状态机和状态转换  
- `interfaces/`: 接口定义和API规范
- `flows/`: 工作流程和业务流程
- `constraints/`: 约束条件和业务规则

## 文档关系

1. **VC Design** → **Contracts** → **Packages**：设计层 → 契约层 → 实现层
2. **ADR**：记录重要决策，与具体设计分离
3. **Research**：支撑设计和决策的调研文档

## 维护指南

1. 新增技术决策：在 `docs/adr/` 创建新的 ADR 文件
2. 更新系统设计：在 `docs/vc_design/` 相应子目录更新文档
3. 修改接口契约：在 `docs/contracts/` 更新契约文件
4. 重要变更：同时更新相关文档，保持一致性
