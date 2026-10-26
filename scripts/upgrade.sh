#!/usr/bin/env bash
# 升级 DeckSmith 到指定版本。两条硬规矩：**先备好新镜像再停服务**、**停服务后先备份
# data/ 再换镜像**——因为库结构会在启动时自动迁移（补列、列改名后整表搬家），那是不可逆的。
#
#   bash scripts/upgrade.sh 0.3.0              # 从源码构建新镜像
#   bash scripts/upgrade.sh 0.3.0 dist         # 从交付目录里的 *.tar.gz 离线导入
#   bash scripts/upgrade.sh 0.3.0 dist --check # 只做到“镜像已备好”，不动在跑的服务
#   bash scripts/upgrade.sh 0.3.0 dist --force-busy  # 明知有任务在跑仍强行升级（默认拒绝）
#
# 停服务前会先查一次「有没有任务在跑或排队」，查不到按「没能比」处理同样停下（见第 2 节）。
#
# 脚本按自身所在目录定位仓库根，所以在任何目录下用绝对路径调用都等价于在仓库根执行
# （这点是被实测咬出来的：从别处调用也会真的往下走，所以才有 --check）。
#
# 其余设计约束同样是实测出来的：
#   1) compose 的 IMAGE_TAG 必填，脚本负责写 .env，别指望人工同步；
#   2) env_file 只在容器创建时读一次，必须 down/up 重建，restart 不生效；
#   3) 离线导入一律 --no-build，否则 compose 有机会转头去构建（无源码树时当场失败）；
#   4) 备份写完必须验一遍能否列出条目，别留一个坏档当“已备份”。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 位置参数与开关可任意顺序：第 1 个非开关参数是版本号，第 2 个是交付包目录
VERSION=""; TAR_DIR=""; CHECK_ONLY=0; FORCE_BUSY=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)       CHECK_ONLY=1 ;;
    --force-busy)  FORCE_BUSY=1 ;;
    *) if [ -z "$VERSION" ]; then VERSION="$1"
       elif [ -z "$TAR_DIR" ]; then TAR_DIR="$1"
       else echo "错误：多余参数 $1（用法见脚本头部注释）" >&2; exit 2; fi ;;
  esac
  shift
done
if [ -z "$VERSION" ]; then
  echo "用法：bash scripts/upgrade.sh <新版本号> [交付包目录] [--check] [--force-busy]" >&2
  exit 2
fi

command -v docker >/dev/null 2>&1 || { echo "错误：未找到 docker" >&2; exit 2; }
[ -f "$ROOT/deploy/docker-compose.yml" ] || {
  echo "错误：$ROOT 下找不到 deploy/docker-compose.yml（这不是 DeckSmith 仓库根？）" >&2; exit 2; }

# 交付目录必须在 cd deploy 之前定成绝对路径：脚本后面会切换工作目录，届时运维给的
# 相对路径（dist、../delivery）会被换掉含义，实测表现为“tar 就在眼前却报找不到”。
if [ -n "$TAR_DIR" ]; then
  [ -d "$TAR_DIR" ] || { echo "错误：交付包目录不存在：$TAR_DIR" >&2; exit 2; }
  TAR_DIR="$(cd "$TAR_DIR" && pwd)"
fi

cd deploy
[ -f .env ] || { echo "错误：deploy/.env 不存在，先 cp .env.example .env 并填好凭证" >&2; exit 2; }
OLD_TAG="$(grep -E '^IMAGE_TAG=' .env | tail -1 | cut -d= -f2 || true)"
echo "=== DeckSmith 升级：${OLD_TAG:-.env 未设 IMAGE_TAG} → $VERSION ==="

# ── 1. 先把新镜像备好：这一步失败就不该停服务 ─────────────────────
NEED_BUILD=1
if [ -n "$TAR_DIR" ]; then
  found=0
  while IFS= read -r tar_path; do
    [ -f "$tar_path" ] || continue
    echo ">>> 导入镜像 $(basename "$tar_path")"
    gzip -dc "$tar_path" | docker load
    found=1
  done < <(find "$TAR_DIR" -maxdepth 1 -name "decksmith-*-${VERSION}-linux-*.tar.gz")
  if [ "$found" = "1" ]; then
    NEED_BUILD=0
    for img in "decksmith/app:$VERSION" "decksmith/web:$VERSION"; do
      docker image inspect "$img" >/dev/null 2>&1 || {
        echo "错误：导入后仍缺镜像 $img（多半是交付包架构与本机不符，先 uname -m 核对）" >&2
        exit 1; }
    done
  else
    # 显式给了交付目录 = 离线升级意图。这种情况绝不转头去源码构建：
    # 离线机没网、构建必然失败，而且那已经不是我同意的路径了。
    echo "错误：$TAR_DIR 下没有 decksmith-\*-${VERSION}-linux-\*.tar.gz" >&2
    echo "      核对两点：交付包版本号是否为 ${VERSION}；架构是否与本机一致（uname -m）。" >&2
    exit 1
  fi
fi
if [ "$NEED_BUILD" = "1" ]; then
  [ -f "$ROOT/packages/backend/Dockerfile" ] || {
    echo "错误：既没从 $TAR_DIR 导入到镜像，本机也没有源码树可构建" >&2
    echo "      服务未做任何改动。离线升级请把交付包目录作为第二个参数传入。" >&2
    exit 1; }
  echo ">>> 从源码构建 ${VERSION}（服务仍在运行，构建完才停）"
  IMAGE_TAG="$VERSION" docker compose build
fi

if [ "$CHECK_ONLY" = "1" ]; then
  echo ">>> --check：预检与镜像准备完成，未停止服务、未改 .env"
  docker image ls --format '{{.Repository}}:{{.Tag}}' | grep -E "^decksmith/(app|web):$VERSION$"
  exit 0
fi

# ── 2. 停服务前：确认没有在跑/排队的任务 ──────────────────────────
# 终态集合与 packages/frontend/src/lib/jobStatus.ts 的 TERMINAL_STATUSES、契约里的
# JobStatus 保持一致（queued/running 为未结束）。
# 为什么加这道闸：2026-10-26 给生产实例升级时，动手前没查过「有没有人正在用」，
# 是事后补查才发现空闲。停服务会当场打断在跑的生成任务，且不可恢复。
# 查不到一律按「没能比」处理——绝不把"查不到"当成"没有在跑"。
if [ "$FORCE_BUSY" = "1" ]; then
  echo ">>> 已用 --force-busy 跳过在跑任务检查（若此刻真有任务，会被这次停服打断）" >&2
else
  echo ">>> 停服前检查：有没有正在跑或排队的任务"
  BUSY_QUERY='select coalesce(group_concat(status || "=" || cnt, " "), "") from (select status, count(*) as cnt from jobs where status not in ("completed","failed","timeout","cancelled") group by status);'
  # 赋值尾部挂 `|| QUERY_RC=$?`：本脚本开了 set -e，命令替换失败会直接把脚本掐死在
  # 退出码 1（那是"有问题"），而"查不到"要报的是"没能比"(2)，两者必须分得开。
  QUERY_RC=0
  BUSY_JSON="$( ( IMAGE_TAG="${OLD_TAG:-$VERSION}" docker compose exec -T app python3 -c '
import json, sqlite3, sys
try:
    con = sqlite3.connect("file:/data/db/decksmith.db?mode=ro", uri=True)
    rows = con.execute(sys.argv[1]).fetchall()
except Exception as exc:
    print(json.dumps({"ok": False, "why": str(exc)}))
    sys.exit(0)
print(json.dumps({"ok": True, "busy": " ".join("%s=%s" % r for r in rows)}))
' "$BUSY_QUERY" 2>/dev/null ) )" || QUERY_RC=$?
  if [ "$QUERY_RC" -ne 0 ] || ! printf '%s' "$BUSY_JSON" | grep -q '"ok": *true'; then
    echo "错误：查不到任务状态（服务没在跑、库读不到，或 app 镜像内无 python3）。" >&2
    echo "      这按「没能比」处理，不等于「没有在跑」——服务未做任何改动。" >&2
    echo "      请先自行确认没有任务在跑，再重跑本脚本；确实要强行升级时加 --force-busy。" >&2
    exit 2
  fi
  BUSY="$(printf '%s' "$BUSY_JSON" | sed -n 's/.*"busy": *"\([^"]*\)".*/\1/p')"
  if [ -n "$BUSY" ]; then
    echo "错误：有任务未结束（${BUSY}），停止服务会打断它们。服务未做任何改动。" >&2
    echo "      等任务跑完再升级；确实要强行打断时加 --force-busy。" >&2
    exit 1
  fi
  echo "    OK：没有在跑或排队的任务"
fi

# ── 3. 停服务（数据保留）──────────────────────────────────────────
echo ">>> 停止服务"
IMAGE_TAG="${OLD_TAG:-$VERSION}" docker compose down

# ── 4. 备份 data/（先停后包，避免 SQLite 处于写入中间态）──────────
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p backups
BACKUP="backups/data-$STAMP-before-v$VERSION.tar.gz"
if [ -d data ]; then
  echo ">>> 打包 deploy/data/ → $BACKUP"
  tar czf "$BACKUP" data/
  gzip -t "$BACKUP"
  ENTRIES="$(tar tzf "$BACKUP" | wc -l | tr -d ' ')"
  [ "${ENTRIES:-0}" -gt 0 ] || { echo "错误：备份包内无条目，中止升级（镜像未换、数据未动）" >&2; exit 1; }
  echo "    备份 OK：$(du -h "$BACKUP" | cut -f1)，$ENTRIES 个条目"
else
  echo ">>> 尚无 deploy/data/，全新安装，跳过备份"
fi

# ── 5. 写 .env 的 IMAGE_TAG（原文件留 .env.bak，不碰其它内容）─────
cp -p .env ".env.bak-$STAMP"
if grep -qE '^IMAGE_TAG=' .env; then
  sed "s|^IMAGE_TAG=.*|IMAGE_TAG=$VERSION|" .env > ".env.tmp-$STAMP"
else
  cp .env ".env.tmp-$STAMP"; printf '\nIMAGE_TAG=%s\n' "$VERSION" >> ".env.tmp-$STAMP"
fi
mv ".env.tmp-$STAMP" .env
echo ">>> .env 的 IMAGE_TAG 已置为 ${VERSION}（原文件备份 .env.bak-${STAMP}）"

# ── 6. 启动并验健康 ───────────────────────────────────────────────
echo ">>> 启动"
if [ "$NEED_BUILD" = "0" ]; then docker compose up -d --no-build; else docker compose up -d; fi

PORT="$(grep -E '^WEB_PORT=' .env | tail -1 | cut -d= -f2 || true)"; PORT="${PORT:-3000}"
echo ">>> 等待 web 就绪（最多 120s）"
ok=0
for _ in $(seq 1 60); do
  if curl -fsS -m 3 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
if [ "$ok" != "1" ]; then
  echo "!!! web 未就绪。取证三条命令：" >&2
  echo "    cd deploy && docker compose ps" >&2
  echo "    cd deploy && docker compose logs --tail=80 app" >&2
  echo "    回滚：把 .env 的 IMAGE_TAG 改回 ${OLD_TAG:-旧版本} 后 docker compose up -d --no-build" >&2
  exit 1
fi

echo ">>> 容器与镜像"
docker compose ps
echo
echo "=== 升级完成：现在跑的是 $VERSION ==="
echo "备份：deploy/$BACKUP"
echo "回滚：镜像从未删除，把 .env 的 IMAGE_TAG 改回 ${OLD_TAG:-旧版本} 再 up -d --no-build 即可；"
echo "      若新版已改过库结构而要退回旧程序，务必同时解回上面那份 data/ 备份。"
