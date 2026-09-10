"""引擎注册表：登记可用的引擎实现。

引擎类型：
- 草稿引擎（DraftEngine）：将用户文件转化为 Markdown 草稿
- 输出引擎（OutputEngine）：将 Markdown 内容生成最终展示文件
"""

from engine.core.protocol import PptEngine, DraftEngine, OutputEngine
from engine.core.models import EngineType

_ENGINES: dict[str, PptEngine] = {}
_DRAFT_ENGINES: dict[str, DraftEngine] = {}
_OUTPUT_ENGINES: dict[str, OutputEngine] = {}


def register(name: str, engine: PptEngine, engine_type: EngineType = EngineType.DRAFT) -> None:
    """注册引擎实现。"""
    if name in _ENGINES:
        raise ValueError(f"引擎已存在: {name}")
    _ENGINES[name] = engine
    if engine_type == EngineType.DRAFT:
        _DRAFT_ENGINES[name] = engine
    else:
        _OUTPUT_ENGINES[name] = engine


def get(name: str) -> PptEngine:
    """获取引擎实现。"""
    try:
        return _ENGINES[name]
    except KeyError:
        raise KeyError(f"未注册的引擎: {name}") from None


def get_draft_engine(name: str) -> DraftEngine:
    """获取草稿引擎实现。"""
    try:
        return _DRAFT_ENGINES[name]
    except KeyError:
        raise KeyError(f"未注册的草稿引擎: {name}") from None


def get_output_engine(name: str) -> OutputEngine:
    """获取输出引擎实现。"""
    try:
        return _OUTPUT_ENGINES[name]
    except KeyError:
        raise KeyError(f"未注册的输出引擎: {name}") from None


def list_engines() -> list[str]:
    """列出所有引擎名称。"""
    return sorted(_ENGINES)


def list_draft_engines() -> list[str]:
    """列出所有草稿引擎名称。"""
    return sorted(_DRAFT_ENGINES)


def list_output_engines() -> list[str]:
    """列出所有输出引擎名称。"""
    return sorted(_OUTPUT_ENGINES)
