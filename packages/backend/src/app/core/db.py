"""数据库单例：引擎、会话工厂、任务根目录（应用启动时初始化一次）。"""
import sqlite3

from .config import get_database_url, get_jobs_root
from ..models import Base, make_engine, make_session_factory

DATABASE_URL = get_database_url()
ENGINE = make_engine(DATABASE_URL)
SessionLocal = make_session_factory(ENGINE)
JOBS_ROOT = get_jobs_root()


def _column_names(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


# 轻量迁移：旧库补齐新列（新库由 create_all 直接建全）
_MIGRATIONS = {
    "jobs": {
        "content_style": "ALTER TABLE jobs ADD COLUMN content_style VARCHAR(64) DEFAULT 'marketing_promo'",
        "task_type": "ALTER TABLE jobs ADD COLUMN task_type VARCHAR(16) DEFAULT 'draft'",
        "status_detail": "ALTER TABLE jobs ADD COLUMN status_detail TEXT",
        "engine_job_id": "ALTER TABLE jobs ADD COLUMN engine_job_id VARCHAR(128)",
        "draft_id": "ALTER TABLE jobs ADD COLUMN draft_id VARCHAR(64)",
        "draft_version": "ALTER TABLE jobs ADD COLUMN draft_version VARCHAR(32)",
    },
    "projects": {
        "project_name": "ALTER TABLE projects ADD COLUMN project_name VARCHAR(255)",
        "current_draft_id": "ALTER TABLE projects ADD COLUMN current_draft_id VARCHAR(64)",
        "current_draft_version": "ALTER TABLE projects ADD COLUMN current_draft_version VARCHAR(32)",
    },
    "drafts": {
        "draft_path": "ALTER TABLE drafts ADD COLUMN draft_path VARCHAR(512)",
    },
    "draft_versions": {
        "task_id": "ALTER TABLE draft_versions ADD COLUMN task_id VARCHAR(64)",
        "draft_path": "ALTER TABLE draft_versions ADD COLUMN draft_path VARCHAR(512)",
    },
}


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
            for table, columns in _MIGRATIONS.items():
                existing = _column_names(conn, table)
                if not existing:
                    continue  # 表不存在（create_all 应已建全）
                for col, ddl in columns.items():
                    if col not in existing:
                        conn.execute(ddl)
            # projects.project_name 兜底：旧行的 name 值迁移过来
            existing = _column_names(conn, "projects")
            if "project_name" in existing and "name" in existing:
                conn.execute("UPDATE projects SET project_name = name WHERE project_name IS NULL OR project_name = ''")
            # 旧库 projects 残留 NOT NULL 的 name 列（模型已改名 project_name），
            # SQLite 去不掉非空约束，须按当前模型重建该表
            if "name" in existing:
                from sqlalchemy.schema import CreateTable
                from ..models import Project
                ddl = str(CreateTable(Project.__table__).compile(ENGINE)).strip() + ";"
                rows = conn.execute(
                    "SELECT id, project_id, COALESCE(NULLIF(project_name, ''), name), "
                    "current_draft_id, current_draft_version, created_at, updated_at FROM projects"
                ).fetchall()
                conn.execute("DROP TABLE projects")
                conn.execute(ddl)
                conn.executemany(
                    "INSERT INTO projects (id, project_id, project_name, current_draft_id, "
                    "current_draft_version, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                    rows,
                )
                print(f"[init_db] projects 表已按模型重建（移除旧 name 列），迁移 {len(rows)} 行")

            # 旧库 presentations 用旧列名 filename/format/path（模型已改名 file_name/file_format/file_path），
            # SQLite 去不掉 NOT NULL 旧列，须按当前模型重建该表
            existing = _column_names(conn, "presentations")
            if "filename" in existing:
                from sqlalchemy.schema import CreateTable
                from ..models import Presentation
                ddl = str(CreateTable(Presentation.__table__).compile(ENGINE)).strip() + ";"
                rows = conn.execute(
                    "SELECT id, presentation_id, project_id, task_id, filename, format, path, created_at "
                    "FROM presentations"
                ).fetchall()
                conn.execute("DROP TABLE presentations")
                conn.execute(ddl)
                conn.executemany(
                    "INSERT INTO presentations (id, presentation_id, project_id, task_id, "
                    "file_name, file_format, file_path, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    rows,
                )
                print(f"[init_db] presentations 表已按模型重建（旧列 filename/format/path → file_name/file_format/file_path），迁移 {len(rows)} 行")
            conn.commit()
