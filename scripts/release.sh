#!/usr/bin/env bash
# 交付包产出：按平台构建 app / web 两张镜像，导出为可离线导入的 tar，并生成校验和与清单。
#
#   bash scripts/release.sh                # 用最新 tag 推版本号，构建当前机器架构
#   bash scripts/release.sh 0.2.0
#   PLATFORMS="linux/amd64" BASE_IMAGE=<内网仓库>/node:20-slim bash scripts/release.sh 0.2.0
#   bash scripts/release.sh 0.6.2 --mirror cn          # 取包改走国内镜像（apt / pip / npm）
#   SERVICES=web bash scripts/release.sh 0.6.2         # 只出一张（默认 app web 两张一起出）
#       ↑ 分批出包用：app 那张约 2.7GB、要四十分钟，中途被外部因素打断时，先出的 web 留得住。
#         注意每次运行都会整体重写 SHA256SUMS / MANIFEST，分批时请自行合并两份清单。
#   RELEASE_DRY_RUN=1 bash scripts/release.sh 0.6.2 --mirror cn
#       ↑ 只打印将执行的构建命令，不构建、不碰 dist/ 下任何出厂清单
#
# 关于 --mirror：不给它时走官方源，构建行为与本开关加入之前逐字一致；这一步是刻意的
# ——交付包的"从哪儿取的包"必须与"取了哪些包"一样可追溯，走没走过国内镜像由镜像标签
# decksmith.build-mirror 留痕。为什么需要它：2026-10-21 出 0.6.2 amd64 时，同一份依赖
# 清单在那台 x86 机器上 apt 连不上 Debian 官方源、pip 下大轮子读到超时，换国内镜像才跑通。
# 详见 docs/adr/ADR-030-发版脚本国内源开关.md。
#
# 设计约束（都是实测撞出来的，别改回"想当然"的写法）：
#
# 1) **每个 (镜像, 平台) 出一个 tar**。`docker save` 的多平台镜像必须先在 registry 上
#    合成 manifest list 才行；本机无法假设客户有 registry，所以退而求其次按平台分别导出，
#    客户 `docker load` 自己架构的那一份即可。
# 2) **amd64 需要能拉到 amd64 基础镜像**。本环境 Docker Hub 不可达（token 请求超时），
#    故拉不到就明确失败并报出补救办法，不做静默降级、也不假装验证过。
# 3) 版本号默认取自最近的 tag，避免手抄漂移；镜像 tag 与 git tag 对齐（去掉前缀 v）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 最近一个 tag 推版本号；无 tag 时退回显式输入，避免用 0.0.0-dev 造出来历不明的交付物
DEFAULT_VERSION="$(git describe --tags --abbrev=0 2>/dev/null | sed 's/^v//' || true)"
VERSION="${1:-${VERSION:-$DEFAULT_VERSION}}"
if [ -z "$VERSION" ]; then
  echo "错误：无法推断版本号（仓库没有 tag），请把版本号作为第一个参数传入" >&2
  exit 2
fi
# shift 在 $# 为 0 时返回非零，本脚本开了 set -e，直接 shift 会把"没带参数"变成"静默退出"
if [ $# -gt 0 ]; then shift; fi

# ── --mirror 解析：不传＝official＝一行源都不改 ──
MIRROR="${MIRROR:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --mirror)   MIRROR="${2:-}"; shift 2 || true ;;
    --mirror=*) MIRROR="${1#*=}"; shift ;;
    *) echo "错误：未知参数 $1（用法见脚本头部注释）" >&2; exit 2 ;;
  esac
done

APT_MIRROR=""; APT_SECURITY_MIRROR=""; PIP_INDEX_URL=""; PIP_EXTRA_INDEX_URL=""
PIP_TIMEOUT=""; PIP_RETRIES=""; NPM_REGISTRY=""
case "$MIRROR" in
  ""|official)
    MIRROR=official
    ;;
  cn)
    # apt 走 http 是实测结果：那张 x86 机器的加密流量被网络侧换过证书，容器里 apt 验不过
    # tuna 的 https（"certificate issuer is unknown"），退回 http 才装上；apt 的索引本身
    # 带签名校验，走 http 不降低完整性保证。pip / npm 的 https 同机实测可过，保持 https。
    APT_MIRROR="http://mirrors.tuna.tsinghua.edu.cn/debian"
    APT_SECURITY_MIRROR="http://mirrors.tuna.tsinghua.edu.cn/debian-security"
    # pip 首选阿里：2026-10-26 在那台 x86 构建机上同一网络路径实测六个源，清华当时只有
    # 约 135 KB/s（一个 79KB 的包下了 137 秒仍断流，四十分钟的构建毁在这一步），
    # 阿里约 1.9 MB/s、南大 0.95 MB/s、华为 0.75 MB/s。清华降为备用索引（阿里缺某个
    # 钉住的版本时仍能落到它上面）。注意备用索引只能传一个地址——Dockerfile 里是
    # `--extra-index-url "$PIP_EXTRA_INDEX_URL"` 整体引用，塞两个会被拆开传错。
    PIP_INDEX_URL="https://mirrors.aliyun.com/pypi/simple"
    PIP_EXTRA_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
    PIP_TIMEOUT="120"
    PIP_RETRIES="10"
    NPM_REGISTRY="https://registry.npmmirror.com"
    ;;
  *)
    echo "错误：--mirror 只认 official（默认）或 cn，收到：${MIRROR}" >&2
    exit 2
    ;;
esac

# 一张镜像从声明到落地都记这个数组，构建与 RELEASE_DRY_RUN 打印共用同一份参数，
# 避免"试跑打印的是复制品、真跑的是另一份"这种假绿灯
MIRROR_ARGS=(
  --build-arg "BUILD_MIRROR=$MIRROR"
  --build-arg "APT_MIRROR=$APT_MIRROR"
  --build-arg "APT_SECURITY_MIRROR=$APT_SECURITY_MIRROR"
  --build-arg "PIP_INDEX_URL=$PIP_INDEX_URL"
  --build-arg "PIP_EXTRA_INDEX_URL=$PIP_EXTRA_INDEX_URL"
  --build-arg "PIP_TIMEOUT=$PIP_TIMEOUT"
  --build-arg "PIP_RETRIES=$PIP_RETRIES"
  --build-arg "NPM_REGISTRY=$NPM_REGISTRY"
)

SERVER_ARCH="$(docker info --format '{{.Architecture}}')"
# stock dockerd 在 x86 机器上报的是 `x86_64`，而 OCI 只认规范名 `amd64`——直接拼进
# --platform 会得到 `linux/x86_64`，构建器不报错、只在跑到中途甩一句
# `failed to receive status: rpc error: ... EOF`，看不出根因（2026-10-26 出 0.6.3 时
# 在那台 x86 构建机实测撞到；同一脚本在 29.4 上报 `x86_64` 之外还曾报过 `aarch64`，
# 说明回报值随 Docker 版本而变，不能只按本机实测的一种写死）。
# 显式传了 PLATFORMS 就照办，本函数只负责兜住默认推导这一条路。
case "$(uname -m)" in
  x86_64 | amd64) NATIVE_ARCH=amd64 ;;
  aarch64 | arm64) NATIVE_ARCH=arm64 ;;
  *) NATIVE_ARCH="$SERVER_ARCH" ;;
esac
case "$SERVER_ARCH" in
  x86_64 | amd64) DOCKER_ARCH=amd64 ;;
  aarch64 | arm64) DOCKER_ARCH=arm64 ;;
  *) DOCKER_ARCH="$SERVER_ARCH" ;;
esac
if [ "$NATIVE_ARCH" != "$DOCKER_ARCH" ]; then
  echo "提醒：本机 CPU 是 $NATIVE_ARCH，docker 回报 $SERVER_ARCH，默认平台按本机 CPU 归一化为 linux/$NATIVE_ARCH" >&2
fi
PLATFORMS="${PLATFORMS:-linux/${NATIVE_ARCH}}"
BASE_IMAGE="${BASE_IMAGE:-node:20-slim}"
# 与 packages/backend/Dockerfile 的 ARG 默认值保持一致；升版需重新端到端验证
CLAUDE_VERSION="${CLAUDE_VERSION:-2.1.197}"
OUT="$ROOT/dist"
IMAGE_PREFIX="${IMAGE_PREFIX:-decksmith}"

# 试跑模式把产物目录指到临时目录：本脚本会**整体重写** dist/SHA256SUMS 与 MANIFEST.txt，
# 少了这道保护，一次试跑就能把上一版出厂记录抹掉（2026-10-16、10-21 各丢过一次）。
if [ "${RELEASE_DRY_RUN:-0}" = "1" ]; then
  OUT="$(mktemp -d)"
fi

echo "=== DeckSmith 交付包产出 ==="
echo "版本      : $VERSION"
echo "目标平台  : $PLATFORMS"
echo "基础镜像  : $BASE_IMAGE"
echo "取包源    : $MIRROR"
if [ "$MIRROR" = "cn" ]; then
  echo "            apt=$APT_MIRROR  pip=$PIP_INDEX_URL  npm=$NPM_REGISTRY"
fi
echo "claude    : ${CLAUDE_VERSION}（构建期钉住；装到别的版本会让构建当场失败）"
echo "产物目录  : $OUT"
if [ "${RELEASE_DRY_RUN:-0}" = "1" ]; then
  echo "模式      : 试跑（只打印命令，不构建、不动真 dist/）"
fi
echo

# ── 前置自检：缺任一项都直接停，不要产出半成品交付包 ──
command -v docker >/dev/null 2>&1 || { echo "错误：未找到 docker" >&2; exit 2; }
docker buildx version >/dev/null 2>&1 || { echo "错误：需要 buildx（Compose V2 自带的 Docker）" >&2; exit 2; }
for probe in lib/ppt-master/AGENTS.md lib/ppt-master/skills/ppt-master/SKILL.md lib/guizang-ppt-skill/SKILL.md; do
  [ -f "$probe" ] || {
    echo "错误：上游技能内容不完整，缺 $probe" >&2
    echo "      先执行：git submodule update --init --recursive" >&2
    exit 2
  }
done
# 编排结构校验放在**临时副本**里做，不在 deploy/ 原地跑。
# 原因（x86_64 干净环境实测撞到）：compose 对**服务级** `env_file` 严格要求文件存在，
# 而 deploy/.env 装的是运行期凭证、按规矩不入库——于是刚 clone 出来的机器（客户机 / CI）
# 跑本脚本会被这道自家守卫拦死，可出包本身并不需要一份真 .env。
# 副本里拿 .env.example 顶替 .env：只满足"文件存在且可解析"，凭证不参与构建。
# 仍显式传 IMAGE_TAG=$VERSION，让校验对准本次要出的标签，而不是模板里写死的那个。
# 别把这道校验当版本守卫：模板自带 IMAGE_TAG 时 compose 不会因缺值报错，
# 它只保证结构与插值可解析；模板标签是否跟上发版由下面那段提醒负责。
PREFLIGHT_DIR="${TMPDIR:-/tmp}/decksmith-compose-preflight"
mkdir -p "$PREFLIGHT_DIR"
cp deploy/docker-compose.yml "$PREFLIGHT_DIR/"
if [ -f deploy/.env.example ]; then
  cp deploy/.env.example "$PREFLIGHT_DIR/.env"
else
  : > "$PREFLIGHT_DIR/.env"
fi
( cd "$PREFLIGHT_DIR" && IMAGE_TAG="$VERSION" docker compose config --quiet ) \
  || { echo "错误：deploy/docker-compose.yml 校验未通过（校验副本：$PREFLIGHT_DIR）" >&2; exit 2; }
# 顺带提醒：模板里的 IMAGE_TAG 应随发版更新，否则运维 cp 出来的 .env 指向旧镜像
if [ -f deploy/.env.example ] && ! grep -q "^IMAGE_TAG=$VERSION\$" deploy/.env.example; then
  echo "提醒：deploy/.env.example 的 IMAGE_TAG 不是 ${VERSION}，发版后请同步更新" >&2
fi

mkdir -p "$OUT"
: > "$OUT/SHA256SUMS"
MANIFEST="$OUT/MANIFEST.txt"
MIRROR_NOTE="官方源"
if [ "$MIRROR" = "cn" ]; then
  MIRROR_NOTE="国内镜像（apt/pip/npm 换源；只换取包地址，取哪些包不变）"
fi

{
  echo "DeckSmith 交付包"
  echo "版本      : $VERSION"
  echo "构建时间  : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "git 提交  : $(git rev-parse --short HEAD)  分支: $(git rev-parse --abbrev-ref HEAD)"
  echo "构建环境  : $(docker version --format '{{.Server.Version}}') / 本机架构 ${NATIVE_ARCH}（docker 回报 ${SERVER_ARCH}）/ 目标平台 ${PLATFORMS}"
  echo "基础镜像  : $BASE_IMAGE"
  echo "取包源    : $MIRROR — $MIRROR_NOTE"
  echo
  echo "导入方式（客户机只需自己架构的那一份）："
  echo "  gzip -dc < 文件名.tar.gz | docker load"
  echo
  echo "清单："
} > "$MANIFEST"

# SERVICES 只用于分批出包（大镜像那轮被打断时，先出的小镜像能留得住），默认仍两张一起出。
SERVICES="${SERVICES:-app web}"
for svc in $SERVICES; do
  case "$svc" in app|web) ;; *) echo "错误：SERVICES 只认 app / web，收到：$svc" >&2; exit 2 ;; esac
done

for platform in $PLATFORMS; do
  arch="${platform##*/}"
  for svc in $SERVICES; do
    case "$svc" in
      app) dockerfile="packages/backend/Dockerfile";  image="$IMAGE_PREFIX/app:$VERSION" ;;
      web) dockerfile="packages/frontend/Dockerfile"; image="$IMAGE_PREFIX/web:$VERSION" ;;
    esac

    tar_path="$OUT/decksmith-${svc}-${VERSION}-linux-${arch}.tar.gz"
    staging="$OUT/.staging-${svc}-${arch}.tar"
    echo ">>> 构建 $image  [$platform]"

    args=(buildx build --platform "$platform" -f "$dockerfile" -t "$image"
          --build-arg "BASE_IMAGE=$BASE_IMAGE"
          "${MIRROR_ARGS[@]}"
          --output "type=docker,dest=$staging")
    # web 的接口代理目标在构建期固化，必须显式传后端 service 名与端口
    [ "$svc" = "web" ] && args+=(--build-arg BACKEND_HOST=app --build-arg BACKEND_PORT=8080)
    [ "$svc" = "app" ] && args+=(--build-arg "CLAUDE_VERSION=$CLAUDE_VERSION")

    # 试跑打印的就是上面这份 args，不另写一遍——否则"试跑看着对、真跑跑的是另一套"
    if [ "${RELEASE_DRY_RUN:-0}" = "1" ]; then
      echo "DRYRUN | docker ${args[*]} ."
      continue
    fi

    if ! docker "${args[@]}" .; then
      echo "!!! 失败：$svc [$platform]" >&2
      echo "    若报『failed to fetch anonymous token』或『no matching manifest for linux/amd64』，" >&2
      echo "    说明该架构的基础镜像在当前网络下拉不到。处置二选一：" >&2
      echo "      1) BASE_IMAGE=<内网加速器>/node:20-slim（须含 $arch 变体）后重跑" >&2
      echo "      2) 换到能访问镜像仓库的机器或 CI 上重跑本脚本" >&2
      echo "    若报『failed to calculate checksum ...: not found』而文件确实在（连 deploy/、" >&2
      echo "    lib/ 整目录都说找不到），那是上一次失败构建留下的半截 buildkit 缓存在骗人，" >&2
      echo "    不是仓库缺文件——先 docker buildx prune -af 清缓存再重跑（2026-10-26 实测）。" >&2
      echo "    在该架构上真跑成功之前，不要把它写成已验证。" >&2
      exit 1
    fi

    # gzip 直接压暂存件（成功后 gzip 自行删除输入），再 mv 覆盖到最终名，全程不需要 rm
    gzip -f "$staging"
    mv -f "${staging}.gz" "$tar_path"

    size="$(wc -c < "$tar_path" | tr -d ' ')"
    if command -v sha256sum >/dev/null 2>&1; then
      sum="$( (cd "$OUT" && sha256sum "$(basename "$tar_path")") | awk '{print $1}')"
    else
      sum="$( (cd "$OUT" && shasum -a 256 "$(basename "$tar_path")") | awk '{print $1}')"
    fi
    printf '%s  %s\n' "$sum" "$(basename "$tar_path")" >> "$OUT/SHA256SUMS"
    printf '  %-14s %-14s %-10s %s B  %s\n' "$svc" "$platform" "$VERSION" "$size" "$sum" >> "$MANIFEST"
    echo "    -> $(basename "$tar_path")  ${size} B"
  done
done

# ── 随包附运维套件：只有 tar 的交付包在客户机上起不来（缺编排、缺 .env 模板、缺升级脚本）──
# 目录结构刻意与仓库一致（ops/deploy/、ops/scripts/），upgrade.sh 按仓库根算相对路径，
# 这样运维把 ops/ 整个搬到目标机就能直接跑，不必自己猜文件放哪。
OPS="$OUT/ops"
mkdir -p "$OPS/deploy" "$OPS/scripts" "$OPS/docs"
cp deploy/docker-compose.yml "$OPS/deploy/"
# 包内副本的 IMAGE_TAG 直接对齐本次版本（不动仓库模板）：否则 MANIFEST 里“IMAGE_TAG 已置
# $VERSION”就成了假话，运维 cp 出来的 .env 会指向旧镜像、进而被 compose 拦下。
sed "s|^IMAGE_TAG=.*|IMAGE_TAG=${VERSION}|" deploy/.env.example > "$OPS/deploy/.env.example"
cp scripts/upgrade.sh "$OPS/scripts/"
chmod +x "$OPS/scripts/upgrade.sh"
cp docs/DEPLOYMENT.md "$OPS/docs/安装与升级.md"
{
  echo
  echo "随包运维套件 ops/（目录结构与仓库一致，整体搬到目标机即可）："
  echo "  deploy/docker-compose.yml  编排定义，IMAGE_TAG 必填"
  echo "  deploy/.env.example        复制为 deploy/.env 填凭证，IMAGE_TAG 已置 $VERSION"
  echo "  scripts/upgrade.sh         升级唯一规定动作：先备份 data/ 再换镜像"
  echo "  docs/安装与升级.md          安装、验证、备份、升级、排障全文"
  echo
  echo "就地升级既有部署（务必保留原 .env 与 data/，两条路径今天均在验证机实测通过）："
  echo "  1) 把 ops/scripts/upgrade.sh 覆盖到现有代码树的 scripts/，"
  echo "     ops/deploy/docker-compose.yml 覆盖到现有 deploy/；"
  echo "     不要覆盖 deploy/.env（凭证与 IMAGE_TAG 在里面）与 deploy/data/（全部业务数据）"
  echo "  2) 到 tar 所在目录执行：bash scripts/upgrade.sh $VERSION ."
  echo "全新安装（本机还没有过部署）：cd ops && bash scripts/upgrade.sh $VERSION .."
  echo "     注意交付目录参数是 ..——tar 与 ops/ 同级；ops/deploy 下只有模板，没有你的 .env"
} >> "$MANIFEST"

echo
echo "=== 完成 ==="
echo "清单 : $MANIFEST"
echo "校验 : $OUT/SHA256SUMS   （客户机 shasum -a 256 -c SHA256SUMS）"
echo
echo "注意：交付前请核对本次实际构建的架构与目标客户机一致。"
echo "     只在 Linux/aarch64 上完整验证过；其他架构首次使用必须先跑一次本脚本确认成功。"
