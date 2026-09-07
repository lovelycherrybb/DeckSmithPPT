"""引擎数据模型（占位骨架，与 docs/contracts/engine.schema.json 对齐）。

最终应由 docs/contracts/engine.schema.json 代码生成，
禁止手写与契约不一致的类型（见 scripts/generate-types.sh）。
"""

from dataclasses import dataclass, field
from enum import Enum


class JobStatus(str, Enum):
    RUNNING = "running"
    DRAFT = "draft"
    TIMEOUT = "timeout"
    FAILED = "failed"
    COMPLETED = "completed"


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
    content: str | None = None
    source: dict | None = None
    content_style: str = "marketing_promo"
    engine_params: dict = field(default_factory=dict)


@dataclass
class Job:
    job_id: JobId
    status: JobStatus
    result_path: str | None = None
    draft_path: str | None = None
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    content_style: str | None = None
