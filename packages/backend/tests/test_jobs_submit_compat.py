"""JobService.engine_submit 兼容适配器回归测试。

钉住一个真实踩过的坑：「重试」路径调用 engine_submit 时不传 sources，
而适配器对旧签名引擎无条件 `del kwargs["sources"]` → KeyError: 'sources'，
于是 guizang_ppt 这类 submit 用单数 `source` 的引擎**一点重试就秒失败**、
连 job 目录都不会创建。ppt_master 的签名带 sources 恰好绕过该分支，所以这个
bug 潜伏到 guizang 上线才暴露。

不发子进程、不碰数据库：只验参数适配这一件事。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
_PACKAGES = Path(__file__).resolve().parents[2]
for p in (str(_BACKEND_SRC), str(_PACKAGES)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.service.jobs import JobService  # noqa: E402


class _OldStyleEngine:
    """旧签名引擎：单数 source、无 engine_type（与 GuizangPptEngine 形态一致）。"""

    def __init__(self) -> None:
        self.received: dict = {}

    def submit(self, content, title=None, pages=None, engine_params=None,
               source=None, job_id=None):  # noqa: D102
        self.received = dict(content=content, title=title, pages=pages,
                             engine_params=engine_params, source=source, job_id=job_id)
        return job_id


class _NewStyleEngine:
    """新签名引擎：复数 sources + engine_type（与 PptMasterEngine 形态一致）。"""

    def __init__(self) -> None:
        self.received: dict = {}

    def submit(self, content=None, title=None, pages=None, engine_params=None,
               sources=None, job_id=None, engine_type=None):  # noqa: D102
        self.received = dict(sources=sources, engine_type=engine_type, job_id=job_id)
        return job_id


def test_重试路径不传sources时不再KeyError():
    """复现 bug 场景：retry 只传 content/title/pages/engine_params/job_id/engine_type。"""
    eng = _OldStyleEngine()
    JobService.engine_submit(
        eng, content="# 草稿", title="继承来的标题", pages=5,
        engine_params={"style": "swiss"}, job_id="j1", engine_type="output",
    )
    assert eng.received["source"] is None
    assert eng.received["title"] == "继承来的标题"
    assert "sources" not in eng.received and "engine_type" not in eng.received


def test_提交路径把sources换算成单个source():
    eng = _OldStyleEngine()
    JobService.engine_submit(
        eng, content="c", title=None, pages=None, engine_params={},
        sources=[{"type": "file_path", "value": "/tmp/a.docx"}, {"type": "url", "value": "u"}],
        job_id="j2", engine_type="output",
    )
    assert eng.received["source"] == {"type": "file_path", "value": "/tmp/a.docx"}


def test_空sources在旧签名引擎上落为None():
    eng = _OldStyleEngine()
    JobService.engine_submit(
        eng, content="c", title="t", pages=1, engine_params={},
        sources=[], job_id="j3", engine_type="output",
    )
    assert eng.received["source"] is None


def test_新签名引擎原样透传sources与engine_type():
    eng = _NewStyleEngine()
    src = [{"type": "text", "value": "hi"}]
    JobService.engine_submit(
        eng, content="c", title="t", pages=1, engine_params={},
        sources=src, job_id="j4", engine_type="draft",
    )
    assert eng.received["sources"] == src and eng.received["engine_type"] == "draft"


def test_guizang签名确实落在旧签名分支():
    """适配器靠签名探测分流：这里断言 guizang 的形态没被无意改成复数。"""
    from engine.impl.guizang_ppt import GuizangPptEngine

    params = inspect.signature(GuizangPptEngine.submit).parameters
    assert "sources" not in params and "source" in params
    assert "engine_type" not in params


def test_缺content等关键字参数不再被误删():
    """pop 容错不得吞掉正常参数（原实现 del 之后仍要能正常调用）。"""
    eng = _OldStyleEngine()
    with pytest.raises(TypeError):
        # 引擎签名没有 foo 参数 → 应把 TypeError 留给引擎本身，而不是适配器 KeyError
        JobService.engine_submit(eng, content="c", foo=1, job_id="j5")
    with pytest.raises(TypeError):
        JobService.engine_submit(_NewStyleEngine(), content="c", foo=1, job_id="j6")


# ── 标题继承：草稿→输出交接不得丢掉标题（否则 running 期显示「未命名」） ──
class _RecordingEngine:
    """假输出引擎：只记录收到的参数，绝不拉起子进程。"""

    def __init__(self) -> None:
        self.received: dict = {}

    def submit(self, content=None, title=None, pages=None, engine_params=None,
               source=None, job_id=None):  # noqa: D102
        self.received = dict(content=content, title=title)
        return job_id


def _seed_db(tmp_path):
    """建一份最小真实库：草稿 job（带标题）→ drafts → draft_versions。"""
    from app.models import Base, make_engine, make_session_factory
    from app.repository.jobs import DraftRepo, DraftVersionRepo, JobRepo

    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    db = make_session_factory(engine)()

    ts = "2026-09-14T12:00:00+00:00"
    JobRepo().create(
        db, job_id="draftjob", project_id="p1", task_type="draft", status="completed",
        title="2025_年交通工程质量安全检查问题", engine="claude_draft", agent="claude",
        content_style="marketing_promo", engine_params={}, created_at=ts, updated_at=ts,
    )
    DraftRepo().create(
        db, draft_id="d1", project_id="p1", task_id="draftjob",
        current_version="20260914120000", created_at=ts,
    )
    DraftVersionRepo().create(
        db, draft_id="d1", version="20260914120000", content="# 草稿正文\n内容", created_at=ts,
    )
    return db


def _submit_output(db, rec, **req_kwargs):
    from fastapi import BackgroundTasks

    from app.core.schemas import DeckRequest
    from app.repository.jobs import JobRepo

    svc = JobService(JobRepo(), engine_factory=lambda name: rec)
    req = DeckRequest(engine="guizang_ppt", engine_type="output", draft_id="d1",
                      project_id="p1", **req_kwargs)
    return svc.submit(db, req, BackgroundTasks())


def test_未传标题时从草稿任务继承(tmp_path):
    from app.repository.jobs import JobRepo

    db = _seed_db(tmp_path)
    rec = _RecordingEngine()
    out = _submit_output(db, rec)

    assert out.title == "2025_年交通工程质量安全检查问题"   # 落库不再为空
    assert rec.received["title"] == out.title              # 引擎也收到同一标题
    assert JobRepo().get(db, out.job_id)["title"] == out.title
    assert rec.received["content"].startswith("# 草稿正文")  # content 仍来自草稿版本


def test_用户显式标题不被覆盖(tmp_path):
    db = _seed_db(tmp_path)
    rec = _RecordingEngine()
    out = _submit_output(db, rec, title="我自己起的名字")
    assert out.title == "我自己起的名字"
    assert rec.received["title"] == "我自己起的名字"
