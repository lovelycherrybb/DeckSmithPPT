"""多份素材全部进草稿的回归（不再只取第一份）。

钉住的缺陷：契约 engine.schema.json 的 sources 写明「支持多文件上传」、界面也提示
「提交时一起交给引擎」，但两个草稿引擎的提取循环都是 `if md: break` 且每轮覆盖 md，
结果三份文件只有一份进草稿、另两份静默丢弃。用 monkeypatch 替掉真实的
source_to_md 子进程，只验拼接语义。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # packages/

from engine.impl.ppt_master.engine import PptMasterDraftEngine  # noqa: E402
from engine.impl.ppt_master.claude_draft import ClaudeDraftEngine  # noqa: E402

# 文件名 → 转换结果；值为 None 表示这份转不动（source_to_md 返回 None）
CONVERTED = {
    "/up/a.docx": "# 甲文件\n\n甲正文",
    "/up/b.xlsx": "# 乙文件\n\n乙表格内容",
    "/up/broken.pdf": None,
}


def _fake_run_source_to_md(mapping: dict):
    def _run(self, path_or_url: str):  # noqa: ANN001
        return mapping.get(path_or_url)
    return _run


def _sources(*values: str) -> list[dict]:
    return [{"type": "file_path", "value": v} for v in values]


def test_默认引擎_两份文件都进草稿且保持上传顺序(tmp_path, monkeypatch):
    eng = PptMasterDraftEngine(jobs_root=tmp_path / "jobs")
    monkeypatch.setattr(PptMasterDraftEngine, "_run_source_to_md", _fake_run_source_to_md(CONVERTED))

    draft, _title = eng._build_draft(  # noqa: SLF001
        content=None, title="T", pages=8, sources=_sources("/up/a.docx", "/up/b.xlsx"),
        engine_params=None,
    )

    assert "甲正文" in draft and "乙表格内容" in draft, "第二份文件被丢弃了"
    assert draft.index("甲正文") < draft.index("乙表格内容"), "素材顺序被打乱"
    # 标注里带来源名，用户能看出这是两份素材拼的
    assert "a.docx" in draft and "b.xlsx" in draft


def test_默认引擎_单份素材不加标注保持原样(tmp_path, monkeypatch):
    """只有一份素材时输出必须和改造前一致，不凭空多出素材分隔块。"""
    eng = PptMasterDraftEngine(jobs_root=tmp_path / "jobs")
    monkeypatch.setattr(PptMasterDraftEngine, "_run_source_to_md", _fake_run_source_to_md(CONVERTED))

    draft, _title = eng._build_draft(  # noqa: SLF001
        content=None, title="T", pages=8, sources=_sources("/up/a.docx"), engine_params=None,
    )

    assert "【素材" not in draft
    assert "# 甲文件\n\n甲正文" in draft


def test_一份转不动时其余照常收下(tmp_path, monkeypatch):
    """旧写法是 break 在第一份成功上；新写法要一份失败只跳过它自己，不牵连其余。"""
    eng = PptMasterDraftEngine(jobs_root=tmp_path / "jobs")
    monkeypatch.setattr(PptMasterDraftEngine, "_run_source_to_md", _fake_run_source_to_md(CONVERTED))

    draft, _title = eng._build_draft(  # noqa: SLF001
        content=None, title="T", pages=8,
        sources=_sources("/up/broken.pdf", "/up/a.docx", "/up/b.xlsx"), engine_params=None,
    )

    assert "甲正文" in draft and "乙表格内容" in draft
    assert "broken.pdf" not in draft, "转不动的素材不该占一个空标注位"


def test_文本素材与文件混排都收下(tmp_path, monkeypatch):
    eng = PptMasterDraftEngine(jobs_root=tmp_path / "jobs")
    monkeypatch.setattr(PptMasterDraftEngine, "_run_source_to_md", _fake_run_source_to_md(CONVERTED))

    draft, _title = eng._build_draft(  # noqa: SLF001
        content=None, title="T", pages=8,
        sources=[{"type": "text", "value": "手打的要点"}, {"type": "file_path", "value": "/up/a.docx"}],
        engine_params=None,
    )

    assert "手打的要点" in draft and "甲正文" in draft


def test_claude引擎_素材提取同样合并两份(tmp_path, monkeypatch):
    eng = ClaudeDraftEngine(jobs_root=tmp_path / "jobs")
    monkeypatch.setattr(ClaudeDraftEngine, "_run_source_to_md", _fake_run_source_to_md(CONVERTED))

    material, _title = eng._extract_material(  # noqa: SLF001
        content=None, title="T", sources=_sources("/up/a.docx", "/up/b.xlsx")
    )

    assert "甲正文" in material and "乙表格内容" in material
