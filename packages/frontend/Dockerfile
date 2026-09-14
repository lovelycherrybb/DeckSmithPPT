# DeckSmith web 镜像：Next.js standalone 产物
#
#   docker build -f packages/frontend/Dockerfile -t decksmith/web:dev .
#   （上下文必须是仓库根：构建期要读 docs/contracts 生成共享类型）
#
# 两个必须知道的前提（都是实测撞出来的，不是设计偏好）：
#
# 1) **保持仓库层级**，不把前端摊平到 /app。因为：
#      tsconfig.json 的 paths 把 `shared-types/*` 映到 `../../packages/shared/types/*`；
#      scripts/generate-types.sh 由自身位置反推 PROJECT_ROOT 并 cd 到 packages/frontend。
#    二者都假设自己还在原始目录深度里，摊平即当场失效。
#
# 2) **构建期必须跑一次类型生成**。`packages/shared/types/` 是契约生成物、按项目规则 7
#    不入库（.gitignore:31），因此镜像上下文里天然没有它；而 `next build` 的 tsc 阶段会
#    因为 `Cannot find module 'shared-types/api'` 直接失败。npm ci 与编译本身都通，
#    卡点只在这一步——开发机能编，是因为本地残留了上一次生成结果。
#
# 3) rewrites() 是**构建期**烘焙进 standalone 路由清单的，运行期改环境变量无效，
#    故 BACKEND_HOST / BACKEND_PORT 必须走 build arg；容器里 127.0.0.1 是 web 自己，
#    打不到后端，compose 必须传 BACKEND_HOST=app。

# ── 依赖层 ──
# 基础镜像提为参数：实测本环境拉不到 Docker Hub，有加速器的内网可自行指向
ARG BASE_IMAGE=node:20-slim
FROM ${BASE_IMAGE} AS deps
WORKDIR /repo/packages/frontend
COPY packages/frontend/package.json packages/frontend/package-lock.json ./
RUN npm ci

# ── 生成 + 构建层 ──
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS build
WORKDIR /repo

# generate-types.sh 第二步要调 python3 跑 scripts/gen-engine-types.py；node 镜像不含 python3
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

ARG BACKEND_HOST=127.0.0.1
ARG BACKEND_PORT=8080
ENV BACKEND_HOST=$BACKEND_HOST BACKEND_PORT=$BACKEND_PORT NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /repo/packages/frontend/node_modules ./packages/frontend/node_modules
COPY packages/frontend/ ./packages/frontend/
COPY docs/contracts/ ./docs/contracts/
COPY scripts/generate-types.sh scripts/gen-engine-types.py ./scripts/
COPY packages/shared/ ./packages/shared/

# 契约先行：类型由 docs/contracts 生成，禁止手写（AGENTS 规则 1）
RUN bash scripts/generate-types.sh \
    && ls -la packages/shared/types/

RUN npm --prefix packages/frontend run build

# ── 运行层 ──
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# standalone 已含最小 node_modules；静态资源与 public 需另行带入。
# 实测：standalone 把 server.js 放在其**根目录**（内部用 __dirname 作为 distDir 基准），
# 因此 static 与 public 必须落在 /app/.next/static 与 /app/public，而不是复刻仓库层级。
# 复用基础镜像内置的 node 用户（uid 1000）：useradd 会因 UID 已占用以 exit 4 失败
COPY --from=build --chown=node:node /repo/packages/frontend/.next/standalone ./
COPY --from=build --chown=node:node /repo/packages/frontend/.next/static ./.next/static
COPY --from=build --chown=node:node /repo/packages/frontend/public ./public

USER node
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "require('http').get('http://127.0.0.1:'+process.env.PORT+'/',r=>process.exit(r.statusCode<500?0:1)).on('error',()=>process.exit(1))"

CMD ["node", "server.js"]
