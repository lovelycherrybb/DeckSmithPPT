"""用户表数据存取（用户系统 v0.8）。

返回普通 dict（不含 password_hash），避免 ORM 对象跨会话失效、也杜绝密码外泄。
需要校验口令的内部路径用 get_raw 拿 ORM 对象（含 hash），仅 auth 层使用。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from ..models import User, UserSession, UserSetting
from ..core.passwords import hash_password

DEFAULT_PASSWORD = "234567"  # 新增用户 / 重置密码统一值
DEFAULT_PREFS = {"default_engine": None, "default_pages": None, "auto_save": True}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _role_from_vip(is_vip: bool) -> str:
    return "vip" if is_vip else "normal"


class UserRepo:
    """用户 CRUD。方法第一参数是 db 会话。"""

    @staticmethod
    def to_dict(obj: User) -> dict:
        return {
            "user_id": obj.user_id,
            "username": obj.username,
            "nickname": obj.nickname,
            "phone": obj.phone,
            "role": obj.role,
            "status": obj.status,
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
        }

    def get_raw(self, db, user_id: str) -> User | None:
        return db.execute(select(User).where(User.user_id == user_id)).scalar_one_or_none()

    def get_raw_by_username(self, db, username: str) -> User | None:
        return db.execute(select(User).where(User.username == username)).scalar_one_or_none()

    def get(self, db, user_id: str) -> dict | None:
        obj = self.get_raw(db, user_id)
        return self.to_dict(obj) if obj else None

    def username_exists(self, db, username: str) -> bool:
        return self.get_raw_by_username(db, username) is not None

    def last_login_map(self, db) -> dict[str, str]:
        """user_id -> 最后一次登录时间（该用户最近一条会话的 created_at）。

        会话只在登录时新建、期间不续期（见 core/auth.py 的 create_session），
        所以这个值是"最近登录"而不是"最近访问"；契约 User.last_login_at 里已写明。
        一条分组查询取全部，不在列表里逐人查。
        """
        rows = db.execute(
            select(UserSession.user_id, func.max(UserSession.created_at))
            .group_by(UserSession.user_id)
        ).all()
        return {uid: ts for uid, ts in rows}

    def list(self, db) -> list[dict]:
        rows = db.execute(select(User).order_by(User.created_at.desc(), User.id.desc())).scalars().all()
        logins = self.last_login_map(db)
        out = []
        for o in rows:
            d = self.to_dict(o)
            d["last_login_at"] = logins.get(o.user_id)
            out.append(d)
        return out

    def create(self, db, *, username: str, nickname: str | None = None,
               phone: str | None = None, is_vip: bool = False,
               password: str | None = None) -> dict:
        """新增普通/高级用户（初始密码默认 234567）。不用于建管理员（管理员由 bootstrap 播种）。"""
        now = _now()
        obj = User(
            user_id=uuid.uuid4().hex,
            username=username,
            nickname=(nickname or username),
            phone=phone,
            role=_role_from_vip(is_vip),
            status="enabled",
            password_hash=hash_password(password or DEFAULT_PASSWORD),
            created_at=now,
            updated_at=now,
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return self.to_dict(obj)

    def update(self, db, user_id: str, *, nickname: str | None = None,
               phone: str | None = None, is_vip: bool | None = None) -> dict | None:
        """改用户信息；用户名不可改。is_vip 在 normal↔vip 间切换，绝不升级到 manager。"""
        obj = self.get_raw(db, user_id)
        if obj is None or obj.role == "manager":
            return None
        if nickname is not None:
            obj.nickname = nickname
        if phone is not None:
            obj.phone = phone
        if is_vip is not None and obj.role in ("normal", "vip"):
            obj.role = _role_from_vip(is_vip)
        obj.updated_at = _now()
        db.commit()
        db.refresh(obj)
        return self.to_dict(obj)

    def set_status(self, db, user_id: str, status: str) -> dict | None:
        """启用/禁用；管理员自身不可被禁用（返回 None 由上层转 403）。"""
        obj = self.get_raw(db, user_id)
        if obj is None or obj.role == "manager":
            return None
        obj.status = status
        obj.updated_at = _now()
        db.commit()
        db.refresh(obj)
        return self.to_dict(obj)

    def set_password(self, db, user_id: str, new_password: str) -> None:
        obj = self.get_raw(db, user_id)
        if obj is None:
            return
        obj.password_hash = hash_password(new_password)
        obj.updated_at = _now()
        db.commit()

    def reset_password(self, db, user_id: str) -> dict | None:
        obj = self.get_raw(db, user_id)
        if obj is None or obj.role == "manager":
            return None
        obj.password_hash = hash_password(DEFAULT_PASSWORD)
        obj.updated_at = _now()
        db.commit()
        db.refresh(obj)
        return self.to_dict(obj)


class UserSettingRepo:
    """用户偏好 CRUD：与用户 1 对 1，prefs 存 JSON 单列（对齐 token_usage 的单列取舍）。"""

    def get(self, db, user_id: str) -> dict:
        obj = db.execute(select(UserSetting).where(UserSetting.user_id == user_id)).scalar_one_or_none()
        if obj is None:
            return {**DEFAULT_PREFS, "updated_at": None}
        prefs = json.loads(obj.prefs or "{}")
        return {**DEFAULT_PREFS, **prefs, "updated_at": obj.updated_at}

    def save(self, db, user_id: str, fields: dict) -> dict:
        """部分更新：只覆盖传入的键，其余保留；无记录则新建。"""
        obj = db.execute(select(UserSetting).where(UserSetting.user_id == user_id)).scalar_one_or_none()
        now = _now()
        if obj is None:
            merged = {**DEFAULT_PREFS, **fields}
            db.add(UserSetting(user_id=user_id, prefs=json.dumps(merged, ensure_ascii=False), updated_at=now))
        else:
            current = json.loads(obj.prefs or "{}")
            obj.prefs = json.dumps({**DEFAULT_PREFS, **current, **fields}, ensure_ascii=False)
            obj.updated_at = now
        db.commit()
        return self.get(db, user_id)
