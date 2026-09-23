"""身份与鉴权路由（用户系统 v0.8）：登录 / 登出 / 当前身份 / 改密。

路由只做「读请求 → 调鉴权核心 → 写响应/Cookie」的薄编排，判定逻辑都在 core/auth.py。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..core.auth import (
    SESSION_COOKIE, VISITOR_COOKIE,
    clear_session_cookie, clear_visitor_cookie, create_session, destroy_session,
    ensure_visitor_cookie, get_db, require_login, resolve_session, set_session_cookie,
)
from ..core.passwords import verify_password
from ..core.schemas import (
    ChangePasswordRequest, CurrentUserOut, LoginRequest, LoginResult, UserOut,
)
from ..repository.jobs import ProjectRepo
from ..repository.users import UserRepo

router = APIRouter()


@router.post("/api/auth/login", response_model=LoginResult)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """校验凭据 → 建会话下发 Cookie → 内联把当前访客的未认领项目并入该用户。"""
    user = UserRepo().get_raw_by_username(db, body.username)
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if user.status != "enabled":
        raise HTTPException(status_code=403, detail="账号已被禁用")

    session_id = create_session(db, user.user_id)
    set_session_cookie(response, session_id)

    # 内联认领：把这台浏览器访客令牌下未认领的项目并入刚登录的用户
    claimed = ProjectRepo().claim_visitor_projects(db, request.cookies.get(VISITOR_COOKIE), user.user_id)
    clear_visitor_cookie(response)  # 认领后旧访客令牌作废，下次试用另起

    return LoginResult(user=UserRepo.to_dict(user), claimed_project_count=claimed)


@router.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """销毁服务端会话并清 Cookie。"""
    destroy_session(db, request.cookies.get(SESSION_COOKIE))
    clear_session_cookie(response)
    clear_visitor_cookie(response)
    return Response(status_code=204)


@router.get("/api/auth/me", response_model=CurrentUserOut)
def me(request: Request, response: Response, db: Session = Depends(get_db)):
    """当前身份三态。未登录时顺带下发访客令牌，供试用与登录后认领。"""
    user = resolve_session(db, request.cookies.get(SESSION_COOKIE))
    if user is None:
        visitor_id = ensure_visitor_cookie(request, response)
        return CurrentUserOut(authenticated=False, user=None, visitor_id=visitor_id)
    return CurrentUserOut(
        authenticated=True,
        user=UserRepo.to_dict(user),
        visitor_id=request.cookies.get(VISITOR_COOKIE),
    )


@router.post("/api/auth/password", status_code=200)
def change_password(body: ChangePasswordRequest, user=Depends(require_login), db: Session = Depends(get_db)):
    """改本人密码：验旧密码 → 写新哈希。当前会话不失效。"""
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="旧密码不正确")
    UserRepo().set_password(db, user.user_id, body.new_password)
    return {"ok": True}
