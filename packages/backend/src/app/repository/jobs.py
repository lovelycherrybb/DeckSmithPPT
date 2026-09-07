"""任务数据存取层（SQLAlchemy）。返回普通 dict，避免 ORM 对象跨会话失效。"""
import json
from sqlalchemy import select

from ..models import Job
from ..core.db import SessionLocal


class JobRepo:
    """任务表 CRUD。方法第一个参数是 db 会话（由调用方提供）。"""

    @staticmethod
    def _row_to_dict(obj: Job) -> dict:
        return {
            "job_id": obj.job_id,
            "status": obj.status,
            "title": obj.title,
            "pages": obj.pages,
            "engine": obj.engine,
            "agent": obj.agent,
            "content_style": obj.content_style,
            "engine_params": json.loads(obj.engine_params) if obj.engine_params else {},
            "content": obj.content,
            "draft_content": obj.draft_content,
            "draft_path": obj.draft_path,
            "result_path": obj.result_path,
            "error": obj.error,
            "created_by": obj.created_by,
            "updated_by": obj.updated_by,
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
        }

    def create(self, db, **fields) -> dict:
        fields.setdefault("content_style", "marketing_promo")
        if isinstance(fields.get("engine_params"), dict):
            fields["engine_params"] = json.dumps(fields["engine_params"], ensure_ascii=False)
        obj = Job(**fields)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)

    def get(self, db, job_id: str) -> dict | None:
        obj = db.execute(select(Job).where(Job.job_id == job_id)).scalar_one_or_none()
        return self._row_to_dict(obj) if obj else None

    def list(self, db, status: str | None = None, limit: int = 20, offset: int = 0) -> list[dict]:
        query = select(Job)
        if status:
            query = query.where(Job.status == status)
        query = query.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit).offset(offset)
        rows = db.execute(query).scalars().all()
        return [self._row_to_dict(obj) for obj in rows]

    def update(self, db, job_id: str, **fields) -> dict | None:
        obj = db.execute(select(Job).where(Job.job_id == job_id)).scalar_one_or_none()
        if obj is None:
            return None
        if "content_style" in fields and fields["content_style"] is None:
            fields["content_style"] = "marketing_promo"
        if isinstance(fields.get("engine_params"), dict):
            fields["engine_params"] = json.dumps(fields["engine_params"], ensure_ascii=False)
        for key, value in fields.items():
            setattr(obj, key, value)
        db.commit()
        db.refresh(obj)
        return self._row_to_dict(obj)
