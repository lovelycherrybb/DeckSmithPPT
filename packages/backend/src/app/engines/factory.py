"""引擎工厂：按**注册表**解析引擎实例，本文件不内置任何引擎名清单。

- sys.path 引导：`engine` 包位于仓库的 `packages/` 下。
- 「注册名 → 实现类」的解析只有一个入口：`registry.engine_class()`（读 `_ENGINE_CLASSES`）。
  此前这里另有一份 `if name == "ppt_master" ...` 的名单，与注册表构成**重复知识源**——
  加一个引擎必须改两处，违反 ADR-001 第 4 条「调用方只依赖协议与注册表」。
- `DECKSMITH_FORCE_STUB=1` 的判据同样取自注册表本身，不再维护硬编码白名单：
  曾经的白名单漏掉新引擎时，新引擎会在离线 CI 里绕过该钩子、真的拉起 claude 子进程烧额度
  （这是 ADR-012 契约测试变异验证实际抓到的缺陷）。
- 唯一保留的特例是 `stub`：它是开发/离线测试用的替身，不是对外引擎，故不在 `ENGINE_REGISTRY` 里。
- 未知引擎名一律 `ValueError`，且不静默降级；该校验语义不被任何钩子绕过。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# engine 包的父目录是 packages/，加入 sys.path 后即可 `import engine`。
_PACKAGES_DIR = Path(__file__).resolve().parents[4]
if str(_PACKAGES_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGES_DIR))

from .registry import ENGINE_REGISTRY, engine_class  # noqa: E402
from .stub import StubPptMasterEngine  # noqa: E402

STUB_NAME = "stub"


def _instantiate(name: str):
    """按注册名实例化真实引擎；未注册名抛 ValueError（不静默降级成 stub）。"""
    try:
        cls = engine_class(name)
    except KeyError:
        raise ValueError(
            f"未知引擎：{name}（可用的引擎见 GET /api/engines）"
        ) from None
    return cls()


def get_engine(name: str | None = None):
    """返回 PptEngine 实例。name 不传时读环境变量 DECKSMITH_ENGINE（默认 stub）。

    可解析的名字 = `ENGINE_REGISTRY` 的键 ∪ {"stub"}；新增引擎只需在注册表里加一项
    （连同 `_ENGINE_CLASSES` 的类指向），本文件与 `runtime.py`、容器 entrypoint 都不动。
    """
    name = name or os.environ.get("DECKSMITH_ENGINE", STUB_NAME)

    # 离线/测试钩子：凡注册过的真实引擎一律落到 stub（它们都要拉起子进程）
    if os.environ.get("DECKSMITH_FORCE_STUB") == "1" and (
        name == STUB_NAME or name in ENGINE_REGISTRY
    ):
        return StubPptMasterEngine()

    if name == STUB_NAME:
        return StubPptMasterEngine()
    return _instantiate(name)
