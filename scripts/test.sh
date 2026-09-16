#!/usr/bin/env bash
# 分层测试入口：unit（后端 + 引擎单测）→ contract（契约）→ e2e（未实现则如实标注）
#
#   bash scripts/test.sh              # 三层全跑
#   bash scripts/test.sh unit         # 只跑单测层（CI Backend 用的就是这条）
#   bash scripts/test.sh contract
#   PYTHON=python3.11 bash scripts/test.sh unit   # 指定解释器
#
# 两条规矩都是被实测逼出来的，别改回“想当然”的写法：
#   1) 任何一层真失败 → 退出码非 0。旧版写的是 `python -m pytest ... || true`，契约测试
#      挂了也照样一路绿到底，等于装了一盏永远亮的灯。
#   2) 缺解释器或缺 pytest 就直接报错并给出安装命令，**不许静默跳过**。旧版在 pytest 不在时
#      打印一句“跳过（选型后实现）”然后正常退出——本地和云端可以连续几周显示“全绿”，
#      而实际一个用例都没跑过。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LAYER="${1:-all}"
PY="${PYTHON:-python3}"

command -v "$PY" >/dev/null 2>&1 || {
  echo "错误：找不到解释器 ${PY}（可用 PYTHON=<path> 指定）" >&2
  exit 2
}
"$PY" -c "import pytest" >/dev/null 2>&1 || {
  echo "错误：${PY} 的环境里没有 pytest。安装：" >&2
  echo "  ${PY} -m pip install -r packages/backend/requirements.txt && ${PY} -m pip install pytest" >&2
  exit 2
}

FAILED=0
SKIPPED=""

run_layer() {  # $1=层名显示，其余为 pytest 目标
  local label="$1"; shift
  echo "==> ${label}"
  if "$PY" -m pytest "$@" -q; then
    echo "    ${label} 通过"
  else
    echo "    ${label} 失败" >&2
    FAILED=1
  fi
}

# 单测层必须强制 stub 引擎：packages/engine/tests 会连带拉起 guizang 引擎，真引擎要 spawn
# claude 子进程（离线 CI 里必挂，而且会白烧模型额度）。注意 DECKSMITH_FORCE_STUB 必须把
# guizang_ppt 也归入 stub，否则仍会拉子进程。
do_unit() {
  echo "==> unit（后端 + 引擎单测）"
  if DECKSMITH_FORCE_STUB=1 "$PY" -m pytest packages/backend/tests packages/engine/tests -q; then
    echo "    unit 通过"
  else
    echo "    unit 失败" >&2
    FAILED=1
  fi
}

do_contract() { run_layer "contract（契约测试）" tests/contract; }

do_e2e() {
  # tests/e2e 目前只有一个 .gitkeep。没用例就明说未实现，既不算通过也不算失败，
  # 更不许为了让汇总好看而假装跑过一层。
  if find tests/e2e -name 'test_*.py' 2>/dev/null | grep -q .; then
    run_layer "e2e（端到端）" tests/e2e
  else
    echo "==> e2e：未实现（tests/e2e 无用例），本层跳过，不计为通过"
    SKIPPED="e2e"
  fi
}

case "$LAYER" in
  unit)     do_unit ;;
  contract) do_contract ;;
  e2e)      do_e2e ;;
  all)      do_unit; do_contract; do_e2e ;;
  *) echo "用法：bash scripts/test.sh [all|unit|contract|e2e]" >&2; exit 2 ;;
esac

echo
if [ "$FAILED" = "1" ]; then
  echo "测试未通过（层：${LAYER}）" >&2
  exit 1
fi
echo "测试通过（层：${LAYER}${SKIPPED:+，跳过 ${SKIPPED}}）"
