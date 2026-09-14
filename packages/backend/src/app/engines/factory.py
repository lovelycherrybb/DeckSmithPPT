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
    """返回 PptEngine 实例。name 不传时读环境变量 DECKSMITH_ENGINE（默认 stub）。

    引擎名映射（与 GET /api/engines 返回的注册名一致）：
    - ppt_master：真实输出引擎（claude 子进程）
    - ppt_master_draft / claude_draft：草稿引擎（当前阶段实现复用
      PptMasterEngine 的草稿逻辑，source_to_md 确定性转换）
    - guizang_ppt：HTML 输出引擎（同为 claude 子进程，执行 guizang-ppt-skill）
    - 其余名：不静默降级，直接抛 ValueError（调用方/前端应只发注册过的引擎名）
    """
    name = name or os.environ.get("DECKSMITH_ENGINE", "stub")
    if name == "ppt_master" or name == "ppt_master_draft":
        from engine.impl.ppt_master.engine import PptMasterEngine
        return PptMasterEngine()
    if name == "claude_draft":
        from engine.impl.ppt_master.claude_draft import ClaudeDraftEngine
        return ClaudeDraftEngine()
    if name == "guizang_ppt":
        from engine.impl.guizang_ppt import GuizangPptEngine
        return GuizangPptEngine()
    if name == "stub":
        return StubPptMasterEngine()
    raise ValueError(f"未知引擎：{name}（可用的引擎见 GET /api/engines）")


# 测试/开发钩子：DECKSMITH_FORCE_STUB=1 时所有引擎请求都落到 stub（不进子进程）。
# 但未知引擎名仍然报错（保证校验语义不被钩子绕过）。
# guizang_ppt 与 ppt_master 一样依赖 claude 子进程，故同样必须落到 stub。
_original_get_engine = get_engine


def _patched_get_engine(name: str | None = None):
    if os.environ.get("DECKSMITH_FORCE_STUB") == "1":
        effective = name or os.environ.get("DECKSMITH_ENGINE", "stub")
        if effective in ("stub", "ppt_master", "ppt_master_draft", "claude_draft", "guizang_ppt"):
            return StubPptMasterEngine()
    return _original_get_engine(name)


get_engine = _patched_get_engine
