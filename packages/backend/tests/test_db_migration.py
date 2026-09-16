"""启动迁移的数据安全回归测试（ADR-013）。

钉住四个真实风险，全部来自 v0.2.x 升级盘点时的实测：

1. 旧实现「DROP 后 CREATE」不包事务，Python sqlite3 的 legacy 模式会在 DDL 前隐式提交
   ——中途崩溃的结果是**表还在、数据 0 行**（本机 3.14 与容器 3.11.2 各复现一次）。
2. 旧实现重建表时只发 CREATE TABLE，**索引随旧表被 DROP 后再没建回来**（唯一约束静默消失）。
3. 迁移改结构前没有备份，客户库一旦搬坏无从回退。
4. 镜像回滚（新库 + 旧程序）时，旧程序会照跑迁移，可能把新结构改坏。

只碰临时目录里的 SQLite 文件，不启动服务、不起子进程。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from app.core.db import SCHEMA_VERSION, run_startup_migrations  # noqa: E402

# 0.1.x 时代的库形态：projects.name / presentations.filename+format+path，jobs 少若干列
_LEGACY_DDL = """
CREATE TABLE projects (
    id INTEGER NOT NULL PRIMARY KEY,
    project_id VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    created_at VARCHAR(32) NOT NULL,
    updated_at VARCHAR(32) NOT NULL
);
CREATE TABLE presentations (
    id INTEGER NOT NULL PRIMARY KEY,
    presentation_id VARCHAR(64) NOT NULL UNIQUE,
    project_id VARCHAR(64) NOT NULL REFERENCES projects (project_id),
    task_id VARCHAR(64) NOT NULL,
    filename VARCHAR(255) NOT NULL,
    format VARCHAR(16) NOT NULL,
    path VARCHAR(512) NOT NULL,
    created_at VARCHAR(32) NOT NULL
);
CREATE TABLE jobs (
    id INTEGER NOT NULL PRIMARY KEY,
    job_id VARCHAR(64) NOT NULL UNIQUE,
    project_id VARCHAR(64) REFERENCES projects (project_id),
    status VARCHAR(16) NOT NULL,
    title VARCHAR(255),
    pages INTEGER,
    engine VARCHAR(64) NOT NULL,
    agent VARCHAR(32) NOT NULL,
    engine_params TEXT NOT NULL,
    content TEXT,
    result_path VARCHAR(512),
    error TEXT,
    created_at VARCHAR(32) NOT NULL,
    updated_at VARCHAR(32) NOT NULL
);
"""


def _make_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    with conn:
        conn.executescript(_LEGACY_DDL)
        conn.execute("INSERT INTO projects VALUES (1,'p-1','旧项目甲','2026-01-01','2026-01-02')")
        conn.execute("INSERT INTO projects VALUES (2,'p-2','旧项目乙','2026-01-01','2026-01-02')")
        conn.execute(
            "INSERT INTO presentations VALUES (1,'pres-1','p-1','t-1','路演.pptx','pptx',"
            "'/data/jobs/t-1/路演.pptx','2026-01-03')"
        )
        conn.execute(
            "INSERT INTO jobs VALUES (1,'j-1','p-1','completed','标题',3,'ppt_master','claude',"
            "'{}',NULL,'/data/jobs/j-1/result.html',NULL,'2026-01-04','2026-01-04')"
        )


def _cols(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _rows(path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _user_version(path: Path) -> int:
    return _rows(path, "PRAGMA user_version")[0][0]


def test_legacy_upgrade_keeps_rows_and_maps_renamed_columns(tmp_path):
    """升级后：行数一条不少，旧列值搬到新列名，版本号写进 user_version。"""
    db = tmp_path / "decksmith.db"
    _make_legacy_db(db)

    result = run_startup_migrations(db)

    assert result["version_to"] == SCHEMA_VERSION
    assert _user_version(db) == SCHEMA_VERSION
    # projects.name → project_name（值保住）
    assert "name" not in _cols(db, "projects")
    assert _rows(db, "SELECT project_name FROM projects ORDER BY id") == [("旧项目甲",), ("旧项目乙",)]
    # presentations.filename/format/path → file_name/file_format/file_path
    assert {"file_name", "file_format", "file_path"} <= _cols(db, "presentations")
    assert _rows(db, "SELECT file_name, file_format, file_path FROM presentations") == [
        ("路演.pptx", "pptx", "/data/jobs/t-1/路演.pptx")
    ]
    # 加列走默认值，历史 job 不因缺列而丢
    assert _rows(db, "SELECT task_type, content_style FROM jobs") == [("draft", "marketing_promo")]


def test_rebuilt_table_keeps_indexes(tmp_path):
    """重建表后索引必须回来——旧实现只发 CREATE TABLE，唯一索引随 DROP 一起丢了。"""
    db = tmp_path / "decksmith.db"
    _make_legacy_db(db)

    run_startup_migrations(db)

    idx = _rows(db, "PRAGMA index_list('projects')")
    assert idx, "projects 上没有任何索引，说明唯一约束/查询索引丢了"
    assert any(row[2] for row in idx), f"缺唯一索引：{idx}"


def test_backup_written_once_before_migration(tmp_path):
    """迁移前先备份；同一版本跃迁只留一份，重复运行不堆文件。"""
    db = tmp_path / "decksmith.db"
    _make_legacy_db(db)

    first = run_startup_migrations(db)
    assert first["backup"] and Path(first["backup"]).exists()
    # 备份是升级**前**的原貌（还带着旧列 name）
    assert "name" in _cols(Path(first["backup"]), "projects")

    run_startup_migrations(db)  # 第二次：已最新，不产新备份
    backups = list((tmp_path / "backup").glob("*.db"))
    assert len(backups) == 1, f"备份堆叠了 {len(backups)} 份"


def test_crash_after_drop_leaves_db_untouched(tmp_path):
    """危险时刻注入：旧表已 DROP、新表未提交时崩溃 → 整轮回滚，数据一条不丢。"""
    db = tmp_path / "decksmith.db"
    _make_legacy_db(db)

    def boom(step: str) -> None:
        if step == "after-drop:projects":
            raise RuntimeError("模拟断电/被杀")

    with pytest.raises(RuntimeError):
        run_startup_migrations(db, fault=boom)

    # 旧表原样还在（含旧列与两行数据），没有半成品临时表，版本号未被推进
    assert "name" in _cols(db, "projects")
    assert _rows(db, "SELECT COUNT(*) FROM projects")[0][0] == 2
    assert _user_version(db) == 0
    assert _rows(db, "SELECT name FROM sqlite_master WHERE name LIKE '%__new'") == []
    # 崩了以后仍能正常升上去（备份沿用第一份原始快照）
    ok = run_startup_migrations(db)
    assert _rows(db, "SELECT COUNT(*) FROM projects")[0][0] == 2
    assert _user_version(db) == SCHEMA_VERSION
    assert ok["backup"] and Path(ok["backup"]).exists()


def test_second_run_is_noop(tmp_path):
    db = tmp_path / "decksmith.db"
    _make_legacy_db(db)
    run_startup_migrations(db)

    again = run_startup_migrations(db)

    assert again["applied"] == []
    assert again["pending"] == []
    assert again["backup"] is None


def test_fresh_empty_db_sets_version_without_backup(tmp_path):
    """全新库（只有空文件）不该产生备份：没有东西可丢。"""
    db = tmp_path / "decksmith.db"
    sqlite3.connect(db).close()

    result = run_startup_migrations(db)

    assert result["backup"] is None
    assert _user_version(db) == SCHEMA_VERSION
    assert not (tmp_path / "backup").exists()


def test_newer_db_than_program_is_left_alone(tmp_path):
    """回滚场景：库版本比程序新时绝不动结构，只告警。"""
    db = tmp_path / "decksmith.db"
    _make_legacy_db(db)
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("PRAGMA user_version = 99")
    conn.close()

    result = run_startup_migrations(db)

    assert result["downgrade"] is True
    assert result["applied"] == []
    assert "name" in _cols(db, "projects")  # 结构未被改动
    assert _user_version(db) == 99
    assert not (tmp_path / "backup").exists()
