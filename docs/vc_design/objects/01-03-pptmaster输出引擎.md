# PPTX 输出引擎（ppt-master）

## 描述

基于 ppt-master 的演示文件生成引擎，将 Markdown 草稿转化为原生可编辑的 DrawingML PPTX 格式演示文件。

## 所属类型

输出引擎（OutputEngine）

## 引擎标识

- **引擎名**：`ppt_master`
- **引擎类型**：`output`
- **Skill 来源**：[ppt-master](https://github.com/JimLiu/ppt-master)（上游只读副本在 `lib/ppt-master/`）

## 核心特点

- **原生 PPTX**：生成 DrawingML 格式，完全可编辑（非图片嵌入）
- **多风格支持**：18 种视觉风格（swiss-minimal、editorial、bold 等）
- **多叙事模式**：auto、presentation、report、storytelling 等
- **AI 驱动**：通过 Claude Code 执行，理解内容后自动排版
- **无外部图片依赖**：全部用原生形状、图标和文本

## 两种生成模式

### Default 模式（完整流程）

1. **Topic Research**：研究主题，收集素材
2. **Strategist**：制定内容策略，确认沟通契约
3. **Image Generator**：生成配图
4. **Executor**：执行 SVG 渲染和 PPTX 导出

适合：需要精细策划的正式演示

### Quick 模式（快速生成）

1. 准备素材/资源
2. 直接决策，跳过策略确认
3. 手写 SVG 页面
4. 执行最终检查并导出

适合：快速原型、内部分享

## 输入输出

- **输入**：Markdown 草稿内容（来自草稿引擎）
- **输出**：PPTX 演示文件（原生 DrawingML，可编辑）

## 参数结构

### 基础参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `content` | string | 是 | 草稿的 Markdown 内容（最多 15000 字符） |
| `title` | string | 否 | PPT 标题 |
| `pages` | integer | 否 | 目标页数 |
| `content_style` | string | 否 | 内容风格（默认 `marketing_promo`） |

### 引擎参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `style` | enum | `swiss-minimal` | 视觉风格（18 种可选） |
| `mode` | enum | `auto` | 叙事模式 |
| `format` | enum | `ppt169` | 画布/输出尺寸 |
| `notes` | boolean | `false` | 是否生成演讲备注 |
| `animation` | enum | `none` | 动画策略 |

### 画布格式

| 格式 | 说明 |
|------|------|
| `ppt169` | 16:9 宽屏（默认） |
| `ppt43` | 4:3 标准 |
| `ppt_wide` | 超宽 21:9 |

## 适用场景

**合适**：
- 正式商业演示
- 需要原生可编辑的 PPTX
- 需要精细排版和视觉风格控制
- 产品发布、年度汇报

**不合适**：
- 需要网页交互/动画效果（用 HTML 引擎）
- 快速原型迭代（用 HTML 引擎更快）
- 纯数据可视化（考虑专用 BI 工具）

## 执行方式

引擎通过 Claude Code 子进程执行：

```bash
claude -p --dangerously-skip-permissions "{prompt}"
```

执行流程：
1. 将草稿内容写入 `draft.md`
2. 构建 prompt 并写入 `prompt.md`
3. 启动 Claude 子进程
4. 监控进程状态和产物生成
5. 输出 PPTX 到 `outputs/` 目录

## 依赖

- Claude Code CLI
- Node.js（用于 SVG 处理）
- Python 3.11+（用于脚本工具）
