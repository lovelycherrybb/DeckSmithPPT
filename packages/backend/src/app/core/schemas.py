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


class VersionOut(BaseModel):
    """当前部署版本（来自发版注入的 DECKSMITH_VERSION）。开发态未注入则为 null。"""
    version: str | None = None


# ── 用户与鉴权（v0.8，对齐 openapi.yaml 同名 schema） ──

class UserOut(BaseModel):
    """用户出参。绝不含 password_hash。"""
    user_id: str
    username: str
    nickname: str
    phone: str | None = None
    role: str  # manager | vip | normal
    status: str  # enabled | disabled
    created_at: str
    updated_at: str


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResult(BaseModel):
    """登录成功：Set-Cookie 下发会话；claimed_project_count 为内联并入的访客项目数。"""
    user: UserOut
    claimed_project_count: int = 0


class CurrentUserOut(BaseModel):
    """/api/auth/me 三态：已登录（user 非空）/ 未登录有访客身份（visitor_id 非空）/ 纯匿名。"""
    authenticated: bool
    user: UserOut | None = None
    visitor_id: str | None = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class ClaimProjectRequest(BaseModel):
    """管理员代为认领：把项目归属到指定真实用户。"""
    target_user_id: str


class UserCreate(BaseModel):
    """管理员新增用户；初始密码固定 234567，不在此传。"""
    username: str
    nickname: str | None = None
    phone: str | None = None
    is_vip: bool = False


class UserUpdate(BaseModel):
    """管理员改用户信息；用户名不可改（不在此字段）。"""
    nickname: str | None = None
    phone: str | None = None
    is_vip: bool | None = None


class UserStatusUpdate(BaseModel):
    status: str  # enabled | disabled


class UserPreferenceOut(BaseModel):
    default_engine: str | None = None
    default_pages: int | None = None
    auto_save: bool = True
    updated_at: str | None = None


class CapacityInfo(BaseModel):
    """只读容量提示（任意身份可取，ADR-020 决策 7）。

    刻意只有三个数：不暴露配置项清单、不暴露密钥状态。给等待中的人解释"为什么慢"，
    不是给普通用户开一个只读的设置面。
    """
    max_concurrent_jobs: int
    running: int
    queued: int


class SystemSettingsFieldOut(BaseModel):
    """一个设置项的元数据 + 生效值（对齐契约 SystemSettingsField）。

    前端按本对象通用渲染，不写死任何键名 —— 加设置项只改后端注册表。
    secret 类型：value 恒 null，只回 configured/masked，明文不出后端。
    """
    key: str
    group: str
    label: str
    description: str | None = None
    type: str  # int | bool | string | secret
    unit: str | None = None
    min: int | None = None
    max: int | None = None
    default: Any = None
    value: Any = None
    configured: bool | None = None
    masked: str | None = None
    overridden: bool
    source: str  # default | override
    restart_required: bool


class SystemSettingsGroupOut(BaseModel):
    id: str
    label: str
    description: str | None = None


class SystemSettingsOut(BaseModel):
    groups: list[SystemSettingsGroupOut]
    fields: list[SystemSettingsFieldOut]
    updated_at: str | None = None


class SystemSettingsUpdate(BaseModel):
    """部分提交：values 里出现的键才改；值为 null 表示删除覆盖、恢复默认。"""
    values: dict[str, Any]


class UserPreferenceUpdate(BaseModel):
    default_engine: str | None = None
    default_pages: int | None = None
    auto_save: bool | None = None


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


class PageItem(BaseModel):
    """单个已完成页（对齐 openapi.yaml 的 PageItem）。

    只描述已确定落盘的页；运行中的转圈格由前端按 status+stage 条件渲染，不在数据里。
    """
    index: int  # 页序，从 1 开始（按文件名数字前缀 int 升序，不依赖 mtime）
    name: str  # 页文件名（如 03_overview.svg）
    url: str  # 指向 /api/jobs/{job_id}/preview/{相对产物根目录的路径}
    kind: str = "image"  # image=内联 SVG/位图走 <Image>；html=整段 HTML 页走 iframe（本期未启用）


class JobPages(BaseModel):
    """逐页预览快照（对齐 openapi.yaml 的 JobPages）。

    只承载已确定的事实：不预测总页数、不含 pending 占位。
    """
    job_id: str
    engine: str | None = None  # 使用的引擎名
    supports_page_preview: bool = False  # 由引擎是否实现逐页枚举钩子派生，非按引擎名硬判
    status: str  # running / timeout / failed / completed，供前端决定末尾格
    stage: str | None = None  # 引擎阶段（generating/exporting 等），决定末尾格语义
    pages_generated: int = 0  # 已完成页数（= len(pages)，单一读盘事实源）
    pages: list[PageItem] = Field(default_factory=list)  # 已完成页列表（不支持逐页时为空数组）


class TokenUsage(BaseModel):
    """任务级 token 消耗（对齐 openapi.yaml 的 TokenUsage，口径见 ADR-016）。

    分项逐项可空：走第三方 Anthropic 兼容网关时 cache_* 可能不回报，
    未回报项为 None（不是 0），total_tokens 只对已回报项求和。
    """
    input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    output_tokens: int | None = None
    # 契约里 total_tokens 是必填，这里给默认值只为防御读：库里这列只可能由
    # engine.core.usage 写出（必带 total），万一出现脏行也不该让整条任务列表 500
    total_tokens: int = 0


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
    status: str  # queued/running/completed/failed/timeout/cancelled（终态判定见 service.jobs）
    status_detail: str | None = None  # 状态明细（引擎当前正在做什么，仅非终态时有意义）
    # 排队位序：仅 status=queued 时有值，读取时按 created_at 现算的派生值，仅供参考
    # （契约 openapi Job.queue_position；不入库、不构成承诺、不参与任何状态判定）
    queue_position: int | None = None
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
    # 引擎主会话累计 token 消耗；None = 未记录（存量任务 / 不上报用量的引擎），界面显示「未记录」
    token_usage: TokenUsage | None = None
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
    # 能力位（如 page_preview）：由「引擎是否真实实现逐页枚举钩子」自动派生，不按引擎名写死
    capabilities: list[str] = Field(default_factory=list)


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
    # 归属（v0.8）：owner_user_id=NULL 表示未认领访客项目；owner_label 出参现算，供管理员列显示
    owner_user_id: str | None = None
    owner_label: str | None = None
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
