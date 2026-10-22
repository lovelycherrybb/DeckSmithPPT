# DeckSmith app 镜像：后端 + engine + claude CLI + 上游技能内容
#
# 构建（上下文必须是仓库根，因为要带 packages/engine 与 lib/）：
#   docker build -f packages/backend/Dockerfile -t decksmith/app:dev .
#
# 依据 docs/adr/ADR-012：
#   * 基础镜像取 node:20-slim（Debian bookworm）—— 一张镜像同时拿到 Node 与 apt 可装的
#     Python 3.11（满足 requires-python ">=3.11"，且避开开发机的 3.14；3.11 的
#     manylinux 轮子覆盖成熟）。Node 20 满足 claude CLI 与 Next 15 的最低版本。
#   * 决策 8：全镜像**只有一个 Python 环境**（/opt/venv），其 bin 排在 PATH 首位，
#     使 claude 子进程调技能脚本用的 `python3` 与引擎的 `sys.executable` 同一解释器。
#     开发机上这两者是分裂的（PATH python3 缺 skia-pathops），镜像里必须掐死。
#   * 决策 4：技能内容随仓库树 COPY 进只读层；凭证与可写数据一律不烤进来。

# 实测本网络环境**拉不到 Docker Hub**（auth.docker.io token 请求超时），故基础镜像
# 提为可覆盖参数：有加速器的内网直接指自己的仓库，无需改本文件。
#   docker build --build-arg BASE_IMAGE=镜像仓库/node:20-slim ...
ARG BASE_IMAGE=node:20-slim
FROM ${BASE_IMAGE}

# ── 取包源开关 ──
# 默认全部为空＝一行源都不改，构建行为与加这个开关之前完全一致；只有
# scripts/release.sh 以 MIRROR=cn 显式传入时才生效。
# 为什么要有（2026-10-21 实测）：同一份依赖清单，境外源在国内机器上会中途断——
# 那台 x86 机器 apt 直接连不上 deb.debian.org，pip 下 68.9MB 的轮子读到超时。
# 换源只改"从哪儿取包"，不改"取哪些包"。走没走过国内源由下面
# decksmith.build-mirror 标签随镜像留痕，交付包事后可核对。
ARG BUILD_MIRROR=official
ARG APT_MIRROR=
ARG APT_SECURITY_MIRROR=
ARG PIP_INDEX_URL=
ARG PIP_EXTRA_INDEX_URL=
ARG PIP_TIMEOUT=
ARG PIP_RETRIES=
ARG NPM_REGISTRY=

# claude CLI 必须钉版本。不钉则 `npm install -g @anthropic-ai/claude-code` 取的是
# **构建当时** registry 的最新版，与开发机上装的 CLI 无关——实测三处各异：
# npm 注册表 2.1.272 / 我们镜像 2.1.197 / 开发机 CLI 2.1.105。
# 默认值取"已在 Debian 12 虚拟机上端到端出片验证过"的那个版本；
# 升版须显式 --build-arg CLAUDE_VERSION=... 并重新验证，不接受静默替换。
ARG CLAUDE_VERSION=2.1.197

# 版本随镜像留痕：现场用 `docker inspect decksmith/app:<版本>` 即可核对实际 CLI 版本
LABEL decksmith.claude-version="${CLAUDE_VERSION}" \
      decksmith.components="backend,engine,claude-cli,upstream-skills" \
      decksmith.build-mirror="${BUILD_MIRROR}"

# APT_MIRROR 为空时整段跳过。不为空时只替换 Debian 归档地址：bookworm 的源在
# deb822 格式的 /etc/apt/sources.list.d/debian.sources 里，老镜像才是
# /etc/apt/sources.list，两处都试一遍。
# 安全更新那条必须先替换——`http://deb.debian.org/debian` 是
# `http://deb.debian.org/debian-security` 的**前缀**，顺序反了就拼出一个不存在的路径。
RUN set -eux; if [ -n "$APT_MIRROR" ]; then \
      for f in /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list; do \
        [ -f "$f" ] || continue; \
        sed -i "s|http://deb.debian.org/debian-security|${APT_SECURITY_MIRROR}|g; s|http://security.debian.org/debian-security|${APT_SECURITY_MIRROR}|g; s|http://deb.debian.org/debian|${APT_MIRROR}|g" "$f"; \
      done; \
    fi

# procps: 引擎用 `ps -p` 判进程存活（_process_alive）；curl 给 HEALTHCHECK；
# git: claude CLI 会 shell 外调它（会话 jsonl 里的 gitBranch 字段即其所写），缺失虽不报错
#       但属静默降级，现场难归因，故一并装上；
# CA + tzdata 给出网调用与时区
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         python3 python3-venv python3-pip python3-dev \
         procps curl git ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN python3 -m venv "$VIRTUAL_ENV"

WORKDIR /app

# ── 依赖层：清单链 backend -> engine -> 只读上游，按仓库原层级摆放后一次安装 ──
# 相对 `-r` 是相对**所在文件目录**解析的，拍平或改名会当场断链：
#   packages/backend/requirements.txt   -r ../engine/requirements.txt
#   packages/engine/requirements.txt    -r ../../lib/ppt-master/requirements.txt
#   lib/ppt-master/requirements.txt     -r skills/ppt-master/requirements.txt
COPY packages/backend/requirements.txt /app/packages/backend/requirements.txt
COPY packages/engine/requirements.txt /app/packages/engine/requirements.txt
COPY lib/ppt-master/requirements.txt /app/lib/ppt-master/requirements.txt
COPY lib/ppt-master/skills/ppt-master/requirements.txt /app/lib/ppt-master/skills/ppt-master/requirements.txt

# 取包地址、超时与重试只在开关打开时附加；三个变量都为空时，这一行与开关之前逐字等价。
RUN pip install ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} \
    ${PIP_EXTRA_INDEX_URL:+--extra-index-url "$PIP_EXTRA_INDEX_URL"} \
    ${PIP_TIMEOUT:+--timeout "$PIP_TIMEOUT"} \
    ${PIP_RETRIES:+--retries "$PIP_RETRIES"} \
    -r /app/packages/backend/requirements.txt

# 决策 8 的构建期断言：装得上不等于 import 得到（开发机上就栽在 PATH python3 缺 pathops）
RUN python3 -c "import pathops, uharfbuzz, pptx, fitz, mammoth, openpyxl, flask, curl_cffi" \
    && echo "[build ok] 技能脚本所需 C 扩展与解析库在 venv 解释器内齐备"

# ── 源码层 ──
# 保留仓库层级不可压缩：engine 的 _REPO_ROOT 由 parents[4] 反推、
# backend 的 REPO_ROOT 由 parents[5] 反推，二者都假设自己还在原始深度。
COPY packages/ /app/packages/
COPY lib/ /app/lib/
COPY docs/contracts/ /app/docs/contracts/
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# claude CLI：两个输出引擎的硬依赖，无离线退路（ADR-011 决策 2）。
# 按钉住的版本精确安装，并**断言装到的就是那个版本**——不信任"npm 应该会给我们这个版本"。
# 版本同时落到镜像 label 与 /opt/claude-version，供交付包 MANIFEST 与现场核对取用。
RUN npm install -g --omit=dev ${NPM_REGISTRY:+--registry "$NPM_REGISTRY"} "@anthropic-ai/claude-code@${CLAUDE_VERSION}" \
    && npm cache clean --force \
    && actual="$(claude --version | awk '{print $1}')" \
    && if [ "$actual" != "$CLAUDE_VERSION" ]; then \
         echo "FATAL: 请求 claude ${CLAUDE_VERSION}，实际装到 ${actual}"; exit 1; \
       fi \
    && printf '%s\n' "$actual" > /opt/claude-version \
    && echo "[build ok] claude ${actual} ；git $(git --version | awk '{print $3}')"

# 运行期路径全部指向数据卷，默认值刻意不用仓库内路径
ENV DECKSMITH_JOBS_ROOT=/data/jobs \
    DATABASE_URL=sqlite:////data/db/decksmith.db \
    HOME=/data/home \
    BACKEND_PORT=8080

# 非 root 运行：直接复用 node 基础镜像内置的 node 用户（uid 1000）。
# 不要 useradd --uid 1000 —— 该 UID 已被占用，useradd 会以 exit 4 失败。
# 代码树与技能树归 root 且去写位，运行期只读不改（决策 1 的机制保护）
RUN mkdir -p /data/jobs /data/db /data/decks /data/home \
    && chown -R node:node /data /home/node \
    && chmod -R a-w /app/packages/engine /app/lib
USER node

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${BACKEND_PORT}/docs" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sh", "-c", "cd /app/packages/backend/src && exec uvicorn app.main:app --host 0.0.0.0 --port ${BACKEND_PORT}"]
