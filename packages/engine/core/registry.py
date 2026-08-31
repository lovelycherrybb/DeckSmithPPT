"""引擎注册表：登记可用的引擎实现。"""

from engine.core.protocol import PptEngine

_ENGINES: dict[str, PptEngine] = {}


def register(name: str, engine: PptEngine) -> None:
    if name in _ENGINES:
        raise ValueError(f"引擎已存在: {name}")
    _ENGINES[name] = engine


def get(name: str) -> PptEngine:
    try:
        return _ENGINES[name]
    except KeyError:
        raise KeyError(f"未注册的引擎: {name}") from None


def list_engines() -> list[str]:
    return sorted(_ENGINES)
