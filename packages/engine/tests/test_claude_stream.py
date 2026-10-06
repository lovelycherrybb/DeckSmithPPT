"""claude_stream 判定器回归：识破网关谎报 success 的断流。"""
from __future__ import annotations

import sys
from pathlib import Path

_PACKAGES = Path(__file__).resolve().parents[2]
if str(_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_PACKAGES))

from engine.core.claude_stream import classify_result, looks_like_api_error  # noqa: E402


def test_干净成功判_ok():
    ok, note = classify_result({"type": "result", "subtype": "success", "is_error": False}, "正常输出")
    assert ok is True and note == ""


def test_is_error_判失败():
    ok, note = classify_result({"subtype": "success", "is_error": True, "result": "限流了"}, "")
    assert ok is False and "限流了" in note


def test_非_success_subtype_判失败_带原文():
    ok, note = classify_result({"subtype": "error_during_execution", "result": "模型限流"}, "")
    assert ok is False and "模型限流" in note


def test_谎报_success_但结果里有断流特征():
    # 正是第三方网关断流：subtype=success，但 result 文本露馅
    ok, note = classify_result(
        {"subtype": "success", "is_error": False,
         "result": "API Error: Unable to connect to API (ENOTFOUND)"}, "")
    assert ok is False and "断流" in note


def test_谎报_success_但流式文本里有特征():
    ok, note = classify_result(
        {"subtype": "success", "is_error": False, "result": ""},
        "前面正常输出…… API Error: terminated")
    assert ok is False


def test_markers_命中判定():
    assert looks_like_api_error("got ECONNRESET") is True
    assert looks_like_api_error("一切正常") is False
    assert looks_like_api_error(None) is False


def test_ppt_master_家族实现_revise_派生_svg_edit():
    """步骤3b：ppt_master 与 ppt_master_v6 实现 revise 钩子 → svg_edit 能力位自动派生
    （前端据此才显示「修改」）。guizang 未实现 → 不该有 svg_edit。三处同步的"派生"端。"""
    import sys
    from pathlib import Path

    backend_src = Path(__file__).resolve().parents[2] / "backend" / "src"
    if str(backend_src) not in sys.path:
        sys.path.insert(0, str(backend_src))
    from app.engines.registry import _derive_capabilities, implements_hook, REVISE_HOOK
    from app.engines.registry import engine_class

    for name in ("ppt_master", "ppt_master_v6"):
        assert implements_hook(engine_class(name), REVISE_HOOK) is True, name
        assert "svg_edit" in _derive_capabilities(name), name
    # 草稿类引擎（ppt_master_draft / claude_draft 均继承输出引擎）必须显式退出 revise，
    # 否则会被误标 svg_edit（实测 /api/engines 曾泄漏给 claude_draft）。
    for name in ("guizang_ppt", "ppt_master_draft", "claude_draft"):
        assert "svg_edit" not in _derive_capabilities(name), name
        assert implements_hook(engine_class(name), REVISE_HOOK) is False, name


def test_revise_产物发现要求单层项目目录(tmp_path):
    """锁死 revise 复制目标深度：成品须在 <job>/projects/<proj>/exports/*.pptx（单层）。
    _newest_export 用 projects/*/exports/*.pptx 单层 glob 发现产物；旧实现多包一层 revise_xxx
    → 成功导出的成品"看不见"→ 误判失败（真跑实测踩过）。"""
    import os
    from engine.impl.ppt_master_v6.engine import PptMasterV6Engine
    eng = PptMasterV6Engine(jobs_root=tmp_path)
    jid = "job1"
    good = tmp_path / jid / "projects" / "proj" / "exports"
    good.mkdir(parents=True)
    gf = good / "a.pptx"
    gf.write_text("x")
    os.utime(gf, (0, 0))
    assert eng._newest_export(jid, 0) is not None, "单层成品应被发现"
    for f in good.glob("*.pptx"):
        f.unlink()
    bad = tmp_path / jid / "projects" / "revise_x" / "proj" / "exports"
    bad.mkdir(parents=True)
    bf = bad / "a.pptx"
    bf.write_text("x")
    os.utime(bf, (0, 0))
    assert eng._newest_export(jid, 0) is None, "双层（旧 revise 布局）不该被单层 glob 发现——正是要修的 bug"


def test_deterministic_export_不调claude_写completed(tmp_path, monkeypatch):
    """pages-only 另存走确定性重导出：subprocess 跑 finalize+svg_to_pptx，成功写 completed+result_path。"""
    import types
    from engine.impl.ppt_master_v6 import engine as v6mod
    from engine.impl.ppt_master_v6.engine import PptMasterV6Engine

    eng = PptMasterV6Engine(jobs_root=tmp_path)
    jid = "r1"
    proj = tmp_path / jid / "projects" / "projA"
    (proj / "exports").mkdir(parents=True)
    (proj / "svg_output").mkdir()
    exp = proj / "exports" / "deck.pptx"

    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(cmd[1])            # 记录被调脚本名
        exp.write_text("pptx")          # 模拟导出脚本产出（mtime 新于 started）
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(v6mod.subprocess, "run", fake_run)
    eng._write_job(jid, status="running")
    eng._deterministic_export(jid, proj)
    data = eng._read_job(jid)
    assert any("finalize_svg.py" in c for c in calls) and any("svg_to_pptx.py" in c for c in calls)
    assert data["status"] == "completed" and data["result_path"].endswith("deck.pptx")


def test_deterministic_export_脚本失败写failed(tmp_path, monkeypatch):
    import types
    from engine.impl.ppt_master_v6 import engine as v6mod
    from engine.impl.ppt_master_v6.engine import PptMasterV6Engine

    eng = PptMasterV6Engine(jobs_root=tmp_path)
    jid = "r2"
    proj = tmp_path / jid / "projects" / "p"; (proj / "exports").mkdir(parents=True)
    monkeypatch.setattr(v6mod.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="boom"))
    eng._write_job(jid, status="running")
    eng._deterministic_export(jid, proj)
    assert eng._read_job(jid)["status"] == "failed"

