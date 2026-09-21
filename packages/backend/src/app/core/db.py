"""数据库单例：引擎、会话工厂、任务根目录（应用启动时初始化一次）。

启动迁移有三条硬约束，都是被实测逼出来的（证据见
docs/research/2026-09-16-升级数据安全与迁移原子性.md，决策见 ADR-013）：

1. **动任何 DDL 之前先把库备份出去**，落在 `data/db/backup/`，一个版本跃迁最多一份。
2. **整轮迁移跑在一个显式事务里**。SQLite 的 DDL 本身是事务性的，但 Python 的
   sqlite3 在 legacy 模式下会在非 DML 语句前隐式提交——实测「DROP 之后崩溃」的结果是
   表还在、数据 0 行。所以必须自己 `BEGIN IMMEDIATE` / `COMMIT` 把 DROP 包住。
3. **库结构版本写进 `PRAGMA user_version`**。迁移步骤本身仍按“缺什么补什么”幂等判定
   （老库可能来自任意版本），版本号负责记录现状，并在“新库 + 旧程序”的降级场景里拒动结构。
"""
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import MetaData
from sqlalchemy.schema import CreateIndex, CreateTable

from .config import get_database_url, get_jobs_root
from ..models import Base, Presentation, Project, make_engine, make_session_factory

DATABASE_URL = get_database_url()
ENGINE = make_engine(DATABASE_URL)
SessionLocal = make_session_factory(ENGINE)
JOBS_ROOT = get_jobs_root()

# 当前模型对应的库结构版本。1 = 老列名形态（projects.name / presentations.filename…），
# 2 = 现模型形态（project_name / file_name / file_format / file_path）。
# 只有改了表/列语义才升号；加可空列这类由 _MIGRATIONS 幂等兜住的改动不必升。
SCHEMA_VERSION = 2

# 备份目录与库文件同级，整包 data/ 一起搬走时不会漏
_BACKUP_SUBDIR = "backup"

# 只加列的轻量迁移（新库由 create_all 直接建全）
_MIGRATIONS = {
    "jobs": {
        "content_style": "ALTER TABLE jobs ADD COLUMN content_style VARCHAR(64) DEFAULT 'marketing_promo'",
        "task_type": "ALTER TABLE jobs ADD COLUMN task_type VARCHAR(16) DEFAULT 'draft'",
        "status_detail": "ALTER TABLE jobs ADD COLUMN status_detail TEXT",
        "engine_job_id": "ALTER TABLE jobs ADD COLUMN engine_job_id VARCHAR(128)",
        "draft_id": "ALTER TABLE jobs ADD COLUMN draft_id VARCHAR(64)",
        "draft_version": "ALTER TABLE jobs ADD COLUMN draft_version VARCHAR(32)",
        # token 消耗（JSON 文本，NULL=未记录）。可空列，按本文件规矩不升 SCHEMA_VERSION
        "token_usage": "ALTER TABLE jobs ADD COLUMN token_usage TEXT",
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

# 需要“按模型整张重建”的老表：SQLite 去不掉旧列的 NOT NULL 约束，只能搬家。
# 值为判定用的遗留列名；_REBUILD 给出目标列与 SELECT 映射（老列名 → 新列名）。
_LEGACY_TRIGGER = {"projects": "name", "presentations": "filename"}
_REBUILD = {
    "projects": {
        "model": Project,
        "cols": ("id", "project_id", "project_name", "current_draft_id",
                 "current_draft_version", "created_at", "updated_at"),
        "select": ("id", "project_id", "COALESCE(NULLIF(project_name, ''), name)",
                   "current_draft_id", "current_draft_version", "created_at", "updated_at"),
    },
    "presentations": {
        "model": Presentation,
        "cols": ("id", "presentation_id", "project_id", "task_id",
                 "file_name", "file_format", "file_path", "created_at"),
        "select": ("id", "presentation_id", "project_id", "task_id",
                   "filename", "format", "path", "created_at"),
    },
}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _needs_rebuild(conn: sqlite3.Connection, table: str) -> bool:
    """老表里是否还留着必须搬掉的旧列。"""
    legacy = _LEGACY_TRIGGER.get(table)
    if legacy is None or not _table_exists(conn, table):
        return False
    return legacy in _column_names(conn, table)


def _current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)


def _pending_ddl(conn: sqlite3.Connection) -> list[str]:
    """列出本轮要做的结构变更；空列表 = 库已是最新，既不必备份也不必迁移。"""
    pending: list[str] = []
    for table, columns in _MIGRATIONS.items():
        if not _table_exists(conn, table):
            continue  # 新库由 create_all 直接建全
        existing = _column_names(conn, table)
        pending += [f"{table}.{col}" for col in columns if col not in existing]
    for table in _LEGACY_TRIGGER:
        if _needs_rebuild(conn, table):
            pending.append(f"{table} 按模型重建（移除旧列）")
    return pending


def _backup_database(db_path: Path, from_version: int) -> Path:
    """用 SQLite 在线备份 API 拍一致性快照（比复制文件安全：WAL、中途写都不怕）。

    文件名带上「从哪个版本升上来」，于是同一个版本跃迁只会落一份；迁移中途失败后
    重启仍复用那份原始快照，不会拿已经改过的库去覆盖它。只增不删，清理交由运维按文档做。
    """
    backup_dir = db_path.parent / _BACKUP_SUBDIR
    backup_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{db_path.stem}-v{from_version}-to-v{SCHEMA_VERSION}-pre-upgrade"
    existing = sorted(backup_dir.glob(f"{prefix}*.db"))
    if existing:
        return existing[-1]

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = backup_dir / f"{prefix}-{stamp}.db"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def _rebuild_table(conn: sqlite3.Connection, table: str) -> int:
    """按当前模型重建一张表并搬数据，全程在调用方的事务里，失败即整体回滚。

    不再用原来的「DROP 完再 CREATE」：那样一旦中途崩溃，DROP 已落盘，表结构在、数据没了。
    本函数的顺序保证任何一步失败时旧表都还是原样。
    """
    spec = _REBUILD[table]
    model, cols, selects = spec["model"], spec["cols"], spec["select"]
    tmp = f"{table}__new"

    # 临时表 DDL 由模型生成（模型是唯一事实来源，不手写、不对 DDL 字符串做替换）。
    # 用独立 scratch MetaData 以免往 Base.metadata 里塞一张临时表；代价是跨表外键的
    # 目标表在 scratch 里不存在（编译时抛 NoReferencedTableError），故按真名把被引用的
    # 对手表也复制进来——从原模型的 foreign_keys 解析，不碰 SQLAlchemy 私有 API。
    scratch = MetaData()
    for fk in model.__table__.foreign_keys:
        referenced = fk.column.table
        if referenced.name not in scratch.tables:
            referenced.to_metadata(scratch)
    tmp_table = model.__table__.to_metadata(scratch, name=tmp)
    conn.execute(str(CreateTable(tmp_table).compile(ENGINE)).strip())

    old_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.execute(
        f"INSERT INTO {tmp} ({', '.join(cols)}) SELECT {', '.join(selects)} FROM {table}"
    )
    new_count = conn.execute(f"SELECT COUNT(*) FROM {tmp}").fetchone()[0]
    if new_count != old_count:
        raise RuntimeError(
            f"{table} 搬家行数不符（旧 {old_count} 行 / 新 {new_count} 行），已中止迁移"
        )

    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {tmp} RENAME TO {table}")
    # 索引随旧表被 DROP，必须在新表上重建，否则唯一约束/查询索引静默消失（旧实现漏了这步）
    for idx in model.__table__.indexes:
        conn.execute(str(CreateIndex(idx).compile(ENGINE)).strip())
    return new_count


def _apply_migrations(
    conn: sqlite3.Connection,
    fault: Callable[[str], None] | None = None,
) -> list[str]:
    """在调用方已开启的事务里执行全部结构变更，返回实际执行的步骤。

    `fault` 是故障注入点（单测用来模拟“旧表已删、新表未提交时进程被杀”），生产传 None。
    """
    applied: list[str] = []
    for table, columns in _MIGRATIONS.items():
        if not _table_exists(conn, table):
            continue
        existing = _column_names(conn, table)
        for col, ddl in columns.items():
            if col not in existing:
                conn.execute(ddl)
                applied.append(f"补列 {table}.{col}")

    # projects.project_name 兜底：旧行的 name 值搬过来
    existing = _column_names(conn, "projects") if _table_exists(conn, "projects") else set()
    if {"project_name", "name"} <= existing:
        cur = conn.execute(
            "UPDATE projects SET project_name = name "
            "WHERE project_name IS NULL OR project_name = ''"
        )
        if cur.rowcount:
            applied.append(f"回填 projects.project_name（{cur.rowcount} 行）")

    for table in ("projects", "presentations"):
        if _needs_rebuild(conn, table):
            n = _rebuild_table(conn, table)
            if fault:
                fault(f"after-drop:{table}")  # 危险时刻：旧表已没了、新表尚未提交
            applied.append(f"{table} 按模型重建，迁移 {n} 行")

    return applied


def run_startup_migrations(
    db_path: str | Path,
    fault: Callable[[str], None] | None = None,
) -> dict:
    """对一个 SQLite 库做启动迁移：备份 → 单事务迁移 → 记版本号。

    返回 {"version_from", "version_to", "pending", "applied", "backup", "downgrade"}。
    """
    db_path = Path(db_path)
    result: dict = {
        "version_from": 0, "version_to": SCHEMA_VERSION, "pending": [],
        "applied": [], "backup": None, "downgrade": False,
    }

    probe = sqlite3.connect(db_path)
    try:
        from_version = _current_version(probe)
        pending = _pending_ddl(probe)
    finally:
        probe.close()
    result["version_from"] = from_version

    if from_version > SCHEMA_VERSION:
        # 库里是更新的结构（典型场景：运维把镜像回滚到旧版本）。旧程序看不懂新列，
        # 绝不能动结构，只告警——数据仍是上一版能读的形状。
        result["downgrade"] = True
        print(f"[init_db] 库结构版本 v{from_version} 高于本程序 v{SCHEMA_VERSION}，"
              f"跳过全部迁移（疑似回滚到旧版本，请勿改结构）")
        return result

    if not pending and from_version == SCHEMA_VERSION:
        return result  # 已是最新，连备份都不做，避免每次重启堆文件

    if pending:
        backup = _backup_database(db_path, from_version)
        result["backup"] = str(backup)
        result["pending"] = pending
        print(f"[init_db] 待迁移 {len(pending)} 项，已备份到 {backup.name}")

    conn = sqlite3.connect(db_path, timeout=30)
    conn.isolation_level = None  # 事务完全由本函数掌控
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            result["applied"] = _apply_migrations(conn, fault=fault)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    finally:
        conn.close()

    for step in result["applied"]:
        print(f"[init_db] {step}")
    print(f"[init_db] 库结构 v{from_version} → v{SCHEMA_VERSION} 完成")
    return result


def _sqlite_path_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite"):
        return None
    return Path(url.replace("sqlite:///", "", 1))


def init_db() -> None:
    """建表 + 轻量迁移（幂等，可重复调用）。在应用启动时执行一次。"""
    db_file = _sqlite_path_from_url(DATABASE_URL)
    if db_file is not None:
        db_file.parent.mkdir(parents=True, exist_ok=True)

    # 新库按当前模型直接建全；老库随后由迁移补齐
    Base.metadata.create_all(bind=ENGINE)

    if db_file is None:
        return  # 非 SQLite 形态：轻量迁移不覆盖，交由 create_all / 外部迁移工具
    run_startup_migrations(db_file)
