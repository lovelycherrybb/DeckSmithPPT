"""SQLAlchemy 数据模型：任务表（jobs）。

一行 = 一个 PPT 生成任务。列 = 任务的属性。
SQLite 不支持原生 JSON 列，用 Text 存 JSON 字符串；时间存 ISO 字符串便于序列化。
"""
from sqlalchemy import (
    Integer, String, Text, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    """所有表的基类（SQLAlchemy 要求的统一地基）。"""


class Job(Base):
    """任务表：记录一次 PPT 生成请求的全生命周期。"""
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    title: Mapped[str] = mapped_column(String(255))
    pages: Mapped[int] = mapped_column(Integer, default=1)
    engine: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(32), default="claude")
    content_style: Mapped[str] = mapped_column(String(64), default="marketing_promo")

    # 引擎特定参数（style/mode/...），存为 JSON 文本
    engine_params: Mapped[str] = mapped_column(Text, default="{}")

    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


def make_engine(database_url: str):
    """根据连接串创建 SQLAlchemy 引擎（SQLite 需 check_same_thread=False）。"""
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def make_session_factory(engine):
    """创建会话工厂（每次请求拿一个 session 操作数据库）。"""
    return sessionmaker(bind=engine, expire_on_commit=False)
