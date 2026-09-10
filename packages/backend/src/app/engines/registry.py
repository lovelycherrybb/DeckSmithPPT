"""引擎注册表：登记可用引擎及其参数说明书（供 GET /api/engines 返回）。

参数说明书直接来自 docs/contracts/engine.schema.json 的 $defs.ppt_master，
保证与契约单一事实来源一致——改契约后无需改这里的代码。

引擎类型：
- draft：草稿生成引擎，将用户文件转化为 Markdown 草稿
- output：输出引擎，将草稿内容生成最终展示文件
"""
import json
from pathlib import Path

# 本文件位于 packages/backend/src/app/engines/registry.py
# parents[0]=engines [1]=app [2]=src [3]=backend [4]=packages [5]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[5]
SCHEMA_PATH = REPO_ROOT / "docs" / "contracts" / "engine.schema.json"


def _load_ppt_master_params() -> dict:
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return data["$defs"]["ppt_master"]


def _load_ppt_master_draft_params() -> dict:
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return data["$defs"]["ppt_master_draft"]


def _load_claude_draft_params() -> dict:
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return data["$defs"]["claude_draft"]


def _load_guizang_ppt_params() -> dict:
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return data["$defs"]["guizang_ppt"]


PPT_MASTER_PARAMS = _load_ppt_master_params()
PPT_MASTER_DRAFT_PARAMS = _load_ppt_master_draft_params()
CLAUDE_DRAFT_PARAMS = _load_claude_draft_params()
GUIZANG_PPT_PARAMS = _load_guizang_ppt_params()

# 引擎注册表：name 必须与 DeckRequest.engine 的枚举值一致
ENGINE_REGISTRY = {
    "ppt_master": {
        "name": "ppt_master",
        "display_name": "PPT Master",
        "description": "基于 AI Agent 的 PPT 生成引擎，支持多风格/多尺寸",
        "engine_type": "output",  # 输出引擎
        "params_schema": PPT_MASTER_PARAMS,
    },
    "ppt_master_draft": {
        "name": "ppt_master_draft",
        "display_name": "默认引擎",
        "description": "普通草稿生成引擎，直接处理用户上传的文件并提取内容生成 Markdown 草稿",
        "engine_type": "draft",  # 草稿引擎
        "params_schema": PPT_MASTER_DRAFT_PARAMS,
    },
    "claude_draft": {
        "name": "claude_draft",
        "display_name": "claude 引擎",
        "description": "基于 Claude Code 的草稿生成引擎，通过 AI 理解用户上传的材料并生成结构化的 Markdown 草稿",
        "engine_type": "draft",  # 草稿引擎
        "params_schema": CLAUDE_DRAFT_PARAMS,
    },
    "guizang_ppt": {
        "name": "guizang_ppt",
        "display_name": "Guizang HTML PPT",
        "description": "基于 guizang-ppt-skill 的 HTML 演示文稿引擎，输出单文件网页 PPT",
        "engine_type": "output",  # 输出引擎
        "params_schema": GUIZANG_PPT_PARAMS,
    },
}


def list_engines(engine_type: str | None = None) -> list[dict]:
    """返回引擎列表，支持按类型筛选。"""
    engines = list(ENGINE_REGISTRY.values())
    if engine_type:
        engines = [e for e in engines if e.get("engine_type") == engine_type]
    return engines


def get_engine_info(name: str) -> dict | None:
    return ENGINE_REGISTRY.get(name)
