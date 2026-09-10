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
    """提交生成请求（对齐 openapi.yaml 的 DeckRequest）。
    
    任务类型区分：
    - 草稿引擎任务：调用草稿生成引擎，将用户文件转化为 Markdown 草稿
    - 输出引擎任务：调用输出引擎，将草稿内容生成最终展示文件
    
    项目自动创建：
    - 如未指定 project_id，系统自动创建新项目
    - 项目名称默认为上传文件名（去除扩展名）
    """
    project_id: str | None = None  # 项目标识（可选，不提供则自动创建新项目）
    engine: str = "ppt_master"
    engine_type: str = "draft"  # draft=草稿引擎任务，output=输出引擎任务
    title: str | None = None  # 可选，可从源材料提取或由 AI 自动生成
    content: str | None = None  # Markdown 内容。输出引擎任务可直接提供，或通过 draft_id + version 引用草稿实体
    draft_id: str | None = None  # 草稿标识（输出引擎任务可选）。提供后系统自动从草稿版本中获取 content
    version: str | None = None  # 草稿版本号（与 draft_id 配合使用）。不提供则使用草稿的当前版本
    content_style: str = "marketing_promo"
    pages: int | None = Field(default=None, ge=1, description="目标页数（可选，可由 AI 决定或使用默认值）")
    agent: str = "claude"
    sources: list[SourceSpec] = Field(default_factory=list)  # 原始素材来源列表（提交时提供，用于生成草稿）。优先于 content。支持多文件上传
    engine_params: dict[str, Any] = Field(default_factory=dict)


class ProgressInfo(BaseModel):
    """运行阶段进度信息。"""
    stage: str  # initializing / generating_svg / exporting
    stage_label: str  # 用户可见的阶段描述
    pages_generated: int = 0  # 已生成的 SVG 页面数
    elapsed_seconds: int = 0  # 已耗时秒数
    current_page: str | None = None  # 当前正在生成的页面
    last_log: str | None = None  # 最近日志输出


class JobOut(BaseModel):
    """任务响应（对齐 openapi.yaml 的 Job）。
    
    任务类型：
    - 草稿引擎任务：调用草稿生成引擎，将用户文件转化为 Markdown 草稿
    - 输出引擎任务：调用输出引擎，将草稿内容生成最终展示文件
    
    状态明细（status_detail）：
    - 仅在 running 状态下有意义
    - 内容随执行进度变化（如"正在生成第3页..."）
    - 前端通过轮询获取最新状态
    """
    model_config = ConfigDict(extra="ignore")
    job_id: str
    project_id: str | None = None
    task_type: str = "draft"  # draft=草稿引擎任务，output=输出引擎任务
    status: str
    status_detail: str | None = None  # 状态明细（引擎当前正在做什么，仅 running 时有意义）
    engine_job_id: str | None = None  # 与 contracts 对齐：引擎任务编号
    title: str | None = None
    pages: int | None = None
    engine: str | None = None
    agent: str | None = None
    content_style: str | None = None
    engine_params: dict[str, Any] = Field(default_factory=dict)
    result_path: str | None = None
    draft_path: str | None = None
    draft_id: str | None = None  # 输出任务所用的草稿
    draft_version: str | None = None  # 所用草稿版本号（演示文件命名依据）
    draft_content: str | None = None
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    progress: ProgressInfo | None = None  # 运行阶段进度信息（兼容旧版）


class ErrorOut(BaseModel):
    """错误响应（对齐 openapi.yaml 的 Error）。"""
    message: str
    code: str | None = None


class EngineInfo(BaseModel):
    """引擎信息（对齐 openapi.yaml 的 EngineInfo）。"""
    name: str
    display_name: str
    description: str = ""
    engine_type: str = "draft"  # draft=草稿生成引擎，output=输出引擎
    params_schema: dict[str, Any]


class UploadResponse(BaseModel):
    """文件上传后的返回（服务器侧路径，供后续提交引用）。"""
    upload_id: str
    filename: str
    path: str
    size: int


class SuggestTitleFile(BaseModel):
    """拟题请求里的单个文件。"""
    filename: str
    path: str | None = None


class SuggestTitleRequest(BaseModel):
    """Agent 拟题请求（对齐 openapi.yaml 的 SuggestTitleRequest）。"""
    files: list[SuggestTitleFile]


class SuggestTitleResult(BaseModel):
    """Agent 拟题结果（对齐 openapi.yaml 的 SuggestTitleResult）。"""
    title: str


class AgentExecuteFile(BaseModel):
    """Agent 执行请求里的单个文件（suggest_title 用）。"""
    filename: str
    path: str | None = None


class AgentExecuteRequest(BaseModel):
    """Agent 文本处理统一请求（对齐 openapi.yaml 的 AgentExecuteRequest）。"""
    kind: str  # 白名单：modify_draft / suggest_title（路由层校验）
    draft_id: str | None = None
    version: str | None = None
    prompt: str | None = None
    files: list[AgentExecuteFile] = Field(default_factory=list)


class AgentExecuteResult(BaseModel):
    """Agent 文本处理统一响应（对齐 openapi.yaml 的 AgentExecuteResult）。"""
    text: str
    kind: str | None = None


class AgentSessionResponse(BaseModel):
    """Agent 异步执行会话响应（对齐 openapi.yaml 的 AgentSessionResponse）。"""
    session_id: str
    agent_type: str  # claude / codex / hermes


class AgentSessionStatus(BaseModel):
    """Agent 会话状态信息（对齐 openapi.yaml 的 AgentSessionStatus）。"""
    session_id: str
    status: str  # running / completed / failed
    kind: str | None = None
    latest_output: str | None = None
    completed_at: str | None = None
    error: str | None = None


class AgentSessionStreamEvent(BaseModel):
    """Agent 会话流式输出事件（对齐 openapi.yaml 的 AgentSessionStreamEvent）。"""
    type: str  # text / status / error / done
    data: str


# ── 项目相关 ──

class ProjectOut(BaseModel):
    """项目响应（对齐 openapi.yaml 的 Project）。"""
    project_id: str
    project_name: str  # 与 contracts 对齐
    current_draft_id: str | None = None  # 与 contracts 对齐
    current_draft_version: str | None = None  # 与 contracts 对齐
    created_at: str
    updated_at: str
    # 列表页统计（由 /api/projects 附带）
    draft_count: int = 0
    presentation_count: int = 0
    running_task_count: int = 0


class ProjectDetailOut(ProjectOut):
    """项目详情响应（对齐 openapi.yaml 的 ProjectDetail）。"""
    tasks: list[JobOut] = Field(default_factory=list)
    drafts: list["DraftOut"] = Field(default_factory=list)
    presentations: list["PresentationOut"] = Field(default_factory=list)


# ── 草稿相关 ──

class DraftOut(BaseModel):
    """草稿响应（对齐 openapi.yaml 的 Draft）。"""
    draft_id: str
    project_id: str
    task_id: str
    current_version: str | None = None
    draft_path: str | None = None
    created_at: str
    updated_at: str


class DraftDetailOut(DraftOut):
    """草稿详情响应（包含版本历史列表）。"""
    content: str | None = None  # 当前版本内容
    versions: list["DraftVersionOut"] = Field(default_factory=list)


class DraftVersionOut(BaseModel):
    """草稿版本响应（对齐 openapi.yaml 的 DraftVersion）。"""
    draft_id: str
    version: str
    content: str
    created_at: str


class SaveDraftVersionRequest(BaseModel):
    """保存草稿新版本请求（对齐 openapi.yaml 的 SaveDraftVersionRequest）。"""
    content: str  # 用户编辑后的 Markdown 内容


class ModifyDraftRequest(BaseModel):
    """修改草稿请求。"""
    current_version: str  # 当前版本号（用于冲突检测）
    prompt: str  # 用户的修改提示词


# ── 演示文件相关 ──

class PresentationOut(BaseModel):
    """演示文件响应（对齐 openapi.yaml 的 Presentation）。"""
    presentation_id: str
    project_id: str
    task_id: str
    file_name: str
    file_format: str = "pptx"
    file_path: str
    created_at: str


# ── 安全检查相关 ──

class SecurityCheckRequest(BaseModel):
    """安全检查请求。"""
    content: str  # 待检查的草稿 Markdown 内容


class SecurityCheckResult(BaseModel):
    """安全检查结果。"""
    passed: bool
    reason: str | None = None
    rules_checked: list[str] = Field(default_factory=list)
