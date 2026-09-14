#!/usr/bin/env bash
# 交付包产出：按平台构建 app / web 两张镜像，导出为可离线导入的 tar，并生成校验和与清单。
#
#   bash scripts/release.sh                # 用最新 tag 推版本号，构建当前机器架构
#   bash scripts/release.sh 0.2.0
#   PLATFORMS="linux/amd64" BASE_IMAGE=<内网仓库>/node:20-slim bash scripts/release.sh 0.2.0
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

SERVER_ARCH="$(docker info --format '{{.Architecture}}')"
PLATFORMS="${PLATFORMS:-linux/${SERVER_ARCH}}"
BASE_IMAGE="${BASE_IMAGE:-node:20-slim}"
OUT="$ROOT/dist"
IMAGE_PREFIX="${IMAGE_PREFIX:-decksmith}"

echo "=== DeckSmith 交付包产出 ==="
echo "版本      : $VERSION"
echo "目标平台  : $PLATFORMS"
echo "基础镜像  : $BASE_IMAGE"
echo "产物目录  : $OUT"
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
( cd deploy && docker compose config --quiet ) || { echo "错误：deploy/docker-compose.yml 校验未通过" >&2; exit 2; }

mkdir -p "$OUT"
: > "$OUT/SHA256SUMS"
MANIFEST="$OUT/MANIFEST.txt"

{
  echo "DeckSmith 交付包"
  echo "版本      : $VERSION"
  echo "构建时间  : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "git 提交  : $(git rev-parse --short HEAD)  分支: $(git rev-parse --abbrev-ref HEAD)"
  echo "构建环境  : $(docker version --format '{{.Server.Version}}') / 本机架构 $SERVER_ARCH"
  echo "基础镜像  : $BASE_IMAGE"
  echo
  echo "导入方式（客户机只需自己架构的那一份）："
  echo "  gzip -dc < 文件名.tar.gz | docker load"
  echo
  echo "清单："
} > "$MANIFEST"

for platform in $PLATFORMS; do
  arch="${platform##*/}"
  for svc in app web; do
    case "$svc" in
      app) dockerfile="packages/backend/Dockerfile";  image="$IMAGE_PREFIX/app:$VERSION" ;;
      web) dockerfile="packages/frontend/Dockerfile"; image="$IMAGE_PREFIX/web:$VERSION" ;;
    esac

    tar_path="$OUT/decksmith-${svc}-${VERSION}-linux-${arch}.tar.gz"
    staging="$OUT/.staging-${svc}-${arch}.tar"
    echo ">>> 构建 $image  [$platform]"

    args=(buildx build --platform "$platform" -f "$dockerfile" -t "$image"
          --build-arg "BASE_IMAGE=$BASE_IMAGE"
          --output "type=docker,dest=$staging")
    # web 的接口代理目标在构建期固化，必须显式传后端 service 名与端口
    [ "$svc" = "web" ] && args+=(--build-arg BACKEND_HOST=app --build-arg BACKEND_PORT=8080)

    if ! docker "${args[@]}" .; then
      echo "!!! 失败：$svc [$platform]" >&2
      echo "    若报『failed to fetch anonymous token』或『no matching manifest for linux/amd64』，" >&2
      echo "    说明该架构的基础镜像在当前网络下拉不到。处置二选一：" >&2
      echo "      1) BASE_IMAGE=<内网加速器>/node:20-slim（须含 $arch 变体）后重跑" >&2
      echo "      2) 换到能访问镜像仓库的机器或 CI 上重跑本脚本" >&2
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

echo
echo "=== 完成 ==="
echo "清单 : $MANIFEST"
echo "校验 : $OUT/SHA256SUMS   （客户机 shasum -a 256 -c SHA256SUMS）"
echo
echo "注意：交付前请核对本次实际构建的架构与目标客户机一致。"
echo "     只在 Linux/aarch64 上完整验证过；其他架构首次使用必须先跑一次本脚本确认成功。"
