"""系统设置路由（ADR-020）。

权限一律后端强制：读取与保存都走 `require_admin`，前端隐藏菜单只是体验。
只读容量口子 `/api/settings/capacity` 不设权限 —— 等待的人有权知道"为什么慢"，
但它只回三个计数，不构成一个只读设置面。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.auth import get_db, require_admin
from ..core.schemas import CapacityInfo, SystemSettingsOut, SystemSettingsUpdate
from ..service.settings import settings_service

router = APIRouter()


@router.get("/api/admin/settings", response_model=SystemSettingsOut)
def get_system_settings(db: Session = Depends(get_db), user=Depends(require_admin)):
    """系统设置全貌：元数据 + 当前生效值（secret 项只回掩码）。"""
    return settings_service.view(db)


@router.put("/api/admin/settings", response_model=SystemSettingsOut)
def save_system_settings(body: SystemSettingsUpdate, db: Session = Depends(get_db),
                         user=Depends(require_admin)):
    """保存系统设置（部分提交；值传 null = 恢复默认）。

    校验失败整次 400 并逐项给出原因，不做部分成功。
    """
    return settings_service.save(db, body.values, getattr(user, "user_id", None))


@router.get("/api/settings/capacity", response_model=CapacityInfo)
def get_capacity(db: Session = Depends(get_db)):
    """并发上限与当前在跑/排队数（任意身份只读）。"""
    return settings_service.capacity(db)
