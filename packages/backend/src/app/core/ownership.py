"""归属与可见性策略（用户系统 v0.8，B3）。

把「谁能看什么、新项目归谁、owner_label 怎么算」集中一处，routes/service 只调用不各写一套，
避免过滤口径在多个端点漂移。判定规则本身见 vc_design constraints/02。

identity 为 None 表示「无鉴权上下文」（内部调用、既有单测）：一律放行、不打戳、不过滤，
保持旧行为不破坏；真实请求都会带 Identity。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Project, User

UNCLAIMED_LABEL = "未认领（访客）"
# 匹配不到任何真实 owner_user_id 的哨兵：用于「匿名且无访客令牌」时让它什么都看不到
_DENY = "\x00__deny__"


def create_stamp(identity) -> dict:
    """新建项目时要写的归属字段。已登录→owner；未登录有访客→visitor；都没有→不写。"""
    if identity is None:
        return {}
    if identity.user is not None:
        return {"owner_user_id": identity.user.user_id}
    if identity.visitor_id:
        return {"visitor_id": identity.visitor_id}
    return {}


def can_edit_engine_params(identity) -> bool:
    """是否可编辑引擎参数：仅 vip/管理员；普通用户与访客不可。identity=None（内部/旧测）放行。"""
    if identity is None:
        return True
    if identity.is_admin:
        return True
    return identity.user is not None and identity.user.role == "vip"


def can_access(project: dict, identity) -> bool:
    """当前身份是否可访问该项目。管理员放行全部；匿名无令牌一律拒绝。"""
    if identity is None:
        return True
    if identity.is_admin:
        return True
    if identity.user is not None:
        return project.get("owner_user_id") == identity.user.user_id
    if identity.visitor_id:
        # 试用态：仅「未认领」且 visitor 令牌命中的项目可见
        return project.get("owner_user_id") is None and project.get("visitor_id") == identity.visitor_id
    return False


def list_filter_kwargs(identity) -> dict:
    """转成 ProjectRepo.list 的关键字过滤参数。"""
    if identity is None:
        return {}  # 无鉴权上下文（内部调用/旧单测）→ 保持不过滤
    if identity.is_admin:
        return {}  # 管理员看全部
    if identity.user is not None:
        return {"owner_user_id": identity.user.user_id}
    if identity.visitor_id:
        return {"visitor_only": identity.visitor_id}
    return {"owner_user_id": _DENY}  # 匿名且无访客令牌 → 什么都看不到


def visible_project_ids(db: Session, identity) -> list[str] | None:
    """当前身份可见的项目 id 集合，用于跨项目列表（如 /api/jobs 不带 project_id）过滤。

    返回 None = 不受限（管理员或无鉴权上下文）；返回 [] = 什么都看不到。
    """
    if identity is None or identity.is_admin:
        return None
    if identity.user is not None:
        rows = db.execute(
            select(Project.project_id).where(Project.owner_user_id == identity.user.user_id)
        ).scalars().all()
        return list(rows)
    if identity.visitor_id:
        rows = db.execute(
            select(Project.project_id).where(
                Project.owner_user_id.is_(None), Project.visitor_id == identity.visitor_id
            )
        ).scalars().all()
        return list(rows)
    return []


def attach_owner_label(db: Session, projects: list[dict]) -> list[dict]:
    """批量派生 owner_label（昵称/用户名；未认领→「未认领（访客）」）。就地改并返回。

    一次查表拿齐所有 owner 名字，避免逐行 N+1；label 不入库，只随出参现算。
    """
    owner_ids = {p.get("owner_user_id") for p in projects if p.get("owner_user_id")}
    names: dict[str, str] = {}
    if owner_ids:
        rows = db.execute(
            select(User.user_id, User.nickname, User.username).where(User.user_id.in_(owner_ids))
        ).all()
        names = {r.user_id: (r.nickname or r.username) for r in rows}
    for p in projects:
        oid = p.get("owner_user_id")
        p["owner_label"] = names.get(oid, UNCLAIMED_LABEL) if oid else UNCLAIMED_LABEL
    return projects
