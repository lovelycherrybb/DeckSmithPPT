"""数据存取层（SQLAlchemy）。返回普通 dict，避免 ORM 对象跨会话失效。

包含项目、任务、草稿、草稿版本、演示文件的 CRUD 操作。
"""
# 须为文件首条语句。本模块的类里定义了名为 `list` 的方法（ProjectRepo.list 等），
# 同名之后、同类中方法的注解 `list[str]` 会在类作用域解析到该函数对象而非内建类型：
# Python 3.11/3.12 在 import 期即抛 `TypeError: 'function' object is not subscriptable`，
# 而 3.14 起注解默认惰性求值（PEP 649）恰好把它掩盖了——开发机因此从未暴露。
# 本行让各版本行为一致，也是 pyproject 里 requires-python ">=3.11" 能成立的前提。
from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlalchemy import select

from ..models import Project, Job, Draft, DraftVersion, Presentation
from ..core.db import SessionLocal


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode_json_columns(fields: dict) -> dict:
    """把「以 JSON 文本存列」的 dict 值就地序列化（engine_params / token_usage）。

    create 与 update 两条写路径必须同规则，否则一边存对象一边存字符串。
    None 保持 None（落库为 NULL）——token_usage 的 NULL 就是「未记录」这个语义
    （ADR-016），序列化成字符串 "null" 就读不回来了。
    """
    for key in ("engine_params", "token_usage"):
        if isinstance(fields.get(key), dict):
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    return fields


class ProjectRepo:
    """项目表 CRUD。"""

    @staticmethod
    def _row_to_dict(obj: Project) -> dict:
        return {
            "project_id": obj.project_id,
            "project_name": obj.project_name,  # 与 contracts 对齐
            "current_draft_id": obj.current_draft_id,  # 与 contracts 对齐
            "current_draft_version": obj.current_draft_version,  # 与 contracts 对齐
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
        }

    def create(self, db, **fields) -> dict:
        obj = Project(**fields)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)

    def get(self, db, project_id: str) -> dict | None:
        obj = db.execute(select(Project).where(Project.project_id == project_id)).scalar_one_or_none()
        return self._row_to_dict(obj) if obj else None

    def list(self, db, limit: int = 20, offset: int = 0) -> list[dict]:
        query = select(Project).order_by(Project.created_at.desc(), Project.id.desc()).limit(limit).offset(offset)
        rows = db.execute(query).scalars().all()
        return [self._row_to_dict(obj) for obj in rows]

    def update(self, db, project_id: str, **fields) -> dict | None:
        obj = db.execute(select(Project).where(Project.project_id == project_id)).scalar_one_or_none()
        if obj is None:
            return None
        for key, value in fields.items():
            setattr(obj, key, value)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)

    def stats(self, db, project_ids: list[str]) -> dict[str, dict]:
        """批量统计各项目的草稿数 / 演示文件数 / 运行中任务数（三条 group-by，避免 N+1）。"""
        from sqlalchemy import func
        if not project_ids:
            return {}

        def group_count(model, column, *extra_filters):
            query = select(column, func.count()).where(column.in_(project_ids))
            for f in extra_filters:
                query = query.where(f)
            return dict(db.execute(query.group_by(column)).all())

        drafts = group_count(Draft, Draft.project_id)
        presentations = group_count(Presentation, Presentation.project_id)
        running = group_count(Job, Job.project_id, Job.status == "running")
        return {
            pid: {
                "draft_count": drafts.get(pid, 0),
                "presentation_count": presentations.get(pid, 0),
                "running_task_count": running.get(pid, 0),
            }
            for pid in project_ids
        }


class JobRepo:
    """任务表 CRUD。方法第一个参数是 db 会话（由调用方提供）。"""

    @staticmethod
    def _row_to_dict(obj: Job) -> dict:
        return {
            "job_id": obj.job_id,
            "project_id": obj.project_id,
            "task_type": obj.task_type,
            "status": obj.status,
            "status_detail": obj.status_detail,
            "engine_job_id": obj.engine_job_id,  # 与 contracts 对齐
            "title": obj.title,
            "pages": obj.pages,
            "engine": obj.engine,
            "agent": obj.agent,
            "content_style": obj.content_style,
            "engine_params": json.loads(obj.engine_params) if obj.engine_params else {},
            "content": obj.content,
            "draft_content": obj.draft_content,
            "draft_path": obj.draft_path,
            "draft_id": obj.draft_id,
            "draft_version": obj.draft_version,
            "result_path": obj.result_path,
            "error": obj.error,
            # JSON 文本 → dict；NULL/空串一律 None（= 未记录，不是 0）
            "token_usage": json.loads(obj.token_usage) if obj.token_usage else None,
            "created_by": obj.created_by,
            "updated_by": obj.updated_by,
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
        }

    def create(self, db, **fields) -> dict:
        fields.setdefault("content_style", "marketing_promo")
        fields.setdefault("task_type", "draft")
        _encode_json_columns(fields)
        obj = Job(**fields)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)

    def get(self, db, job_id: str) -> dict | None:
        obj = db.execute(select(Job).where(Job.job_id == job_id)).scalar_one_or_none()
        return self._row_to_dict(obj) if obj else None

    def list(self, db, project_id: str | None = None, status: str | None = None, 
             task_type: str | None = None, limit: int = 20, offset: int = 0) -> list[dict]:
        query = select(Job)
        if project_id:
            query = query.where(Job.project_id == project_id)
        if status:
            query = query.where(Job.status == status)
        if task_type:
            query = query.where(Job.task_type == task_type)
        query = query.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit).offset(offset)
        rows = db.execute(query).scalars().all()
        return [self._row_to_dict(obj) for obj in rows]

    def update(self, db, job_id: str, **fields) -> dict | None:
        obj = db.execute(select(Job).where(Job.job_id == job_id)).scalar_one_or_none()
        if obj is None:
            return None
        if "content_style" in fields and fields["content_style"] is None:
            fields["content_style"] = "marketing_promo"
        _encode_json_columns(fields)
        for key, value in fields.items():
            setattr(obj, key, value)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)


class DraftRepo:
    """草稿表 CRUD。"""

    @staticmethod
    def _row_to_dict(obj: Draft) -> dict:
        return {
            "draft_id": obj.draft_id,
            "project_id": obj.project_id,
            "task_id": obj.task_id,
            "current_version": obj.current_version,
            "draft_path": obj.draft_path,  # 与 contracts 对齐
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
        }

    def create(self, db, **fields) -> dict:
        obj = Draft(**fields)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)

    def get(self, db, draft_id: str) -> dict | None:
        obj = db.execute(select(Draft).where(Draft.draft_id == draft_id)).scalar_one_or_none()
        return self._row_to_dict(obj) if obj else None

    def list_by_project(self, db, project_id: str) -> list[dict]:
        query = select(Draft).where(Draft.project_id == project_id).order_by(Draft.created_at.desc())
        rows = db.execute(query).scalars().all()
        return [self._row_to_dict(obj) for obj in rows]

    def update(self, db, draft_id: str, **fields) -> dict | None:
        obj = db.execute(select(Draft).where(Draft.draft_id == draft_id)).scalar_one_or_none()
        if obj is None:
            return None
        for key, value in fields.items():
            setattr(obj, key, value)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)


class DraftVersionRepo:
    """草稿版本表 CRUD。"""

    @staticmethod
    def _row_to_dict(obj: DraftVersion) -> dict:
        return {
            "draft_id": obj.draft_id,
            "version": obj.version,
            "content": obj.content,
            "created_at": obj.created_at,
        }

    def create(self, db, **fields) -> dict:
        obj = DraftVersion(**fields)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)

    def get(self, db, draft_id: str, version: str) -> dict | None:
        obj = db.execute(
            select(DraftVersion).where(
                DraftVersion.draft_id == draft_id,
                DraftVersion.version == version
            )
        ).scalar_one_or_none()
        return self._row_to_dict(obj) if obj else None

    def list_by_draft(self, db, draft_id: str) -> list[dict]:
        query = select(DraftVersion).where(DraftVersion.draft_id == draft_id).order_by(DraftVersion.created_at.desc())
        rows = db.execute(query).scalars().all()
        return [self._row_to_dict(obj) for obj in rows]


class PresentationRepo:
    """演示文件表 CRUD。"""

    @staticmethod
    def _row_to_dict(obj: Presentation) -> dict:
        return {
            "presentation_id": obj.presentation_id,
            "project_id": obj.project_id,
            "task_id": obj.task_id,
            "file_name": obj.file_name,  # 与 contracts 对齐
            "file_format": obj.file_format,  # 与 contracts 对齐
            "file_path": obj.file_path,  # 与 contracts 对齐
            "created_at": obj.created_at,
        }

    def create(self, db, **fields) -> dict:
        obj = Presentation(**fields)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)

    def get(self, db, presentation_id: str) -> dict | None:
        obj = db.execute(select(Presentation).where(Presentation.presentation_id == presentation_id)).scalar_one_or_none()
        return self._row_to_dict(obj) if obj else None

    def list_by_project(self, db, project_id: str) -> list[dict]:
        query = select(Presentation).where(Presentation.project_id == project_id).order_by(Presentation.created_at.desc())
        rows = db.execute(query).scalars().all()
        return [self._row_to_dict(obj) for obj in rows]
