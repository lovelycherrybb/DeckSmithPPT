"""Agent 会话管理器：管理异步 Agent 任务的会话状态和流式输出。

会话生命周期：
1. 创建会话：execute-async 接口创建会话，返回 session_id
2. 执行任务：后台异步执行 Agent 任务
3. 查询状态：通过 session_id 查询会话状态
4. 流式输出：通过 session_id 获取实时输出流

会话存储：
- 使用内存字典存储会话状态（生产环境应使用 Redis 或数据库）
- 会话包含：session_id, agent_type, status, output_buffer, completed_at, error
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator
from dataclasses import dataclass, field


@dataclass
class AgentSession:
    """Agent 会话状态。"""
    session_id: str
    agent_type: str  # claude / codex / hermes
    kind: str  # modify_draft / suggest_title
    status: str = "running"  # running / completed / failed
    output_buffer: list[str] = field(default_factory=list)
    completed_at: str | None = None
    error: str | None = None
    result: str | None = None  # 清洗后的最终文本
    task: asyncio.Task | None = None
    proc: Any = None  # 在跑的子进程（Popen），供 cancel 按进程组击杀
    cancelled: bool = False  # 用户已请求取消：worker 收尾时别再覆盖状态/错误


class SessionManager:
    """会话管理器：管理所有 Agent 会话。"""
    
    def __init__(self):
        self._sessions: dict[str, AgentSession] = {}
    
    def create_session(self, agent_type: str, kind: str) -> AgentSession:
        """创建新会话。"""
        session_id = str(uuid.uuid4())
        session = AgentSession(
            session_id=session_id,
            agent_type=agent_type,
            kind=kind,
        )
        self._sessions[session_id] = session
        return session
    
    def get_session(self, session_id: str) -> AgentSession | None:
        """获取会话。"""
        return self._sessions.get(session_id)
    
    def update_session_status(self, session_id: str, status: str, error: str | None = None):
        """更新会话状态。"""
        session = self._sessions.get(session_id)
        if session:
            session.status = status
            if status in ("completed", "failed"):
                session.completed_at = datetime.now(timezone.utc).isoformat()
            if error:
                session.error = error
    
    def set_result(self, session_id: str, result: str):
        """设置清洗后的最终结果（供 stream_output 以 result 事件下发）。"""
        session = self._sessions.get(session_id)
        if session:
            session.result = result

    def attach_proc(self, session_id: str, proc) -> None:
        """登记在跑的子进程句柄，供取消端点按进程组击杀。"""
        session = self._sessions.get(session_id)
        if session:
            session.proc = proc

    def request_cancel(self, session_id: str) -> bool:
        """用户取消在跑会话：置 cancelled + 杀进程组 + failed/已取消。幂等。

        会话不存在返回 False；已结束返回 True（空操作）。cancelled 标记让 worker
        收尾时不再用"子进程退出码非 0"覆盖掉"已取消"这个真实原因。
        """
        session = self._sessions.get(session_id)
        if session is None:
            return False
        if session.status != "running":
            return True
        session.cancelled = True
        proc = session.proc
        if proc is not None:
            try:
                # 按进程组杀：claude 自己还 fork 子树，只 terminate 直系会留孤儿继续写产物
                from engine.core.proc import terminate_tree
                terminate_tree(getattr(proc, "pid", None))
            except Exception:  # noqa: BLE001 - 杀不掉也要把状态置了，别把取消本身弄失败
                pass
        self.update_session_status(session_id, "failed", "已取消")
        return True

    def append_output(self, session_id: str, output: str, type_: str = "text"):
        """追加事件到会话缓冲区（type_: text / thinking）。"""
        session = self._sessions.get(session_id)
        if session:
            session.output_buffer.append({"type": type_, "data": output})
    
    def get_latest_output(self, session_id: str, max_chars: int = 1000) -> str | None:
        """获取最新输出片段（仅正文）。"""
        session = self._sessions.get(session_id)
        if session and session.output_buffer:
            recent = [e.get("data", "") for e in session.output_buffer[-20:] if e.get("type") == "text"]
            return "".join(recent)[-max_chars:]
        return None
    
    async def stream_output(self, session_id: str) -> AsyncGenerator[str, None]:
        """流式输出会话内容。"""
        session = self._sessions.get(session_id)
        if not session:
            yield f"data: {json.dumps({'type': 'error', 'data': '会话不存在'})}\n\n"
            return
        
        # 等待一小段时间，让输出缓冲区有内容
        await asyncio.sleep(0.1)
        
        last_index = 0
        while session.status == "running":
            # 检查是否有新输出
            if last_index < len(session.output_buffer):
                for i in range(last_index, len(session.output_buffer)):
                    ev = session.output_buffer[i]
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                last_index = len(session.output_buffer)
            
            # 等待一小段时间
            await asyncio.sleep(0.1)
        
        # 刷完缓冲区剩余增量（任务结束瞬间可能还有未下发的片段）
        if last_index < len(session.output_buffer):
            for i in range(last_index, len(session.output_buffer)):
                ev = session.output_buffer[i]
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            last_index = len(session.output_buffer)
        
        # 发送最后的状态
        if session.status == "completed":
            if session.result is not None:
                yield f"data: {json.dumps({'type': 'result', 'data': session.result})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'data': '完成'})}\n\n"
        elif session.status == "failed":
            yield f"data: {json.dumps({'type': 'error', 'data': session.error or '未知错误'})}\n\n"
    
    def cleanup_session(self, session_id: str):
        """清理会话（可选，用于释放资源）。"""
        session = self._sessions.get(session_id)
        if session and session.task:
            session.task.cancel()
        self._sessions.pop(session_id, None)


# 全局会话管理器实例
session_manager = SessionManager()
