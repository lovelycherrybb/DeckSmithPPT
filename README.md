# DeckSmithPPT 2.0

内容 → 原生可编辑 PPTX / HTML 演示文稿的生成工具。核心设计：**引擎可插拔 + 契约先行**。
当前已发版至 `v0.2.3`，交付形态为 Docker Compose（详见[容器化交付](#容器化交付)）。

## 目录导航

- `docs/research/` — 研究过程、实验笔记（草稿纸，可乱可过时）
- `docs/adr/` — 架构决策记录（选型定稿，索引见 `docs/adr/README.md`）
- `docs/contracts/` — 前后端/引擎契约（唯一事实来源，语言无关）
- `docs/vc_design/` — VC设计文档（对象/状态/接口/流程/约束）
- `docs/DEVELOPMENT.md` — 开发指南（本机服务拓扑、已知陷阱）
- `docs/DEPLOYMENT.md` — 部署指南（安装、验证、备份、升级、离线、排障）
- `packages/engine/` — PPT 生成引擎（抽象协议 + 各实现）
- `packages/backend/` — 后端服务（FastAPI + SQLAlchemy）
- `packages/frontend/` — 前端应用（Next.js + React + Ant Design）
- `packages/shared/` — 共享产物（仅由契约生成，禁止手写）
- `lib/ppt-master/`、`lib/guizang-ppt-skill/` — 上游技能（git submodule，只读）
- `tests/` — 跨包测试（`contract/` 契约测试；`e2e/` 端到端，尚无用例）
- `scripts/` — 开发、测试、类型生成、发版与升级脚本
- `deploy/` — 交付编排（`docker-compose.yml` + `entrypoint.sh` + `.env.example`），运行态落 `deploy/data/`
- `designs/` — UI 与 logo 设计稿

## 技术栈

- **后端**：Python + FastAPI + SQLAlchemy + SQLite
- **前端**：Next.js (App Router) 15 + React 19 + TypeScript + Ant Design 5
- **引擎**：可插拔，按职责分草稿引擎与输出引擎两类（见下节）
- **契约**：OpenAPI 3.1 + JSON Schema，类型由 `scripts/generate-types.sh` 生成
- **依赖单一入口**：`packages/backend/requirements.txt`（`-r` 引用引擎与上游技能清单，勿拍平勿改名）

## 引擎

协议定义在 `packages/engine/core/protocol.py`（`PptEngine` / `DraftEngine` / `OutputEngine`），
登记在 `packages/backend/src/app/engines/registry.py`。新增引擎只实现协议 + 加一项登记，不改调用方；
能力位（如逐页预览）由实现类是否真实实现钩子自动派生，不按引擎名写死。

| 注册名 | 类型 | 说明 |
|---|---|---|
| `ppt_master_draft` | 草稿 | 直接处理上传文件，抽取内容生成 Markdown 草稿 |
| `claude_draft` | 草稿 | 经 Claude Code 理解材料后生成结构化 Markdown 草稿 |
| `ppt_master` | 输出 | SVG 版面 → 原生可编辑 PPTX，支持多风格/多尺寸 |
| `guizang_ppt` | 输出 | 基于 guizang-ppt-skill 输出单文件网页 PPT |

各引擎都通过 Claude Code 拉起子进程调用大模型，**没有离线退路**：运行环境必须能访问
Anthropic 兼容端点（公网、代理或企业内部兼容网关，三者择一）。凭证由使用者自行配置，
dev 形态走主机上的 CLI 配置，容器形态走 `deploy/.env`（见 `docs/DEPLOYMENT.md` 第四节）。

## 快速开始（开发形态）

```bash
# 1) 取代码与上游技能
git clone --recurse-submodules <本仓库地址>
cd "<项目目录>" && git submodule update --init --recursive

# 2) 装 Python 依赖（单一入口，含引擎与上游技能清单）
python3 -m venv .venv && source .venv/bin/activate
pip install -r packages/backend/requirements.txt && pip install pytest

# 3) 装前端依赖
cd packages/frontend && npm install && cd -

# 4) 一条命令起后端 + 前端
./scripts/dev.sh
```

访问：

- 前端：http://localhost:3180 （`-H 0.0.0.0`，同网段其他机器也可打开）
- 后端 API：http://127.0.0.1:8180 ，Swagger 在 `/docs`（**刻意只绑回环**，外部访问走前端代理）
- 端口可在仓库根 `.env` 用 `BACKEND_PORT` / `FRONTEND_PORT` 覆盖，脚本不硬编码

⚠️ 只起一个前端实例。手工 `npm run dev -- --port 3180` 的代理目标兜底是 **8080**，而
`dev.sh` 显式传 **8180**——两者同时在跑时界面都"正常"但读的是不同后端、不同库。起服务前先
`lsof -nP -iTCP -sTCP:LISTEN | grep -E '3180|8080|8180'` 看一眼。更多本机拓扑与已知陷阱见
`docs/DEVELOPMENT.md`。

## 容器化交付

编排文件在 `deploy/`，两个常驻服务（`app` 后端 + 引擎、`web` 界面）加一个一次性 `data-init`
（把 bind 卷属主交给容器运行身份，真实 Linux 首装的必需项），全部可写状态落 `deploy/data/`，
删容器不丢数据。

```bash
cd deploy
cp .env.example .env        # 填 ANTHROPIC_* 凭证与 IMAGE_TAG（IMAGE_TAG 必填，无兜底）

docker compose up -d --build     # 源码构建
docker compose up -d --no-build  # 用交付包导入的镜像（离线场景必须带 --no-build）
```

访问 http://localhost:3000 （端口由 `WEB_PORT` 控制）。自检：

```bash
docker compose ps           # 两个服务均 running / healthy
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/
curl -s http://localhost:3000/api/engines
```

发版与升级（脚本按自身位置定位仓库根，任意目录调用等价）：

```bash
PLATFORMS=linux/amd64 bash scripts/release.sh 0.3.0   # 出交付包：按平台导 tar + 校验和
bash scripts/upgrade.sh 0.3.0                         # 源码构建升级
bash scripts/upgrade.sh 0.3.0 dist                    # 从交付目录离线导入
bash scripts/upgrade.sh 0.3.0 dist --check            # 只验镜像已备好，不动在跑的服务
```

升级次序固定为**预检 → 备镜像 → 停服 → 备份 `data/` → 改 TAG → 起 → 验健康**：库结构会在启动时
自动迁移且不可逆，所以必须先备好新镜像才允许停服。重建容器前先看 `GET /api/jobs` 有无
`status=running` 的任务，否则会连带杀掉引擎拉起的子进程、任务永久停在 running。
完整安装、备份迁移、离线部署与排障见 `docs/DEPLOYMENT.md`。

## 测试与校验

```bash
bash scripts/test.sh [all|unit|contract|e2e]   # 分层：包内单测 → 根契约测试 → e2e
bash scripts/generate-types.sh                 # 契约变更后重新生成 packages/shared 类型
bash scripts/check-drift.sh                    # 漂移检查：openapi.yaml ↔ 后端真实路由，双向比对
```

单测在各包内 `packages/*/tests/`，根 `tests/` 只放 e2e 与 contract。缺 pytest 或缺解释器时脚本
直接报错并给出安装命令，不静默跳过；某一层没有用例时如实标注跳过，不计为通过。

> 测试解释器需要 `httpx`（`tests/conftest.py` 用 starlette TestClient）与 `jsonschema`
> （按契约校验真实响应），已列入 `packages/backend/requirements-dev.txt`。

## 当前状态

已完成：

- [x] 仓库骨架与 monorepo 结构
- [x] 技术栈选型（ADR-003）
- [x] 契约定义与类型生成（`openapi.yaml` + `engine.schema.json`）
- [x] 引擎抽象层（`PptEngine` / `DraftEngine` / `OutputEngine` + 能力位派生）
- [x] 后端服务（路由、任务生命周期、预览端点与路径防护、启动期引擎预检）
- [x] 前端重设计与功能实现（ADR-005~010）
- [x] 引擎实现：ppt_master（原生 PPTX）与 guizang_ppt（网页 PPT），含运行时数据归位（ADR-011/012）
- [x] 容器化交付、离线导入、升级期数据安全（ADR-012/013）
- [x] CI 四条流水线：后端单测、契约（漂移 + 对齐 + 契约测试）、前端（类型检查 + standalone 构建）、e2e 最小闭环（ADR-026）
- [x] 发版 `v0.1.0` → `v0.2.3`

尚未完成：

- [ ] e2e 覆盖面：目前只有"提交 → 调度 → 出片 → 下载"这一条最小闭环（草稿、输出、归属隔离），
      撤销/超时/逐页修改/多身份并发尚未端到端覆盖
- [ ] 前端自动化测试（`packages/frontend/tests/` 仍为空，`npm test` 仍是 TODO；
      CI Frontend 跑的是类型检查与构建，不含组件测试）
- [ ] 契约改用纯 3.1 语法（现存 18 处 3.0 的 `nullable: true`，已由 `api_contract_test.py`
      登记上限、只准减少；过渡期由测试侧归一化，见 ADR-026）
- [ ] 单元测试存在闪断：`packages/engine/tests/test_draft_async.py` 与
      `packages/backend/tests/test_guizang_engine.py` 各有一条在 5 连跑中约 1 次失败，
      表现为后台线程与轮询 deadlines 的竞态。**未修**，见 ADR-026 后果第 4 条

## 架构决策

详见 `docs/adr/`（索引含状态与日期）：

| 编号 | 主题 |
|---|---|
| ADR-001 | 引擎可插拔架构 |
| ADR-002 | Monorepo 结构 |
| ADR-003 | 技术栈选型（FastAPI + Next.js + SQLAlchemy/SQLite） |
| ADR-004 | `title` / `pages` 字段改为可选 |
| ADR-005 | 前端页面重新设计 |
| ADR-006~008 | 前端实现与设计对齐检查、详细对比、差异分析 |
| ADR-009~010 | 前端功能实现完成、运行时错误修复 |
| ADR-011 | guizang 引擎运行时归位 Claude 技能执行 |
| ADR-012 | 交付形态与引擎运行时数据布局 |
| ADR-013 | 升级期数据安全 |
