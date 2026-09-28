#!/usr/bin/env python3
"""从 docs/contracts/engine.schema.json 的 $defs 生成独立 TS 接口。

json-schema-to-typescript 只输出根 schema 的接口，而 engine.schema.json 的
实际内容全在 $defs 里。本脚本把每个 def 提升为独立 schema 逐一生成后拼接；
$defs 内部 $ref 一律内联展开（枚举/嵌套结构都自包含，无跨文件依赖）。

用法（在仓库根）：
    python3 scripts/gen-engine-types.py > packages/shared/types/engine.d.ts
由 scripts/generate-types.sh 自动调用。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "docs" / "contracts" / "engine.schema.json"

NAME_MAP = {
    "ppt_master": "PptMasterParams",
    "ppt_master_draft": "PptMasterDraftParams",
    "claude_draft": "ClaudeDraftParams",
    "guizang_ppt": "GuizangPptParams",
}
# 只导出前端实际要用的 def（引擎参数 + 接口结构）
WANTED = [
    "ppt_master", "ppt_master_draft", "claude_draft", "guizang_ppt",
    "DraftEngine", "OutputEngine",
]


def to_pascal(name: str) -> str:
    return NAME_MAP.get(name) or "".join(p.capitalize() for p in name.split("_"))


def inline_refs(node, defs):
    """递归把 {'$ref': '#/$defs/X'} 替换为 X 的定义本体（自包含）。"""
    if isinstance(node, dict):
        if "$ref" in node:
            target = node["$ref"].split("/")[-1]
            if target in defs:
                return inline_refs(json.loads(json.dumps(defs[target])), defs)
            return {}
        return {k: inline_refs(v, defs) for k, v in node.items() if not k.startswith("$")}
    if isinstance(node, list):
        return [inline_refs(x, defs) for x in node]
    return node


def main() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    defs = schema.get("$defs", {})

    out = [
        "/* eslint-disable */",
        "/**",
        " * This file was automatically generated from docs/contracts/engine.schema.json.",
        " * DO NOT MODIFY BY HAND. Run `bash scripts/generate-types.sh` to regenerate.",
        " */",
        "",
    ]

    for name in WANTED:
        if name not in defs:
            continue
        d = inline_refs(json.loads(json.dumps(defs[name])), defs)
        d["title"] = to_pascal(name)
        proc = subprocess.run(
            ["npx", "json-schema-to-typescript", "/dev/stdin"],
            input=json.dumps(d, ensure_ascii=False),
            capture_output=True, text=True,
            cwd=str(REPO_ROOT / "packages" / "frontend"),
        )
        if proc.returncode != 0:
            print(f"WARN: {name} 生成失败：{proc.stderr[:200]}", file=sys.stderr)
            continue
        out.append(proc.stdout.strip())
        out.append("")

    # 枚举导出（字面量联合，前端状态映射用）
    # 注意 JobStatus 已改名 EngineJobStatus 并与 openapi 的 JobStatus 分层
    # （queued / cancelled 属编排层，引擎不产出，见 ADR-018）；两个名字都留着导出，
    # 契约里缺哪个就跳过哪个，避免生成器与契约改名互相绑死。
    for enum_name in ("EngineJobStatus", "JobStatus", "EngineType", "TaskType"):
        if enum_name in defs:
            values = defs[enum_name].get("enum", [])
            union = " | ".join(f'"{v}"' for v in values)
            out.append(f"export type {enum_name} = {union};")
            out.append("")

    print("\n".join(out))


if __name__ == "__main__":
    main()
