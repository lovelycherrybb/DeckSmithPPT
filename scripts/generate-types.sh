#!/usr/bin/env bash
# 从 docs/contracts/ 生成各端 TS 类型（契约先行：禁止手写 packages/shared 里的类型）
#
# 工具（装在 packages/frontend，--prefix 指定）：
#   openapi-typescript      → openapi.yaml  → api.d.ts（API 请求/响应类型）
#   json-schema-to-typescript → engine.schema.json → engine.d.ts（引擎参数类型）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CONTRACTS_DIR="$PROJECT_ROOT/docs/contracts"
SHARED_TYPES_DIR="$PROJECT_ROOT/packages/shared/types"
FRONTEND_DIR="$PROJECT_ROOT/packages/frontend"

echo "=== 生成 TypeScript 类型 ==="
echo "契约目录: $CONTRACTS_DIR"
echo "输出目录: $SHARED_TYPES_DIR"
echo ""

mkdir -p "$SHARED_TYPES_DIR"

if ! command -v npx &> /dev/null; then
    echo "错误: npx 未安装，请先安装 Node.js"
    exit 1
fi

# 1. API 接口类型（openapi.yaml → api.d.ts）
echo "1. 生成 API 接口类型..."
if [ -f "$CONTRACTS_DIR/openapi.yaml" ]; then
    (cd "$FRONTEND_DIR" && npx openapi-typescript "$CONTRACTS_DIR/openapi.yaml" -o "$SHARED_TYPES_DIR/api.d.ts")
    echo "   ✅ api.d.ts 生成完成"
else
    echo "   ❌ openapi.yaml 不存在"
    exit 1
fi

# 2. 引擎契约类型（engine.schema.json 是纯 JSON Schema，$defs 拆分生成）
echo "2. 生成引擎契约类型..."
if [ -f "$CONTRACTS_DIR/engine.schema.json" ]; then
    python3 "$PROJECT_ROOT/scripts/gen-engine-types.py" > "$SHARED_TYPES_DIR/engine.d.ts"
    echo "   ✅ engine.d.ts 生成完成"
else
    echo "   ❌ engine.schema.json 不存在"
    exit 1
fi

echo ""
echo "=== 类型生成完成 ==="
ls -la "$SHARED_TYPES_DIR/"
echo ""
echo "使用方式（jsconfig/tsconfig 需把 packages/shared 映射进路径别名）："
echo "  import type { components } from 'shared-types/api';"
