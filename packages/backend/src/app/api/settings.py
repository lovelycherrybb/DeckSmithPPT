"""用户偏好路由（用户系统 v0.8，本人）。

需登录；每人一份、跨设备带回。偏好只作初始值，不绕过角色门控（引擎参数仍按角色禁用）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.auth import get_db, require_login
from ..core.schemas import UserPreferenceOut, UserPreferenceUpdate
from ..repository.users import UserSettingRepo

router = APIRouter()


@router.get("/api/settings/preferences", response_model=UserPreferenceOut)
def get_preferences(db: Session = Depends(get_db), user=Depends(require_login)):
    """获取本人偏好；无记录返回系统默认。"""
    return UserSettingRepo().get(db, user.user_id)


@router.put("/api/settings/preferences", response_model=UserPreferenceOut)
def save_preferences(body: UserPreferenceUpdate, db: Session = Depends(get_db), user=Depends(require_login)):
    """保存本人偏好（只覆盖传入的键）。"""
    return UserSettingRepo().save(db, user.user_id, body.model_dump(exclude_unset=True))
