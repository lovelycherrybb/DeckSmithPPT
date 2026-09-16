# DeckSmithPPT 2.0 部署指南（容器化交付）

本文面向安装与运维人员，覆盖**容器化交付形态**的安装、验证、备份、升级与故障排查。
开发环境的本地启动方式见 `docs/DEVELOPMENT.md`，不在本文范围内。

本文所有命令均以仓库根为工作目录，编排目录为 `deploy/`。

## 一、交付形态

系统由两个常驻服务、一个一次性初始化服务与四个数据卷构成。服务无状态，全部状态落在数据卷，因此重建容器不丢数据。

| 组成 | 内容 | 监听 |
|---|---|---|
| `app` | 后端服务、生成引擎、Claude Code 命令行工具、Node 运行时、随仓库锁定的上游技能内容 | 容器内 8080，不对外发布 |
| `web` | 界面服务（Next.js standalone 产物），承担对 `app` 的接口代理 | 默认 3000，可按 `WEB_PORT` 改 |
| `data-init` | 一次性服务：把数据卷宿主目录的属主交给容器运行身份（uid 1000）后即退出。**必需**——宿主目录不存在时由 dockerd 以 root 创建，非 root 的 `app` 会写不进而循环重启；复用 `app` 镜像，故不额外交付 | 无常驻 |
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
| `ANTHROPIC_API_KEY` | `deploy/.env` | 二选一 | 凭证，走 `x-api-key` 头（官方端点）。**只进运行时环境，绝不写入镜像层** |
| `ANTHROPIC_AUTH_TOKEN` | `deploy/.env` | 二选一 | 凭证，走 `Authorization: Bearer` 头（**第三方兼容网关常见**）。与上一项填其一即可，填错类型的表现见第八节 |
| `ANTHROPIC_BASE_URL` | `deploy/.env` | 否 | Anthropic 兼容端点地址；使用官方端点可留空，自建/第三方网关必填 |
| `ANTHROPIC_MODEL` | `deploy/.env` | 否 | 模型名，须与端点支持的名称一致（网关侧模型名常与官方不同） |
| `ANTHROPIC_DEFAULT_SONNET_MODEL`<br>`..._OPUS_MODEL` `..._HAIKU_MODEL` | `deploy/.env` | 第三方网关必填 | claude 内部还按 sonnet/opus/haiku 三档挑模型，默认值是官方 `claude-*` 名；网关上不存在这几档时，**填对 key 也会在后续调用失败**，需一并指到网关自己的模型名。实测这些变量名硬编码在 claude 原生二进制内，容器无需 settings.json |
| `WEB_PORT` | `deploy/.env` | 否 | 对外端口，默认 3000 |
| `BASE_IMAGE` | 构建参数 | 否 | 基础镜像地址，默认 `node:20-slim` |
| `IMAGE_TAG` | `deploy/.env` | **是** | 镜像标签。**故意不给兜底默认值**——写死默认会把人引向旧版本，且标签所指镜像不存在时 compose 会转头构建（离线环境随即失败）。每次发版同步更新 |
| `CLAUDE_VERSION` | `deploy/.env`／构建参数 | 否 | 钉住的 claude CLI 版本（默认 `2.1.197`，即端到端验证过的那个）。构建期会断言实际装到的版本，不符即失败 |
| `HTTPS_PROXY` | `deploy/.env` | 否 | 仅能走代理出网的环境填写 |

**改完 `.env` 必须重建容器才生效**：`env_file` 只在容器创建时读取一次，`up -d` 若判定配置
未变就不会重建。正确动作是 `docker compose up -d --force-recreate app`，随后核对是否真的
注入了：

```bash
docker compose exec app env | grep -c ANTHROPIC     # 应为 2 或 3，0 表示没生效
```

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

程序启动时会自动补齐数据库结构（加列、列改名后整表搬家），并在**动任何 DDL 之前**用
SQLite 在线备份接口把库拍到 `deploy/data/db/backup/`，文件名形如
`decksmith-v0-to-v2-pre-upgrade-<UTC时间>.db`。规则是：一次版本跃迁最多一份、
重复启动不堆文件、**只增不删**——这些快照占的空间很小，但清理责任在运维，
确认新版稳定后再按需清退。整轮迁移跑在单个事务内，中途断电或崩溃会整体回滚，
库仍是升级前的样子（实测证据见 ADR-013）。

## 七、升级与回滚

**升级的唯一规定动作**（先备份数据、再换镜像，中间不停机做别的事）：

```bash
bash scripts/upgrade.sh 0.3.0            # 本机已有镜像，或从源码构建
bash scripts/upgrade.sh 0.3.0 dist       # 离线：从交付目录导入 *.tar.gz 后启动
```

脚本按顺序做：停服务 → 打包 `deploy/data/` 到 `deploy/backups/` 并校验可读 →
导入或构建新镜像 → 幂等改写 `.env` 的 `IMAGE_TAG`（原文件留 `.env.bak-<时间>`）→
启动 → 轮询 web 就绪后打印容器清单。任一步失败即停下，**数据尚未被动过**。

为什么不能省掉备份直接 `docker compose build && up -d`：库结构由程序在启动时自动迁移
（补列、列改名后整表搬家），这是**不可逆**的程序行为。手工路径仍可用，但必须先自行
按第六节打包 `data/`：

```bash
git pull && git submodule update --init --recursive
cd deploy && tar czf "backups/data-$(date +%FT%TZ).tar.gz" data/ \
  && docker compose build && docker compose up -d
```

回滚：镜像从未被删除，把 `.env` 的 `IMAGE_TAG` 改回旧值再
`docker compose up -d --no-build` 即可。若新版已经改过库结构而旧程序读不懂，
先停服务、解回升级前的 `data/` 备份再启动——程序遇到比自身更新的库版本会拒改结构并告警，
但仍应以恢复数据为准。补充一条实测结论：`docker compose down --volumes`
不会删除 bind 卷对应的宿主目录（本交付形态全部是 bind 卷），删数据只有 `rm -rf data/` 一条路。

## 八、故障排查

| 现象 | 定位方向 | 处置 |
|---|---|---|
| 容器启动即退出，日志含 `前置条件缺失` | 上游技能未取全，或 Claude Code 不可解析 | 按提示执行 `git submodule update --init --recursive`；确认构建未跳过工具安装层 |
| 任务十几秒即 `failed`、`run.log` 几乎为空、`result_path` 为 null | 鉴权未通过：凭证没填、鉴权头类型不对（`x-api-key` 与 `Bearer` 二选一）、或改了 `.env` 没重建容器 | 看 `data/home/.claude/projects/<转义目录>/*.jsonl` 末条，若为 `authentication_failed` 即凭证问题；核对 `docker compose exec app env \| grep -c ANTHROPIC`，为 0 就 `--force-recreate app` |
| 任务运行很久后失败、无产物 | 模型端点对超长输出中途掐断，流式结果仍可能报成功 | 查 `run.log` 与第五节第 3 项的会话记录；必要时更换模型或收敛单次输出规模 |
| 界面能打开、接口全部失败 | 界面到后端的代理目标固化在构建期 | `docker compose build web && docker compose up -d` |
| 上传大文件失败或连接被重置 | 请求体上限 50 MB | 确认未改小 `middlewareClientMaxBodySize`；同时检查反向代理链路各层的请求体限制 |
| 预览图裂、产物下载后图片缺失 | 产物中被写入宿主绝对路径 | 检查 `deploy/data/jobs/<ID>/projects/` 下产物文本是否含 `/data/` 开头的绝对路径 |
| 容器反复重启，日志为 `mkdir '/data/home/.claude': Permission denied` | 宿主 `data/` 目录由 dockerd 以 root 创建，容器以 uid 1000 运行写不进 | 编排内置的一次性 `data-init` 服务已在每次启动时修正属主；若仍出现，多半是用了旧版 `docker-compose.yml`，或有人手工把目录改成了别的属主 |
| 某服务依赖检查失败后，另一服务一直停在 `Created` 不启动 | 前置服务不健康会让被依赖服务不进入启动流程 | 修复前者后需**再次执行** `docker compose up -d`（`ps -a` 可看到停留状态），不会自动续启 |
| 端口占用 | `WEB_PORT` 与既有服务冲突 | 改 `deploy/.env` 的 `WEB_PORT` 后 `docker compose up -d` |
| 涉及 shape 布尔运算的页面失败 | 镜像内出现第二个 Python 解释器，`skia-pathops` 不在其内 | 启动预检会给出告警；确保 PATH 首位为 `/opt/venv/bin`，不要在镜像内另装系统级 Python 包 |

## 九、离线（无外网）部署

无外网环境可以安装，但**模型调用仍需可达端点**，请先确认第二节第 1 项以内部兼容网关形式
满足。交付包由仓库自带脚本产出，它按"镜像 × 平台"分别导出、并生成校验和与清单：

1. 在可联网机器上执行（版本号与 git tag 对齐，省略则取最近 tag）：

   ```bash
   git submodule update --init --recursive
   bash scripts/release.sh <版本>
   # 目标机是 x86_64 时：PLATFORMS=linux/amd64 bash scripts/release.sh <版本>
   # 有内网加速器时：  BASE_IMAGE=<内网仓库>/node:20-slim bash scripts/release.sh <版本>
   # 要换 claude CLI 版本：CLAUDE_VERSION=<版本> bash scripts/release.sh <版本>
   #   （版本被钉住且构建期断言实际安装版本，装错即构建失败；改版本需重新端到端验证）
   ```

2. 产物在 `dist/`：`decksmith-{app,web}-<版本>-linux-<架构>.tar.gz`、`SHA256SUMS`、
   `MANIFEST.txt`（内含构建时间、git 提交、架构与校验和）。连同代码仓库一并搬运。
3. 目标机先自验完整性再导入（客户只需自己架构那一份）：

   ```bash
   shasum -a 256 -c SHA256SUMS
   gzip -dc decksmith-app-<版本>-linux-<架构>.tar.gz | docker load
   gzip -dc decksmith-web-<版本>-linux-<架构>.tar.gz | docker load
   cd deploy && cp .env.example .env && 编辑 .env 填凭证与 IMAGE_TAG=<版本>
   docker compose up -d --no-build     # 必须 --no-build：只用镜像，不给 compose 构建的机会
   ```

脚本会在前置自检失败时直接停下（缺 Docker、缺 buildx、上游技能不完整、编排配置不合法都
不产出半成品包）。若确实无法使用脚本，手工命令为
`docker save decksmith/app:<版本> decksmith/web:<版本> | gzip > decksmith-images.tgz`，
但它不产生校验和、也不区分架构，只在应急时使用。

**注意架构必须匹配**：`MANIFEST.txt` 里记录的是实际构建架构，交付前必须核对它与目标机
`uname -m` 一致；不一致时导入的镜像无法运行。

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
数据卷读写边界、`lib/` 只读保护生效；**交付包链路已在真实客户环境形态下跑通**——
`scripts/release.sh` 产出 tar 与校验和，传到一台**全新 Debian 12 虚拟机**（无 git、无
Docker、访问不到 Docker Hub 与 pypi）后 `sha256sum -c` 通过、装 docker-ce、`docker load`
导入、`IMAGE_TAG=0.2.0 docker compose up -d` 起服务，并从异机验证首页与 `/api/engines`
代理、确认数据库在空卷中正常建表。

在该虚拟机上发现并修复了一处**只在真实 Linux 上暴露**的首装阻塞：bind 卷宿主目录由
dockerd 以 root 创建，非 root 的 `app` 写不进而循环重启（开发机的 OrbStack 做 uid 映射，
把它掩盖了）。对策是编排内置一次性 `data-init` 服务。

> **一个已修复的发版缺口（留档）**：`v0.2.0` 的镜像与安装说明可用，但其
> `docker-compose.yml` 尚无 `data-init`，用 stock dockerd 的客户按 `v0.2.0` 首装会在
> `app` 上循环重启。该修复自 **`v0.2.1`** 起生效；手上仍是 `v0.2.0` 包时，临时绕过办法是
> 先执行一次 `sudo chown -R 1000:1000 deploy/data` 再 `up -d`。

验证平台：Linux/aarch64；Docker 29.4（macOS 侧）与 29.8（虚拟机，stock dockerd）。

**版本锚定**：当前交付锚点为 **`v0.2.1`**（已推送远端，含 `data-init`，已在 Debian 12 上
以离线包形式验证过导入与启动）。`v0.2.0` 存在前述 `data-init` 缺口，不要用于 stock
dockerd 首装；`v0.1.0` 早于容器化工作，**按它交付无法安装**。三个已发布 tag 均保持原样
不改写。注意 `main` 目前领先 `v0.2.1` 两个提交（注册表驱动契约测试、引擎能力位修正），
**未重出包**——用 `v0.2.1` 镜像时 `GET /api/engines` 仍会把草稿引擎标为具备
`page_preview`（不影响功能，草稿本就产不出逐页物）。

**须在交付现场核验**：

1. `linux/amd64`（x86_64 客户机）上的构建与运行。验证环境拉不到任何 amd64 基础镜像，
   该架构的依赖轮子可得性**未经验证**；首次交付前先执行
   `PLATFORMS=linux/amd64 bash scripts/release.sh <版本>` 确认能构建成功。
2. 带真实凭证的端到端出片。此前的失败已定位为凭证未填（`authentication_failed`），
   真实生成链路仍待一次成功跑通。
3. 长期运行下的并发与数据库表现。SQLite 加后台线程的组合未在目标环境压测。

**已知待修（不影响能否安装，影响可复现性）**：

1. ~~claude CLI 版本未锁定~~ **已修**： `packages/backend/Dockerfile:65` 是
   `npm install -g @anthropic-ai/claude-code`，取的是**构建当时** npm 上的最新版，与开发机上
   装的那个 claude 无关（实测三处各异：npm 注册表 2.1.272 / 镜像内 2.1.197 / 开发机 CLI
   2.1.105）。后果是重建镜像会**静默更换生成引擎的实际执行者**，出片质量或行为一变，
   无法区分是我们的改动还是 claude 升版。现由 `CLAUDE_VERSION` build-arg 钉死（默认 2.1.197），
   构建期断言实际安装版本、不符即失败，版本同时写入镜像 label 与 `release.sh` 产出信息。
2. ~~镜像内没有 `git`~~ **已修**： claude 会 shell 出去调它（会话记录里的 `gitBranch` 即其所写），
   缺失属静默降级。已补进 `apt-get install`。
