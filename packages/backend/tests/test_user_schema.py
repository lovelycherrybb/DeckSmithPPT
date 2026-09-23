"""用户系统 v0.8 数据层回归（B1）。

覆盖：新表由 create_all 建出、老库迁移补 owner_user_id/visitor_id 列、内置 manager 播种幂等、
密码哈希往返与防篡改。不起服务、不碰网络。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
_PACKAGES = Path(__file__).resolve().parents[2]
for p in (str(_BACKEND_SRC), str(_PACKAGES)):
    if p not in sys.path:
        sys.path.insert(0, p)

from sqlalchemy import select  # noqa: E402

from app.core.bootstrap import DEFAULT_ADMIN_USERNAME, seed_default_admin  # noqa: E402
from app.core.db import run_startup_migrations  # noqa: E402
from app.core.passwords import hash_password, verify_password  # noqa: E402
from app.models import Base, User, UserSession, UserSetting, make_engine, make_session_factory  # noqa: E402


# ── 密码哈希 ──
def test_密码哈希往返_且每次盐不同():
    h1 = hash_password("234567")
    h2 = hash_password("234567")
    assert h1 != h2  # 随机盐：同口令两次哈希不同
    assert verify_password("234567", h1) and verify_password("234567", h2)
    assert not verify_password("wrong", h1)


def test_畸形存储串判失败不抛():
    assert not verify_password("x", "not-a-valid-hash")
    assert not verify_password("x", "sha1$1$deadbeef$cafe")  # 算法不符


# ── 建表 + 播种 ──
def _session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'u.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)()


def test_新表建出且播种manager幂等不重置密码(tmp_path):
    db = _session(tmp_path)
    # 三张表都在
    assert {t.name for t in Base.metadata.sorted_tables} >= {"users", "user_sessions", "user_settings"}

    assert seed_default_admin(db) is True  # 首次新建
    admin = db.execute(select(User).where(User.username == DEFAULT_ADMIN_USERNAME)).scalar_one()
    assert admin.role == "manager" and admin.status == "enabled"
    assert verify_password("234567", admin.password_hash)

    # 管理员改了密码后重启播种：不得被冲回默认密码
    admin.password_hash = hash_password("my-new-pw")
    db.commit()
    assert seed_default_admin(db) is False
    again = db.execute(select(User).where(User.username == DEFAULT_ADMIN_USERNAME)).scalar_one()
    assert verify_password("my-new-pw", again.password_hash)
    assert not verify_password("234567", again.password_hash)


# ── 老库迁移补归属列 ──
def test_老库迁移补owner_user_id与visitor_id(tmp_path):
    db_path = tmp_path / "decksmith.db"
    conn = sqlite3.connect(db_path)
    with conn:
        conn.executescript(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, project_id VARCHAR(64) UNIQUE,"
            " project_name VARCHAR(255), current_draft_id VARCHAR(64),"
            " current_draft_version VARCHAR(32), created_at VARCHAR(32), updated_at VARCHAR(32));"
            "CREATE TABLE users (id INTEGER PRIMARY KEY, user_id VARCHAR(64));"  # 让 FK 目标存在
        )
        conn.execute(
            "INSERT INTO projects (project_id, project_name, created_at, updated_at)"
            " VALUES ('p1','老项目','t','t')"
        )
    conn.close()

    run_startup_migrations(db_path)

    cols = {r[1] for r in sqlite3.connect(db_path).execute("PRAGMA table_info(projects)")}
    assert {"owner_user_id", "visitor_id"} <= cols
    # 老行归属为 NULL（未归属，迁移不臆造 owner）
    row = sqlite3.connect(db_path).execute(
        "SELECT owner_user_id, visitor_id FROM projects WHERE project_id='p1'").fetchone()
    assert row == (None, None)
