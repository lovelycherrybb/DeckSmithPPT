"""业务逻辑层：提交/查询，串联引擎（PptEngine 协议）与数据存取。

- 只依赖 PptEngine 协议，不关心具体引擎（stub 或真实 PptMasterEngine 皆可替换）。
- 草稿引擎任务和输出引擎任务各自独立提交，提交即运行。
- 支持项目自动创建、草稿版本管理、状态明细（status_detail）。
"""
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import Session

from ..core.db import SessionLocal, JOBS_ROOT
from ..repository.jobs import ProjectRepo, JobRepo, DraftRepo, DraftVersionRepo, PresentationRepo
from ..core.schemas import DeckRequest, JobOut, ProgressInfo


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _version_stamp() -> str:
    """版本号：UTC 年月日时分秒（14 位）。"""
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


_VERSION_RE = re.compile(r"^[0-9]{14}$")

# 上传文件名消毒：只留安全字符，防路径穿越
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]")

_DEFAULT_POLL_INTERVAL = 15
_DEFAULT_TIMEOUT_SECONDS = 3600


def sanitize_filename(name: str | None) -> str:
    """消毒上传文件名：去路径部分、替换危险字符、限制长度。"""
    if not name:
        return "upload.bin"
    # 只取 basename（防 ../../x 和绝对路径）
    name = Path(name).name
    name = _SAFE_FILENAME_RE.sub("_", name).strip("._") or "upload.bin"
    return name[:120]


class JobService:
    def __init__(self, repo: JobRepo, engine_factory):
        self.repo = repo
        self.project_repo = ProjectRepo()
        self.draft_repo = DraftRepo()
        self.draft_version_repo = DraftVersionRepo()
        self.presentation_repo = PresentationRepo()
        self._engine_factory = engine_factory  # 按引擎名创建实例（引擎可插拔）

    # ── 引擎选择：按请求里的 engine 名拿实例，而非全局单例 ──
    def _get_engine(self, engine_name: str | None):
        return self._engine_factory(engine_name)

    def _get_engine_safe(self, engine_name: str | None):
        """容错版引擎获取：无效引擎名返回 None（用于查询路径，不抛错）。"""
        try:
            return self._get_engine(engine_name)
        except ValueError:
            return None

    @staticmethod
    def _resolve_style(engine_params: dict | None) -> str:
        return (engine_params or {}).get("style") or "swiss-minimal"

    def _enrich_with_progress(self, job: JobOut, engine) -> JobOut:
        """如果任务正在运行，附加进度信息。engine 为 None（无效引擎名）时跳过。"""
        if engine is None:
            return job
        if job.status == "running" and hasattr(engine, 'get_progress'):
            try:
                progress_data = engine.get_progress(job.job_id)
            except Exception:
                progress_data = None
            if progress_data:
                job.progress = ProgressInfo(**progress_data)
        return job

    def _default_project_name(self, req: DeckRequest) -> str:
        """项目默认名：优先 title，其次第一个上传文件名（去扩展名），最后 Untitled。"""
        if req.title:
            return req.title
        for s in req.sources:
            if s.type == "file_path":
                stem = Path(s.value).stem
                if stem:
                    return stem[:100]
            elif s.type == "text" and s.value:
                first = s.value.strip().splitlines()[0].lstrip("# ").strip()
                if first:
                    return first[:50]
        return "Untitled"

    def _ensure_project(self, db: Session, project_id: str | None, default_name: str) -> str:
        """确保项目存在，如果不存在则自动创建。返回 project_id。"""
        if project_id:
            project = self.project_repo.get(db, project_id)
            if project:
                return project_id
            now = _now()
            self.project_repo.create(
                db,
                project_id=project_id,
                project_name=default_name,
                created_at=now,
                updated_at=now,
            )
            return project_id

        new_project_id = uuid.uuid4().hex
        now = _now()
        self.project_repo.create(
            db,
            project_id=new_project_id,
            project_name=default_name,
            created_at=now,
            updated_at=now,
        )
        return new_project_id

    # ── submit：提交任务（草稿引擎任务或输出引擎任务）──
    def submit(self, db: Session, req: DeckRequest, background_tasks: BackgroundTasks) -> JobOut:
        # 校验版本号格式（与契约 pattern 一致）
        if req.version and not _VERSION_RE.match(req.version):
            raise HTTPException(status_code=400, detail="version 必须是 14 位数字（年月日时分秒）")

        # 如果提供了 draft_id，从草稿版本中获取 content
        content = req.content
        used_version: str | None = None  # 本任务实际使用的草稿版本号（演示文件命名用）
        if req.draft_id and req.engine_type == "output":
            draft = self.draft_repo.get(db, req.draft_id)
            if draft is None:
                raise HTTPException(status_code=404, detail="草稿不存在")

            version = req.version or draft.get("current_version")
            if not version:
                raise HTTPException(status_code=400, detail="草稿没有版本，请指定 version")

            draft_version = self.draft_version_repo.get(db, req.draft_id, version)
            if draft_version is None:
                raise HTTPException(status_code=404, detail="草稿版本不存在")

            content = draft_version.get("content")
            used_version = version

        job_id = uuid.uuid4().hex
        sources = [s.model_dump() for s in req.sources] if req.sources else []
        engine_params = dict(req.engine_params or {})

        # 确保项目存在
        project_id = self._ensure_project(db, req.project_id, self._default_project_name(req))

        # 按请求选择引擎（引擎可插拔：选什么跑什么，不再静默降级到全局单例）
        try:
            engine = self._get_engine(req.engine)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # 先落库（running），再调引擎；引擎抛异常时回滚为 failed
        now = _now()
        row = self.repo.create(
            db,
            job_id=job_id,
            project_id=project_id,
            task_type=req.engine_type,
            status="running",
            status_detail="正在启动引擎",
            title=req.title,
            pages=req.pages,
            engine=req.engine,
            agent=req.agent,
            content_style=req.content_style,
            engine_params=engine_params,
            content=content,
            draft_id=req.draft_id if req.engine_type == "output" else None,
            draft_version=used_version,
            created_at=now,
            updated_at=now,
        )

        try:
            self.engine_submit(engine, content=content, title=req.title, pages=req.pages,
                               engine_params=engine_params, sources=sources,
                               job_id=job_id, engine_type=req.engine_type)
        except Exception as exc:
            self.repo.update(db, job_id, status="failed", error=f"引擎提交失败：{exc}",
                             updated_at=_now(), status_detail="引擎提交失败")
            row = self.repo.get(db, job_id)
            return JobOut(**row)

        # 所有任务类型都走后台轮询：终态由引擎真实状态决定（不再对 draft 假 completed）
        background_tasks.add_task(self._poll_until_done, job_id, req.engine)

        return JobOut(**row)

    @staticmethod
    def engine_submit(engine, **kwargs):
        """调引擎 submit（对旧签名引擎做兼容适配）。"""
        import inspect
        sig = inspect.signature(engine.submit)
        params = sig.parameters
        # 旧签名引擎（单数 source、无 engine_type）
        if "sources" not in params:
            source = kwargs.get("sources") or None
            kwargs["source"] = source[0] if source else None
            del kwargs["sources"]
        if "engine_type" not in params:
            kwargs.pop("engine_type", None)
        engine.submit(**kwargs)

    def list(self, db: Session, project_id: str | None = None, status: str | None = None,
             task_type: str | None = None, limit: int = 20, offset: int = 0) -> list[JobOut]:
        rows = self.repo.list(db, project_id=project_id, status=status, task_type=task_type, limit=limit, offset=offset)
        jobs = [JobOut(**row) for row in rows]
        # 为 running 状态的任务附加进度（引擎按各自任务的 engine 名选择；无效引擎名容错跳过）
        return [self._enrich_with_progress(job, self._get_engine_safe(job.engine)) for job in jobs]

    # ── get：查任务 ──
    def get(self, db: Session, job_id: str) -> JobOut:
        row = self.repo.get(db, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        job = JobOut(**row)
        return self._enrich_with_progress(job, self._get_engine_safe(job.engine))

    # ── retry：重试失败的任务（创建新任务）──
    def retry(self, db: Session, job_id: str, background_tasks: BackgroundTasks) -> JobOut:
        row = self.repo.get(db, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if row["status"] not in ("failed", "timeout"):
            raise HTTPException(status_code=409, detail="任务状态不允许重试（非 failed/timeout）")

        # 创建新任务，复用原任务的参数
        new_job_id = uuid.uuid4().hex
        now = _now()

        content = row.get("content") or ""

        new_row = self.repo.create(
            db,
            job_id=new_job_id,
            project_id=row.get("project_id"),
            task_type=row.get("task_type"),
            status="running",
            status_detail="正在重试",
            title=row.get("title"),
            pages=row.get("pages"),
            engine=row.get("engine"),
            agent=row.get("agent"),
            content_style=row.get("content_style"),
            engine_params=row.get("engine_params") or {},
            content=content,
            created_at=now,
            updated_at=now,
        )

        # 调用引擎提交新任务（参数存于 job.json / 引擎内部，无需重读 DB）
        try:
            engine = self._get_engine(row.get("engine"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        try:
            self.engine_submit(engine, content=content,
                               title=row.get("title"), pages=row.get("pages"),
                               engine_params=row.get("engine_params") or {},
                               job_id=new_job_id, engine_type=row.get("task_type"))
        except Exception as exc:
            self.repo.update(db, new_job_id, status="failed", error=f"引擎提交失败：{exc}",
                             updated_at=_now(), status_detail="引擎提交失败")
            new_row = self.repo.get(db, new_job_id)
            return JobOut(**new_row)

        # 所有任务类型都挂轮询（draft 任务重试也需要终态回写）
        background_tasks.add_task(self._poll_until_done, new_job_id, row.get("engine"))

        return JobOut(**new_row)

    # ── restore_draft_version：还原草稿到历史版本 ──
    def restore_draft_version(self, db: Session, draft_id: str, version: str) -> dict:
        draft = self.draft_repo.get(db, draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="草稿不存在")

        # 检查版本是否存在
        draft_version = self.draft_version_repo.get(db, draft_id, version)
        if draft_version is None:
            raise HTTPException(status_code=404, detail="草稿版本不存在")

        # 更新当前版本
        now = _now()
        self.draft_repo.update(db, draft_id, current_version=version, updated_at=now)

        # 同步项目的当前草稿版本（Project.current_draft_version 指向同一草稿时）
        if draft.get("project_id"):
            project = self.project_repo.get(db, draft["project_id"])
            if project and project.get("current_draft_id") == draft_id:
                self.project_repo.update(db, draft["project_id"],
                                         current_draft_version=version, updated_at=now)

        return self.draft_repo.get(db, draft_id)

    # ── 草稿实体落库：draft 任务 completed 后写 Draft + DraftVersion ──
    def _persist_draft_entity(self, job_row: dict) -> None:
        db = SessionLocal()
        try:
            draft_id = uuid.uuid4().hex
            version = _version_stamp()
            # 同秒冲突：等待下一秒
            while self.draft_version_repo.get(db, draft_id, version) is not None:
                time.sleep(1.0)
                version = _version_stamp()
            now = _now()
            # 草稿真实内容：读引擎写的 draft.md（DB 的 draft_content 列已废弃、不再写入）
            job_dir = JOBS_ROOT / job_row["job_id"]
            draft_path = job_dir / "draft.md"
            content = ""
            if draft_path.exists():
                try:
                    content = draft_path.read_text(encoding="utf-8")
                except OSError:
                    content = ""
            else:
                # 引擎未落盘时兜底用请求内容
                content = job_row.get("content") or ""

            self.draft_repo.create(
                db,
                draft_id=draft_id,
                project_id=job_row.get("project_id"),
                task_id=job_row["job_id"],
                current_version=version,
                created_at=now,
                updated_at=now,
            )
            self.draft_version_repo.create(
                db,
                draft_id=draft_id,
                version=version,
                content=content,
                draft_path=str(draft_path),
                created_at=now,
            )
            # 更新项目的当前草稿
            self.project_repo.update(db, job_row.get("project_id"),
                                     current_draft_id=draft_id,
                                     current_draft_version=version,
                                     updated_at=now)
        finally:
            db.close()

    # ── 演示文件落库：输出任务 completed 后写 Presentation ──
    def _persist_presentation(self, job_row: dict, result_path: str) -> None:
        db = SessionLocal()
        try:
            presentation_id = uuid.uuid4().hex
            now = _now()
            project = self.project_repo.get(db, job_row.get("project_id"))
            project_name = ((project or {}).get("project_name") or "Untitled").replace("/", "_")
            # 按引擎真实产物后缀判定格式（契约 Presentation.file_format: pptx | html）
            # guizang_ppt 引擎产出 result.html，不能一律伪装成 pptx
            suffix = Path(result_path).suffix.lower()
            file_format = "html" if suffix in (".html", ".htm") else "pptx"
            # 命名规则（docs/contracts/style-guide.md）：项目名称_所用草稿版本号
            # 无草稿版本（直接给 content 生成）时退回生成时刻时间戳
            file_name = f"{project_name}_{job_row.get('draft_version') or _version_stamp()}.{file_format}"
            self.presentation_repo.create(
                db,
                presentation_id=presentation_id,
                project_id=job_row.get("project_id"),
                task_id=job_row["job_id"],
                file_name=file_name,
                file_format=file_format,
                file_path=result_path,
                created_at=now,
            )
        finally:
            db.close()

    # ── 引擎上报的标题（引擎 job.json 里的 title 字段；引擎协议未定义 getter，直接读文件） ──
    @staticmethod
    def _engine_reported_title(engine, job_id: str) -> str | None:
        try:
            from engine.core.config import get_jobs_root
            job_json = get_jobs_root() / job_id / "job.json"
            if job_json.exists():
                data = json.loads(job_json.read_text(encoding="utf-8"))
                t = data.get("title")
                return t if isinstance(t, str) and t.strip() and t != "Untitled" else None
        except Exception:
            return None
        return None

    # ── 后台轮询：把引擎状态同步回 SQLite 任务表 ──
    def _poll_until_done(self, job_id: str, engine_name: str | None = None,
                         timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS) -> None:
        from engine.core.models import JobStatus  # 延迟导入，确保 engine 包已在 sys.path

        engine = self._get_engine(engine_name)
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            status = engine.status(job_id)
            if status == JobStatus.COMPLETED:
                try:
                    result_path = engine.result(job_id)
                except Exception:
                    result_path = None
                db = SessionLocal()
                try:
                    row = JobRepo().get(db, job_id)
                    # 引擎提取的标题回填（用户未传 title 时，引擎从素材拟了标题）
                    engine_title = self._engine_reported_title(engine, job_id)
                    update_fields: dict = {
                        "result_path": result_path,
                        "updated_at": _now(),
                        "status_detail": "生成完成",
                    }
                    if engine_title and not (row or {}).get("title"):
                        update_fields["title"] = engine_title
                    JobRepo().update(db, job_id, status="completed", **update_fields)
                    row = JobRepo().get(db, job_id)  # 取回填后的行给落库逻辑用
                    # 落库演示文件 / 草稿实体（幂等：仅在尚未落库时）
                    if row:
                        if row.get("task_type") == "output" and result_path:
                            self._persist_presentation(row, result_path)
                        elif row.get("task_type") == "draft":
                            self._persist_draft_entity(row)
                finally:
                    db.close()
                return
            if status in (JobStatus.FAILED, JobStatus.TIMEOUT):
                db = SessionLocal()
                try:
                    # 注意用 .value（str(status) 会得到 "JobStatus.FAILED" 而非 "failed"）
                    JobRepo().update(db, job_id, status=status.value,
                                     error="生成失败，详见引擎 run.log", updated_at=_now(),
                                     status_detail="生成失败")
                finally:
                    db.close()
                return
            # 刷新 status_detail（给前端实时反馈）
            db = SessionLocal()
            try:
                JobRepo().update(db, job_id, status_detail=f"引擎执行中（{status.value}）", updated_at=_now())
            finally:
                db.close()
            time.sleep(_DEFAULT_POLL_INTERVAL)
        # 超时兜底
        db = SessionLocal()
        try:
            JobRepo().update(db, job_id, status="timeout", error="生成超时", updated_at=_now(),
                             status_detail="生成超时")
        finally:
            db.close()
