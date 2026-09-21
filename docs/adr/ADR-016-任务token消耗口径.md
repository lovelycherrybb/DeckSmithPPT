# ADR-016: 任务级 token 消耗口径（分项四项 + 派生总数，只算引擎主会话）

- 状态: 已接受（契约与代码均已落地；端到端真跑验证待用户批准额度后执行）
- 日期: 2026-09-21
- 决策者: 龚成
- 相关: ADR-001（引擎可插拔）、ADR-012（引擎运行时数据布局 job.json/run.log）、
  ADR-013（升级期数据安全：迁移前拍库、单事务）、ADR-015（ppt-master 双引擎，现共 4 个引擎实现）

## 背景

需求：每个草稿任务与生成任务都要有一个「token 消耗」字段。盘点现状后有四个事实决定了本 ADR 的形状：

1. **数据源已经存在，是我们自己丢掉的。** 四个引擎（`ppt_master`、`ppt_master` 草稿、
   `ppt_master_v6`、`guizang_ppt`）全部以 `claude -p --output-format stream-json` 起子进程，
   `_pump_run_stream` 里都有 `elif t == "result"` 分支，而 result 事件自带用量数据。
   `guizang_ppt/engine.py:497-504` 甚至已经把 result 的若干字段存进 job.json，只挑了
   `subtype/is_error/num_turns/result`，恰好把 `usage` 掐掉。
2. **`result.usage` 是整段会话累计，不是末轮快照** —— 这点必须先钉死，否则"总数"是假的。
   证据取自本机 CLI 包 `@anthropic-ai/claude-code@2.1.105` 的 `cli.js`：result 事件运行时构造写的是
   `usage: this.totalUsage, modelUsage: GV(), total_cost_usd: mX()`（`cli.js:13419125` 等 8 处），
   `totalUsage` 的零值原型含 `input_tokens / cache_creation_input_tokens / cache_read_input_tokens /
   output_tokens / server_tool_use / service_tier / cache_creation`；CLI 自己算输入总量用的是
   `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`（`cli.js:9643945`、`9714389`）。
3. **分项可能部分缺失。** 本机走第三方 Anthropic 兼容网关时，会话 jsonl 里 assistant message 的
   `cache_read_input_tokens` 等为 `null`，只有 `input_tokens/output_tokens`。因此四项不能写成必填，
   否则要么丢数要么造假。
4. **一个任务可能有多个 result。** `guizang_ppt` 有 `--resume` 断点续跑（`engine.py:555-561`），
   续跑是新进程、新 `totalUsage`，若按"覆盖"处理就会少算，必须按分项累加且同一 result 只计一次。

另有一条边界必须先划：后端自己还有拟题（`app/agents/title_suggester.py`）与草稿改写/编辑
（`app/agents/draft_modifier.py`、`claude_stream.py`）两类辅助 LLM 调用，它们不隶属任何 job。

## 决策

**一、字段挂在 Job 上，草稿引擎任务与输出引擎任务同一字段、同一口径。**
契约新增 `TokenUsage` 组件（`docs/contracts/openapi.yaml`）与同名 `$defs`
（`docs/contracts/engine.schema.json`），两边字段一致；对象模型层同步到
`docs/vc_design/objects/03-任务-Task.md`。本轮**不做**项目级/引擎级聚合，
历史项目表格也不加列。

**二、形态为分项四项 + 派生总数，且分项逐项可空。**
`input_tokens / cache_creation_input_tokens / cache_read_input_tokens / output_tokens` 各可为 null；
`total_tokens` 必填，算法取 `input + cache_creation + cache_read + output`（缺报项按 0 参与求和），
与 CLI 自身对输入总量的算法一致。**四项全缺时整体 `token_usage` 为 null，语义是「未记录」**——
引擎不得用 0 冒充实测，后端不得补 0，界面不得把 null 渲染成 0。

**三、归集边界只含引擎主会话。** 同一任务 `--resume` 续跑产生的多个 result 按分项累加；
后端辅助 agent（拟题、草稿改写/编辑）的消耗**不混入**任务数字，本轮也不另开账。
收割时机为**所有终态一律回填**（completed / failed / timeout，含轮询超时兜底）：
引擎侧每个 result 都已把用量累加进 job.json，失败任务烧掉的 token 同样是事实，
如实入账不算冒领（2026-09-21 实施时拍板补充）。

**四、采集必须发生在引擎侧，禁止后端解析 run.log 反推。**
理由是可插拔约束（AGENTS.md 规则 2：新增引擎只实现协议并注册，禁止修改调用方）：
run.log 是给人在界面上读的人类可读蒸馏，把它当数据源会把日志格式变成隐式契约。
落地链路固定为
`引擎 _pump_run_stream 接住 result.usage → 累加落 job.json → _to_job 返回带 token_usage 的引擎 Job →
后端 service 落库 → 契约出字段 → 前端类型由 generate-types.sh 生成`。
`engine.core.models.Job` 增**可选**字段 `token_usage`，不新增必选钩子：
老引擎与 stub 不填即视为未记录，保持"引擎可不改也能跑"。

**五、SQLite `jobs` 表用单列 JSON 文本存（`token_usage_json`），不拆五列。**
分项可空、且未来可能加维度（如按模型分档），单列不逼着每次加字段都改表结构；
本轮无跨任务排序/聚合需求，不需要为可索引性付列的代价。迁移走 `app/core/db.py`
既有加列清单，遵守 ADR-013：迁移前拍库、整轮单事务。

**六、展示口径。** 草稿任务行与生成任务行统一显示「token 消耗 N」，**数字以 M（百万）为单位、
最多两位小数（去尾零）**，详情处分项同口径展开；`null` 一律显示「未记录」。分项里哪几项未回报
要在详情里可见，否则跨网关的数字会被误当可比。真 0 显示「0M」，非 0 但不足 0.005M（约 5,000）
显示「<0.01M」，不把「确实烧了一点」误读成「没烧」。M 只是展示层换算，落库与契约仍是精确整数
（多轮会话读缓存动辄数百万，千分位长串不便阅读——2026-09-21 由「千分位」改为本口径）。

## 未采纳的方案

- **只记一个总数**：数字最省，但 cache_read 在多轮会话里量级远大于新增输入，合并后无法解释，
  也堵死了将来算真实新增消耗的路。
- **同时记 `total_cost_usd`**：该值是 CLI 按公开价自算的，走第三方兼容网关时与实际结算不一致，
  放进契约会被当账单读。将来确有需要应以独立字段、独立口径引入。
- **从 run.log 或 `~/.claude/projects` jsonl 回填历史任务**：旧日志已蒸馏、不保证每项都在，
  且 jsonl 的 per-message `input_tokens` 是当轮上下文规模而非增量，求和会严重虚高。
  存量任务就让它停在「未记录」。

## 后果

正面：4 个引擎各改一处 pump 即可全量覆盖，调用方零分支；契约先行，前端类型自动生成，
不存在手写共享类型；历史数据诚实（不冒领、不估算）。

代价与风险：
1. 四个引擎都要动，且 guizang 的 resume 路径要实现"分项累加 + 同一 result 不重复计数"，
   这是本字段唯一容易算错的地方，须有单测覆盖（复用 `packages/engine/tests/test_guizang_engine.py`
   的 `_fake_claude` 基建，注入带 usage 的 result 事件）。
2. 走第三方网关时 cache 分项可能全 null，此时 `total_tokens` 只含 input+output，
   与走官方网关的数字**不可直接比较**——界面必须露出分项缺失情况，避免误读。
3. 真跑验证要消耗付费额度（一次草稿 + 一次生成），实施后须先给出量化证据再征求同意，
   不以单测通过冒充端到端已验证。

## 实施记录（2026-09-21）

- 引擎侧：新增 `packages/engine/core/usage.py`（取值归一 / 分项累加 / 派生总数三条不变式）；
  4 个引擎的 result 分支全部接住 `usage` 落 job.json——`ppt_master`/`ppt_master_v6` 的
  `_pump_run_stream`（改绑 job_id）、`guizang_ppt` 的续跑累加路径、
  `claude_draft` 改 `--output-format json` 一次调用同时取回草稿正文与用量（解析失败退回
  整段 stdout、用量按未记录，不判草稿失败）。
- 后端侧：`jobs` 表加 `token_usage` 单列 JSON 文本（走 `_MIGRATIONS` 加列清单）；
  `JobRepo` create/update 统一 `_encode_json_columns`；轮询**所有终态**经
  `_engine_reported_usage` 从 job.json 收割回填（不调 `engine.resume()`，避免为数字重烧额度）。
- 前端侧：`lib/tokenUsage.ts` 统一格式化（以 M 为单位、最多两位小数；null→「未记录」、
  分项缺报→「未回报」）；
  生成任务行/详情抽屉与草稿任务行/查看抽屉展示，running 行不显示（终态才落库）。
- 测试：`packages/engine/tests/test_token_usage.py`（15 项）+
  `test_guizang_engine.py::test_resume续跑的token消耗按分项累加` +
  `packages/backend/tests/test_token_usage_store.py`（9 项，含旧库升级补列、
  终态回填、收割容错）；check-alignment 全绿、前端 tsc/build 通过、
  dev 后端重启后浏览器复验存量任务显示「未记录」。
- 待办：一次草稿 + 一次生成的真跑端到端验证（消耗付费额度，须用户批准）。
