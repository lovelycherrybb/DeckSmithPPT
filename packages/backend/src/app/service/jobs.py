"""业务逻辑层：提交/查询/确认，串联引擎（PptEngine 协议）与数据存取。

- 只依赖 PptEngine 协议，不关心具体引擎（stub 或真实 PptMasterEngine 皆可替换）。
- 两段式：submit 产草稿（draft）；confirm 调引擎正式生成（running→completed），后台轮询状态。
"""
import time
import uuid
from datetime import datetime, timezone

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import Session

from ..core.db import SessionLocal, JOBS_ROOT
from ..repository.jobs import JobRepo
from ..core.schemas import DeckRequest, JobOut, ProgressInfo


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_DEFAULT_POLL_INTERVAL = 15
_DEFAULT_TIMEOUT_SECONDS = 3600


class JobService:
    def __init__(self, repo: JobRepo, engine):
        self.repo = repo
        self.engine = engine  # 任意实现了 PptEngine 协议的对象

    @staticmethod
    def _resolve_style(engine_params: dict | None) -> str:
        return (engine_params or {}).get("style") or "swiss-minimal"

    def _enrich_with_progress(self, job: JobOut) -> JobOut:
        """如果任务正在运行，附加进度信息。"""
        if job.status == "running" and hasattr(self.engine, 'get_progress'):
            progress_data = self.engine.get_progress(job.job_id)
            if progress_data:
                job.progress = ProgressInfo(**progress_data)
        return job

    # ── submit：产草稿 ──
    def submit(self, db: Session, req: DeckRequest) -> JobOut:
        job_id = uuid.uuid4().hex
        source = req.source.model_dump() if req.source else None
        engine_params = {**(req.engine_params or {}), "content_style": req.content_style}
        # 调引擎产出草稿 Markdown（不调 AI、不计费），写到 jobs_root/<job_id>/draft.md
        self.engine.submit(
            content=req.content,
            title=req.title,
            pages=req.pages,
            engine_params=engine_params,
            source=source,
            job_id=job_id,
        )
        job_dir = JOBS_ROOT / job_id
        draft_path = job_dir / "draft.md"
        draft_content = draft_path.read_text(encoding="utf-8") if draft_path.exists() else ""

        now = _now()
        row = self.repo.create(
            db,
            job_id=job_id,
            status="draft",
            title=req.title,
            pages=req.pages,
            engine=req.engine,
            agent=req.agent,
            content_style=req.content_style,
            engine_params=engine_params,
            content=req.content,
            draft_content=draft_content,
            draft_path=str(draft_path),
            created_at=now,
            updated_at=now,
        )
        return JobOut(**row)

    def list(self, db: Session, status: str | None = None, limit: int = 20, offset: int = 0) -> list[JobOut]:
        rows = self.repo.list(db, status=status, limit=limit, offset=offset)
        jobs = [JobOut(**row) for row in rows]
        # 为 running 状态的任务附加进度
        return [self._enrich_with_progress(job) for job in jobs]

    # ── get：查任务 ──
    def get(self, db: Session, job_id: str) -> JobOut:
        row = self.repo.get(db, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        job = JobOut(**row)
        return self._enrich_with_progress(job)

    # ── save_draft：暂存用户修改（不触发生成）──
    def save_draft(self, db: Session, job_id: str, content: str) -> JobOut:
        row = self.repo.get(db, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if row["status"] != "draft":
            raise HTTPException(status_code=409, detail="任务当前不是 draft 状态，无法保存草稿")
        (JOBS_ROOT / job_id / "draft.md").write_text(content or "", encoding="utf-8")
        row = self.repo.update(db, job_id, draft_content=content, updated_at=_now())
        return JobOut(**row)

    # ── confirm：正式生成 ──
    def confirm(self, db: Session, job_id: str, content: str, engine_params: dict, background_tasks: BackgroundTasks) -> JobOut:
        row = self.repo.get(db, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if row["status"] != "draft":
            raise HTTPException(status_code=409, detail="任务当前不是 draft 状态，无法确认")

        # 用户改好的内容落盘，状态置 running
        (JOBS_ROOT / job_id / "draft.md").write_text(content or "", encoding="utf-8")
        confirm_engine_params = dict(engine_params or {})
        confirm_engine_params.setdefault("content_style", row.get("content_style") or "marketing_promo")
        merged_engine_params = {**(row.get("engine_params") or {}), **confirm_engine_params}
        merged_engine_params.setdefault("content_style", row.get("content_style") or "marketing_promo")
        merged_engine_params.setdefault("style", self._resolve_style(row.get("engine_params")))
        self.repo.update(db, job_id, status="running", content=content,
                         content_style=row.get("content_style") or "marketing_promo",
                         engine_params=merged_engine_params, updated_at=_now())

        # 调引擎正式生成（真实引擎在此拉起 claude 子进程；stub 起后台线程）
        self.engine.confirm(job_id, content, merged_engine_params)

        # 后台轮询引擎状态，直到 completed/failed/timeout 再回写 SQLite
        background_tasks.add_task(self._poll_until_done, job_id)
        return self.get(db, job_id)

    # ── 后台轮询：把引擎状态同步回 SQLite 任务表 ──
    def _poll_until_done(self, job_id: str, timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS) -> None:
        from engine.core.models import JobStatus  # 延迟导入，确保 engine 包已在 sys.path

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            status = self.engine.status(job_id)
            if status == JobStatus.COMPLETED:
                try:
                    result_path = self.engine.result(job_id)
                except Exception:
                    result_path = None
                db = SessionLocal()
                try:
                    JobRepo().update(db, job_id, status="completed",
                                     result_path=result_path, updated_at=_now())
                finally:
                    db.close()
                return
            if status in (JobStatus.FAILED, JobStatus.TIMEOUT):
                db = SessionLocal()
                try:
                    JobRepo().update(db, job_id, status=str(status),
                                     error="生成失败，详见引擎 run.log", updated_at=_now())
                finally:
                    db.close()
                return
            time.sleep(_DEFAULT_POLL_INTERVAL)
        # 超时兜底
        db = SessionLocal()
        try:
            JobRepo().update(db, job_id, status="timeout", error="生成超时", updated_at=_now())
        finally:
            db.close()
