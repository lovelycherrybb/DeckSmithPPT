"""guizang_ppt 引擎：将 Markdown 内容生成 HTML 演示文稿。

基于 guizang-ppt-skill，支持两种风格：
- magazine：电子杂志风格
- swiss：瑞士国际主义风格
"""
from .engine import GuizangPptEngine

__all__ = ["GuizangPptEngine"]
