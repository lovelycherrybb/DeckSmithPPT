#!/usr/bin/env bash
# 检查契约与实现是否漂移（CI 调用）。逻辑在 check-drift.py，本文件只是入口——
# 与 generate-types.sh + gen-engine-types.py 同一分工，CI 那侧的命令不用改。
#
# 这里**不做** `|| true`，也不做任何"取不到数据就当通过"的兜底：
# 漂移检查的价值全在于它能红（见 scripts/test.sh 顶部那两条规矩的来历）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"

command -v "$PY" >/dev/null 2>&1 || { echo "错误：找不到解释器 ${PY}（可用 PYTHON=<path> 指定）" >&2; exit 2; }
exec "$PY" "$ROOT/scripts/check-drift.py"
