# ADR-032: Agent 设置——配置载体走 settings.json，供应商档案多套可切换

- 状态: **已接受**（龚成 2026-11-02 批示：载体走 `settings.json`、档案多套可切换、
  `.env` **不清空、保留旧配置兼容**、继续写代码）
- 日期: 2026-11-02
- 决策者: 龚成（2026-11-02 定档载体与档案两项）；本 ADR 由 QwenWork 起草
- 相关: ADR-020（系统设置三层边界、secret 三约束、决策 3 被本 ADR 修订）、ADR-026（假绿灯）、
  ADR-012（数据卷与容器 `HOME`）、`docs/vc_design/interfaces/02-AgentCLI-AgentCLI.md`（多 agent 预留）、
  `docs/research/2026-10-21-agent设置方案.md`（本 ADR 的前置调研）

## 背景

供应商（模型端点 + 凭据 + 模型名）今天只有一条配置通道：`deploy/.env`。改一次要
`docker compose up -d --force-recreate app`，重建会打断在跑的任务——这与 ADR-020 当初
要解决的"现场调参不该走发布通道"是同一个病，只是换了参数。

ADR-020 决策 4 已把这一层留成扩展位：*"agent 分组待用户明确要放哪些键时由注册表加项带出，
契约与前端均不需要改动"*。本次就是那次"明确"。

已核事实（文档原文级，非推测）：

1. Claude Code 每次启动**直接从文件读** `env` 块，且**文件里的值覆盖同名 shell 环境变量**
   （官方 settings 参考原话：*A value here overwrites the same variable exported in your shell* /
   *Claude Code writes each `env` entry into the process environment, replacing the value inherited
   from the shell*）。
2. 容器内 `HOME=/data/home` 且挂在持久卷（`deploy/docker-compose.yml:60,64`），
   所以生效文件就是宿主的 `deploy/data/home/.claude/settings.json`：改它不动镜像、不动容器、
   重建也不丢。
3. 全仓 11 处拉起 `claude` 的子进程都没传 `env=`，继承的是服务进程环境——那份环境在容器创建
   那一刻定死，这才是"今天改 `.env` 必须重建容器"的真实原因。
4. `~/.claude/settings.json` 是"用户写的配置"；`~/.claude.json` 是 **CLI 自己写的状态文件**
   （登录态、逐项目信任、UI 开关、个人 MCP），且解析失败时 `-p` 模式直接报错退出。

## 决策

### 决策 1：生效载体 = `settings.json` 的 `env` 块，本系统是唯一写入方

放弃 ADR-020 决策 3 的"调度器 fork 子进程时注入该子进程 env"，改为**渲染文件**。
这是对决策 3 的修订，ADR-020 其余决策不动。

理由三条：

1. 改文件本身即热生效（事实 1、2），再叠一层注入只会多一处真源、多出"注入被文件压住"的
   覆盖关系要解释；
2. 11 处 spawn 零改动（事实 3），改动面从"跨 engine 与 backend 两个包"收缩到"后端设置面一处"；
3. 界面显示的文件内容与实际生效内容**是同一份**，不存在"界面说的和 CLI 读的不一样"。

代价明说：注入方案的"凭据不落盘"好处没有了（见决策 3 的边界处理）；`.env` 降级为兼容层，
不再是生效通道（处置口径见决策 6，已定稿）。

### 决策 2：供应商档案是实体，不是设置项

- 新增表 `agent_provider`：一行一套档案，列 = `id / agent_id / name / values(JSON) / is_active /
  created_at / updated_at / updated_by`。
- **"当前哪套生效"只有这一个真源**：档案表的 `is_active` 列。
  `system_settings` 里**不再**另放一个 `agent.provider_active` 键——起草时曾打算放，复核时判为
  自己造了 ADR-020 后果 1 点名的病（同一个值两个来源互相不知道对方存在），故撤销。
  界面要显示生效哪套，一律从 `GET /api/admin/agents` 的 `active_provider_id` 取，
  那个字段由档案表算出来，不是第二份存储。

为什么不塞进 `system_settings`：

| 做法 | 问题 |
| --- | --- |
| 一行一档案（`agent.provider.<name>`） | 键名要**运行时动态生成**，绕过 `BY_KEY` 注册表；档案有"增/删/列表"语义，与该表"只存覆盖、删行即回落默认"的语义正面冲突；改档案和改闸门值混在同一处，审计分不开 |
| 整包 JSON 存一行 | 直接违背 ADR-020 后果 2 定下的"一行一项、单项可审计、可单独回落" |

分层因此是清楚的：**档案是实体（表，含"哪套生效"这一面）→ 生效值渲染成一份文件**。
每层一个真源。

切换动作 = 在一个事务里清掉同 agent 其他行的 `is_active`、置本行为真，然后立即把新档案重渲染进文件。
文件永远只有一份生效配置，这正好承载"多套记录"，两套东西不混淆。

### 决策 3：secret 三约束只在接口边界生效，落盘必然含明文

ADR-020 决策 5 的三条（GET 恒不回明文、只回 `configured`/`masked`、日志与错误不落明文）
原样继承。新增的是**盘上边界**：

- 写文件时明文只在这一刻离开数据库；文件权限 `0600`，位置在 `data/home` 卷内，
  不进 git（`deploy/data/` 本就被排除）、不进交付包。
- 与现状同风险等级：`deploy/.env` 今天已是明文落盘，本决策不比现状更敞。
- **凭据一律不得进命令行参数**——`ps` 可见，且会被 `run.log` 抓走。渲染成文件恰好避开了这一点。

### 决策 4：托管键与非托管键，两个入口不许改同一个值

- 本系统只重写自己声明的 `ANTHROPIC_*` 键（托管键）；文件里其他内容（`permissions`、`hooks`、
  第三方 env）原样保留、只读展示。
- 原始 JSON 编辑口只允许动非托管键；托管键在该视图只读并显示掩码。要改凭据只能走档案字段。
  理由：两个入口写同一个值是必然漂移的来源。
- 文件里出现"我们没声明、但会生效"的 `ANTHROPIC_*` 键 → 判为**外来键**，界面标出来，
  不许静默生效（ADR-026 那一类"永远绿的灯"）。
- 写入纪律：先 JSON 校验 → 落带时间戳备份 → 原子替换（写临时文件再 rename）。
  校验不过整次 400，绝不"尽力写一半"。
- 并发护栏：读取时回 `sha256`，保存时带 `expected_sha256` 回来；不匹配即 409，
  避免两个人（或界面与运维手工）互相覆盖而无人知晓。

### 决策 5：档案完备性缺键当场标红，不阻止保存但阻止"以为生效了"

第三方网关下模型名要填满 5 个变量（`ANTHROPIC_MODEL` + SONNET/OPUS/HAIKU 档位映射），
缺任一档就命中官方模型名报错——这条 `deploy/.env.example:20-26` 已按实测写明。
因此：档案允许存残缺草稿，但**激活**必须返回缺口清单并在界面标红；任务详情带上"当前生效档案名"，
把"这次失败是哪套档案造成的"变成可追溯事实。

### 决策 6：`.env` 保留为兼容层，不清空，但生效来源必须逐项如实显示

走文件之后，`.env` 里那批 `ANTHROPIC_*` 仍会注入服务进程、被子进程继承。
官方优先级是**文件赢**，所以会出现"档案里删了某项、`.env` 的旧值又顶上"。龚成 2026-11-02 定档：
**保留旧配置兼容、不清空 `.env`**，代价换成人话来讲就是——系统必须替管理员把"这一项现在到底是谁在说话"讲清楚。

三条落实口径：

1. **没有任何生效档案时，本系统一个字都不写配置文件**。老部署升级完代码，行为与升级前完全一致
   （仍走 `.env`），不会因为"上了新功能"而改变现网出片路径。
2. 一旦有生效档案：档案里填了的项 → 写进文件（压过 `.env`）；档案里**留空**的项 → 从文件的
   托管键里**删掉**（不给它留旧值，也不替用户做主塞默认值，同 AGENTS.md 规则 10 的"不选就不下发"）。
3. 界面逐项显示真实来源，三种状态分开讲：**档案** / **配置文件里的外来键** / **部署环境变量继承**。
   激活与保存时把"仍从 `.env` 继承的托管键"列进 `warnings[]`，不许静默生效。

## 后果

1. **契约升 0.11.0**：新增 agent 注册表读取、档案 CRUD、激活、连通探针、原始文件读写共 8 个路径
   （清单见下节）。`scripts/generate-types.sh` 需重跑生成前端类型。
2. `scripts/check-alignment.py` 的接口映射表是**手写清单**，实现批次必须同步补行——
   不补则第 5 项检查对新端点形同虚设（正是 ADR-026 消灭的那类假绿灯）。
3. 结构变更 = **一张新表**（`agent_provider`），不改任何既有列的语义与约束，
   按 `core/db.py` 口径**不升 `SCHEMA_VERSION`**；老库靠 `create_all` 建出新表即可，
   既有 `system_settings` 行不动一行。迁移预演须证明：拿现网库副本起新码，
   新表存在、老表行数与取值一条不差。
4. 界面不再是"零改动"：字段仍由注册表通用渲染，但**档案列表**是新形状，需要新组件
   （增/改/删/切/探针）。这与 ADR-020 决策 4 的承诺相比多花一笔前端工作量，如实记录。
5. 第二个 agent（Codex / Hermes，`vc_design/interfaces/02` 已预留）接入时：它的文件形态、
   托管键、env 变量名由它自己的注册项声明，`agent_provider` 表与本契约不需改形状——
   注册表遍历、不写死 agent 名，这条由检查闸守。
6. 探针只证明"网关活着、凭据活着、模型名活着"，**不证明能出片**；ADR 的验收硬标准
   （真实上传材料跑出一张成片）不被探针替代，且跑它消耗付费额度，须单独请示。

## 契约层已改动清单（本草案实际落的内容）

- `docs/contracts/openapi.yaml`：
  - `info.version` 0.10.0 → **0.11.0**；
  - 新增路径 8 个：`GET /api/admin/agents`、`GET|PUT /api/admin/agents/{agent_id}/settings-file`、
    `GET|POST /api/admin/providers`、`PUT|DELETE /api/admin/providers/{provider_id}`、
    `POST /api/admin/providers/{provider_id}/activate`、`POST /api/admin/providers/{provider_id}/probe`；
  - 新增 schema 10 个：`AgentInfo` / `AgentField` / `SettingsFileMeta` / `SettingsFileView` /
    `SettingsFileUpdate` / `AgentProviderProfile` / `AgentProviderProfileIn` / `AgentProviderList` /
    `ProviderActivateResult` / `ProviderProbeResult`。
- **未改动**：`engine.schema.json`（引擎参数面与本决策无关）、`scripts/check-alignment.py`
  （留待实现批次补映射行）、任何 Python/TS 代码。
- 待裁项：决策 6 三个选项未定，定稿时补进本 ADR 再开工。

## 验证判据（实现批次逐条跑，留可复核证据）

1. **热切换主证**：容器不重启，界面把生效档案从 A 切到 B → 新任务的 `run.log` 里实际请求地址
   是 B 的端点。（真跑一次出片需付费额度，跑前单独请示；此条允许先用最小任务取证）
2. **覆盖关系实验**：`.env` 留旧地址、文件声明新地址 → 界面显示"档案 B 生效"，且子进程实际
   打到新地址；文件删掉该键 → 界面把它列为外来键并指向 `.env`（不许静默用旧值）。
3. **argv 检查**：`ps -ef` 里所有 claude 子进程命令行不含凭据明文。
4. **删除护栏**：删除生效中的档案被拒并给出人话原因。
5. **原子写与并发**：写入前后各有一份带时间戳备份；`expected_sha256` 不匹配返回 409；
   故意提交非法 JSON 返回 400 且**磁盘上的原文件字节不变**。
6. **单测新增**（`packages/backend/tests/`）：secret 不出接口、托管键不可经原始视图改、
   外来键识别、缺键标红、档案增删改查与激活互斥、注册表遍历不含硬编码 agent 名。
7. **迁移**：照 `tests/test_db_migration.py` 风格造 v3 老库 → 起新码 → 断言新表存在、
   既有 `system_settings` 行数与值不变。
8. **契约与类型**：`scripts/generate-types.sh` 与 `scripts/check-alignment.py` 全绿；
   前端 `npx tsc --noEmit` + `npm run build` 全绿（注意 `npm run build` 会打坏正在跑的
   `next dev`，跑前确认 `scripts/dev.sh` 未运行、跑完重启）。

## 未验到（不许当结论用）

- 我们钉住的 CLI 版本是 **2.1.197**，而官方关于"`env` 块覆盖 shell 变量""改文件后运行期重放"
  的表述来自当前文档；`env` 里哪些变量属"仅启动时读"官方只举了 OpenTelemetry 一类。
  **这两条须在 2.1.197 上实测后再写进交付文档**（判据见验证 1、2）。
- 官方提到宿主内嵌 CLI 时可设 `CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST` 让宿主配置压过设置文件，
  属另一条路，本 ADR 不采用、也未实测。
- 若历史容器里曾做过官方订阅登录（`~/.claude.json` / `.credentials.json` 有登录态），
  它与档案凭据共存时的实际优先级未在交付镜像里验过；实现批次须在干净交付容器里跑一次取证。
