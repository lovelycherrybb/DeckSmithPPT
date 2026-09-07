# DECKSMITHPPT2.0

## 背景

DECKSMITHPPT是一款 PPT 生成产品，不限于各种 pptx 、 html 等格式的 ppt 生成展示，可以通过用户输入 pdf 、 docx 、 md 、 txt 等格式文件自动生成统一的 md ppt 草稿文件，通过用户修改后，提交生成引擎进行 PPT 生成。由于目前生成类似产品的引擎基本都是 skill，因此该项目强依赖 AIagent 。

DECKSMITHPPT2.0 基于 DECKSMITHPPT1.0失败的 vibecoding 构建经验进行二次建设，其产品设计、技术栈等和 1.0 几乎没有任何关系，仅仅吸收 1.0 粗放建设导致的项目不可维护经验，让2.0 项目可维护。

## 项目 coding 结构

基于背景， 2.0 项目建立了三层 coding 结构：vibecodingdesign层+constacts 层+package 层，三层之间强强绑定，表达高度一致。

### vibecodingdesgin（vc_design）层

整个项目的最基础设计层。负责整体对话级设计，可以很口语化、也可以很专业化，设计取决于操控者的实际数字化能力，但不影响代码产出。

在这一层，包含 objects 、 states 、interfaces 、 flows 、constraints 、 README（本文）6层结构，分别代表着对象、状态机、 接口、流程、约束和 README综述。

### constacts 层

作为项目中间层，负责翻译vibecodingdegsin层内容，并生成契约。包含了各类 API 、对象、参数的定义。

### package 层

前后端等代码实现部分同称为 package 层，基本上到这里，vibecoding 选手已经看不懂，但是由于契约层的强约束，可以控制 package 层的很好表达。

## 项目设计

### md 草稿

在 ppt 生成的第一阶段，产品会根据用户上传的格式文件转化成 md 草稿，经研究，pptmaster [github.com/hugohe3/ppt-master](https://github.com/hugohe3/ppt-master) 内已存在`source_to_md.py`可以实现草稿生成，采用该方案。同时，生成的草稿允许用户进行编辑修改，甚至调用 agent 进行草稿调整等。

### 生成引擎

草稿确认后就可以丢给生成引擎去生成了，目前，生成引擎仅有 pptmaster，产物是pptx 。

### 任务

任务贯穿整个生命周期，在用户提交格式文件后，任务就被触发了。它分为以下几个阶段：草稿阶段（草稿未被确认之前）；运行阶段（根据选取引擎进行运行，目前是 pptmaster-草稿+参数封装成提示词给到 claudecode）；完成阶段（产物生成）。
