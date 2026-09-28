# ADR-018: 任务状态分层枚举，新增 queued 与 cancelled（为并发调度让路）

- 状态: 已接受（**契约已落，代码待实现**，与 ADR-016 同一节奏）
- 日期: 2026-09-28
- 决策者: 龚成
- 相关: ADR-001（引擎可插拔 / 能力位按钩子派生）、ADR-003（技术栈，SQLite → 未来 PG）、
  ADR-012（cwd 归位与产物落点）、ADR-016（token 口径，撤销后已烧掉的量不抹）、
  `docs/vc_design/states/01-任务状态-TaskStatus.md`（状态机人读权威源）

## 背景

并发能力盘点（2026-09-28）暴露两件事，都直接 rooted 在"任务状态只有 4 个、且提交即 running"这个前提上：

1. **没有队列，也就没有并发上限可言**。`POST /api/decks` 落库即 `status="running"`，随即在请求线程池里挂一个
   `_poll_until_done` 后台任务，每 15 秒醒一次、最长占 3600 秒。经最小同构复现实测：uvicorn 单进程
   （Dockerfile CMD 无 `--workers`）下，**约 36 个运行中任务就会把 anyio 默认 40-token 线程池占满**，
   此后新提交与一个纯内存只读接口一起排到 29.8 秒才返回——不是降级，是整站冻结。要做队列，
   就必须有一个能表达"已受理、还没起跑"的状态。

2. **"还没起跑"和"正在跑"在接口上长得一样**。这个语义此前只能塞进 `status_detail` 的自由文本
   （`正在启动引擎`），机器不可判定。后果是容器重启/升级后，随进程消失的轮询线程留下的任务永久停在
   `running`，与"正在正常执行"完全无法区分——线上库里已有一条被人工标记为
   "（人工标记）引擎进程约47小时无输出、supervisor_pid 为空，判定为卡死幽灵"的记录就是这件事的实物证据，
   且 `retry` 因"仅 failed/timeout 可重试"把幽灵彻底封死。

同时，全仓库把"非 running 即终态"写死成了隐式假设（后端 `service/jobs.py` 的进度挂载与
`repository/jobs.py:102` 的 `running_task_count`，前端 `GenerationJobsPanel`/`PagePreviewGrid`/`DraftsPanel`/
`usePollingJobs` 共 7 处判据）。不把这个假设显式化，任何新增的非终态都会立刻被这些分支误判成"已结束"。

## 决策

### 决策 1：状态枚举分两层，不再是一张表

| 层 | 枚举 | 值域 | 所在契约 |
|----|------|------|---------|
| 任务态（面向用户与前端） | `JobStatus` | queued / running / completed / failed / timeout / cancelled | `openapi.yaml` |
| 引擎态（`PptEngine.status()` 值域） | `EngineJobStatus` | running / completed / failed / timeout | `engine.schema.json` |

约束关系为**引擎态 ⊆ 任务态**，由 `scripts/check-alignment.py` 从"两集合相等"改为"子集"校验。

理由：枚举边界应按"谁能产出它"划，不按"谁要看它"划。排队与撤销是编排层的决定，引擎没有队列概念，
把 `queued`/`cancelled` 写进引擎协议会让新引擎作者以为自己的 `status()` 要实现排队语义。这与
ADR-015/能力位那条"草稿与输出分属两个类，否则能力位会被误标"是同一个判断维度。

### 决策 2：`queued` 的精确定义与流转

`queued` = 任务已入库、**引擎子进程尚未 fork 成功**；fork 成功才翻 `running`。
流转：`提交 → queued → (认领) → running → completed / failed / timeout`。
`status_detail` 退回纯人读补充，不再承载任何机器可判语义。

### 决策 3：新增 `cancelled` 与 `POST /api/jobs/{job_id}/cancel`

撤销作用于全部非终态，两种语义不同：`queued` 直接摘队；`running` 请求引擎终止子进程。

**能否真中断由能力位决定**：新增引擎可选钩子 `cancel(job_id)`，`EngineInfo.capabilities` 含 `cancel`
按 `implements_hook()` 鸭子类型派生（与 `page_preview`、`prepare_runtime` 同族，遵守 ADR-001
「不出现 `if engine_name == ...`」）。不具备该能力的引擎对 `running` 任务**返回 409，
绝不静默返回"已撤销"而让子进程继续跑继续烧额度**——这条是本决策的硬约束。

### 决策 4：`retry` 门控从"failed/timeout"改为"未成功的终态"

即 `failed / timeout / cancelled` 可重试；`completed` 与 `queued/running` 一律 409。
注意这里刻意**不**采用"非终态即拒绝"的写法——那会把 `completed` 也变成可重试，
一个已成功出片的历史条目因此获得二次烧额度的入口。新任务从 `queued` 起。

### 决策 5：`queue_position` 进契约，定性为"仅供参考的派生值"

`Job` 出参新增可空整型 `queue_position`，仅 `queued` 有值，读取时按 `created_at` 现算，
不入库、不参与任何状态判定。理由：只报"排队中"对用户没有等待量信息；代价是它会抖，
故契约里明确写"不构成承诺"。

## 后果

**收益**

1. 队列有了合法的存在形态：`queued` + worker 认领是把"每任务一条轮询线程"改成"单个调度循环"的前提，
   那一步才能把 36 的天花板抬到几百。
2. 幽灵任务从"只能人工读库判定"变成可判定：`queued` 长时间无人认领、或 `running` 却进程已亡，
   都是明确的异常信号，可据此做启动期兜底扫描。
3. 误提交与卡死任务有出路（撤销），不再需要手工改库。
4. "非终态"第一次成为契约里的显式集合，7 处 `status !== 'running'` 的隐式假设有统一判据可改。

**代价与风险**

1. **契约改完，代码就是错的**：本 ADR 落地时只动了契约层（`openapi.yaml`、`engine.schema.json`、
   `vc_design/states/01`、两个脚本），后端仍会写 `status="running"`，`EngineJobStatus` 在
   `packages/engine/core/models.py` 里仍叫 `JobStatus`。这是 ADR-016 同款的"契约先行、代码待实现"窗口，
   必须在下一次改动里关闭，否则契约与实现漂移。
2. `check-drift.sh` 目前只有 5 行、本体是 `echo "TODO"`，`check-alignment.py` **没有任何 CI 挂点**
   （全仓库只在 ADR-016 的一句 prose 里被提到）——也就是说"改枚举要不要同步 5 处副本"这件事现在零机器守卫。
   已把 `check-alignment.py` 改成子集校验并纳入本次改动，但**是否挂进 `ci-contract.yml` 仍待批准**：
   基线是全绿的，挂上去不会立刻红，但它是长期约束。
3. 状态值的手工副本共 5 处（openapi 两处、engine.schema 一处、`vc_design/states/01` 一处、
   `engine/core/models.py` 一处手写 Python 枚举）。`models.py` 的 docstring 自认"最终应由
   `engine.schema.json` 代码生成"，`gen-engine-types.py` 只产 TS——这是 AGENTS 规则 1 的既有偏差，
   本 ADR 不顺手治，另案。
4. 前端 `StatusTag` 有未知值兜底（显示英文原值不崩），所以老前端遇到 `queued` 是"显示不对"而非"白屏"；
   但 `api.d.ts` 未入库（`.gitignore` 第 31 行），类型靠构建时生成，因此**必须重跑
   `scripts/generate-types.sh`** 才能拿到新的联合类型。
5. `running → cancelled` 要求给 `ppt_master`/`ppt_master_v6` 补 kill 路径（这两个引擎此前连超时都不杀子进程），
   实现面比"只撤 queued"大；这是决策 3 换取"撤销按钮语义诚实"所付的价。

## 契约层已改动清单

- `docs/contracts/openapi.yaml`：`JobStatus` 6 值 + 状态机描述；`/api/jobs?status=` 的内联枚举副本
  改为 `$ref`（同一份知识不再抄两遍）；新增 `/api/jobs/{job_id}/cancel`；`retry` 的 summary/描述/409 口径；
  `Job.queue_position` 新字段；`status_detail`/`error`/`JobPages.status`/`EngineInfo.capabilities` 描述同步；
  `info.description` 与 `/api/decks` 的流转 prose。
- `docs/contracts/engine.schema.json`：`JobStatus` → `EngineJobStatus`（仍 4 值）+ 分层说明，`Job.status` 的 `$ref` 同步。
- `docs/vc_design/states/01-任务状态-TaskStatus.md`：状态表加"终态？"列、补引擎态子集说明、转换规则、
  显式终态/非终态判据、`queue_position`、撤销机制、重试可重试集合。
- `scripts/check-alignment.py`：状态层校验改子集；接口映射加"撤销任务"。
- `scripts/gen-engine-types.py`：枚举导出兼容改名。
