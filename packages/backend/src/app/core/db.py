"""数据库单例：引擎、会话工厂、任务根目录（应用启动时初始化一次）。"""
import sqlite3

from .config import get_database_url, get_jobs_root
from ..models import Base, make_engine, make_session_factory

DATABASE_URL = get_database_url()
ENGINE = make_engine(DATABASE_URL)
SessionLocal = make_session_factory(ENGINE)
JOBS_ROOT = get_jobs_root()


def init_db() -> None:
    """建表（幂等，可重复调用）。在应用启动时执行一次。"""
    # 确保 SQLite 文件所在目录存在（DATABASE_URL 为 sqlite 时）
    if DATABASE_URL.startswith("sqlite"):
        from pathlib import Path
        db_path = Path(DATABASE_URL.replace("sqlite:///", "", 1))
        db_path.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=ENGINE)
    if DATABASE_URL.startswith("sqlite"):
        db_path = Path(DATABASE_URL.replace("sqlite:///", "", 1))
        with sqlite3.connect(db_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "content_style" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN content_style VARCHAR(64) DEFAULT 'marketing_promo'")
                conn.commit()
