"""引擎数据模型（占位骨架，与 docs/contracts/engine.schema.json 对齐）。

最终应由 docs/contracts/engine.schema.json 代码生成，
禁止手写与契约不一致的类型（见 scripts/generate-types.sh）。

与 vc_design 一致：任务状态只有 running、completed、failed、timeout 4种。
"""

from dataclasses import dataclass, field
from enum import Enum


class JobStatus(str, Enum):
    """任务状态（与 vc_design 一致：只有4种状态）。"""
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
    status: JobStatus
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
