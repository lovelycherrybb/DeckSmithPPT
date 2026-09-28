"""API 路由层：把 HTTP 请求转交给 service，自身不写业务逻辑。

与 openapi.yaml 保持一致：支持项目管理、草稿版本管理、演示文件管理、安全检查。
"""
import re
from datetime import datetime, timezone
from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from ..core.db import SessionLocal, JOBS_ROOT
from ..core.config import get_app_version
from ..core.auth import Identity, get_identity, require_admin
from ..core import ownership
from ..repository.jobs import ProjectRepo, JobRepo, DraftRepo, DraftVersionRepo, PresentationRepo
from ..repository.users import UserRepo
from ..service.jobs import JobService, sanitize_filename
from ..engines.factory import get_engine
from ..engines.registry import list_engines
from ..core.schemas import (
    DeckRequest, JobOut, EngineInfo, UploadResponse,
    ProjectOut, ProjectDetailOut, DraftOut, DraftDetailOut, DraftVersionOut, ModifyDraftRequest, SaveDraftVersionRequest,
    PresentationOut, SecurityCheckRequest, SecurityCheckResult,
    SuggestTitleRequest, SuggestTitleResult, JobPages, VersionOut, ClaimProjectRequest,
)

router = APIRouter()
repo = JobRepo()
project_repo = ProjectRepo()
draft_repo = DraftRepo()
draft_version_repo = DraftVersionRepo()
presentation_repo = PresentationRepo()
# 引擎工厂注入 service：按请求的 engine 名创建实例（引擎可插拔）
service = JobService(repo, get_engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ===== 归属守卫（v0.8）：解析资源所属项目 → can_access → 否则 404（不泄露存在性） =====

def _guard_project(db, project_id, identity):
    project = project_repo.get(db, project_id) if project_id else None
    if project is None or not ownership.can_access(project, identity):
        raise HTTPException(status_code=404, detail="资源不存在")
    return project


def _guard_job(db, job_id, identity):
    row = repo.get(db, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    _guard_project(db, row.get("project_id"), identity)
    return row


def _guard_draft(db, draft_id, identity):
    draft = draft_repo.get(db, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="草稿不存在")
    _guard_project(db, draft.get("project_id"), identity)
    return draft


def _guard_presentation(db, presentation_id, identity):
    pres = presentation_repo.get(db, presentation_id)
    if pres is None:
        raise HTTPException(status_code=404, detail="演示文件不存在")
    _guard_project(db, pres.get("project_id"), identity)
    return pres


# ===== 版本 =====


@router.get("/api/version", response_model=VersionOut)
def get_version():
    """返回当前安装包版本（发版时由 compose 用 IMAGE_TAG 注入 DECKSMITH_VERSION）。

    开发态未注入该 env 时 version 为 null，前端据此不显示版本——版本是已发布产物的属性。
    """
    return VersionOut(version=get_app_version())


# ===== 引擎相关 =====

@router.get("/api/engines", response_model=list[EngineInfo])
def api_list_engines(type: str | None = None):
    """获取引擎列表及参数说明书。支持按引擎类型筛选（草稿引擎/输出引擎）。"""
    return list_engines(engine_type=type)


# ===== 文件上传 =====

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB


@router.post("/api/upload", response_model=UploadResponse)
async def api_upload(file: UploadFile = File(...)):
    """上传源文件（docx/pdf 等）。文件名经消毒，防路径穿越。"""
    upload_id = uuid.uuid4().hex
    dest_dir = JOBS_ROOT / "uploads" / upload_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        import shutil
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise HTTPException(status_code=413, detail=f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限")
    safe_name = sanitize_filename(file.filename)
    dest = dest_dir / safe_name
    # 双保险：确认落点仍在 uploads 目录内
    if not dest.resolve().is_relative_to(dest_dir.resolve()):
        raise HTTPException(status_code=400, detail="非法文件名")
    dest.write_bytes(data)
    return UploadResponse(upload_id=upload_id, filename=safe_name, path=str(dest), size=len(data))


@router.post("/api/engines/suggest-title", response_model=SuggestTitleResult)
def api_suggest_title(body: SuggestTitleRequest):
    """为演示文件拟标题。

    规则：
    - 单个文件：直接用文件名（去扩展名），不调 Agent
    - 多个文件：Agent 通读各文件内容后拟一个概括性标题（与 modify 同模式）
    """
    if not body.files:
        raise HTTPException(status_code=400, detail="文件列表不能为空")

    # 单文件：文件名即标题（去扩展名），秒回
    if len(body.files) == 1:
        stem = Path(body.files[0].filename).stem.strip()
        if not stem:
            raise HTTPException(status_code=400, detail="文件名无效")
        return SuggestTitleResult(title=stem[:60])

    # 多文件：读内容交给 Agent
    from ..core.security import check_draft_content
    filenames: list[str] = []
    contents: list[str] = []
    for f in body.files:
        filenames.append(f.filename)
        content = ""
        if f.path:
            p = Path(f.path)
            # 路径必须位于 uploads 目录内（防任意读）
            if not p.resolve().is_relative_to((JOBS_ROOT / "uploads").resolve()):
                raise HTTPException(status_code=400, detail=f"非法文件路径：{f.filename}")
            if p.exists():
                try:
                    raw = p.read_bytes()[:20000]
                    # 去掉二进制内容（docx/pdf 等）：剥控制字符和 null byte
                    # null byte 会让 subprocess 参数直接报错
                    content = raw.decode(encoding="utf-8", errors="ignore")
                    content = "".join(ch for ch in content if ch >= " " or ch in "\n\r\t")
                    content = content.strip()[:5000]
                except OSError:
                    content = ""
        contents.append(content)

    # 安全门禁：文件内容过检查，通过才交给 Agent
    for name, content in zip(filenames, contents):
        passed, reason = check_draft_content(content)
        if not passed:
            raise HTTPException(status_code=400, detail=f"文件「{name}」安全检查不通过：{reason}")

    from ..agents.title_suggester import suggest_title_with_claude
    try:
        title = suggest_title_with_claude(filenames, contents)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Agent 拟题失败：{exc}")

    return SuggestTitleResult(title=title)


# ===== 项目相关 =====

@router.get("/api/projects", response_model=list[ProjectOut])
def api_list_projects(limit: int = 20, offset: int = 0, db: Session = Depends(get_db),
                      identity: Identity = Depends(get_identity)):
    """获取项目列表（含统计）。按归属过滤：普通/高级只见自己，管理员见全部并带 owner_label。"""
    projects = project_repo.list(db, limit=limit, offset=offset, **ownership.list_filter_kwargs(identity))
    ownership.attach_owner_label(db, projects)
    stats = project_repo.stats(db, [p["project_id"] for p in projects])
    for p in projects:
        p.update(stats.get(p["project_id"], {}))
    return projects


@router.get("/api/projects/{project_id}", response_model=ProjectDetailOut)
def api_get_project(project_id: str, db: Session = Depends(get_db),
                    identity: Identity = Depends(get_identity)):
    """获取项目详情，包含关联的任务、草稿、演示文件列表。非归属人（非管理员）→ 404。"""
    project = project_repo.get(db, project_id)
    if project is None or not ownership.can_access(project, identity):
        raise HTTPException(status_code=404, detail="项目不存在")
    ownership.attach_owner_label(db, [project])

    # 获取关联的任务、草稿、演示文件
    tasks = repo.list(db, project_id=project_id)
    drafts = draft_repo.list_by_project(db, project_id)
    presentations = presentation_repo.list_by_project(db, project_id)

    return ProjectDetailOut(
        **project,
        tasks=[JobOut(**t) for t in tasks],
        drafts=[DraftOut(**d) for d in drafts],
        presentations=[PresentationOut(**p) for p in presentations],
    )


@router.delete("/api/projects/{project_id}", status_code=200)
def api_delete_project(project_id: str, db: Session = Depends(get_db),
                       admin=Depends(require_admin)):
    """删除项目（仅管理员，本期限未认领的访客项目）。已归属真实用户 → 409。"""
    result = project_repo.delete(db, project_id)
    if result == "not_found":
        raise HTTPException(status_code=404, detail="项目不存在")
    if result == "owned":
        raise HTTPException(status_code=409, detail="该项目已归属真实用户，本期不允许删除")
    return {"ok": True}


@router.post("/api/projects/{project_id}/claim", response_model=ProjectOut)
def api_claim_project(project_id: str, body: ClaimProjectRequest, db: Session = Depends(get_db),
                      admin=Depends(require_admin)):
    """代为认领（仅管理员）：把项目归属到指定真实用户。项目或目标用户不存在 → 404。"""
    if UserRepo().get(db, body.target_user_id) is None:
        raise HTTPException(status_code=404, detail="目标用户不存在")
    updated = project_repo.set_owner(db, project_id, body.target_user_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    ownership.attach_owner_label(db, [updated])
    return ProjectOut(**updated)


# ===== 任务相关 =====

@router.post("/api/decks", response_model=JobOut, status_code=202)
def api_submit(req: DeckRequest, db: Session = Depends(get_db),
               identity: Identity = Depends(get_identity)):
    """提交生成任务（草稿引擎任务或输出引擎任务）。支持项目自动创建。

    新建项目按当前身份打归属戳（登录→owner / 试用→visitor）；传入他人 project_id → 404。
    受理即排队（status=queued），由调度循环认领；未完成任务数超上限 → 409。
    """
    return service.submit(db, req, identity)


@router.get("/api/jobs", response_model=list[JobOut])
def api_list_jobs(project_id: str | None = None, status: str | None = None, 
                  task_type: str | None = None, limit: int = 20, offset: int = 0, 
                  db: Session = Depends(get_db), identity: Identity = Depends(get_identity)):
    """获取任务历史列表。支持按项目ID、状态、任务类型筛选。按归属过滤（管理员见全部）。"""
    if project_id:
        _guard_project(db, project_id, identity)  # 指定项目：先验归属
        visible = None
    else:
        visible = ownership.visible_project_ids(db, identity)  # 跨项目：限定可见项目
    return service.list(db, project_id=project_id, status=status, task_type=task_type, 
                       limit=limit, offset=offset, project_ids=visible)


@router.get("/api/jobs/{job_id}", response_model=JobOut)
def api_get(job_id: str, db: Session = Depends(get_db), identity: Identity = Depends(get_identity)):
    """查询任务状态，包含当前状态和状态明细。非归属任务 → 404。"""
    _guard_job(db, job_id, identity)
    return service.get(db, job_id)


@router.post("/api/jobs/{job_id}/retry", response_model=JobOut, status_code=202)
def api_retry_job(job_id: str, db: Session = Depends(get_db),
                  identity: Identity = Depends(get_identity)):
    """重试未成功结束的任务（新建一条从 queued 开始的任务）。非归属任务 → 404。"""
    _guard_job(db, job_id, identity)
    return service.retry(db, job_id, identity)


@router.post("/api/jobs/{job_id}/cancel", response_model=JobOut, status_code=202)
def api_cancel_job(job_id: str, db: Session = Depends(get_db),
                   identity: Identity = Depends(get_identity)):
    """撤销未到终态的任务。非归属任务 → 404；已终态或引擎不支持中断运行中任务 → 409。

    语义与状态机见契约 `/api/jobs/{job_id}/cancel` 与 ADR-019 决策 5：queued 直接摘队，
    running 只有在引擎实现了 cancel 钩子（能力位含 cancel）时才会真的杀进程。
    """
    _guard_job(db, job_id, identity)
    return service.cancel(db, job_id)


@router.get("/api/presentations/{presentation_id}/download")
def api_download_presentation(presentation_id: str, db: Session = Depends(get_db),
                              identity: Identity = Depends(get_identity)):
    """按演示文件标识下载文件。非归属 → 404。"""
    presentation = _guard_presentation(db, presentation_id, identity)
    file_path = presentation.get("file_path")
    if not file_path or not Path(file_path).exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    file_format = presentation.get("file_format", "pptx")
    media_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation" if file_format == "pptx" else "text/html"
    return FileResponse(
        file_path,
        filename=presentation.get("file_name", f"{presentation_id}.{file_format}"),
        media_type=media_type,
    )


@router.get("/api/jobs/{job_id}/download")
def api_download(job_id: str, db: Session = Depends(get_db), identity: Identity = Depends(get_identity)):
    """下载生成的 PPTX。非归属任务 → 404。"""
    _guard_job(db, job_id, identity)
    row = repo.get(db, job_id)
    if row is None or not row.get("result_path"):
        raise HTTPException(status_code=404, detail="任务不存在或未生成产物")
    if not Path(row["result_path"]).exists():
        raise HTTPException(status_code=404, detail="产物文件不存在或已被清理")
    result_path = row["result_path"]
    # 按引擎真实产物后缀决定下载文件名与 MIME，避免把 HTML 伪装成 pptx
    suffix = Path(result_path).suffix.lower()
    if suffix in (".html", ".htm"):
        filename = f"{job_id}.html"
        media_type = "text/html"
    else:
        filename = f"{job_id}.pptx"
        media_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    return FileResponse(
        result_path,
        filename=filename,
        media_type=media_type,
    )


@router.get("/api/jobs/{job_id}/pages", response_model=JobPages)
def api_job_pages(job_id: str, db: Session = Depends(get_db), identity: Identity = Depends(get_identity)):
    """获取任务逐页预览快照（引擎无关，能力由钩子实现派生）。非归属 → 404。

    只列已完成页，不含预测总数与 pending 占位；演示文件按其 task_id 复用本端点。
    """
    _guard_job(db, job_id, identity)
    return service.list_pages(db, job_id)


@router.get("/api/jobs/{job_id}/preview/{path:path}")
def api_job_preview(job_id: str, path: str, db: Session = Depends(get_db),
                    identity: Identity = Depends(get_identity)):
    """内联返回任务产物根目录内的页/资源文件（svg、页内 ../images 相对引用等）。非归属 → 404。

    安全校验（越界 400 / 类型 415 / 不存在 404）在 service.resolve_preview_file；
    不给 filename——预览是内联展示，不触发下载。
    """
    _guard_job(db, job_id, identity)
    target, media_type = service.resolve_preview_file(db, job_id, path)
    if media_type == "text/html":
        # 产物 HTML 由 agent 生成，同源内联返回就是存储型 XSS 面。用响应头强制
        # CSP sandbox（opaque origin）+ nosniff，即使前端 iframe 忘了写 sandbox
        # 属性也仍然隔离；引导脚本（存储垫片 + 页定位）由 service.build_html_preview 注入。
        return Response(
            service.build_html_preview(target),
            media_type="text/html",
            headers={
                "Content-Security-Policy": "sandbox allow-scripts",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )
    return FileResponse(target, media_type=media_type)


# ===== 草稿相关 =====

@router.get("/api/projects/{project_id}/drafts", response_model=list[DraftOut])
def api_list_project_drafts(project_id: str, db: Session = Depends(get_db),
                            identity: Identity = Depends(get_identity)):
    """获取项目下的草稿列表。草稿是独立实体，有唯一标识和版本管理。非归属项目 → 404。"""
    _guard_project(db, project_id, identity)
    return draft_repo.list_by_project(db, project_id)


@router.get("/api/drafts/{draft_id}", response_model=DraftDetailOut)
def api_get_draft(draft_id: str, db: Session = Depends(get_db), identity: Identity = Depends(get_identity)):
    """获取草稿详情，包含版本历史列表。非归属草稿 → 404。"""
    draft = _guard_draft(db, draft_id, identity)

    # 获取版本历史
    versions = draft_version_repo.list_by_draft(db, draft_id)

    # 获取当前版本内容
    content = None
    if draft.get("current_version"):
        current_version = draft_version_repo.get(db, draft_id, draft["current_version"])
        if current_version:
            content = current_version.get("content")

    return DraftDetailOut(
        **draft,
        content=content,
        versions=[DraftVersionOut(**v) for v in versions],
    )


@router.get("/api/drafts/{draft_id}/versions/{version}", response_model=DraftVersionOut)
def api_get_draft_version(draft_id: str, version: str, db: Session = Depends(get_db),
                          identity: Identity = Depends(get_identity)):
    """获取草稿指定版本。非归属草稿 → 404。"""
    _guard_draft(db, draft_id, identity)
    draft_version = draft_version_repo.get(db, draft_id, version)
    if draft_version is None:
        raise HTTPException(status_code=404, detail="草稿版本不存在")
    return DraftVersionOut(**draft_version)


@router.post("/api/drafts/{draft_id}/modify", response_model=DraftVersionOut)
def api_modify_draft(draft_id: str, body: ModifyDraftRequest, db: Session = Depends(get_db),
                     identity: Identity = Depends(get_identity)):
    """让 Agent 修改草稿。用户发送修改提示词，Agent 理解意图并返回修改后的内容。

    安全约束（契约）：调用 Agent 前先做草稿安全检查（POST /api/drafts/check 的同套规则）。
    修改结果仅返回给前端渲染，不落库——用户确认后再调 POST /versions 保存。非归属草稿 → 404。
    """
    _guard_draft(db, draft_id, identity)

    # 获取当前版本内容
    current_version = draft_version_repo.get(db, draft_id, body.current_version)
    if current_version is None:
        raise HTTPException(status_code=404, detail="当前版本不存在")

    content = current_version.get("content") or ""

    # 安全检查：草稿内容 + 用户提示词都过一遍（提示词注入防护）
    from ..core.security import check_draft_content
    for text, label in ((content, "草稿内容"), (body.prompt, "修改指令")):
        passed, reason = check_draft_content(text)
        if not passed:
            raise HTTPException(status_code=400, detail=f"{label}安全检查不通过：{reason}")

    # 调 Claude Code 修改（同步，纯文本改写，不跳过权限模式）
    from ..agents.draft_modifier import modify_draft_with_claude
    try:
        modified, _ = modify_draft_with_claude(content, body.prompt)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Agent 修改失败：{exc}")

    # 不保存，仅返回修改后的内容（用户确认后另调保存接口）
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    return DraftVersionOut(
        draft_id=draft_id,
        version=body.current_version,  # 仍是当前版本号；保存时才生成新版本
        content=modified,
        draft_path=current_version.get("draft_path"),
        created_at=now,
    )


@router.post("/api/drafts/{draft_id}/restore", response_model=DraftOut)
def api_restore_draft_version(draft_id: str, body: dict, db: Session = Depends(get_db),
                              identity: Identity = Depends(get_identity)):
    """还原草稿到历史版本。非归属草稿 → 404。"""
    _guard_draft(db, draft_id, identity)
    version = body.get("version")
    if not version:
        raise HTTPException(status_code=400, detail="缺少 version 参数")
    if not re.match(r"^[0-9]{14}$", str(version)):
        raise HTTPException(status_code=400, detail="version 必须是 14 位数字（年月日时分秒）")
    return service.restore_draft_version(db, draft_id, str(version))


@router.post("/api/drafts/{draft_id}/versions", response_model=DraftVersionOut, status_code=201)
def api_save_draft_version(draft_id: str, body: SaveDraftVersionRequest, db: Session = Depends(get_db),
                           identity: Identity = Depends(get_identity)):
    """保存草稿新版本。用户编辑草稿后调用，保存为新版本。非归属草稿 → 404。"""
    _guard_draft(db, draft_id, identity)

    # 生成新版本号（UTC 年月日时分秒）
    version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    # 检查版本冲突
    existing = draft_version_repo.get(db, draft_id, version)
    if existing is not None:
        raise HTTPException(status_code=409, detail="版本冲突（同一秒内已保存过）")

    # 创建新版本（含契约必填的 draft_path）
    now = datetime.now(timezone.utc).isoformat()
    job_dir = JOBS_ROOT / "drafts" / draft_id
    job_dir.mkdir(parents=True, exist_ok=True)
    draft_path = job_dir / f"{version}.md"
    draft_path.write_text(body.content, encoding="utf-8")
    draft_version_repo.create(
        db,
        draft_id=draft_id,
        version=version,
        content=body.content,
        draft_path=str(draft_path),
        created_at=now,
    )

    # 更新草稿的当前版本
    draft_repo.update(db, draft_id, current_version=version, updated_at=now)

    return DraftVersionOut(draft_id=draft_id, version=version, content=body.content,
                           draft_path=str(draft_path), created_at=now)



# ===== 演示文件相关 =====

@router.get("/api/projects/{project_id}/presentations", response_model=list[PresentationOut])
def api_list_project_presentations(project_id: str, db: Session = Depends(get_db),
                                   identity: Identity = Depends(get_identity)):
    """获取项目下的演示文件列表。演示文件由输出引擎任务生成。非归属项目 → 404。"""
    _guard_project(db, project_id, identity)
    return presentation_repo.list_by_project(db, project_id)


# ===== 安全检查 =====

@router.post("/api/drafts/check", response_model=SecurityCheckResult)
def api_check_draft_security(body: SecurityCheckRequest):
    """草稿安全检查。在提交给 Agent 之前进行安全检查（提示词注入等）。"""
    from ..core.security import check_draft_content, list_rules
    passed, reason = check_draft_content(body.content or "")
    return SecurityCheckResult(
        passed=passed,
        reason=reason,
        rules_checked=list_rules(),
    )
