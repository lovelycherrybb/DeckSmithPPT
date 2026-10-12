# DeckSmithPPT 2.0

把**内容**变成**演示文稿**的自托管 Web 应用：上传材料或给出主题，产出 PowerPoint 可逐个文字框编辑的**原生 PPTX**，或单文件可翻页的**网页 PPT**。生成之后还能在界面里逐页看、逐页改、增删页面、留版本血缘。

核心设计两条：**引擎可插拔** + **契约先行**。当前最新发布标签 `v0.5.0`。许可：MIT。

**关于生成能力**：本项目自身不训练、也不直连模型，而是把第三方 PPT 生成工作流（上游技能）收进统一的引擎协议、任务契约与界面之下。所有引擎都需要一个可访问的 Anthropic 兼容端点，**没有离线退路**，凭证由使用者自行配置。

## 能做什么

| 维度 | 数量 | 说明 |
|---|---|---|
| 视觉风格 | 18 | 瑞士极简 / 柔和圆角 / 玻璃拟态 / 深色科技 / 蓝图 / 编辑出版 / 摄影编辑 / 数据新闻 / 粗野主义 / 孟菲斯 / 小刊 / 复古海报 / 剪纸 / 手绘笔记 / 墨迹笔记 / 黑板 / 水墨 / 像素风 |
| 画布尺寸 | 8 | PPT 16:9、4:3、小红书、朋友圈、竖版故事、公众号头图、横幅、A4 |
| 叙事模式 | 5 + auto | 金字塔 / 叙事 / 教学 / 展演 / 简报 |
| 产物形态 | 2 | 原生可编辑 PPTX、单文件网页 PPT |
| 输出引擎 | 3 | 见下表，可插拔 |
| 接口 | 43 个 `/api` 路径 | 由契约单一事实来源生成类型 |

界面能力一览：

- **逐页预览** — 产物按页存 SVG，界面里逐页看，不必下载打开才知道好坏。
- **逐页拖拽修改** — 直接在页面上拖动文字块与图形（自研覆盖层方案，不引 canvas 编辑库，ADR-025）。
- **对话式改稿** — 用自然语言提要求，模型只回修改块、后端应用后回传整页，实测输出量降到约十六分之一（ADR-028）。
- **页面增删与上下移** — 结构改动不消耗模型调用，走整稿有序物化通道（ADR-027）。
- **确定性重导出** — 只改版式内容的另存不调用模型，实测从约 6 分钟降到 12 秒、零 token。
- **版本血缘** — 每次改稿留版本，可看"这一版改自哪一版"并回退。
- **真流式进度与取消** — 生成日志边跑边出，取消按进程组终止子进程树，不留僵尸。
- **排队与并发闸** — 闸只约束输出任务，草稿与改稿走快通道不占闸位（ADR-024）；闸值运行期可调，无需重启（ADR-020）。
- **多用户隔离** — 登录、用户管理、访客闸；项目归属与认领，管理员可代为认领或删除未认领访客项目。
- **token 分项记账** — 每任务记 `input` / `cache_creation` / `cache_read` / `output` 四项并派生总数；网关未回报的记为"未回报"而非 0（ADR-016）。

## 技术栈

- **后端**：Python + FastAPI + SQLAlchemy + SQLite
- **前端**：Next.js (App Router) 15 + React 19 + TypeScript + Ant Design 5
- **引擎**：可插拔，分草稿引擎与输出引擎两类
- **契约**：OpenAPI 3.1 + JSON Schema，类型由脚本生成
- **交付**：Docker Compose + 离线镜像包 + 固定次序的升级脚本

## 引擎

协议在 `packages/engine/core/protocol.py`（`PptEngine` / `DraftEngine` / `OutputEngine`），登记在 `packages/backend/src/app/engines/registry.py`。新增引擎只实现协议 + 加一项登记，**不改调用方**。

| 注册名 | 类型 | 逐页预览 | 中断生成 | 逐页修改 |
|---|---|---|---|---|
| `ppt_master_draft` | 草稿 | — | — | — |
| `claude_draft` | 草稿 | — | — | — |
| `ppt_master` | 输出 | 支持 | 支持 | 支持 |
| `ppt_master_v6` | 输出 | 支持 | 支持 | 支持 |
| `guizang_ppt` | 输出 | 支持 | 支持 | — |

**能力位不写死**：上表三列由「引擎实现类是否真实实现对应钩子」（`get_pages` / `cancel` / `revise`）自动派生。实现即点亮，前端与路由零改动；也不按引擎名门控，避免出现"界面显示支持、实际报错"的假能力。

上游版本采用双引擎策略：`ppt_master` 锁旧版技能、`ppt_master_v6` 专供 v6.6.0，各自演化互不牵连（ADR-015）。

## 快速开始（开发形态）

```bash
# 1) 取代码与上游技能
git clone --recurse-submodules <仓库地址>
cd DeckSmithPPT && git submodule update --init --recursive

# 2) 装 Python 依赖（单一入口，含引擎与上游技能清单，勿拍平勿改名）
python3 -m venv .venv && source .venv/bin/activate
pip install -r packages/backend/requirements.txt && pip install pytest

# 3) 装前端依赖
cd packages/frontend && npm install && cd -

# 4) 一条命令起后端 + 前端
./scripts/dev.sh
```

访问：

- 前端 http://localhost:3180 （`-H 0.0.0.0`，同网段其他机器也可打开）
- 后端 API http://127.0.0.1:8180 ，Swagger 在 `/docs`（**刻意只绑回环**，外部访问走前端代理）
- 端口可在仓库根 `.env` 用 `BACKEND_PORT` / `FRONTEND_PORT` 覆盖，脚本不硬编码

配置模型端点：走主机上的 Claude Code 配置（容器形态走 `deploy/.env`）。若使用第三方 Anthropic 兼容网关，需同时设 `ANTHROPIC_AUTH_TOKEN`（Bearer 鉴权，区别于 `ANTHROPIC_API_KEY` 的 `x-api-key`）与 `ANTHROPIC_DEFAULT_SONNET_MODEL` / `ANTHROPIC_DEFAULT_OPUS_MODEL` / `ANTHROPIC_DEFAULT_HAIKU_MODEL` 三档模型名，否则任务会在鉴权或模型解析阶段失败。

⚠️ 只起一个前端实例。手工 `npm run dev -- --port 3180` 的代理目标兜底是 **8080**（`next.config.mjs`），而 `dev.sh` 显式传 **8180**——两者同时在跑时界面都"正常"但读的是不同后端、不同库。`rewrites()` 还在 `next build` 时求值并烘焙进 `.next/standalone` 的路由清单，构建期的 `BACKEND_PORT` 会固化进镜像。起服务前先 `lsof -nP -iTCP -sTCP:LISTEN | grep -E '3180|8080|8180'` 看一眼。更多本机拓扑与已知陷阱见 `docs/DEVELOPMENT.md`。

## 容器化交付

编排文件在 `deploy/`：两个常驻服务（`app` 后端 + 引擎、`web` 界面）加一个一次性 `data-init`（把 bind 卷属主交给容器运行身份，真实 Linux 首装的必需项）。全部可写状态落 `deploy/data/`，删容器不丢数据。

```bash
cd deploy
cp .env.example .env        # 填 ANTHROPIC_* 凭证与 IMAGE_TAG（IMAGE_TAG 必填，无兜底）

docker compose up -d --build     # 源码构建
docker compose up -d --no-build  # 用交付包导入的镜像（离线场景必须带 --no-build）
```

访问 http://localhost:3000 （端口由 `WEB_PORT` 控制）。自检：

```bash
docker compose ps
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/
curl -s http://localhost:3000/api/engines
```

发版与升级：

```bash
PLATFORMS=linux/amd64 bash scripts/release.sh <新版本号>   # 出交付包：按平台导 tar + 校验和
bash scripts/upgrade.sh <新版本号>                         # 源码构建升级
bash scripts/upgrade.sh <新版本号> dist                    # 从交付目录离线导入
bash scripts/upgrade.sh <新版本号> dist --check            # 只验镜像已备好，不动在跑的服务
```

升级次序固定为**预检 → 备镜像 → 停服 → 备份 `data/` → 改 TAG → 起 → 验健康**：库结构在启动期自动迁移且不可逆，必须先备好新镜像才允许停服（ADR-013）。重建容器前先看 `GET /api/jobs` 有无 `status=running` 的任务，否则会连带杀掉引擎拉起的子进程、任务永久停在 running。完整安装、备份迁移、离线部署与排障见 `docs/DEPLOYMENT.md`。

## 契约、测试与校验

`docs/contracts/` 是唯一事实来源（OpenAPI + 引擎参数 JSON Schema），各端类型一律由 `scripts/generate-types.sh` 生成，禁止手写共享类型；`packages/shared/types/` 不入库。

```bash
bash scripts/test.sh [all|unit|contract|e2e]   # 分层：包内单测 → 根契约测试 → e2e
bash scripts/generate-types.sh                 # 契约变更后重新生成共享类型
bash scripts/check-drift.sh                    # 漂移检查：openapi.yaml ↔ 后端真实路由，双向比对
```

- 规模：23 个测试文件、208 个测试函数；后端约 6.7k 行、前端约 6.4k 行、引擎层约 5.2k 行（不含上游）。
- 单测在各包内 `packages/*/tests/`，根 `tests/` 只放 e2e 与 contract。
- 缺 pytest 或缺解释器时脚本直接报错并给出安装命令，不静默跳过；某一层没有用例时如实标注跳过，不计为通过。
- CI 四条流水线：Backend、Contract（漂移 + 对齐 + 契约测试）、Frontend（类型检查 + standalone 构建）、E2E 最小闭环。这四项由 ADR-026 从"永远绿的占位检查"改成会真的失败。

> 测试解释器需要 `httpx`（`tests/conftest.py` 用 starlette TestClient）与 `jsonschema`（按契约校验真实响应），已列入 `packages/backend/requirements-dev.txt`。

## 目录结构

| 路径 | 内容 |
|---|---|
| `packages/backend/` | 后端服务（FastAPI + SQLAlchemy） |
| `packages/frontend/` | 前端应用（Next.js + React + Ant Design） |
| `packages/engine/` | PPT 生成引擎（抽象协议 + 各实现） |
| `packages/shared/` | 共享产物（仅由契约生成，禁止手写） |
| `docs/contracts/` | 前后端/引擎契约（唯一事实来源，语言无关） |
| `docs/adr/` | 架构决策记录 28 份，含状态与日期索引 |
| `docs/DEVELOPMENT.md` | 开发指南（本机服务拓扑、已知陷阱） |
| `docs/DEPLOYMENT.md` | 部署指南（安装、验证、备份、升级、离线、排障） |
| `lib/` | 上游技能（git submodule，**只读**） |
| `tests/` | 跨包测试（`contract/` 契约、`e2e/` 端到端） |
| `scripts/` | 开发、测试、类型生成、发版与升级脚本 |
| `deploy/` | 交付编排（`docker-compose.yml` + `entrypoint.sh` + `.env.example`） |
| `designs/` | UI 与 logo 设计稿 |

## 已知限制

如实列出，不做修饰：

- **没有离线退路**。所有引擎经 Claude Code 子进程调用大模型，运行环境必须能访问 Anthropic 兼容端点（公网、代理或企业内部兼容网关，三者择一）。
- **成本真实**。实测整稿输出（未显式指定页数时按默认 10 页）单次数百万 token、十几到几十分钟；逐页改稿按补丁协议显著低于整稿，页面增删与版式另存为零 token。批量运行前请核算额度。
- **e2e 覆盖有限**。目前只覆盖"提交 → 调度 → 出片 → 下载"这一条最小闭环，撤销、超时、逐页修改、多身份并发尚未端到端覆盖。
- **前端无组件测试**。`packages/frontend/tests/` 仍为空，`npm test` 仍是 TODO；CI Frontend 跑的是类型检查与构建。
- **两条单测存在闪断**。`packages/engine/tests/test_draft_async.py` 与 `packages/backend/tests/test_guizang_engine.py` 各有一条在 5 连跑中约 1 次失败，表现为后台线程与轮询超时的竞态。未修，见 ADR-026 后果第 4 条。
- **契约未完全收敛到 3.1**。现存 18 处 OpenAPI 3.0 的 `nullable: true`，由 `api_contract_test.py` 登记上限、只准减少，过渡期由测试侧归一化。
- **产物质量非确定**。结果依赖上游技能与模型版本，可能出现结构缺陷；守卫会就地修复可修复项，修不了则如实判失败而非交出一件坏产物（ADR-021）。单次生成 token 消耗大，不做自动重跑。
- **上游是独立开源项目**。`lib/` 下三个 submodule 均为只读上游副本，本项目不修改上游；使用需同时遵守各自许可。

## 上游与许可

- 本项目：MIT，见 `LICENSE`。
- 上游技能（`lib/` 下 submodule，只读、不修改）：
  - [ppt-master](https://github.com/hugohe3/ppt-master) — SVG 版面 → 原生 PPTX 的生成工作流（旧版与 v6.6.0 各钉一个引用）。
  - [guizang-ppt-skill](https://github.com/op7418/guizang-ppt-skill) — 单文件网页 PPT 生成技能。
- 使用本项目即同时受上游各自许可约束。
