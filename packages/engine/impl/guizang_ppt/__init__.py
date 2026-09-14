"""guizang_ppt 引擎：驱动 Claude Code 执行 guizang-ppt-skill，输出单文件 HTML 演示文稿。

支持上游技能的两种风格（参数契约见 docs/contracts/engine.schema.json $defs.guizang_ppt）：
- magazine：电子杂志 × 电子墨水
- swiss：瑞士国际主义
"""
from .engine import GuizangPptEngine, ensure_skill_installed

__all__ = ["GuizangPptEngine", "ensure_skill_installed"]
