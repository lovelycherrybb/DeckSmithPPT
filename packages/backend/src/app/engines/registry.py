"""引擎注册表：登记可用引擎及其参数说明书（供 GET /api/engines 返回）。

参数说明书直接来自 docs/contracts/engine.schema.json 的 $defs.ppt_master，
保证与契约单一事实来源一致——改契约后无需改这里的代码。
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


PPT_MASTER_PARAMS = _load_ppt_master_params()

# 引擎注册表：name 必须与 DeckRequest.engine 的枚举值一致
ENGINE_REGISTRY = {
    "ppt_master": {
        "name": "ppt_master",
        "display_name": "PPT Master",
        "description": "基于 AI Agent 的 PPT 生成引擎，支持多风格/多尺寸",
        "params_schema": PPT_MASTER_PARAMS,
    }
}


def list_engines() -> list[dict]:
    return list(ENGINE_REGISTRY.values())


def get_engine_info(name: str) -> dict | None:
    return ENGINE_REGISTRY.get(name)
