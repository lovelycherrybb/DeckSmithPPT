"""接口层数据形状（Pydantic），与 docs/contracts/openapi.yaml 对齐。

- 请求进来：用 *Request 模型校验前端发的 JSON
- 响应回去：用 *Out / *Info 模型格式化后端回的 JSON
"""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── 通用层 ──

class SourceSpec(BaseModel):
    """原始素材来源（提交时提供，用于生成草稿）。"""
    type: str  # url / file_path / text
    value: str


class DeckRequest(BaseModel):
    """提交生成请求（对齐 openapi.yaml 的 DeckRequest）。"""
    title: str | None = None  # 可选，可从源材料提取或由 AI 自动生成
    pages: int | None = Field(default=None, ge=1, description="目标页数（可选，可由 AI 决定或使用默认值）")
    engine: str = "ppt_master"
    content: str | None = None
    source: SourceSpec | None = None
    content_style: str = "marketing_promo"
    engine_params: dict[str, Any] = Field(default_factory=dict)
    agent: str = "claude"


class ProgressInfo(BaseModel):
    """运行阶段进度信息。"""
    stage: str  # initializing / generating_svg / exporting
    stage_label: str  # 用户可见的阶段描述
    pages_generated: int = 0  # 已生成的 SVG 页面数
    elapsed_seconds: int = 0  # 已耗时秒数
    current_page: str | None = None  # 当前正在生成的页面
    last_log: str | None = None  # 最近日志输出


class JobOut(BaseModel):
    """任务响应（对齐 openapi.yaml 的 Job）。"""
    model_config = ConfigDict(extra="ignore")
    job_id: str
    status: str
    title: str | None = None
    pages: int | None = None
    engine: str | None = None
    agent: str | None = None
    content_style: str | None = None
    engine_params: dict[str, Any] = Field(default_factory=dict)
    result_path: str | None = None
    draft_path: str | None = None
    draft_content: str | None = None
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    progress: ProgressInfo | None = None  # 运行阶段进度信息


class ErrorOut(BaseModel):
    """错误响应（对齐 openapi.yaml 的 Error）。"""
    message: str
    code: str | None = None


class EngineInfo(BaseModel):
    """引擎信息（对齐 openapi.yaml 的 EngineInfo）。"""
    name: str
    display_name: str
    description: str = ""
    params_schema: dict[str, Any]


class UploadResponse(BaseModel):
    """文件上传后的返回（服务器侧路径，供后续提交引用）。"""
    upload_id: str
    filename: str
    path: str
    size: int


class ConfirmRequest(BaseModel):
    """确认草稿请求（对齐 /api/jobs/{id}/confirm）。

    content 为必填（用户改好的 Markdown）；engine_params 可选，
    在此阶段选择风格/模式/尺寸等（结构见 GET /api/engines 的 params_schema）。
    """
    content: str
    engine_params: dict[str, Any] = Field(default_factory=dict)

class SaveDraftRequest(BaseModel):
    """保存草稿请求（对齐 /api/jobs/{id}/draft）。content 为用户修改后的 Markdown。"""
    content: str
