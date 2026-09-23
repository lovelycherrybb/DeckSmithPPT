"""归属与可见性回归（用户系统 v0.8，B3 Pass 1）。

临时库直测 core/ownership 与 ProjectRepo 过滤、以及 service._ensure_project 的打戳与守卫。
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

from app.core.auth import Identity  # noqa: E402
from app.core.ownership import (  # noqa: E402
    UNCLAIMED_LABEL, attach_owner_label, can_access, create_stamp, list_filter_kwargs,
)
from app.models import Base, Project, User, make_engine, make_session_factory  # noqa: E402
from app.models import Job, Draft, Presentation  # noqa: E402
from app.repository.jobs import JobRepo, ProjectRepo  # noqa: E402


def _now():
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'own.db'}")
    Base.metadata.create_all(engine)
    s = make_session_factory(engine)()
    yield s
    s.close()


def _user(db, uid, role="normal"):
    u = User(user_id=uid, username=uid, nickname=uid + "昵称", role=role, status="enabled",
             password_hash="x", created_at=_now(), updated_at=_now())
    db.add(u); db.commit(); db.refresh(u)
    return u


def _project(db, pid, owner=None, visitor=None):
    db.add(Project(project_id=pid, project_name=pid, owner_user_id=owner, visitor_id=visitor,
                   created_at=_now(), updated_at=_now()))
    db.commit()


# ── create_stamp ──
def test_打戳_登录写owner_试用写visitor_匿名不写(db):
    u = _user(db, "u1")
    assert create_stamp(Identity(user=u, visitor_id=None)) == {"owner_user_id": "u1"}
    assert create_stamp(Identity(user=None, visitor_id="V")) == {"visitor_id": "V"}
    assert create_stamp(Identity(user=None, visitor_id=None)) == {}
    assert create_stamp(None) == {}


# ── can_access ──
def test_访问守卫_各态(db):
    u1 = _user(db, "u1"); u2 = _user(db, "u2"); mgr = _user(db, "mgr", role="manager")
    _project(db, "p_own", owner="u1")
    _project(db, "p_other", owner="u2")
    _project(db, "p_trial", visitor="V1")
    _project(db, "p_legacy")  # 无 owner 无 visitor（历史/无主）
    pr = ProjectRepo()

    assert can_access(pr.get(db, "p_own"), Identity(user=u1, visitor_id=None)) is True
    assert can_access(pr.get(db, "p_other"), Identity(user=u1, visitor_id=None)) is False
    assert can_access(pr.get(db, "p_own"), Identity(user=mgr, visitor_id=None)) is True   # 管理员看全部
    assert can_access(pr.get(db, "p_trial"), Identity(user=None, visitor_id="V1")) is True  # 访客看自己未认领
    assert can_access(pr.get(db, "p_own"), Identity(user=None, visitor_id="V1")) is False   # 访客看不到别人的
    assert can_access(pr.get(db, "p_own"), Identity(user=None, visitor_id=None)) is False   # 匿名看不到
    assert can_access(pr.get(db, "p_legacy"), Identity(user=None, visitor_id=None)) is False  # 匿名看不到无主


# ── list_filter_kwargs + repo.list 过滤 ──
def test_列表按归属过滤(db):
    u1 = _user(db, "u1"); _user(db, "u2"); mgr = _user(db, "mgr", role="manager")
    _project(db, "a", owner="u1")
    _project(db, "b", owner="u2")
    _project(db, "c", visitor="V")
    pr = ProjectRepo()

    u1_ids = {p["project_id"] for p in pr.list(db, **list_filter_kwargs(Identity(user=u1, visitor_id=None)))}
    assert u1_ids == {"a"}
    admin_ids = {p["project_id"] for p in pr.list(db, **list_filter_kwargs(Identity(user=mgr, visitor_id=None)))}
    assert admin_ids == {"a", "b", "c"}
    trial_ids = {p["project_id"] for p in pr.list(db, **list_filter_kwargs(Identity(user=None, visitor_id="V")))}
    assert trial_ids == {"c"}
    anon_ids = {p["project_id"] for p in pr.list(db, **list_filter_kwargs(Identity(user=None, visitor_id=None)))}
    assert anon_ids == set()


# ── owner_label 派生 ──
def test_owner_label_昵称与未认领(db):
    _user(db, "u1")
    _project(db, "a", owner="u1")
    _project(db, "c", visitor="V")
    pr = ProjectRepo()
    rows = pr.list(db)
    attach_owner_label(db, rows)
    by = {r["project_id"]: r["owner_label"] for r in rows}
    assert by["a"] == "u1昵称"
    assert by["c"] == UNCLAIMED_LABEL


# ── _ensure_project 打戳与守卫 ──
def test_ensure_project_打戳与越权404(db):
    from app.service.jobs import JobService
    svc = JobService(JobRepo(), engine_factory=lambda name: None)
    u1 = _user(db, "u1"); u2 = _user(db, "u2")
    _project(db, "p_u2", owner="u2")

    # 传别人的 project_id 且非管理员 → 404
    with pytest.raises(HTTPException) as e:
        svc._ensure_project(db, "p_u2", "x", Identity(user=u1, visitor_id=None))
    assert e.value.status_code == 404

    # owner 本人可复用
    got = svc._ensure_project(db, "p_u2", "x", Identity(user=u2, visitor_id=None))
    assert got == "p_u2"

    # 新建：登录身份打 owner
    pid = svc._ensure_project(db, None, "新项目", Identity(user=u1, visitor_id=None))
    assert ProjectRepo().get(db, pid)["owner_user_id"] == "u1"

    # 新建：试用打 visitor、owner 留空
    pid2 = svc._ensure_project(db, None, "试用", Identity(user=None, visitor_id="VZ"))
    row = ProjectRepo().get(db, pid2)
    assert row["owner_user_id"] is None and row["visitor_id"] == "VZ"


# ── Pass 2：按 id 直取端点的归属守卫 ──
def test_守卫_跨用户404_本人和管理员放行(db):
    from fastapi import HTTPException
    from app.api import routes as R
    u1 = _user(db, "u1"); u2 = _user(db, "u2"); mgr = _user(db, "mgr", role="manager")
    _project(db, "p1", owner="u1")
    db.add(Job(job_id="j1", project_id="p1", status="completed", engine="x", agent="claude",
               engine_params="{}", created_at=_now(), updated_at=_now()))
    db.add(Draft(draft_id="d1", project_id="p1", created_at=_now(), updated_at=_now()))
    db.add(Presentation(presentation_id="pr1", project_id="p1", task_id="j1", file_name="a",
                        file_format="pptx", file_path="/x", created_at=_now()))
    db.commit()
    i_u1 = Identity(user=u1, visitor_id=None)
    i_u2 = Identity(user=u2, visitor_id=None)
    i_admin = Identity(user=mgr, visitor_id=None)

    # 本人放行
    R._guard_job(db, "j1", i_u1); R._guard_draft(db, "d1", i_u1); R._guard_presentation(db, "pr1", i_u1)
    # 管理员放行
    R._guard_job(db, "j1", i_admin)
    # 他人 → 404
    for fn, arg in [(R._guard_job, "j1"), (R._guard_draft, "d1"), (R._guard_presentation, "pr1")]:
        with pytest.raises(HTTPException) as e:
            fn(db, arg, i_u2)
        assert e.value.status_code == 404


def test_visible_project_ids_各态(db):
    from app.core.ownership import visible_project_ids
    u1 = _user(db, "u1"); mgr = _user(db, "mgr", role="manager")
    _project(db, "a", owner="u1"); _project(db, "b", owner="u2" if False else "zz"); _project(db, "c", visitor="V")
    assert visible_project_ids(db, Identity(user=u1, visitor_id=None)) == ["a"]
    assert visible_project_ids(db, Identity(user=mgr, visitor_id=None)) is None      # 管理员不受限
    assert set(visible_project_ids(db, Identity(user=None, visitor_id="V"))) == {"c"}
    assert visible_project_ids(db, Identity(user=None, visitor_id=None)) == []        # 匿名看不到


# ── Pass 3：管理员删除 + 代为认领 ──
def test_删除_只删未认领并级联_已归属拒删(db):
    _user(db, "u1")
    _project(db, "p_unclaimed", visitor="V")
    db.add(Job(job_id="jx", project_id="p_unclaimed", status="completed", engine="x",
               agent="claude", engine_params="{}", created_at=_now(), updated_at=_now()))
    db.add(Draft(draft_id="dx", project_id="p_unclaimed", created_at=_now(), updated_at=_now()))
    db.commit()

    pr = ProjectRepo()
    assert pr.delete(db, "nope") == "not_found"
    assert pr.delete(db, "p_unclaimed") == "deleted"
    assert pr.get(db, "p_unclaimed") is None
    # 关联 job/draft 一并级联删除
    assert db.query(Job).filter_by(job_id="jx").first() is None
    assert db.query(Draft).filter_by(draft_id="dx").first() is None

    _project(db, "p_owned", owner="u1")
    assert pr.delete(db, "p_owned") == "owned"


def test_set_owner_代为认领(db):
    _user(db, "u1")
    _project(db, "p1", visitor="V")
    pr = ProjectRepo()
    updated = pr.set_owner(db, "p1", "u1")
    assert updated["owner_user_id"] == "u1"
    assert pr.set_owner(db, "missing", "u1") is None


def test_删除与认领路由已注册():
    from app.main import app
    paths = app.openapi()["paths"]
    assert "delete" in paths.get("/api/projects/{project_id}", {})
    assert "post" in paths.get("/api/projects/{project_id}/claim", {})


def test_引擎参数门控_仅vip和管理员(db):
    from app.core.ownership import can_edit_engine_params
    normal = _user(db, "n1", role="normal")
    vip = _user(db, "v1", role="vip")
    mgr = _user(db, "m1", role="manager")
    assert can_edit_engine_params(None) is True                              # 内部/旧测不门控
    assert can_edit_engine_params(Identity(user=normal, visitor_id=None)) is False
    assert can_edit_engine_params(Identity(user=vip, visitor_id=None)) is True
    assert can_edit_engine_params(Identity(user=mgr, visitor_id=None)) is True
    assert can_edit_engine_params(Identity(user=None, visitor_id="V")) is False  # 访客不可


class _RecordingEngine:
    def __init__(self):
        self.received = {}
    def submit(self, content=None, title=None, pages=None, engine_params=None,
               source=None, sources=None, job_id=None, engine_type=None):
        self.received = dict(engine_params=engine_params)
        return job_id
    def status(self, job_id):
        from engine.core.models import JobStatus
        return JobStatus.RUNNING


def test_submit_普通用户引擎参数被后端剥离(db):
    from fastapi import BackgroundTasks
    from app.core.schemas import DeckRequest
    from app.service.jobs import JobService
    rec = _RecordingEngine()
    svc = JobService(JobRepo(), engine_factory=lambda name: rec)
    svc._get_engine = lambda name: rec
    normal = _user(db, "n2", role="normal")
    vip = _user(db, "v2", role="vip")
    req = DeckRequest(engine="guizang_ppt", engine_type="output", content="# 草稿\n正文",
                      engine_params={"style": "swiss", "pages": 9})

    svc.submit(db, req, BackgroundTasks(), Identity(user=normal, visitor_id=None))
    assert rec.received["engine_params"] == {}  # 普通用户：后端剥离自定义参数

    rec2 = _RecordingEngine()
    svc._get_engine = lambda name: rec2
    svc.submit(db, req, BackgroundTasks(), Identity(user=vip, visitor_id=None))
    assert rec2.received["engine_params"] == {"style": "swiss", "pages": 9}  # 高级用户：保留
