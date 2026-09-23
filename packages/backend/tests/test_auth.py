"""鉴权机制回归（用户系统 v0.8，B2）。

用临时 SQLite 直接测 core/auth 与会话/认领的纯函数层，不起 TestClient、不碰真实库；
另断言 auth 路由已注册。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
_PACKAGES = Path(__file__).resolve().parents[2]
for p in (str(_BACKEND_SRC), str(_PACKAGES)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.core.auth import (  # noqa: E402
    create_session, destroy_session, resolve_session,
)
from app.core.passwords import hash_password, verify_password  # noqa: E402
from app.models import Base, Project, User, UserSession, make_engine, make_session_factory  # noqa: E402
from app.repository.jobs import ProjectRepo  # noqa: E402
from app.repository.users import UserRepo  # noqa: E402


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'auth.db'}")
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()
    yield session
    session.close()


def _mk_user(db, username="alice", role="normal", status="enabled") -> User:
    now = datetime.now(timezone.utc).isoformat()
    u = User(user_id=username + "-id", username=username, nickname=username, role=role,
             status=status, password_hash=hash_password("pw123"), created_at=now, updated_at=now)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ── 会话生命周期 ──
def test_建会话可按id解析出用户_登出后失效(db):
    u = _mk_user(db)
    sid = create_session(db, u.user_id)
    assert resolve_session(db, sid).user_id == u.user_id
    destroy_session(db, sid)
    assert resolve_session(db, sid) is None


def test_过期会话解析为None并清理(db):
    u = _mk_user(db)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    db.add(UserSession(session_id="old", user_id=u.user_id, created_at=past, expires_at=past))
    db.commit()
    assert resolve_session(db, "old") is None
    assert db.query(UserSession).filter_by(session_id="old").first() is None


def test_禁用账号会话解析为None(db):
    u = _mk_user(db, status="disabled")
    sid = create_session(db, u.user_id)
    assert resolve_session(db, sid) is None


def test_空session_id安全返回None(db):
    assert resolve_session(db, None) is None
    destroy_session(db, None)  # 不抛


# ── 登录凭据校验 ──
def test_用户名查得对象且密码可校验(db):
    _mk_user(db, username="bob")
    got = UserRepo().get_raw_by_username(db, "bob")
    assert got is not None and verify_password("pw123", got.password_hash)
    assert not verify_password("nope", got.password_hash)


# ── 访客项目认领 ──
def _mk_project(db, pid, visitor_id, owner_user_id=None):
    now = datetime.now(timezone.utc).isoformat()
    db.add(Project(project_id=pid, project_name=pid, visitor_id=visitor_id,
                   owner_user_id=owner_user_id, created_at=now, updated_at=now))
    db.commit()


def test_认领只并入该访客未归属项目(db):
    _mk_project(db, "p1", "V1")                 # 未认领 → 应并入
    _mk_project(db, "p2", "V1")                 # 未认领 → 应并入
    _mk_project(db, "p3", "V1", owner_user_id="x")  # 已归属 → 不碰
    _mk_project(db, "p4", "V2")                 # 别的访客 → 不碰

    n = ProjectRepo().claim_visitor_projects(db, "V1", "alice-id")
    assert n == 2
    got = {p.project_id: p.owner_user_id for p in db.query(Project).all()}
    assert got["p1"] == "alice-id" and got["p2"] == "alice-id"
    assert got["p3"] == "x" and got["p4"] is None


def test_无访客令牌认领返回0(db):
    assert ProjectRepo().claim_visitor_projects(db, None, "alice-id") == 0


# ── 路由注册 ──
def test_auth路由已挂载():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/api/auth/login", "/api/auth/logout", "/api/auth/me", "/api/auth/password"} <= paths
