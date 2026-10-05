"""Agent 文本处理路由：所有「丢提示词给 AI、拿文本回来」能力的统一入口。

kind 白名单（与契约 AgentExecuteRequest.kind 枚举一致）：
- modify_draft：草稿版本内容 + 修改指令 → 改写后的 Markdown
- suggest_title：文件列表 → 标题（单文件直接文件名，多文件 Agent 概括）

统一流程：参数校验 → 组装上下文（草稿内容/文件内容）→ 安全门禁 → Agent 调用 → 文本返回。
新增 kind：在 _KINDS 注册 + 写模板函数 + 契约同步枚举。
"""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.db import SessionLocal, JOBS_ROOT
from ..core.schemas import (
    AgentExecuteRequest, AgentExecuteResult,
    AgentSessionResponse, AgentSessionStatus, AgentSessionStreamEvent,
)
from ..repository.jobs import DraftRepo, DraftVersionRepo

router = APIRouter()

draft_repo = DraftRepo()
draft_version_repo = DraftVersionRepo()


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── 上下文读取 ──

def _read_uploaded_text(path: str) -> str:
    """读上传文件内容并剥控制字符（docx/pdf 等二进制的 null byte 会让 subprocess 报错）。"""
    p = Path(path)
    if not p.resolve().is_relative_to((JOBS_ROOT / "uploads").resolve()):
        raise HTTPException(status_code=400, detail=f"非法文件路径：{p.name}")
    if not p.exists():
        return ""
    try:
        raw = p.read_bytes()[:20000]
        text = raw.decode(encoding="utf-8", errors="ignore")
        return "".join(ch for ch in text if ch >= " " or ch in "\n\r\t").strip()[:5000]
    except OSError:
        return ""


# ── kind 实现：返回 (给 Agent 的上下文描述, prompt 模板渲染函数, 需要安全检查的文本列表) ──

def _handle_modify_draft(req: AgentExecuteRequest, db: Session) -> tuple[str, str, list[tuple[str, str]]]:
    """草稿修改：返回 (修改指令, 草稿内容, 安全检查对)"""
    if not req.draft_id:
        raise HTTPException(status_code=400, detail="modify_draft 需要 draft_id")
    if not (req.prompt or "").strip():
        raise HTTPException(status_code=400, detail="modify_draft 需要 prompt")

    draft = draft_repo.get(db, req.draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="草稿不存在")
    version = req.version or draft.get("current_version")
    draft_version = draft_version_repo.get(db, req.draft_id, version or "")
    if draft_version is None:
        raise HTTPException(status_code=404, detail="草稿版本不存在")

    content = draft_version.get("content") or ""
    # 安全检查对：(文本, 标签)
    checks = [(content, "草稿内容"), ((req.prompt or ""), "修改指令")]
    return req.prompt, content, checks


def _handle_suggest_title(req: AgentExecuteRequest, db: Session) -> tuple[str, str, list[tuple[str, str]]]:
    """拟题：多文件返回 (指令, 组装上下文, 检查对)；单文件在路由层直接短路。"""
    files = req.files or []
    if not files:
        raise HTTPException(status_code=400, detail="suggest_title 需要 files")

    contents = []
    checks: list[tuple[str, str]] = []
    for f in files:
        content = _read_uploaded_text(f.path) if f.path else ""
        contents.append(content)
        if content:
            checks.append((content, f"文件「{f.filename}」"))

    context = "\n\n".join(
        f"【文件：{f.filename}】\n{c}" for f, c in zip(files, contents)
    )
    return "为这份演示文件拟一个标题", context, checks


def _handle_revise_pages(req: AgentExecuteRequest, db: Session) -> tuple[str, str, list[tuple[str, str]]]:
    """对话式改稿：给定若干页 SVG + 指令，返回改好的页（只回传、不落库/不出版本）。

    context 存 pages 的 JSON（异步分支据此重建 prompt）；安全检查覆盖指令与每页内容。
    """
    import json as _json
    pages = [p.model_dump() for p in req.pages]
    instruction = (req.instruction or "").strip()
    if not pages:
        raise HTTPException(status_code=400, detail="revise_pages 需要 pages")
    if not instruction:
        raise HTTPException(status_code=400, detail="revise_pages 需要 instruction")
    checks: list[tuple[str, str]] = [(instruction, "改稿指令")] + [
        (p.get("content") or "", f"第 {p.get('index')} 页内容") for p in pages
    ]
    return instruction, _json.dumps(pages, ensure_ascii=False), checks


_KINDS = {
    "modify_draft": _handle_modify_draft,
    "suggest_title": _handle_suggest_title,
    "revise_pages": _handle_revise_pages,
}


@router.post("/api/agent/execute", response_model=AgentExecuteResult)
def api_agent_execute(req: AgentExecuteRequest, db: Session = Depends(_get_db)):
    """Agent 文本处理统一入口。"""
    handler = _KINDS.get(req.kind)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"未知任务类型：{req.kind}（已注册：{', '.join(_KINDS)}）")

    # suggest_title 单文件短路：文件名即标题，不调 Agent（与 vc_design 规则一致）
    if req.kind == "suggest_title" and req.files and len(req.files) == 1:
        stem = Path(req.files[0].filename).stem.strip()
        if not stem:
            raise HTTPException(status_code=400, detail="文件名无效")
        return AgentExecuteResult(text=stem[:60], kind=req.kind)

    instruction, context, checks = handler(req, db)

    # 安全门禁：所有输入文本过检查，通过才交给 Agent
    from ..core.security import check_draft_content
    for text, label in checks:
        passed, reason = check_draft_content(text)
        if not passed:
            raise HTTPException(status_code=400, detail=f"{label}安全检查不通过：{reason}")

    # Agent 调用：按 kind 选模板
    import json as _json
    from ..agents.draft_modifier import modify_draft_with_claude
    from ..agents.title_suggester import suggest_title_with_claude
    from ..agents.page_revise import revise_pages_with_claude
    try:
        if req.kind == "modify_draft":
            text, _ = modify_draft_with_claude(context, instruction)
        elif req.kind == "suggest_title":
            filenames = [f.filename for f in (req.files or [])]
            contents = [_read_uploaded_text(f.path) if f.path else "" for f in (req.files or [])]
            text = suggest_title_with_claude(filenames, contents)
        elif req.kind == "revise_pages":
            text = revise_pages_with_claude(_json.loads(context), instruction)
        else:
            raise HTTPException(status_code=400, detail=f"任务类型未实现：{req.kind}")
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Agent 调用失败：{exc}")

    return AgentExecuteResult(text=text, kind=req.kind)



# ── 异步 Agent 接口 ──

@router.post("/api/agent/execute-async", response_model=AgentSessionResponse)
async def api_agent_execute_async(req: AgentExecuteRequest, db: Session = Depends(_get_db)):
    """Agent 文本处理异步接口：立即返回会话ID，后台线程执行。"""
    from ..agents.session_manager import session_manager

    handler = _KINDS.get(req.kind)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"未知任务类型：{req.kind}（已注册：{', '.join(_KINDS)}）")
    
    # suggest_title 单文件短路
    if req.kind == "suggest_title" and req.files and len(req.files) == 1:
        stem = Path(req.files[0].filename).stem.strip()
        if not stem:
            raise HTTPException(status_code=400, detail="文件名无效")
        # 直接返回结果，不创建异步会话
        session = session_manager.create_session("claude", req.kind)
        session_manager.set_result(session.session_id, stem[:60])
        session_manager.update_session_status(session.session_id, "completed")
        return AgentSessionResponse(session_id=session.session_id, agent_type="claude")
    
    instruction, context, checks = handler(req, db)
    
    # 安全门禁
    from ..core.security import check_draft_content
    for text, label in checks:
        passed, reason = check_draft_content(text)
        if not passed:
            raise HTTPException(status_code=400, detail=f"{label}安全检查不通过：{reason}")
    
    # 创建会话
    session = session_manager.create_session("claude", req.kind)

    # 后台线程执行（不用 asyncio.create_task：uvicorn 长跑后事件循环停摆会导致
    # create_task 静默不执行，会话永远 running、SSE 一个事件都发不出）
    def run_agent_task():
        try:
            from ..agents.claude_stream import stream_claude_text
            from ..agents.draft_modifier import _build_prompt as modify_prompt, clean_markdown
            from ..agents.title_suggester import _build_prompt as title_prompt, clean_title
            from ..agents.page_revise import _build_prompt as revise_prompt, clean_pages

            if req.kind == "modify_draft":
                prompt = modify_prompt(context, instruction)
            elif req.kind == "suggest_title":
                filenames = [f.filename for f in (req.files or [])]
                contents = [_read_uploaded_text(f.path) if f.path else "" for f in (req.files or [])]
                prompt = title_prompt(filenames, contents)
            elif req.kind == "revise_pages":
                import json as _json
                prompt = revise_prompt(_json.loads(context), instruction)
            else:
                raise RuntimeError(f"任务类型未实现：{req.kind}")

            acc: list[str] = []
            payload: str | None = None
            for ev in stream_claude_text(prompt):
                if "delta" in ev:
                    acc.append(ev["delta"])
                    session_manager.append_output(session.session_id, ev["delta"], "text")
                elif "thinking" in ev:
                    # 思考增量：实时透传，让用户看到 AI 的推理过程
                    session_manager.append_output(session.session_id, ev["thinking"], "thinking")
                elif "error" in ev:
                    session_manager.update_session_status(session.session_id, "failed", ev["error"])
                    return
                elif ev.get("done"):
                    payload = ev.get("text") or "".join(acc)
                    break
            if payload is None:
                payload = "".join(acc)

            if req.kind == "suggest_title":
                cleaned = clean_title(payload)
            elif req.kind == "revise_pages":
                cleaned = clean_pages(payload)
            else:
                cleaned = clean_markdown(payload)
            session_manager.set_result(session.session_id, cleaned)
            session_manager.update_session_status(session.session_id, "completed")
        except Exception as e:
            session_manager.update_session_status(session.session_id, "failed", str(e))

    def _watchdog():
        s = session_manager.get_session(session.session_id)
        if s and s.status == "running":
            session_manager.update_session_status(session.session_id, "failed", "任务超时（15 分钟未完成）")

    watchdog = threading.Timer(900, _watchdog)
    watchdog.daemon = True
    watchdog.start()

    threading.Thread(target=run_agent_task, daemon=True, name=f"agent-{session.session_id[:8]}").start()

    return AgentSessionResponse(session_id=session.session_id, agent_type="claude")


@router.get("/api/agent/sessions/{session_id}", response_model=AgentSessionStatus)
async def api_get_agent_session(session_id: str):
    """查询 Agent 会话状态（代理接口）。"""
    from ..agents.session_manager import session_manager
    
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    return AgentSessionStatus(
        session_id=session.session_id,
        status=session.status,
        kind=session.kind,
        latest_output=session_manager.get_latest_output(session_id),
        completed_at=session.completed_at,
        error=session.error,
    )


@router.get("/api/agent/sessions/{session_id}/stream")
async def api_get_agent_session_stream(session_id: str):
    """获取 Agent 会话流式输出（代理接口）。"""
    from ..agents.session_manager import session_manager
    from fastapi.responses import StreamingResponse
    import json
    
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    async def event_generator():
        async for chunk in session_manager.stream_output(session_id):
            yield chunk
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            # 注：浏览器 EventSource 必带 Accept-Encoding: gzip，Next 代理的
            # compression 中间件会把 gzip 的 SSE 憋到流结束才吐出（声明
            # Content-Encoding: identity 并不能跳过，其逻辑恰好相反）。
            # 解法在 packages/frontend/next.config.mjs 的 compress: false。
        },
    )
