"""API 路由层：把 HTTP 请求转交给 service，自身不写业务逻辑。"""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..core.db import SessionLocal, JOBS_ROOT
from ..repository.jobs import JobRepo
from ..service.jobs import JobService
from ..engines.factory import get_engine
from ..engines.registry import list_engines
from ..core.schemas import DeckRequest, ConfirmRequest, SaveDraftRequest, JobOut, EngineInfo, UploadResponse

router = APIRouter()
repo = JobRepo()
engine = get_engine()
service = JobService(repo, engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/api/engines", response_model=list[EngineInfo])
def api_list_engines():
    return list_engines()


@router.post("/api/decks", response_model=JobOut, status_code=202)
def api_submit(req: DeckRequest, db: Session = Depends(get_db)):
    return service.submit(db, req)


@router.get("/api/jobs", response_model=list[JobOut])
def api_list_jobs(status: str | None = None, limit: int = 20, offset: int = 0, db: Session = Depends(get_db)):
    return service.list(db, status=status, limit=limit, offset=offset)


@router.get("/api/jobs/{job_id}", response_model=JobOut)
def api_get(job_id: str, db: Session = Depends(get_db)):
    return service.get(db, job_id)


@router.post("/api/jobs/{job_id}/confirm", response_model=JobOut, status_code=202)
def api_confirm(job_id: str, body: ConfirmRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    return service.confirm(db, job_id, body.content, body.engine_params, background_tasks)

@router.patch("/api/jobs/{job_id}/draft", response_model=JobOut)
def api_save_draft(job_id: str, body: SaveDraftRequest, db: Session = Depends(get_db)):
    return service.save_draft(db, job_id, body.content)


@router.post("/api/upload", response_model=UploadResponse)
async def api_upload(file: UploadFile = File(...)):
    upload_id = uuid.uuid4().hex
    dest_dir = JOBS_ROOT / "uploads" / upload_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    dest = dest_dir / file.filename
    dest.write_bytes(data)
    return UploadResponse(upload_id=upload_id, filename=file.filename, path=str(dest), size=len(data))


@router.get("/api/jobs/{job_id}/download")
def api_download(job_id: str, db: Session = Depends(get_db)):
    row = repo.get(db, job_id)
    if row is None or not row.get("result_path"):
        raise HTTPException(status_code=404, detail="任务不存在或未生成产物")
    return FileResponse(
        row["result_path"],
        filename=f"{job_id}.pptx",
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
