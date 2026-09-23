"""用户管理路由（用户系统 v0.8，仅管理员）。

整组端点由 require_admin 守门：未登录 401、非管理员 403。
管理员账号 manager 不在可改/可停/可重置范围内。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.auth import get_db, require_admin
from ..core.schemas import UserCreate, UserOut, UserStatusUpdate, UserUpdate
from ..repository.users import UserRepo

router = APIRouter()


def _get_non_admin_or_404(db, user_id: str):
    """取目标用户；不存在 404；目标是管理员则 403（不可被管理）。"""
    obj = UserRepo().get_raw(db, user_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if obj.role == "manager":
        raise HTTPException(status_code=403, detail="管理员账号不可被修改/停用/重置")
    return obj


@router.get("/api/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), admin=Depends(require_admin)):
    """用户列表（不含密码）。"""
    return UserRepo().list(db)


@router.post("/api/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """新增用户；初始密码 234567；用户名唯一。"""
    repo = UserRepo()
    if repo.username_exists(db, body.username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    return repo.create(db, username=body.username, nickname=body.nickname,
                       phone=body.phone, is_vip=body.is_vip)


@router.patch("/api/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserUpdate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """改用户信息（用户名不可改）。"""
    _get_non_admin_or_404(db, user_id)
    updated = UserRepo().update(db, user_id, nickname=body.nickname, phone=body.phone, is_vip=body.is_vip)
    if updated is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return updated


@router.post("/api/users/{user_id}/reset-password", status_code=200)
def reset_password(user_id: str, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """重置密码为 234567，不返回明文。"""
    _get_non_admin_or_404(db, user_id)
    UserRepo().reset_password(db, user_id)
    return {"ok": True}


@router.patch("/api/users/{user_id}/status", response_model=UserOut)
def set_status(user_id: str, body: UserStatusUpdate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """启用/禁用；禁用只挡登录、保留数据。"""
    if body.status not in ("enabled", "disabled"):
        raise HTTPException(status_code=400, detail="status 必须是 enabled 或 disabled")
    _get_non_admin_or_404(db, user_id)
    updated = UserRepo().set_status(db, user_id, body.status)
    if updated is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return updated
