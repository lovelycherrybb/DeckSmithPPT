"""引擎工厂：让后端能拿到 PptEngine 实例（stub 默认，真实 ppt_master 可选）。

- 把仓库的 packages/ 加入 sys.path，使 engine 包可被 import。
- 通过环境变量 DECKSMITH_ENGINE 选择引擎：
  - stub（默认）：假数据、秒级，用于开发/测试跑通链路
  - ppt_master：真实引擎，拉起 claude 子进程跑 ppt-master 生成 PPTX
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# engine 包的父目录是 packages/，加入 sys.path 后即可 `import engine`。
_PACKAGES_DIR = Path(__file__).resolve().parents[4]
if str(_PACKAGES_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGES_DIR))

from ..engines.stub import StubPptMasterEngine  # noqa: E402


def get_engine(name: str | None = None):
    """返回 PptEngine 实例。name 不传时读环境变量 DECKSMITH_ENGINE（默认 stub）。"""
    name = name or os.environ.get("DECKSMITH_ENGINE", "stub")
    if name == "ppt_master":
        from engine.impl.ppt_master.engine import PptMasterEngine
        return PptMasterEngine()
    return StubPptMasterEngine()
