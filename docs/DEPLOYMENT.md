# DeckSmithPPT 2.0 部署指南（容器化交付）

本文面向安装与运维人员，覆盖**容器化交付形态**的安装、验证、备份、升级与故障排查。
开发环境的本地启动方式见 `docs/DEVELOPMENT.md`，不在本文范围内。

本文所有命令均以仓库根为工作目录，编排目录为 `deploy/`。

## 一、交付形态

系统由两个服务与四个数据卷构成。服务无状态，全部状态落在数据卷，因此重建容器不丢数据。

| 组成 | 内容 | 监听 |
|---|---|---|
| `app` | 后端服务、生成引擎、Claude Code 命令行工具、Node 运行时、随仓库锁定的上游技能内容 | 容器内 8080，不对外发布 |
| `web` | 界面服务（Next.js standalone 产物），承担对 `app` 的接口代理 | 默认 3000，可按 `WEB_PORT` 改 |
| 数据卷 | `deploy/data/jobs`（任务产物与日志）、`data/db`（数据库）、`data/home`（模型工具的技能登记与会话记录）、`data/decks`（归位前的历史产物） | — |

**关于基础镜像**：构建需要 `node:20-slim`（Debian bookworm，内含 Node 20；Python 由 apt
安装，实测为 3.11.2）。若目标环境访问不到 Docker Hub，用 `--build-arg
BASE_IMAGE=<内部仓库>/node:20-slim` 指向自有镜像仓库，或按第九节离线导入。

## 二、前置条件

1. **网络可访问模型服务。** 两个生成引擎都通过 Claude Code 拉起子进程调用大模型，
   **没有离线退路**（项目不保留纯模板生成的备用实现）。因此运行环境必须能访问
   Anthropic 兼容端点：公网直达、走代理、或部署企业内部兼容网关，三者择一。
   此条不满足时系统可安装、界面可用，但无法出片。
2. **已安装 Docker 与 Docker Compose。** 实测验证环境为 Docker 29.4、Compose V2
   （`docker compose` 子命令形式）。
3. **代码与上游技能完整。** 仓库以 git submodule 锁定两份上游技能；缺失时系统会在启动期
   直接拒绝运行。克隆须带 `--recurse-submodules`，已有克隆补执行
   `git submodule update --init --recursive`。
4. **磁盘。** `app` 镜像约 1.9 GB（含模型工具与全部依赖），构建期另有若干 GB 缓存，
   建议预留 20 GB。任务产物单件为 MB 级。容器时区默认 UTC，需按本地时间显示日志时在
   `deploy/docker-compose.yml` 的 `app.environment` 增加 `TZ`。

## 三、安装步骤

第一步，取得代码与技能内容：

```bash
git clone --recurse-submodules <本仓库地址> DeckSmithPPT
cd DeckSmithPPT
```

第二步，准备配置。`deploy/.env.example` 是配置模板，复制为 `deploy/.env` 后填写：

```bash
cd deploy
cp .env.example .env
```

第三步，填写必填项。`ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`、`ANTHROPIC_MODEL`
三项决定能否出片；使用官方端点时 `ANTHROPIC_BASE_URL` 可留空。

第四步，构建并启动：

```bash
docker compose build          # 内网环境按需追加 --build-arg BASE_IMAGE=...
docker compose up -d
```

启动时容器先执行**引擎运行前置条件预检**：任一引擎判定为硬缺失即拒绝启动并返回非零码。
看到 `前置条件缺失（该引擎不可运行）` 时，按其提示的补救命令处理后重新 `up -d`。

第五步，确认就绪：

```bash
docker compose ps                        # 两个服务均为 running / healthy
docker compose logs app | grep runtime   # 应逐条输出「前置条件就绪」
```

## 四、配置项说明

| 变量 | 位置 | 必填 | 说明 |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | `deploy/.env` | 是 | 模型服务凭证。**只进运行时环境，绝不写入镜像层** |
| `ANTHROPIC_BASE_URL` | `deploy/.env` | 否 | Anthropic 兼容端点地址；使用官方端点可留空 |
| `ANTHROPIC_MODEL` | `deploy/.env` | 否 | 指定模型名；留空则由工具默认 |
| `WEB_PORT` | `deploy/.env` | 否 | 对外端口，默认 3000 |
| `BASE_IMAGE` | 构建参数 | 否 | 基础镜像地址，默认 `node:20-slim` |
| `HTTPS_PROXY` | `deploy/.env` | 否 | 仅能走代理出网的环境填写 |

以下路径已在镜像内固化为环境变量，一般无需改动；确需调整时在
`deploy/docker-compose.yml` 的 `app.environment` 覆盖：`DECKSMITH_JOBS_ROOT=/data/jobs`、
`DATABASE_URL=sqlite:////data/db/decksmith.db`、`HOME=/data/home`、`BACKEND_PORT=8080`。

**一处必须知道的限制**：界面到后端的代理地址在**构建期**即被固化进界面产物，运行期修改
环境变量无效。因此改动后端服务名或端口后必须 `docker compose build web` 重新构建，
仅 `up -d` 不会生效。

## 五、安装验证

先确认对外入口与代理接线：

```bash
curl -s -o /dev/null -w "界面 HTTP %{http_code}\n" http://localhost:3000/
curl -s http://localhost:3000/api/engines | head -c 200   # 经界面打到后端，返回引擎清单即通
```

再走一次真实出片。本指南的完整验证必须包含这一步，仅接口返回成功不算装好：
打开界面 → 上传材料 → 选择引擎 → 提交，等待任务转为完成。

排障所需的三个观测点，位置固定：

1. 任务实时日志：`deploy/data/jobs/<任务ID>/run.log`；
2. 任务状态与产物路径：同目录下的 `job.json`；
3. **模型工具的完整会话记录**：`deploy/data/home/.claude/projects/` 下按任务目录转义命名
   的 `.jsonl`。任务显示成功但产物异常时，到该文件末尾查找 `stop_reason` 与
   `API Error` 原文——任务日志会把它蒸馏掉，这里才是原始证据。

## 六、数据、备份与迁移

全部状态位于 `deploy/data/`，四个子目录用途见第一节。停止服务不影响数据。

备份即打包该目录（先停服务，避免数据库文件处于写入中间态）：

```bash
docker compose down
tar czf decksmith-backup-$(date +%F).tgz data/
```

迁移到新环境时，恢复 `data/` 后执行 `docker compose up -d` 即可；代码与镜像需在新环境
重新构建。**不要把 `data/home/.claude` 里的技能软链带到别的机器**——软链记录绝对路径，
换目录即断链；启动预检会按新路径重建。

## 七、升级与回滚

升级：拉取代次、更新技能子模块、重建并滚动启动，数据卷不受影响。

```bash
git pull && git submodule update --init --recursive
cd deploy && docker compose build && docker compose up -d
```

回滚：切回上一代码版本，重新构建后启动即可。建议每次交付都为镜像保留旧标签
（如先 `docker tag decksmith/app:0.1.0 decksmith/app:0.1.0-prev` 再构建新版），
使回滚不必重新构建。

## 八、故障排查

| 现象 | 定位方向 | 处置 |
|---|---|---|
| 容器启动即退出，日志含 `前置条件缺失` | 上游技能未取全，或 Claude Code 不可解析 | 按提示执行 `git submodule update --init --recursive`；确认构建未跳过工具安装层 |
| 任务运行很久后失败、无产物 | 模型端点对超长输出中途掐断，流式结果仍可能报成功 | 查 `run.log` 与第五节第 3 项的会话记录；必要时更换模型或收敛单次输出规模 |
| 界面能打开、接口全部失败 | 界面到后端的代理目标固化在构建期 | `docker compose build web && docker compose up -d` |
| 上传大文件失败或连接被重置 | 请求体上限 50 MB | 确认未改小 `middlewareClientMaxBodySize`；同时检查反向代理链路各层的请求体限制 |
| 预览图裂、产物下载后图片缺失 | 产物中被写入宿主绝对路径 | 检查 `deploy/data/jobs/<ID>/projects/` 下产物文本是否含 `/data/` 开头的绝对路径 |
| 端口占用 | `WEB_PORT` 与既有服务冲突 | 改 `deploy/.env` 的 `WEB_PORT` 后 `docker compose up -d` |
| 涉及 shape 布尔运算的页面失败 | 镜像内出现第二个 Python 解释器，`skia-pathops` 不在其内 | 启动预检会给出告警；确保 PATH 首位为 `/opt/venv/bin`，不要在镜像内另装系统级 Python 包 |

## 九、离线（无外网）部署

无外网环境可以安装，但**模型调用仍需可达端点**，请先确认第二节第 1 项以内部兼容网关形式
满足。构建与传输步骤：

1. 在可联网机器上构建：`docker compose build`；
2. 导出镜像：`docker save decksmith/app:0.1.0 decksmith/web:0.1.0 | gzip > decksmith-images.tgz`；
3. 连同代码仓库一并搬运（须先在该机器执行 `git submodule update --init --recursive`，
   确保 `lib/` 下两份技能内容是完整的）；
4. 目标机导入：`docker load < decksmith-images.tgz`，随后 `docker compose up -d`
   （本地已有镜像则无需再构建）。

## 十、卸载

```bash
cd deploy && docker compose down            # 停服务并删除容器，数据保留
docker compose down --volumes               # 同上，另清理无名卷
rm -rf data/                                # 确认无需历史数据后再执行
```

`data/` 是本系统全部业务数据，删除前请确认已按第六节备份。

## 附：本指南的验证边界

为免把未核实内容当作事实，此处明确区分两类。

**已实测通过**：镜像构建、编排启动、启动预检的正反两向（含技能缺失时确实拒绝启动并返回
非零码）、界面入口与接口代理接线、逐页预览、依赖在 Linux 上全部命中预编译轮子、
数据卷读写边界、`lib/` 只读保护生效。验证平台为 **Linux / aarch64**，Docker 29.4。

**须在交付现场核验**：

1. `linux/amd64`（x86_64 客户机）上的构建与运行。验证环境拉不到任何 amd64 基础镜像，
   该架构的依赖轮子可得性**未经验证**；首次装机请先单独演练一次 `docker compose build`。
2. 带真实凭证的端到端出片。本轮验证使用占位凭证，只覆盖到工具可执行、卷可写、
   技能可发现，未覆盖真实生成链路。
3. 长期运行下的并发与数据库表现。SQLite 加后台线程的组合未在目标环境压测。
