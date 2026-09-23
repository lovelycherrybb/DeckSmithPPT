"""用户管理 + 偏好回归（用户系统 v0.8，B4）。

临时库直测 UserRepo / UserSettingRepo 与关键路由函数（绕过 Depends 直接传 admin/user）。
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

from app.core.passwords import verify_password  # noqa: E402
from app.core.schemas import UserCreate, UserPreferenceUpdate, UserUpdate  # noqa: E402
from app.models import Base, User, make_engine, make_session_factory  # noqa: E402
from app.repository.users import UserRepo, UserSettingRepo  # noqa: E402


def _now():
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'b4.db'}")
    Base.metadata.create_all(engine)
    s = make_session_factory(engine)()
    yield s
    s.close()


def _mgr(db):
    u = User(user_id="mgr", username="manager", nickname="管理员", role="manager", status="enabled",
             password_hash="x", created_at=_now(), updated_at=_now())
    db.add(u); db.commit(); db.refresh(u)
    return u


# ── UserRepo CRUD ──
def test_新增用户_默认密码与角色(db):
    u = UserRepo().create(db, username="alice", is_vip=True)
    assert u["role"] == "vip" and u["status"] == "enabled" and u["nickname"] == "alice"
    obj = UserRepo().get_raw_by_username(db, "alice")
    assert verify_password("234567", obj.password_hash)  # 初始密码


def test_改信息_用户名不可改_不升级manager(db):
    UserRepo().create(db, username="bob", is_vip=False)
    uid = UserRepo().get_raw_by_username(db, "bob").user_id
    upd = UserRepo().update(db, uid, nickname="新名", is_vip=True)
    assert upd["nickname"] == "新名" and upd["role"] == "vip"
    assert UserRepo().get_raw(db, uid).username == "bob"  # 用户名没动


def test_管理员账号受保护(db):
    mgr = _mgr(db)
    assert UserRepo().update(db, mgr.user_id, nickname="x") is None
    assert UserRepo().set_status(db, mgr.user_id, "disabled") is None
    assert UserRepo().reset_password(db, mgr.user_id) is None


def test_启用禁用与重置(db):
    UserRepo().create(db, username="carol")
    uid = UserRepo().get_raw_by_username(db, "carol").user_id
    assert UserRepo().set_status(db, uid, "disabled")["status"] == "disabled"
    UserRepo().reset_password(db, uid)
    assert verify_password("234567", UserRepo().get_raw(db, uid).password_hash)


# ── 路由函数：重名 409、改管理员 403 ──
def test_create路由_重名409(db):
    from app.api.users import create_user
    mgr = _mgr(db)
    create_user(UserCreate(username="dave"), db=db, admin=mgr)
    with pytest.raises(HTTPException) as e:
        create_user(UserCreate(username="dave"), db=db, admin=mgr)
    assert e.value.status_code == 409


def test_update路由_目标是管理员403(db):
    from app.api.users import update_user
    mgr = _mgr(db)
    with pytest.raises(HTTPException) as e:
        update_user(mgr.user_id, UserUpdate(nickname="x"), db=db, admin=mgr)
    assert e.value.status_code == 403


# ── UserSettingRepo ──
def test_偏好默认与部分更新持久化(db):
    repo = UserSettingRepo()
    default = repo.get(db, "u1")
    assert default["auto_save"] is True and default["default_engine"] is None

    repo.save(db, "u1", {"default_engine": "guizang_ppt"})
    got = repo.get(db, "u1")
    assert got["default_engine"] == "guizang_ppt" and got["auto_save"] is True  # 未传的键保留

    repo.save(db, "u1", {"auto_save": False})
    got = repo.get(db, "u1")
    assert got["auto_save"] is False and got["default_engine"] == "guizang_ppt"  # 合并非覆盖


def test_偏好路由函数读写(db):
    from app.api.settings import get_preferences, save_preferences
    user = UserRepo().create(db, username="erin")
    uobj = UserRepo().get_raw(db, user["user_id"])
    save_preferences(UserPreferenceUpdate(default_pages=8), db=db, user=uobj)
    out = get_preferences(db=db, user=uobj)
    assert out["default_pages"] == 8


def test_用户与偏好路由已注册():
    from app.main import app
    paths = app.openapi()["paths"]
    assert "/api/users" in paths and "/api/users/{user_id}/reset-password" in paths
    assert "/api/users/{user_id}/status" in paths
    assert "/api/settings/preferences" in paths
