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

    # 关系
    jobs: Mapped[list["Job"]] = relationship("Job", back_populates="project", lazy="dynamic")
    drafts: Mapped[list["Draft"]] = relationship("Draft", back_populates="project", lazy="dynamic")
    presentations: Mapped[list["Presentation"]] = relationship("Presentation", back_populates="project", lazy="dynamic")


class Job(Base):
    """任务表：记录一次 PPT 生成请求的全生命周期。
    
    任务类型：
    - 草稿引擎任务（task_type=draft）：调用草稿生成引擎，将用户文件转化为 Markdown 草稿
    - 输出引擎任务（task_type=output）：调用输出引擎，将草稿内容生成最终展示文件
    
    状态（与 vc_design 一致）：
    - running：运行中，引擎正在执行
    - completed：完成，已成功生成输出
    - failed：失败，执行过程中出错
    - timeout：超时，未能在规定时间内完成
    """
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("projects.project_id"), nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(String(16), default="draft")  # draft=草稿引擎任务，output=输出引擎任务
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    status_detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # 状态明细（引擎当前正在做什么）
    engine_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 与 contracts 对齐：引擎任务编号
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 可选，可从源材料提取或由 AI 自动生成
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 可选，可由 AI 决定或使用默认值
    engine: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(32), default="claude")
    content_style: Mapped[str] = mapped_column(String(64), default="marketing_promo")

    # 引擎特定参数（style/mode/...），存为 JSON 文本
    engine_params: Mapped[str] = mapped_column(Text, default="{}")

    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    draft_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 输出任务所用的草稿
    draft_version: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 所用草稿版本号（演示文件命名用）
    result_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

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


def make_engine(database_url: str):
    """根据连接串创建 SQLAlchemy 引擎（SQLite 需 check_same_thread=False）。"""
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def make_session_factory(engine):
    """创建会话工厂（每次请求拿一个 session 操作数据库）。"""
    return sessionmaker(bind=engine, expire_on_commit=False)
