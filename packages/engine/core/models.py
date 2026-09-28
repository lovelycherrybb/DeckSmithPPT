"""引擎数据模型（与 docs/contracts/engine.schema.json 的 $defs 对齐）。

契约是唯一事实来源；本文件目前是**人工同步的第 5 份副本**（docstring 里的
"最终应由 engine.schema.json 代码生成"这条偏差仍未消除，见 ADR-018 后果 3），
改名/加值时务必与契约同批修改。

分层枚举（ADR-018）：引擎只报告自己那个子进程的真相，状态值域是 `EngineJobStatus`
（4 值）。面向用户与前端的 `queued`（等待调度认领）与 `cancelled`（被撤销）属编排层
概念，引擎永远不产出，故**不在本枚举内**——不要在这里加它们。
"""

from dataclasses import dataclass, field
from enum import Enum


class EngineJobStatus(str, Enum):
    """引擎侧任务状态（`PptEngine.status()` 的值域，契约见 engine.schema.json）。"""
    RUNNING = "running"
    TIMEOUT = "timeout"
    FAILED = "failed"
    COMPLETED = "completed"


class EngineType(str, Enum):
    """引擎类型：draft=草稿生成引擎，output=输出引擎。"""
    DRAFT = "draft"
    OUTPUT = "output"


@dataclass(frozen=True)
class JobId:
    value: str

    def __str__(self) -> str:
        return self.value


@dataclass
class DeckContent:
    """通用层：提交给引擎的生成请求（契约 DeckContent）。"""

    title: str
    pages: int
    engine: str = "ppt_master"
    engine_type: EngineType = EngineType.DRAFT
    content: str | None = None
    source: dict | None = None
    content_style: str = "marketing_promo"
    engine_params: dict = field(default_factory=dict)


@dataclass
class Job:
    job_id: JobId
    status: EngineJobStatus
    task_type: EngineType = EngineType.DRAFT
    status_detail: str | None = None
    result_path: str | None = None
    draft_path: str | None = None
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    content_style: str | None = None
    # 引擎主会话累计的 token 消耗（契约 TokenUsage）。可选：不实现用量上报的引擎
    # 留 None 即「未记录」，调用方不得据此填 0（ADR-016），故不设必选钩子。
    token_usage: dict | None = None
