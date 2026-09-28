"""调度循环与并发闸的回归（ADR-019）。

钉住的全是"只有并发才会露"的行为，逐条对应设计里的互锁：

1. 认领是 CAS：**同一行只可能被一个认领者抢到**，否则就是同时跑两份 claude、双份烧额度。
2. 并发闸口径是 DB 里 running 的行数，不是内存计数器 —— 跨重启可见、不会漏放。
3. fork 成功但提升 CAS 输掉 = 撤销赢了：必须**回收刚起的子进程**，否则"库里已
   cancelled、claude 还在跑"（本设计最容易写错的一处）。
4. running 任务的撤销绑在引擎 `cancel` 钩子上：没实现就 409，**绝不静默假撤销**。
5. 陈旧认领按证据收尸：有执行证据 → 提升接管；无证据 → 清占位重新排队。
6. guizang 的 supervisor 看到 cancel_requested 必须**不再 --resume**，否则撤销完
   额度照烧。

不起真引擎、不拉真子进程：假引擎 + 临时 SQLite + 临时 jobs 根。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
_PACKAGES = Path(__file__).resolve().parents[2]
for p in (str(_BACKEND_SRC), str(_PACKAGES)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.core.db import JOBS_ROOT  # noqa: E402,F401  (仅确保模块可导入)
from app.core.schemas import DeckRequest  # noqa: E402
from app.models import Base, Job, User, make_engine, make_session_factory  # noqa: E402
from app.repository.jobs import JobRepo  # noqa: E402
from app.service.jobs import JobService  # noqa: E402
from app.service.scheduler import Scheduler  # noqa: E402
from engine.core.models import EngineJobStatus  # noqa: E402


def _now():
    return datetime.now(timezone.utc).isoformat()


class FakeEngine:
    """记录每次收到的提交；status 由外部脚本决定；cancel 记名字并返回可控结果。"""

    def __init__(self, status_value="running", cancel_ok=True, raise_on_submit=None):
        self.submitted: list[dict] = []
        self.cancelled: list[str] = []
        self.status_value = status_value
        self.cancel_ok = cancel_ok
        self.raise_on_submit = raise_on_submit

    def submit(self, content=None, title=None, pages=None, engine_params=None,
               sources=None, job_id=None, engine_type=None):
        if self.raise_on_submit:
            raise self.raise_on_submit
        self.submitted.append(dict(job_id=job_id, content=content, sources=sources,
                                   engine_params=engine_params, engine_type=engine_type))
        return job_id

    def status(self, job_id):
        return EngineJobStatus(self.status_value)

    def result(self, job_id):
        return f"/tmp/{job_id}.pptx"

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        return self.cancel_ok

    @property
    def submitted_ids(self):
        return sorted(s["job_id"] for s in self.submitted)


class NoCancelEngine(FakeEngine):
    """显式退出 cancel 钩子的引擎（与草稿引擎 `cancel = None` 同形态）。"""
    cancel = None


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'sched.db'}")
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()
    yield session
    session.close()


@pytest.fixture
def jobs_root(tmp_path, monkeypatch):
    """调度器读 job.json 走 app.core.db.JOBS_ROOT（函数内取模块属性），所以打这个。"""
    import app.core.db as app_db

    root = tmp_path / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_db, "JOBS_ROOT", root)
    return root


def _mk(db, job_id, status="queued", *, claimed_by=None, claimed_at=None,
        task_type="output", engine="fake", created_at=None, **kw):
    JobRepo().create(
        db, job_id=job_id, project_id="p1", task_type=task_type, status=status,
        engine=engine, agent="claude", content_style="marketing_promo",
        engine_params={}, content="# 草稿", created_at=created_at or _now(),
        updated_at=_now(), claimed_by=claimed_by, claimed_at=claimed_at, **kw)
    return job_id


def _sched(*engines, service=None, max_concurrent=5, db=None, timeout=3600):
    """造调度器：engine_factory 按提交顺序轮换引擎（用于"两个认领者抢同一行"那类用例）。"""
    it = iter(engines)
    last = engines[-1]
    svc = service or JobService(JobRepo(), engine_factory=lambda name: last)
    return Scheduler(
        service=svc, max_concurrent=max_concurrent, timeout_seconds=timeout,
        engine_factory=lambda name: next(it, last),
        session_factory=lambda: db)


# ───────────────────────── 1. 认领的原子性 ─────────────────────────

def test_两个调度器抢同一行只有一个成功(db):
    """CAS 认领：两拍并发打同一队列，那一条只会被提交一次。

    这是"多副本不会重复执行同一任务"的机制保证 —— 少了它，两个调度器会各自 fork
    一条 claude，用户为同一份 PPT 付两次钱。
    """
    _mk(db, "solo")
    e1, e2 = FakeEngine(), FakeEngine()
    _sched(e1, db=db).sweep()
    _sched(e2, db=db).sweep()

    assert e1.submitted_ids == ["solo"], e1.submitted
    assert e2.submitted_ids == [], "第二个调度器不该也抢到这一行"
    assert JobRepo().get(db, "solo")["status"] == "running"


def test_认领按创建时间严格FIFO(db):
    _mk(db, "later", created_at="2026-01-02T00:00:00+00:00")
    _mk(db, "earlier", created_at="2026-01-01T00:00:00+00:00")
    eng = FakeEngine()
    _sched(eng, max_concurrent=1, db=db).sweep()

    assert eng.submitted_ids == ["earlier"], "先提交的先走"
    assert JobRepo().get(db, "later")["status"] == "queued"


# ───────────────────────── 2. 并发闸 ─────────────────────────

def test_闸门口径是DB里running的行数(db):
    """3 条排队、闸给 2 → 一拍只放行 2；再跑一拍不放行；腾出一个位置才补上。"""
    for i in range(3):
        _mk(db, f"j{i}")
    gate = FakeEngine()          # status 恒为 running
    _sched(gate, max_concurrent=2, db=db).sweep()
    assert len(gate.submitted) == 2, "闸门只放 2 条"
    assert JobRepo().count_by_status(db, "running") == 2

    _sched(gate, max_concurrent=2, db=db).sweep()   # 第二拍：没有空位
    assert len(gate.submitted) == 2, "两条还在跑，不该再放"

    # 让一条完成（换引擎：status 报 completed）→ 下一拍应补位
    done = FakeEngine(status_value="completed")
    _sched(done, max_concurrent=2, db=db).sweep()
    assert len(done.submitted) == 1, "腾出的槽位被队头那条补上"
    # 这一拍里两条 running 被判完成、腾出的位子又被第三条占上 → 净剩 1 条在跑
    assert JobRepo().count_by_status(db, "running") == 1
    assert JobRepo().count_by_status(db, "completed") == 2


def test_queued不算进运行中但算进未结束(db):
    """`running_task_count` 语义 = 未结束（queued + running）。

    只数 running 的话，项目卡上会出现"看着 0 个在跑、其实 3 个在排队"的假象
    （ADR-018 消费侧口径）。
    """
    from app.models import Project

    db.add(Project(project_id="p1", project_name="P", created_at=_now(), updated_at=_now(),
                   owner_user_id=None))
    db.commit()
    _mk(db, "q1", status="queued")
    _mk(db, "r1", status="running")
    from app.repository.jobs import ProjectRepo

    stats = ProjectRepo().stats(db, ["p1"])
    assert stats["p1"]["running_task_count"] == 2


# ───────────────── 3. fork 失败 / CAS 输掉的回收 ─────────────────

def test_引擎提交抛异常判failed并交出槽位(db):
    boom = FakeEngine(raise_on_submit=RuntimeError("claude CLI 缺失"))
    _mk(db, "bad")
    _sched(boom, db=db).sweep()

    row = JobRepo().get(db, "bad")
    assert row["status"] == "failed"
    assert "claude CLI 缺失" in row["error"]
    assert row["claimed_by"] is None, "失败后不能把槽位焊死，否则这条永远卡住"


def test_提升CAS输掉时必须回收刚fork的进程(db):
    """最容易写错的一条：fork 成功的空档里用户撤销了 → 落库 CAS 会输。

    如果此时不杀子进程，就是"库里已 cancelled、claude 还在跑还在烧额度"的新僵尸。
    """
    eng = FakeEngine()
    _mk(db, "race")

    real_promote = JobRepo.promote_to_running

    def promote_loses(self, session, job_id, claimant):
        session.query(Job).filter(Job.job_id == job_id).update({"status": "cancelled"})
        session.commit()
        return real_promote(self, session, job_id, claimant)   # 恒 False：状态已不是 queued

    monkey_patch = pytest.MonkeyPatch()
    monkey_patch.setattr(JobRepo, "promote_to_running", promote_loses)
    try:
        _sched(eng, db=db).sweep()
    finally:
        monkey_patch.undo()

    assert eng.submitted_ids == ["race"], "引擎确实被调用过（fork 发生了）"
    assert eng.cancelled == ["race"], "CAS 输掉后必须把刚起的进程收掉"
    assert JobRepo().get(db, "race")["status"] == "cancelled"


# ───────────────────────── 4. 撤销 ─────────────────────────

def test_撤销排队中的任务直接摘队(db):
    _mk(db, "q")
    svc = JobService(JobRepo(), engine_factory=lambda name: FakeEngine())
    out = svc.cancel(db, "q")

    assert out.status == "cancelled"
    assert JobRepo().get(db, "q")["claimed_by"] is None
    # 摘掉之后调度器再也看不到它
    eng = FakeEngine()
    _sched(eng, db=db).sweep()
    assert eng.submitted == []


def test_运行中任务无cancel能力则409且不假撤销(db):
    """契约硬约束：**绝不静默返回"已撤销"而让子进程继续跑**。

    草稿类引擎显式 `cancel = None`，所以只能撤排队中的；运行中的撤不了、如实报错。
    """
    from fastapi import HTTPException

    _mk(db, "run", status="running")
    svc = JobService(JobRepo(), engine_factory=lambda name: NoCancelEngine())
    with pytest.raises(HTTPException) as exc:
        svc.cancel(db, "run")

    assert exc.value.status_code == 409
    assert JobRepo().get(db, "run")["status"] == "running", "撤销没成就是没成，不许改库"


def test_杀进程失败时不落cancelled(db):
    _mk(db, "run2", status="running")
    stubborn = FakeEngine(cancel_ok=False)
    svc = JobService(JobRepo(), engine_factory=lambda name: stubborn)
    with pytest.raises(Exception) as exc:
        svc.cancel(db, "run2")

    assert exc.value.status_code == 409
    assert stubborn.cancelled == ["run2"], "确实尝试过终止"
    assert JobRepo().get(db, "run2")["status"] == "running"


def test_运行中任务有cancel能力则真杀并落cancelled(db):
    _mk(db, "run3", status="running")
    eng = FakeEngine()
    svc = JobService(JobRepo(), engine_factory=lambda name: eng)
    out = svc.cancel(db, "run3")

    assert eng.cancelled == ["run3"]
    assert out.status == "cancelled"
    assert out.token_usage is None       # 未上报就是未记录，不冒领也不抹掉（ADR-016）


def test_终态任务不可撤销(db):
    from fastapi import HTTPException

    _mk(db, "done", status="completed")
    svc = JobService(JobRepo(), engine_factory=lambda name: FakeEngine())
    with pytest.raises(HTTPException) as exc:
        svc.cancel(db, "done")
    assert exc.value.status_code == 409


# ───────────────── 5. 崩溃收尸（重启自愈的机制面） ─────────────────

def test_陈旧认领无证据则重新排队(db, jobs_root):
    _mk(db, "stale", claimed_by="dead-host:1",
        claimed_at=(datetime.now(timezone.utc)
                    - timedelta(seconds=JobRepo.CLAIM_STALE_SECONDS + 10)).isoformat())
    eng = FakeEngine()
    stats = _sched(eng, db=db).sweep()

    assert stats["reaped"] == 1, "陈旧占位要被收回"
    # 收尸之后同一拍就该正常认领它（不是丢回队尾等下一轮），且只提交了一次
    assert eng.submitted_ids == ["stale"]
    row = JobRepo().get(db, "stale")
    assert row["status"] == "running"
    assert row["claimed_by"] != "dead-host:1", "占位者已换成新的认领者"


def test_陈旧认领有执行证据则提升接管(db, jobs_root):
    """上一个认领者崩了，但 job.json 里留着 result_path → fork 真发生过 → 接管。"""
    (jobs_root / "orphan").mkdir(parents=True, exist_ok=True)
    (jobs_root / "orphan" / "job.json").write_text(
        json.dumps({"pid": 1, "result_path": str(jobs_root / "orphan" / "x.pptx")}),
        encoding="utf-8")
    _mk(db, "orphan", claimed_by="dead-host:2",
        claimed_at=(datetime.now(timezone.utc)
                    - timedelta(seconds=JobRepo.CLAIM_STALE_SECONDS + 10)).isoformat())

    eng = FakeEngine()
    stats = _sched(eng, db=db).sweep()

    assert stats["reaped"] == 1
    assert JobRepo().get(db, "orphan")["status"] == "running"
    assert eng.submitted == [], "接管 ≠ 重新提交：绝不能再 fork 一份"


def test_引擎返回未知状态时按failed兜底(db, monkeypatch):
    """映射表与引擎契约脱节时不能崩，也不能永远挂着。"""
    _mk(db, "weird", status="running")
    eng = FakeEngine(status_value="completed")   # 引擎报了终态，但映射表被掏空
    monkeypatch.setattr("app.service.scheduler._ENGINE_TO_TASK_STATUS", {})
    _sched(eng, db=db).sweep()

    row = JobRepo().get(db, "weird")
    assert row["status"] == "failed"


# ───────────────── 6. 排队位序与粗闸 ─────────────────

def test_queue_position只数未认领的队头(db):
    _mk(db, "a", created_at="2026-01-01T00:00:00+00:00")
    _mk(db, "b", created_at="2026-01-02T00:00:00+00:00")
    _mk(db, "c", created_at="2026-01-03T00:00:00+00:00")
    repo = JobRepo()
    assert repo.queue_position(db, "a") == 0
    assert repo.queue_position(db, "c") == 2
    # b 已被占位（轮到它了）→ 不再计入别人前面的排队数
    db.query(Job).filter(Job.job_id == "b").update({"claimed_by": "h:1"})
    db.commit()
    assert repo.queue_position(db, "c") == 1
    assert repo.queue_position(db, "a") == 0
    # 非 queued 一律 None
    assert repo.queue_position(db, "nope") is None


def test_排队状态明细现算带位序(db):
    """queued 行的 status_detail 出参时现算成排队文案（用户点名：不给静态"排队中"）。

    钉三件事：① 位序被写进明细；② 这是读取时快照、**不回写 DB**（原位序会过期）；
    ③ 只有 queued 被改，running/终态行的明细不受这条路径影响。
    """
    _mk(db, "a", created_at="2026-01-01T00:00:00+00:00")
    _mk(db, "b", created_at="2026-01-02T00:00:00+00:00")
    _mk(db, "c", created_at="2026-01-03T00:00:00+00:00")
    _mk(db, "run", status="running")
    eng = FakeEngine()
    svc = JobService(JobRepo(), engine_factory=lambda name: eng)

    assert svc.get(db, "c").status_detail == "排队中 · 前方还有 2 个任务"
    # 队首不说"前方还有 0 个"，说"即将开始"
    assert svc.get(db, "a").status_detail == "排队中 · 即将开始"

    # 派生值不污染存储：读取后 DB 里仍是静态原值（None），位序不进库
    assert JobRepo().get(db, "c")["status_detail"] is None

    # list 路径同样补齐，且不动 running 行的明细
    listed = {j.job_id: j for j in svc.list(db, limit=50)}
    assert listed["b"].status_detail == "排队中 · 前方还有 1 个任务"
    assert listed["run"].status_detail is None


def test_重试探针保留重试语义(db):
    """重试行的落库明细带"（重试）"标记，现算后必须保留，不能被排队文案冲掉。"""
    _mk(db, "rt", status_detail="排队中（重试）")
    svc = JobService(JobRepo(), engine_factory=lambda name: FakeEngine())
    out = svc.get(db, "rt")
    assert out.status_detail == "排队中（重试） · 即将开始"


def test_单身份未完成任务达上限时提交被拒(db):
    """per-identity 粗闸（ADR-019 决策 7）：取代加权调度，防止一个人把队排到 50 个。"""
    from fastapi import HTTPException

    from app.core.auth import Identity

    # 不用手建项目：submit 会按身份自动建项目并打 owner 戳，粗闸正是沿 projects 归属计数
    user = User(user_id="u1", username="u1", nickname="u", role="vip", status="enabled",
                password_hash="x", created_at=_now(), updated_at=_now())
    db.add(user)
    db.commit()

    eng = FakeEngine()
    svc = JobService(JobRepo(), engine_factory=lambda name: eng)
    identity = Identity(user=user, visitor_id=None)
    req = DeckRequest(engine="fake", engine_type="output", content="# c")

    import app.service.jobs as sj

    # 上限设 2（真实默认 10，用例里没必要造十条）。
    # 桩打在 service.jobs 里那个**已绑定的名字**上：模块顶部是
    # `from ..core import runtime_settings`，再去 patch core.config 的原函数对本次调用无效。
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sj.runtime_settings, "max_unfinished_per_identity", lambda: 2)
    try:
        first = svc.submit(db, req, identity)
        second = svc.submit(db, req, identity)
        with pytest.raises(HTTPException) as exc:
            svc.submit(db, req, identity)
    finally:
        monkey.undo()

    assert first.status == "queued" and second.status == "queued"
    assert exc.value.status_code == 409
    assert "上限" in exc.value.detail


def test_调度器异常不带走循环(db, monkeypatch):
    """单拍里抛异常只影响这一拍：整个循环绝不能死（死了就是全站任务卡死）。"""
    _mk(db, "boom")
    eng = FakeEngine()
    sched = _sched(eng, db=db)
    monkeypatch.setattr(sched, "_claim_queued",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("炸了")))
    sched.interval = 0.01
    sched.start()
    try:
        import time as _t
        _t.sleep(0.2)
        assert sched._thread.is_alive(), "单拍异常后线程必须还活着"
    finally:
        sched.stop()
    assert sched._thread is None
