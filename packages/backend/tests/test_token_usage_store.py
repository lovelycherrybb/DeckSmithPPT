"""任务 token 消耗的后端落库回归测试（ADR-016）。

钉住三件事：
1. 存储是单列 JSON 文本，create/update 两条写路径编码规则一致；NULL=未记录，
   绝不出现字符串 "null" 或 0 冒充；
2. 收割通道 `_engine_reported_usage` 只认带派生总数的完整形态，残缺对象按未记录处理；
3. 轮询的所有终态（completed/failed/timeout/超时兜底）都回填用量——失败任务烧掉的
   token 也是事实（本轮拍板：failed 也回填）。

不起真引擎、不拉子进程：JobRepo 用临时 SQLite，status() 用假引擎。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
_PACKAGES = Path(__file__).resolve().parents[2]
for p in (str(_BACKEND_SRC), str(_PACKAGES)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.core.schemas import JobOut  # noqa: E402
from app.repository.jobs import JobRepo  # noqa: E402
from app.service.jobs import JobService  # noqa: E402

_USAGE = {
    "input_tokens": 100,
    "cache_creation_input_tokens": None,
    "cache_read_input_tokens": 900,
    "output_tokens": 40,
    "total_tokens": 1040,
}


@pytest.fixture
def db(tmp_path):
    from app.models import Base, make_engine, make_session_factory

    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()
    yield session
    session.close()


def _ts():
    return "2026-09-21T12:00:00+00:00"


# ── 存储层：JSON 单列往返 ──
def test_用量经repo往返_分项null不被抹成0(db):
    JobRepo().create(
        db, job_id="j1", status="completed", engine="ppt_master", agent="claude",
        engine_params={}, token_usage=_USAGE, created_at=_ts(), updated_at=_ts(),
    )
    row = JobRepo().get(db, "j1")
    assert row["token_usage"] == _USAGE  # 读回来还是 dict，null 分项还是 None

    # 库里存的就是 JSON 文本（ADR-016 决策五：单列，不拆五列）
    raw = sqlite3.connect(str(db.bind.url.database)).execute(
        "SELECT token_usage FROM jobs WHERE job_id='j1'"
    ).fetchone()[0]
    assert json.loads(raw) == _USAGE


def test_update写路径与create编码规则一致(db):
    JobRepo().create(
        db, job_id="j2", status="running", engine="guizang_ppt", agent="claude",
        engine_params={}, created_at=_ts(), updated_at=_ts(),
    )
    assert JobRepo().get(db, "j2")["token_usage"] is None  # 未记录 = NULL，不是 "null"/0

    JobRepo().update(db, "j2", status="completed", token_usage=_USAGE)
    assert JobRepo().get(db, "j2")["token_usage"] == _USAGE

    JobRepo().update(db, "j2", token_usage=None)
    row = JobRepo().get(db, "j2")
    assert row["token_usage"] is None
    raw = sqlite3.connect(str(db.bind.url.database)).execute(
        "SELECT token_usage FROM jobs WHERE job_id='j2'"
    ).fetchone()[0]
    assert raw is None  # None 落 NULL，不被序列化成字符串 "null"


def test_旧库升级补token_usage列且历史行为null(tmp_path):
    from app.core.db import run_startup_migrations

    db_path = tmp_path / "decksmith.db"
    conn = sqlite3.connect(db_path)
    with conn:
        conn.executescript(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, project_id VARCHAR(64) UNIQUE,"
            " name VARCHAR(255) NOT NULL, created_at VARCHAR(32), updated_at VARCHAR(32));"
            "CREATE TABLE presentations (id INTEGER PRIMARY KEY, presentation_id VARCHAR(64),"
            " project_id VARCHAR(64), task_id VARCHAR(64), filename VARCHAR(255),"
            " format VARCHAR(16), path VARCHAR(512), created_at VARCHAR(32));"
            "CREATE TABLE jobs (id INTEGER PRIMARY KEY, job_id VARCHAR(64) UNIQUE,"
            " project_id VARCHAR(64), status VARCHAR(16) NOT NULL, title VARCHAR(255),"
            " pages INTEGER, engine VARCHAR(64), agent VARCHAR(32), engine_params TEXT,"
            " content TEXT, result_path VARCHAR(512), error TEXT,"
            " created_at VARCHAR(32), updated_at VARCHAR(32));"
        )
        conn.execute(
            "INSERT INTO jobs (job_id,status,engine,agent,engine_params,created_at,updated_at)"
            " VALUES ('old1','completed','ppt_master','claude','{}',?,?)", (_ts(), _ts())
        )
    conn.close()

    run_startup_migrations(db_path)

    after = sqlite3.connect(db_path).execute(
        "SELECT token_usage FROM jobs WHERE job_id='old1'"
    ).fetchone()[0]
    assert after is None  # 存量任务停在「未记录」，不回填估算（ADR-016 未采纳方案三）


# ── JobOut 出参 ──
def test_jobout缺usage为None_分项null原样出参():
    job = JobOut(job_id="j3", status="completed")
    assert job.token_usage is None

    part = JobOut(job_id="j4", status="completed", token_usage=_USAGE)
    assert part.token_usage.cache_creation_input_tokens is None
    assert part.token_usage.total_tokens == 1040
    assert part.model_dump()["token_usage"]["cache_read_input_tokens"] == 900


# ── 收割通道：job.json → dict ──
def _reported_usage(tmp_path, monkeypatch, job_json: dict | None):
    from engine.core import config as engine_config

    jobs_root = tmp_path / "jobs" / "jid"
    jobs_root.mkdir(parents=True, exist_ok=True)
    if job_json is not None:
        (jobs_root / "job.json").write_text(json.dumps(job_json), encoding="utf-8")
    monkeypatch.setattr(engine_config, "get_jobs_root", lambda: tmp_path / "jobs")
    return JobService._engine_reported_usage("jid")


def test_收割只认带派生总数的形态(tmp_path, monkeypatch):
    assert _reported_usage(tmp_path, monkeypatch, {"token_usage": _USAGE}) == _USAGE
    # 残缺对象（没算 total）按未记录处理，不补 0
    assert _reported_usage(tmp_path, monkeypatch, {"token_usage": {"input_tokens": 5}}) is None
    # 文件不存在 / 无键：都是未记录
    assert _reported_usage(tmp_path, monkeypatch, None) is None
    assert _reported_usage(tmp_path, monkeypatch, {"status": "completed"}) is None


# ── 轮询终态一律回填（completed / failed / 超时兜底）──
class _FakeEngine:
    """status() 依次吐出给定序列，吐完后停在最后一个值（轮询会多次调用）。"""

    def __init__(self, statuses):
        self._queue = list(statuses)

    def status(self, job_id):
        from engine.core.models import JobStatus

        raw = self._queue.pop(0) if self._queue else self._last
        self._last = raw
        return JobStatus(raw)

    def result(self, job_id):
        raise FileNotFoundError(job_id)

    def get_progress(self, job_id):
        return None


def _seed_running(db, job_id="pj1"):
    JobRepo().create(
        db, job_id=job_id, status="running", task_type="output", engine="guizang_ppt",
        agent="claude", engine_params={}, title="t", created_at=_ts(), updated_at=_ts(),
    )


def _make_service(monkeypatch, db, tmp_path, statuses):
    import app.service.jobs as sj

    monkeypatch.setattr(sj, "SessionLocal", lambda: db)
    monkeypatch.setattr(sj, "_DEFAULT_POLL_INTERVAL", 0)
    from engine.core import config as engine_config

    jobs_root = tmp_path / "jobs" / "pj1"
    jobs_root.mkdir(parents=True, exist_ok=True)
    (jobs_root / "job.json").write_text(json.dumps({"token_usage": _USAGE}), encoding="utf-8")
    monkeypatch.setattr(engine_config, "get_jobs_root", lambda: tmp_path / "jobs")

    svc = JobService(JobRepo(), engine_factory=lambda name: None)
    monkeypatch.setattr(svc, "_get_engine", lambda name: _FakeEngine(statuses))
    return svc


def test_failed任务也回填用量(db, tmp_path, monkeypatch):
    _seed_running(db)
    svc = _make_service(monkeypatch, db, tmp_path, ["failed"])
    svc._poll_until_done("pj1", "guizang_ppt")  # noqa: SLF001

    row = JobRepo().get(db, "pj1")
    assert row["status"] == "failed"
    assert row["token_usage"] == _USAGE  # 烧掉的 token 是事实，失败也入账


def test_completed任务收割用量(db, tmp_path, monkeypatch):
    _seed_running(db)
    svc = _make_service(monkeypatch, db, tmp_path, ["completed"])
    monkeypatch.setattr(JobService, "_persist_presentation", lambda self, row, p: None)
    svc._poll_until_done("pj1", "guizang_ppt")  # noqa: SLF001

    row = JobRepo().get(db, "pj1")
    assert row["status"] == "completed"
    assert row["token_usage"] == _USAGE


def test_轮询超时兜底也收割用量(db, tmp_path, monkeypatch):
    _seed_running(db)
    svc = _make_service(monkeypatch, db, tmp_path, ["running"])
    svc._poll_until_done("pj1", "guizang_ppt", timeout_seconds=0)  # noqa: SLF001

    row = JobRepo().get(db, "pj1")
    assert row["status"] == "timeout"
    assert row["token_usage"] == _USAGE
