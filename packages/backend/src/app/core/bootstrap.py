"""应用启动播种（用户系统 v0.8）。

在 init_db 建表/迁移之后调用，确保内置管理员账号存在。幂等：已存在则原样跳过，
不重置密码（否则每次重启会把管理员改过的口令冲回 234567）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import User
from .passwords import hash_password

DEFAULT_ADMIN_USERNAME = "manager"
DEFAULT_ADMIN_PASSWORD = "234567"  # 与「重置密码」统一；上线后应尽快自助改密


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def seed_default_admin(db: Session) -> bool:
    """确保内置 manager 存在；返回是否新建。已存在不改动（保护用户改过的密码）。"""
    exists = db.execute(
        select(User).where(User.username == DEFAULT_ADMIN_USERNAME)
    ).scalar_one_or_none()
    if exists is not None:
        return False

    now = _now()
    db.add(User(
        user_id=uuid.uuid4().hex,
        username=DEFAULT_ADMIN_USERNAME,
        nickname="管理员",
        phone=None,
        role="manager",
        status="enabled",
        password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
        created_at=now,
        updated_at=now,
    ))
    db.commit()
    return True
