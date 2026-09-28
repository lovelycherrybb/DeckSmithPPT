"""`cancel` 能力位的派生回归（ADR-019 决策 5）。

这条测试是被真浏览器复验逼出来的：契约写了 cancel 能力位、前端也按它决定
"停止生成"按钮的可见性，但 `registry._derive_capabilities()` 当时只派生
`page_preview` —— 于是按钮在界面上**永远不出现**，三层各说各话、没有一层报错。
能力位必须由"实现类是否真有这个钩子"单一来源派生，并且被钉住。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT / "packages"), str(ROOT / "packages/backend/src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.engines.registry import ENGINE_REGISTRY, implements_hook  # noqa: E402


def test_输出引擎点亮cancel而草稿引擎不点亮():
    """三个输出引擎有可杀的 claude 子进程 → cancel；两个草稿引擎显式退出 → 不点亮。"""
    expected = {
        "ppt_master": True,
        "ppt_master_v6": True,
        "guizang_ppt": True,
        "ppt_master_draft": False,
        "claude_draft": False,
    }
    for name, want in expected.items():
        caps = ENGINE_REGISTRY[name]["capabilities"]
        assert ("cancel" in caps) is want, f"{name} 的 cancel 能力位应为 {want}，实际 {caps}"


def test_代码产出的能力位都在契约描述里():
    """契约 EngineInfo.capabilities 的描述是"已知值"清单；代码若产出清单外的能力位，
    前端就无从消费（反向的漏——文档写了代码不产出——由上一条用例守）。
    """
    import yaml

    doc = yaml.safe_load((ROOT / "docs/contracts/openapi.yaml").read_text(encoding="utf-8"))
    desc = doc["components"]["schemas"]["EngineInfo"]["properties"]["capabilities"]["description"]
    emitted = {c for entry in ENGINE_REGISTRY.values() for c in entry["capabilities"]}
    unknown = {c for c in emitted if c not in desc}
    assert not unknown, f"代码产出了契约未声明的能力位：{sorted(unknown)}"
    assert {"page_preview", "cancel"} <= emitted, "两个已知能力位至少要被某个引擎真实产出过"


def test_implements_hook对显式退出的类判否():
    """草稿引擎用 `cancel = None` 声明退出（与 get_pages 同一约定），必须被判为未实现。"""
    from app.engines.registry import engine_class

    assert implements_hook(engine_class("claude_draft"), "cancel") is False
    assert implements_hook(engine_class("ppt_master_draft"), "cancel") is False
    assert implements_hook(engine_class("guizang_ppt"), "cancel") is True
