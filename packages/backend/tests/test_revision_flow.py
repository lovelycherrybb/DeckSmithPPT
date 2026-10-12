"""逐页修改（revise）的后端流转回归（ADR-023 / ADR-024 步骤四）。

覆盖：submit_revision 的校验矩阵、调度器把 revise 任务派发到 engine.revise、
终态落子演示文件（挂在版本树上、可继续分叉）。用假 revise 引擎，不起真进程、不碰付费额度。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
_PACKAGES = Path(__file__).resolve().parents[2]
for p in (str(_BACKEND_SRC), str(_PACKAGES)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.core.schemas import ReviseRequest, PageEdit  # noqa: E402
from app.models import Base, Job, Project, make_engine, make_session_factory  # noqa: E402
from app.repository.jobs import JobRepo, PresentationRepo, SubPresentationRepo  # noqa: E402
from app.service.jobs import JobService  # noqa: E402
from app.service.scheduler import Scheduler  # noqa: E402
from engine.core.models import EngineJobStatus  # noqa: E402


def _now():
    return datetime.now(timezone.utc).isoformat()


class ReviseEngine:
    """实现 revise 钩子的假引擎：记录调用、status 可脚本、result 回一个假 pptx 路径。"""

    def __init__(self, status_value="completed"):
        self.revised: list[dict] = []
        self.status_value = status_value

    def revise(self, job_id, *, source_task_id=None, pages=None, instruction=None,
               deck=None, engine_params=None):
        self.revised.append({"job_id": job_id, "source_task_id": source_task_id,
                             "pages": pages, "instruction": instruction, "deck": deck})
        return job_id

    def status(self, job_id):
        return EngineJobStatus(self.status_value)

    def result(self, job_id):
        return f"/tmp/{job_id}.pptx"


class NoReviseEngine(ReviseEngine):
    """显式退出 revise（implements_hook 认作未实现）——用来验 403 门控。"""
    revise = None  # type: ignore[assignment]


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path / 'rev.db'}")
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()
    # persist_* 内部用模块级 SessionLocal（默认打开发库）；改指临时库的独立会话，避免污染开发库
    monkeypatch.setattr("app.service.jobs.SessionLocal", lambda: make_session_factory(engine)())

    rev, norev = ReviseEngine(), NoReviseEngine()
    engines = {"rev": rev, "norev": norev}
    svc = JobService(JobRepo(), engine_factory=lambda name: engines[name])
    # 种子：项目 p1 → 产出任务 genjob(engine=rev) → 根演示文件 pres1
    session.add(Project(project_id="p1", project_name="P", created_at=_now(), updated_at=_now()))
    session.add(Job(job_id="genjob", project_id="p1", task_type="output", status="completed",
                    engine="rev", agent="claude", content_style="marketing_promo",
                    engine_params="{}", created_at=_now(), updated_at=_now()))
    session.commit()
    PresentationRepo().create(session, presentation_id="pres1", project_id="p1", task_id="genjob",
                              file_name="P_1.pptx", file_format="pptx",
                              file_path="/tmp/genjob.pptx", created_at=_now())

    def _sched():
        return Scheduler(service=svc, max_concurrent=1, timeout_seconds=3600,
                         engine_factory=lambda name: engines[name], session_factory=lambda: session)

    yield {"db": session, "svc": svc, "rev": rev, "sched": _sched}
    session.close()


def _submit(env, **over):
    req = ReviseRequest(presentation_id="pres1",
                        pages=[PageEdit(index=1, content="<svg/>")], **over)
    return env["svc"].submit_revision(env["db"], req, identity=None)


def _drive_to_sub(env, job_id):
    """跑两拍：快通道认领→engine.revise→running；轮询到 completed→落子文件。返回修订调用记录数。"""
    env["sched"]().sweep()
    env["sched"]().sweep()
    return SubPresentationRepo().list_by_presentation(env["db"], "pres1")


# ── 校验矩阵 ──
def test_提交修改落_revise_任务并带_meta(env):
    out = _submit(env)
    assert out.task_type == "revise"
    meta = JobRepo().get(env["db"], out.job_id)["revise_meta"]
    assert meta["source_task_id"] == "genjob"
    assert meta["root_presentation_id"] == "pres1"
    assert meta["parent_sub_presentation_id"] is None
    assert meta["pages"][0]["index"] == 1
    assert JobRepo().get(env["db"], out.job_id)["engine"] == "rev"


def test_pages_与_instruction_全空_400(env):
    with pytest.raises(HTTPException) as e:
        env["svc"].submit_revision(env["db"], ReviseRequest(presentation_id="pres1"), identity=None)
    assert e.value.status_code == 400


def test_源标识二选一缺失_400(env):
    with pytest.raises(HTTPException) as e:
        env["svc"].submit_revision(
            env["db"], ReviseRequest(pages=[PageEdit(index=1, content="x")]), identity=None)
    assert e.value.status_code == 400


def test_源不存在_404(env):
    with pytest.raises(HTTPException) as e:
        env["svc"].submit_revision(
            env["db"], ReviseRequest(presentation_id="ghost",
                                     pages=[PageEdit(index=1, content="x")]), identity=None)
    assert e.value.status_code == 404


def test_引擎无_revise_能力_403(env):
    with pytest.raises(HTTPException) as e:
        env["svc"].submit_revision(
            env["db"], ReviseRequest(presentation_id="pres1", engine="norev",
                                     pages=[PageEdit(index=1, content="x")]), identity=None)
    assert e.value.status_code == 403


# ── 端到端：调度器派发 revise + 终态落子文件（走快通道，不占生成闸）──
def test_调度器派发_revise_并落子演示文件(env):
    out = _submit(env)
    before = len(env["rev"].revised)
    subs = _drive_to_sub(env, out.job_id)
    assert len(env["rev"].revised) == before + 1, "revise 被调用了一次"
    assert env["rev"].revised[-1]["source_task_id"] == "genjob"
    assert len(subs) == 1
    sub = subs[0]
    assert sub["presentation_id"] == "pres1"
    assert sub["parent_sub_presentation_id"] is None
    assert sub["task_id"] == out.job_id
    assert sub["file_name"].startswith("P_") and sub["file_name"].endswith(".pptx")
    assert sub["file_path"] == f"/tmp/{out.job_id}.pptx"


def test_子文件可在其下继续分叉(env):
    out1 = _submit(env)
    _drive_to_sub(env, out1.job_id)
    s1 = SubPresentationRepo().list_by_presentation(env["db"], "pres1")[0]["sub_presentation_id"]
    # 基于 s1 再改 → 新子文件的 parent 指向 s1，root 仍是 pres1
    out2 = env["svc"].submit_revision(
        env["db"], ReviseRequest(sub_presentation_id=s1, instruction="把第 2 页标题改红"),
        identity=None)
    meta = JobRepo().get(env["db"], out2.job_id)["revise_meta"]
    assert meta["parent_sub_presentation_id"] == s1
    assert meta["root_presentation_id"] == "pres1"
    subs = _drive_to_sub(env, out2.job_id)
    assert len(subs) == 2
    assert s1 in {s["parent_sub_presentation_id"] for s in subs}
