# DeckSmithPPT UI 设计方案

本文件夹包含三套 UI 设计方案和主题切换功能。

## 📁 文件夹结构

```
decksmith-ui-v2/
├── v1-light/                    # 方案一：浅色主题
├── v2-dark-standalone/          # 方案二：深色主题（独立设计）
├── v3-dark-faithful/            # 方案三：深色主题（功能一致）⭐ 推荐
├── themes/                      # 主题切换功能
└── assets/                      # 资源文件
```

---

## ⭐ 推荐方案：v3-dark-faithful

**特点**：保持现有功能完全一致，只改变视觉样式为深色主题

| 文件 | 说明 |
|------|------|
| `studio-faithful.html` | 工作台页面 |
| `history-faithful.html` | 历史项目页面 |
| `preview-faithful.html` | 预览页面 |

**功能保持**：
- ✅ 顶部导航栏（Logo、Tab导航、返回首页）
- ✅ 子标签页（创建向导、草稿任务、生成任务、演示文件库）
- ✅ 创建向导（步骤指示器、素材来源切换、表单）
- ✅ 草稿列表（版本管理、查看/编辑/生成）
- ✅ 生成任务（进度条、下载/重试）
- ✅ 演示文件库（文件卡片、下载）
- ✅ 历史项目（表格、查看/操作、只读详情）

---

## 方案一：v1-light

**特点**：浅色主题，与现有设计文件对应

| 文件 | 说明 |
|------|------|
| `index.html` | 首页 |
| `studio.html` | 工作台 |
| `history.html` | 历史项目 |
| `preview.html` | 预览页面 |

---

## 方案二：v2-dark-standalone

**特点**：深色主题，独立设计（功能有调整）

| 文件 | 说明 |
|------|------|
| `studio-v2.html` | 工作台 |
| `history-v2.html` | 历史项目 |
| `preview-v2.html` | 预览页面 |

---

## 主题切换：themes

**特点**：支持深色/浅色主题切换

| 文件 | 说明 |
|------|------|
| `studio-light.html` | 工作台（浅色版） |
| `history-light.html` | 历史项目（浅色版） |
| `index-with-theme.html` | 带主题切换的首页 |
| `preview-themes.html` | 主题切换功能预览 |

---

## 资源文件：assets

| 文件 | 说明 |
|------|------|
| `_d_meta.json` | 设计元数据 |
| `implementation-summary.md` | 实现摘要 |
| `preview-fragment.html` | 可视化片段 |
| `preview-rendered.html` | 渲染后的预览 |

---

## 预览方式

```bash
# 推荐方案（v3）
open v3-dark-faithful/preview-faithful.html

# 浅色主题（v1）
open v1-light/preview.html

# 主题切换功能
open themes/preview-themes.html
```
