"""用户列表的"最后一次登录时间"（契约字段 User.last_login_at）。

口径要说清：会话只在登录时新建、期间不续期（core/auth.py 的 create_session），
所以这一列是**最近登录**，不是"最近访问"——挂着账号一直用不会刷新。契约里已写明，
本文件把这条口径钉成断言，将来谁改成续期语义，这里的注释与用例要一起动。

临时库直测 UserRepo，不碰真库。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import event

_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
_PACKAGES = Path(__file__).resolve().parents[2]
for p in (str(_BACKEND_SRC), str(_PACKAGES)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.models import Base, User, UserSession, make_engine, make_session_factory  # noqa: E402
from app.repository.users import UserRepo  # noqa: E402


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'last_login.db'}")
    Base.metadata.create_all(engine)
    s = make_session_factory(engine)()
    yield s
    s.close()


def _add_user(db, user_id: str, username: str) -> User:
    u = User(user_id=user_id, username=username, nickname=username, role="normal",
             status="enabled", password_hash="x",
             created_at=_now().isoformat(), updated_at=_now().isoformat())
    db.add(u)
    db.commit()
    return u


def _add_session(db, user_id: str, *, minutes_ago: int) -> None:
    ts = _now() - timedelta(minutes=minutes_ago)
    db.add(UserSession(session_id=f"{user_id}-{minutes_ago}", user_id=user_id,
                       created_at=ts.isoformat(),
                       expires_at=(ts + timedelta(days=7)).isoformat()))
    db.commit()


def test_从未登录过的用户该字段为_无(db):
    _add_user(db, "u1", "alice")
    row = next(r for r in UserRepo().list(db) if r["user_id"] == "u1")
    assert row["last_login_at"] is None, "没有会话时应给 null，不能凭 created_at 凑一个假时间"


def test_多条会话取最近一次登录(db):
    _add_user(db, "u2", "bob")
    _add_session(db, "u2", minutes_ago=600)   # 早先一次登录
    _add_session(db, "u2", minutes_ago=30)    # 最近一次登录
    row = next(r for r in UserRepo().list(db) if r["user_id"] == "u2")
    got = datetime.fromisoformat(row["last_login_at"])
    assert abs((got - (_now() - timedelta(minutes=30))).total_seconds()) < 60, (
        f"应取最近一条会话，实得 {row['last_login_at']}")


def test_各用户互不串(db):
    _add_user(db, "u3", "carol")
    _add_user(db, "u4", "dave")
    _add_session(db, "u3", minutes_ago=100)
    rows = {r["user_id"]: r for r in UserRepo().list(db)}
    assert rows["u3"]["last_login_at"] is not None
    assert rows["u4"]["last_login_at"] is None, "把别人的登录时间算到没登录的人头上即口径错"


def test_列表不逐人查询(db):
    """N+1 守卫：10 个用户 10 条会话，取列表的 SELECT 次数必须是常数级。"""
    for i in range(10):
        _add_user(db, f"u{i}", f"user{i}")
        _add_session(db, f"u{i}", minutes_ago=i + 1)
    counts = {"select": 0}
    key = db.bind

    @event.listens_for(key, "before_cursor_execute")
    def _count(conn, cursor, statement, *args, **kwargs):
        if statement.strip().lower().startswith("select"):
            counts["select"] += 1

    UserRepo().list(db)
    event.remove(key, "before_cursor_execute", _count)
    assert counts["select"] <= 3, (
        f"列表查询发出 {counts['select']} 条 SELECT，疑似逐人查会话（应一条分组查询取全部）")


def test_新字段不带走密码散列(db):
    _add_user(db, "u9", "erin")
    _add_session(db, "u9", minutes_ago=5)
    row = next(r for r in UserRepo().list(db) if r["user_id"] == "u9")
    assert "password_hash" not in row, "补字段时把整份模型字典转出去＝泄散列"
