"""鉴权机制（用户系统 v0.8）：服务端会话 + httpOnly Cookie + 访客身份 + FastAPI 依赖。

设计口径（见 vc_design constraints/02、flows/03）：
- 会话存服务端 user_sessions 表，Cookie 只承载不可猜测的 session_id（httpOnly）；
  登出/禁用即时失效，不依赖前端持有任何可离线校验的凭据。
- 访客身份不建表：未登录试用时下发一个不可猜测的 visitor_id Cookie，试用产生的项目记
  projects.visitor_id；登录后按该令牌把未认领项目并入真实用户。
- 权限一律后端强制：require_login / require_admin 是路由的守门依赖，前端隐藏只是体验。

核心函数都接收 db 会话，便于用临时库单测；依赖包装层才碰 Request/Response。
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import User, UserSession
from .db import SessionLocal

SESSION_COOKIE = "ds_session"
VISITOR_COOKIE = "ds_visitor"
SESSION_TTL = timedelta(days=7)
VISITOR_TTL = timedelta(days=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def get_db():
    """FastAPI 依赖：每请求一个会话。auth 自带一份，避免与 routes.get_db 循环导入。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── 会话核心（纯函数，接收 db） ──
def create_session(db: Session, user_id: str) -> str:
    sid = new_token()
    db.add(UserSession(
        session_id=sid, user_id=user_id,
        created_at=_now().isoformat(), expires_at=(_now() + SESSION_TTL).isoformat(),
    ))
    db.commit()
    return sid


def resolve_session(db: Session, session_id: str | None) -> Optional[User]:
    """按 session_id 取当前有效用户；过期即删、账号禁用即视为无身份。"""
    if not session_id:
        return None
    row = db.execute(select(UserSession).where(UserSession.session_id == session_id)).scalar_one_or_none()
    if row is None:
        return None
    if datetime.fromisoformat(row.expires_at) < _now():
        db.delete(row)
        db.commit()
        return None
    user = db.execute(select(User).where(User.user_id == row.user_id)).scalar_one_or_none()
    if user is None or user.status != "enabled":
        return None
    return user


def destroy_session(db: Session, session_id: str | None) -> None:
    if not session_id:
        return
    db.execute(delete(UserSession).where(UserSession.session_id == session_id))
    db.commit()


# ── Cookie 助手 ──
def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, session_id, httponly=True, samesite="lax",
        max_age=int(SESSION_TTL.total_seconds()), path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def ensure_visitor_cookie(request: Request, response: Response) -> str:
    """返回现有访客令牌；没有就生成一个并下发 Cookie。"""
    vid = request.cookies.get(VISITOR_COOKIE)
    if not vid:
        vid = new_token()
        response.set_cookie(
            VISITOR_COOKIE, vid, httponly=True, samesite="lax",
            max_age=int(VISITOR_TTL.total_seconds()), path="/",
        )
    return vid


def clear_visitor_cookie(response: Response) -> None:
    response.delete_cookie(VISITOR_COOKIE, path="/")


# ── 身份与依赖 ──
@dataclass
class Identity:
    user: Optional[User]          # 已登录时为 ORM 对象（含 role/status），否则 None
    visitor_id: Optional[str]

    @property
    def authenticated(self) -> bool:
        return self.user is not None

    @property
    def is_admin(self) -> bool:
        return self.user is not None and self.user.role == "manager"


def get_identity(request: Request, db: Session = Depends(get_db)) -> Identity:
    user = resolve_session(db, request.cookies.get(SESSION_COOKIE))
    return Identity(user=user, visitor_id=request.cookies.get(VISITOR_COOKIE))


def require_login(identity: Identity = Depends(get_identity)) -> User:
    if identity.user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return identity.user


def require_admin(identity: Identity = Depends(get_identity)) -> User:
    if identity.user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    if identity.user.role != "manager":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    return identity.user
