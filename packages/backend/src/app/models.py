"""SQLAlchemy 数据模型：项目表、任务表、草稿表、草稿版本表、演示文件表。

一行 = 一个实体。列 = 实体的属性。
SQLite 不支持原生 JSON 列，用 Text 存 JSON 字符串；时间存 ISO 字符串便于序列化。
"""
from sqlalchemy import (
    Integer, String, Text, ForeignKey, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, relationship


class Base(DeclarativeBase):
    """所有表的基类（SQLAlchemy 要求的统一地基）。"""


class Project(Base):
    """项目表：顶层聚合根，管理所有相关任务。"""
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    project_name: Mapped[str] = mapped_column(String(255))  # 与 contracts 对齐
    current_draft_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 与 contracts 对齐
    current_draft_version: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 与 contracts 对齐
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))
    # 归属主体（用户系统 v0.8）：owner_user_id 指向真实用户；未登录试用先挂 visitor_id、
    # owner_user_id 留 NULL（= 未认领），登录或管理员代为认领后写入 owner_user_id。
    # 迁移走 _MIGRATIONS 补列（可空，不升 SCHEMA_VERSION）。
    owner_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.user_id"), nullable=True, index=True)
    visitor_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # 关系
    jobs: Mapped[list["Job"]] = relationship("Job", back_populates="project", lazy="dynamic")
    drafts: Mapped[list["Draft"]] = relationship("Draft", back_populates="project", lazy="dynamic")
    presentations: Mapped[list["Presentation"]] = relationship("Presentation", back_populates="project", lazy="dynamic")


class Job(Base):
    """任务表：记录一次 PPT 生成请求的全生命周期。
    
    任务类型：
    - 草稿引擎任务（task_type=draft）：调用草稿生成引擎，将用户文件转化为 Markdown 草稿
    - 输出引擎任务（task_type=output）：调用输出引擎，将草稿内容生成最终展示文件
    
    状态（与 contracts 的 JobStatus 一致，见 ADR-018 分层枚举）：
    - queued：已入库、等待调度认领，引擎子进程尚未 fork 成功
    - running：引擎子进程已在执行
    - completed / failed / timeout / cancelled：四个终态，不可逆
    判"是否还在推进"用 {queued, running}，不要用"是否等于 running"（ADR-018 消费侧口径）。
    """
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("projects.project_id"), nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(String(16), default="draft")  # draft=草稿引擎任务，output=输出引擎任务
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    status_detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # 状态明细（引擎当前正在做什么）
    # ── 调度器内部字段（ADR-019 决策 3；**不进契约**，编排层私有状态）──
    # 两段式认领：先把 queued 行的槽位占住（写 claimed_by），fork 成功后才 CAS 成 running。
    # 不这么做的后果：契约把 queued 定义为"子进程尚未 fork 成功"，先置 running 再 fork 会让
    # fork 失败的行冒充 running。
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)  # "host:pid"
    claimed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)               # 占位时刻（判陈旧用）
    started_at: Mapped[str | None] = mapped_column(String(32), nullable=True)               # 真正开始跑引擎的时刻，超时预算的起点
    engine_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 与 contracts 对齐：引擎任务编号
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 可选，可从源材料提取或由 AI 自动生成
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 可选，可由 AI 决定或使用默认值
    engine: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(32), default="claude")
    content_style: Mapped[str] = mapped_column(String(64), default="marketing_promo")

    # 引擎特定参数（style/mode/...），存为 JSON 文本
    engine_params: Mapped[str] = mapped_column(Text, default="{}")

    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 素材来源（JSON 文本）。ADR-019 决策 2 把 engine.submit 从请求线程搬进调度器，
    # 于是认领时必须能还原 sources —— 它原先只在请求线程的栈上活着，从不入库。
    # 这是搬家的**硬前置**：漏了这一列，带上传文件的草稿任务会静默丢掉素材。
    sources: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    draft_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 输出任务所用的草稿
    draft_version: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 所用草稿版本号（演示文件命名用）
    result_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 本任务引擎主会话累计的 token 消耗，存为 JSON 文本（与 engine_params 同一存法）。
    # NULL = 未记录（存量任务、或引擎不上报用量），不得用 "0" 冒充（ADR-016）
    token_usage: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))

    # 关系
    project: Mapped["Project | None"] = relationship("Project", back_populates="jobs")


class Draft(Base):
    """草稿表：独立实体，有唯一标识和版本管理。
    
    草稿版本管理：
    - 版本号格式：年月日时分秒（如 20260907143025）
    - 同一草稿同一秒内只能保存一次（防止版本冲突）
    - 历史版本不可变，可随时还原
    """
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.project_id"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 生成此草稿的草稿引擎任务标识
    current_version: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 当前版本号（年月日时分秒格式）
    draft_path: Mapped[str | None] = mapped_column(String(512), nullable=True)  # 与 contracts 对齐：草稿路径
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # 关系
    project: Mapped["Project"] = relationship("Project", back_populates="drafts")
    versions: Mapped[list["DraftVersion"]] = relationship("DraftVersion", back_populates="draft", lazy="dynamic")


class DraftVersion(Base):
    """草稿版本表：记录草稿的每个版本。
    
    版本号格式：年月日时分秒（如 20260907143025）
    同一草稿同一秒内只能保存一次（防止版本冲突）
    """
    __tablename__ = "draft_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[str] = mapped_column(String(64), ForeignKey("drafts.draft_id"), index=True)
    version: Mapped[str] = mapped_column(String(32))  # 版本号（年月日时分秒格式）
    content: Mapped[str] = mapped_column(Text)  # 该版本的 Markdown 内容
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 生成任务（哪个草稿引擎任务创建的）
    draft_path: Mapped[str | None] = mapped_column(String(512), nullable=True)  # 草稿文件路径
    created_at: Mapped[str] = mapped_column(String(32))

    # 关系
    draft: Mapped["Draft"] = relationship("Draft", back_populates="versions")


class Presentation(Base):
    """演示文件表：记录输出引擎任务生成的最终展示文件。"""
    __tablename__ = "presentations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    presentation_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.project_id"), index=True)
    task_id: Mapped[str] = mapped_column(String(64))  # 生成此文件的输出引擎任务标识
    file_name: Mapped[str] = mapped_column(String(255))  # 与 contracts 对齐：文件名称
    file_format: Mapped[str] = mapped_column(String(16))  # 与 contracts 对齐：文件格式（pptx/html）
    file_path: Mapped[str] = mapped_column(String(512))  # 与 contracts 对齐：文件路径
    created_at: Mapped[str] = mapped_column(String(32))

    # 关系
    project: Mapped["Project"] = relationship("Project", back_populates="presentations")


class User(Base):
    """用户表：权限判定与数据归属的主体（用户系统 v0.8）。

    角色三值枚举 manager/vip/normal；状态 enabled/disabled（禁用只挡登录、保留数据）。
    密码仅存哈希（见 core/passwords.py），任何响应不得回显。
    内置唯一管理员账号 manager 由 bootstrap 播种，不可被增删改接口触碰。
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # 登录凭据，创建后不可改
    nickname: Mapped[str] = mapped_column(String(255), default="")  # 展示名，缺省同 username
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="normal", index=True)  # manager|vip|normal
    status: Mapped[str] = mapped_column(String(16), default="enabled")  # enabled|disabled
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class UserSession(Base):
    """登录会话表：session_id（httpOnly Cookie 承载）→ 用户，服务端可即时失效。

    访客身份不建表——访客令牌直接记在 projects.visitor_id 上，登录后按该令牌并入。
    """
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), index=True)
    created_at: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[str] = mapped_column(String(32), index=True)


class UserSetting(Base):
    """用户偏好表：与用户 1 对 1，存 JSON 单列（对齐 token_usage 的单列取舍，加字段不改表）。"""
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), unique=True, index=True)
    prefs: Mapped[str] = mapped_column(Text, default="{}")  # {default_engine, default_pages, auto_save}
    updated_at: Mapped[str] = mapped_column(String(32))


def make_engine(database_url: str):
    """根据连接串创建 SQLAlchemy 引擎（SQLite 需 check_same_thread=False）。"""
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def make_session_factory(engine):
    """创建会话工厂（每次请求拿一个 session 操作数据库）。"""
    return sessionmaker(bind=engine, expire_on_commit=False)
