# ADR-023: pptmaster 逐页修改——修改任务作 Job 第三类、编辑以任务输入提交不开新写面、子演示文件产物隔离

- 状态: 提议（待接受后据此改 `docs/contracts/openapi.yaml` 与 `engine.schema.json`）
- 日期: 2026-10-05
- 决策者: 龚成
- 相关: ADR-015（双引擎策略）、ADR-016（token 口径）、ADR-018（分层枚举）、ADR-019（并发闸）、ADR-021（产物结构守卫）、ADR-024（并发闸只约束输出任务、修改走快通道）；`docs/vc_design/objects/08-子演示文件`、`docs/vc_design/flows/04-pptmaster修改流程`、`docs/vc_design/constraints/01-约束条件`（逐页预览/产物隔离）；`lib/ppt-master-v6` 的 `finalize_svg`/`svg_to_pptx` 与 `svg_editor`

## 背景

要在 pptmaster 产物生成后做逐页修改，每次"保存"另存一个**子演示文件**（可分叉的版本树，见 vc_design）。落到契约层，必须先钉死三件事：

1. **这种任务在模型里是什么**。vc_design 已定：不新造对象，它是任务（Job）的第三类，复用排队/状态/token/重试/撤销。
2. **引擎侧怎么接**。引擎可插拔（AGENTS.md 规则 2），修改能力只能像 `page_preview`/`cancel`/`prepare_runtime` 那样"按引擎是否实现某钩子自动派生"，不能在后端按引擎名写分支。
3. **改好的逐页 SVG 怎么回到服务端并导出**——这条最敏感：现有预览端点是**只读**的，它的文件类型白名单"既是功能开关又是隔离边界"（产物和 prompt/job/log 同在 job 目录，只靠这张表挡 415）。**任何为"写回 SVG"而开的写接口都会扩大这张边界**，是必须先算清的账。

## 决策

### 决策 1：修改任务 = `Job.task_type` 的第三类，token `revise`

- `Job.task_type` 枚举从 `[draft, output]` 扩到 `[draft, output, revise]`（openapi 的 `Job.task_type`、`/api/jobs` 的 `task_type` 过滤参数、`engine.schema.json` 的 `TaskType` 同步）。
- **不新增状态**：`revise` 任务走 `JobStatus` 全套（running/…/cancelled），复用 token 归集、重试、撤销口径。它和普通任务唯一的区别是"输入是一个已有版本 + 修改内容，输出是子演示文件"。
- **不进生成排队闸**：`revise` 是"重导出/改稿"类任务，按 ADR-024 走独立快通道——不占 `max_concurrent_jobs` 生成槽、一般不出现 `queued`/`queue_position`。只有输出引擎（生成）任务受并发闸约束。
- `engine_type` 仍是 `output`（驱动它的是输出引擎），区分只体现在 `task_type`。这样生成（`task_type=output`）与修改（`task_type=revise`）共用"输出引擎"这一类实现，不必新造引擎类型。

### 决策 2：引擎侧新增可选运行钩子 `revise`，派生能力位 `svg_edit`

- `engine.schema.json` 的 `OutputEngine` 增加 `x-runtime-hooks.revise`（与既有 `prepare_runtime` 并列），签名语义：在给定 job 目录、以一份已备好的逐页设计稿为源，按"编辑集"或"对话指令"改稿后重导出，产出成品。
- 探测方式沿用既有约定——**沿 MRO 判是否真实实现该钩子**，据此自动派生引擎能力位，与 `get_pages→page_preview`、`cancel→cancel`、`prepare_runtime` 完全同构。**禁止在后端/前端按 `ppt_master*` 这类引擎名硬判**。
- `EngineInfo.capabilities` 增加取值 `svg_edit`（有 `revise` 钩子的引擎才有），前端据此决定是否显示"修改"入口。加能力位遵循"契约 + 注册表派生 + 前端消费"三处同步，否则按钮静默不出现。

### 决策 3（安全关键）：编辑以「任务输入」提交，绝不在只读预览树上开写接口

- 前端的逐页 SVG 编辑器把改动**持在前端**；用户点"保存"时，把"改好的逐页设计稿（或某几页）+ 可选的一句对话指令"作为**新端点的请求体**提交。
- 新端点 `POST /api/sub-presentations`：受理即返回一个 `queued` 的 `revise` 任务（202）。后端在**该次修改自己新建的 job 目录**里落地这份逐页设计稿，再交 `revise` 钩子重导出。
- **不开** `PUT /api/jobs/{id}/preview/...` 这类"往既有产物目录写"的接口。理由：预览树的白名单是隔离边界，扩一个写面就要重评它挡什么、放什么、谁能写哪个 job——把一次功能上线变成安全审计。以"任务输入"承载编辑，复用的正是 `DeckRequest.content` 早已在用的"提交时带正文"模式，边界不变。
- 代价：整副逐页设计稿的改动要随保存提交一次（受通用层长度上限约束）。MVP 可接受；将来若嫌重，另立 ADR 评估"分块上传 + 临时草稿区"，但那仍须走"任务输入"而非写既有树。

### 决策 4：子演示文件与产物隔离

- 新模型 `SubPresentation`：`sub_presentation_id`、`presentation_id`（根，恒有）、`parent_sub_presentation_id`（可空：空=改自根演示文件，非空=改自某个子文件——用这一条把可分叉的版本树表达清楚）、`task_id`（产生它的那次 revise 任务）、`file_name`、`file_format`、`file_path`、`created_at`。
- **每次修改独立 job 目录**（逐页设计稿 + 成品各一份）；后端在拉起 revise 前，先把"父版本"（根或该子）的逐页设计稿**复制**进新 job 目录当工作稿，改动只落副本，**绝不回写父版本**（保可回溯、保"根↔分支"预览不串）。
- 归属不单独存：`子 → 演示文件 → 项目` 推导，沿用"项目是唯一按用户隔离点"。
- 命名 `项目名称_保存时刻时间戳`（与演示文件同规则，见 ADR 同期把命名从草稿版本号改成生成/保存时间戳那笔）；同一项目同一秒只落一个（同秒唯一）。

### 决策 5：预览/下载尽量复用既有 per-job 端点

- 子文件逐页预览：**复用** `GET /api/jobs/{task_id}/pages`（子文件天然有自己的 revise `task_id`），不新增预览端点。
- 列版本树：新增 `GET /api/presentations/{presentation_id}/sub-presentations`，返回**扁平**列表（每条带 `parent_sub_presentation_id`），前端拼缩进树。
- 下载：新增 `GET /api/sub-presentations/{sub_presentation_id}/download`（根仍用既有 `GET /api/presentations/{presentation_id}/download`）。选择器以缩进树呈现，选哪版下哪版。

## 契约落点清单（本 ADR 被接受后据此改）

- `openapi.yaml`：
  - `EngineInfo.capabilities` 描述增 `svg_edit`。
  - `Job.task_type` 枚举 + `revise`；`/api/jobs` 的 `task_type` 查询枚举 + `revise`；`/api/decks` 与 `Job` 描述补"第三类=修改任务"。
  - `JobStatus`、`Job.queue_position` 描述补"仅输出引擎任务会排队/有位序"（承 ADR-024：修改任务走快通道、不进生成闸）。
  - 新 `SubPresentation`、`ReviseRequest`（`source` 指向根或某子 + 可选 `pages`/`instruction`）schema。
  - 新端点：`POST /api/sub-presentations`、`GET /api/presentations/{id}/sub-presentations`、`GET /api/sub-presentations/{id}/download`。
- `engine.schema.json`：`TaskType` 枚举 + `revise`；`OutputEngine.x-runtime-hooks` + `revise` 钩子。
- 改后跑 `scripts/generate-types.sh` 重生成 `packages/shared/types/{api.d.ts,engine.d.ts}`，跑 `scripts/check-alignment.py`。

## 后果

1. 修改能力接入不破坏引擎可插拔：新引擎只要实现 `revise` 钩子就自动获得入口，不实现就没有；调用方零改动。
2. 预览的只读隔离边界**没有被扩大**——这是本决策刻意守住的。
3. 子演示文件与草稿版本共用"时间戳版本号 / 同秒唯一 / 历史不改 / 可回溯"心智，用户只理解一套版本概念。
4. **代价**：保存非瞬时（要等后台重导出），且需处理导出失败（失败不留半成品）；逐页设计稿随保存提交有体积上限。
5. **已知边界**：本 ADR 只做"逐页 SVG 编辑 + 重导出"这一条上游路线；"改一份已交付的外部 PPTX"（Edit Native round-trip）是另一条生命周期，另案另立 ADR，不混进 `revise`。

## 修订 A（草稿载体模型；实现复验后补记）

复验时纠正了交互模型：**不是每改一页就出一个版本**。改为"临时草稿载体"：

- 点「修改」= 从选中版本复制一份**会话级临时草稿**；多页手动改、多轮对话改都攒进这份草稿，草稿**不落库**，不"另存"就丢弃、重开即全新（回到源版本）。
- **唯一"落版本"动作 = 整体另存为新版本**：把草稿攒的**所有改页**一次性提交给 `POST /api/sub-presentations`（`pages` 为数组）→ 一次修改任务 → 出成功落**一个**子文件。
- **对话式改稿 = Agent 文本能力新增种类 `revise_pages`**：输入=被选页当前内容 + 指令，输出=AI 改好的逐页内容，**只回传进草稿，不落库、不导出、不出版本**。定版仍走"整体另存"。
- 契约落点补充：`openapi.yaml` 的 `AgentExecuteRequest.kind` 白名单加 `revise_pages`（+ `pages`/`instruction` 字段）；`POST /api/sub-presentations` 语义即"批量另存"。对话改稿是 agent 文本调用、**不进 `task_type`、不建 Job**，与"另存=修改任务"分列。

## 已定项（原"待确认"，实施时已采纳）

- 任务枚举 token `revise`、能力位 `svg_edit` —— 已采用。
- `SubPresentation` 用 `presentation_id` + 可空 `parent_sub_presentation_id` 表达版本树 —— 已采用。
- `POST /api/sub-presentations`（返回任务、异步）—— 已采用。

## 顺带发现（非本 ADR 范围，待单独处理）

`engine.schema.json` 的 `DeckContent.engine` 枚举缺 `ppt_master_v6`，而 `openapi.yaml` 的 `DeckRequest.engine` 有——一处既有漂移，研究记录开放问题 5 也提过文档口径不齐。建议另起一笔对齐，勿混进本改造。
