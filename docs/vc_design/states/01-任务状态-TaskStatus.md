# 任务状态

## 描述

任务从创建到结束的生命周期状态。

状态值分两层，**不是一张表**（理由见「设计考虑」）：

- **任务态 `JobStatus`**（面向用户与前端）：定义在 `docs/contracts/openapi.yaml`
- **引擎态 `EngineJobStatus`**（引擎协议 `PptEngine.status()` 的值域）：定义在 `docs/contracts/engine.schema.json`

## 状态定义（任务态）

| 状态 | 终态？ | 说明 |
|------|--------|------|
| queued | 否 | 已入库、等待调度认领，引擎子进程尚未拉起 |
| running | 否 | 引擎子进程已 fork 并在执行 |
| completed | 是 | 成功产出 |
| failed | 是 | 执行过程中出错 |
| timeout | 是 | 超过最大执行时间 |
| cancelled | 是 | 被撤销（queued 摘队，或 running 被中断） |

## 引擎态（4 值，是任务态的子集）

`running` / `completed` / `failed` / `timeout`。

`queued` 与 `cancelled` **不在引擎态里**：排队与撤销是编排层的决定，引擎只报告自己那个子进程的真相，它永远不会产出这两个值。约束关系「引擎态 ⊆ 任务态」由 `scripts/check-alignment.py` 校验。

## 状态流转

```
提交 → queued ──认领──▶ running ──▶ completed
 │                          ├─────▶ failed
 └────────撤销──────────────┴─────▶ cancelled
```

## 状态转换规则

| 当前状态 | 可转换状态 | 触发条件 |
|---------|-----------|---------|
| queued | running | 调度侧认领，引擎子进程 fork 成功 |
| queued | cancelled | 用户撤销 |
| running | completed | 引擎成功生成输出 |
| running | failed | 引擎执行出错 |
| running | timeout | 超过最大执行时间 |
| running | cancelled | 用户撤销且该引擎具备 `cancel` 能力位 |
| completed | - | 终态，不可转换 |
| failed | - | 终态，不可转换 |
| timeout | - | 终态，不可转换 |
| cancelled | - | 终态，不可转换 |

## 消费侧判定口径（重要）

**终态集合 = {completed, failed, timeout, cancelled}**，**非终态集合 = {queued, running}**。

判"任务是否还在推进"必须用 `status ∈ {queued, running}` 或"是否终态"，**不要用 `status !== 'running'`**。后者在引入 `queued` 之前等价于"是否终态"，之后就错了——排队中的任务会被当成已结束的分支处理（显示下载按钮、不刷新、计数漏算等）。

## 状态明细

### 说明

- 当前状态下引擎正在做什么的描述
- 仅在非终态（`queued` / `running`）下有意义
- 前端通过轮询获取最新状态
- 状态语义一律走 `status`；`status_detail` 只做人读补充，不得承载机器判据

### 示例

**排队中**：`排队中，前面还有 2 个任务`

**草稿引擎任务**：
```
正在读取文件...
→ 正在理解材料内容...
→ 正在生成 Markdown 草稿...
→ 正在优化格式...
```

**输出引擎任务**：
```
正在解析 Markdown...
→ 正在应用视觉风格...
→ 正在生成幻灯片...
→ 正在导出 PPTX...
```

## 排队位序

`queue_position`（整型，可为 null）：本任务前面还有多少个尚未被认领的 `queued` 任务。仅 `status=queued` 时给出。

它是读取时按 `created_at` 现算的派生值——**仅供参考、不构成承诺**，两次轮询之间会因他人撤销或调度认领而变化；不入库，也不参与任何状态判定。

## 撤销机制

### 说明

- 撤销只作用于非终态任务，`POST /api/jobs/{job_id}/cancel`
- `queued`：直接从队列摘掉，不产生任何引擎进程
- `running`：请求引擎终止其子进程。**能否真中断取决于引擎能力位**——`EngineInfo.capabilities` 含 `cancel`（由引擎是否实现取消钩子自动派生，与 `page_preview` 同一套鸭子类型探测）的引擎才会真杀掉进程；不具备该能力的引擎对 `running` 任务返回 409，**绝不静默返回"已撤销"而让子进程继续跑继续烧额度**
- 已写出的中间产物与成品文件不删除（文件保护口径，重跑与审计可能还要用）
- 撤销前已消耗的 token 如实保留，不抹掉（ADR-016 口径）

## 重试机制

### 说明

- 重试不改变原任务的终态，而是创建一个新任务、复用原任务的参数
- **可重试的状态 = 未成功的终态**：`failed` / `timeout` / `cancelled`
- `completed`（已经成功）与 `queued` / `running`（还没结束）一律拒绝（409）
- 新任务有独立的 job_id，从 `queued` 开始排队

### 流程

```
原任务（failed / timeout / cancelled）→ 创建新任务（queued）→ 认领（running）→ 完成/失败/超时
```

## 设计考虑

- **为什么分层枚举**：枚举的边界应该按"谁能产出它"划，不按"谁要看它"划。把 `queued`/`cancelled` 塞进引擎协议，会让新引擎作者以为自己的 `status()` 需要实现排队语义，而引擎根本没有队列概念。这与「草稿引擎与输出引擎分属两个类，否则能力位会被误标」是同一个判断维度。
- **为什么要 `queued` 独立成态**：在此之前，"已受理但还没起跑"只能塞进 `status_detail` 的自由文本（`正在启动引擎`），机器不可判定——于是"进程重启后没人再管这个任务"和"任务正在正常跑"在接口上长得一模一样，只能靠人工读库判断卡死的幽灵任务。收回 `status` 层之后，`queued` 长时间无人认领本身就是异常信号。
- **终态不可逆**：四条终态都不可转换。
- **重试即新建**：不复活原任务，保持审计链完整。
