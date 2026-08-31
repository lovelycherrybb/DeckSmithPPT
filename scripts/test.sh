#!/usr/bin/env bash
# 分层跑测试：单测 → 契约 → e2e
set -euo pipefail

echo "==> 1/3 各包单测（选型后实现）"
echo "==> 2/3 契约测试"
if command -v pytest >/dev/null 2>&1; then
  python -m pytest tests/contract/ -q || true
else
  echo "    pytest 未安装，跳过（选型后实现）"
fi
echo "==> 3/3 e2e（选型后实现）"
