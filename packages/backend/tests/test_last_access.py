"""最后访问时间（User.last_access_at）的落库节流。

口径：该用户会话内每次请求都算"访问"，但同一用户 60 秒内最多落一次库
（每个带登录态的请求都会路过鉴权入口，不节流等于每个接口都写一次库）。
计时用单调钟，落库值用 UTC ISO，与全库其他时间戳同口径。

临时库直测 _stamp_last_access 本体；"鉴权入口真的会调用它"由临时环境端到端实测把关。
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

from app.core import auth as auth_mod  # noqa: E402
from app.models import Base, User, make_engine, make_session_factory  # noqa: E402


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'access.db'}")
    Base.metadata.create_all(engine)
    s = make_session_factory(engine)()
    yield s
    s.close()


def _add_user(db, user_id: str) -> User:
    u = User(user_id=user_id, username=user_id, nickname=user_id, role="normal",
             status="enabled", password_hash="x",
             created_at=_now().isoformat(), updated_at=_now().isoformat())
    db.add(u)
    db.commit()
    db.refresh(u)
    auth_mod._access_stamp_seen.clear()  # 隔离节流状态，别让上一条用例影响这一条
    return u


def test_第一次访问立即落库(db):
    u = _add_user(db, "a1")
    assert u.last_access_at is None
    auth_mod._stamp_last_access(db, u)
    db.refresh(u)
    assert u.last_access_at is not None, "第一次访问就该落库"
    got = datetime.fromisoformat(u.last_access_at)
    assert abs((_now() - got).total_seconds()) < 30, f"落的是现在，实得 {u.last_access_at}"


def test_节流窗口内第二次不重写(db):
    u = _add_user(db, "a2")
    auth_mod._stamp_last_access(db, u)
    db.refresh(u)
    first = u.last_access_at
    auth_mod._stamp_last_access(db, u)  # 立刻再来一次，应被 60 秒窗口挡住
    db.refresh(u)
    assert u.last_access_at == first, "窗口内的访问不该重写库，否则每个接口都在写"


def test_窗口过后会更新(db, monkeypatch):
    u = _add_user(db, "a3")
    auth_mod._stamp_last_access(db, u)
    db.refresh(u)
    first = u.last_access_at
    # 把节流间隔调成 0 模拟"时间已过窗口"，不必真等 60 秒
    monkeypatch.setattr(auth_mod, "ACCESS_STAMP_INTERVAL", 0.0)
    auth_mod._stamp_last_access(db, u)
    db.refresh(u)
    assert u.last_access_at != first, "过了窗口就该刷新，否则这列永远是第一次的值"


def test_落库失败不拖垮业务请求(db, monkeypatch):
    u = _add_user(db, "a4")
    calls = {"commit": 0}

    def boom():
        calls["commit"] += 1
        raise RuntimeError("库被锁了")

    monkeypatch.setattr(db, "commit", boom)
    auth_mod._stamp_last_access(db, u)  # 不应抛异常——访问时间不值得让请求失败
    assert calls["commit"] == 1, "commit 失败应被吞掉并回滚"
    monkeypatch.undo()
    db.rollback()
    db.refresh(u)
    assert u.last_access_at is None, "失败的写入不能留下半个值"


def test_多人互不串且各自节流(db):
    u1 = _add_user(db, "a5")
    u2 = _add_user(db, "a6")
    auth_mod._stamp_last_access(db, u1)
    auth_mod._stamp_last_access(db, u2)
    db.refresh(u1)
    db.refresh(u2)
    assert u1.last_access_at and u2.last_access_at, "两人各写各的"
    assert u2.last_access_at >= u1.last_access_at
