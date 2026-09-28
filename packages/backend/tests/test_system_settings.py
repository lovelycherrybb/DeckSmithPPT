"""系统设置的存取、校验与热生效回归（ADR-020）。

钉住的都是"只有真去读生效值才会露"的行为，逐条对应设计决策：

1. 无覆盖时生效值 = env 默认；有覆盖时 = 覆盖值；提交 null 恢复默认（决策 1）。
2. 校验失败**整次 400 且一个都不落库** —— 不做部分成功（决策 6）。未知键也 400。
3. 调度器**每拍**重读闸门：管理员在设置页改完，下一拍放行数量就变（决策 2）。
   这条是"改了不用重启容器"的实质证明，不是注释里的承诺。
4. secret 项明文不出接口：只回 configured/masked，整份响应里搜不到明文（决策 5）。
5. 表不存在 / 覆盖值脏 → 退回默认并告警，绝不把调度循环带崩。
6. 后端返回的字段与分组必须在契约声明的枚举内 —— 防"代码偷偷长出一个契约没有的面"。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
_PACKAGES = Path(__file__).resolve().parents[2]
_REPO = Path(__file__).resolve().parents[3]
for p in (str(_BACKEND_SRC), str(_PACKAGES)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.core import runtime_settings, settings_catalog  # noqa: E402
from app.core.settings_catalog import BY_KEY, FIELDS, validate  # noqa: E402
from app.models import Base, User, make_engine, make_session_factory  # noqa: E402
from app.repository.settings import SystemSettingRepo  # noqa: E402
from app.service.jobs import JobRepo  # noqa: E402
from app.service.settings import SystemSettingsService  # noqa: E402


def _now():
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def db(tmp_path, monkeypatch):
    """临时库 + 把 app.core.db.SessionLocal 也指过来。

    必须连 SessionLocal 一起换：生效值是 `runtime_settings` 自己开会话去读表的，
    只换测试用的 session 会让断言打到真·开发库上，热生效那几条就成了假绿。
    这里给工厂（每次新建会话）而不是同一个会话对象——runtime_settings 用完会 close。
    """
    import app.core.db as app_db

    engine = make_engine(f"sqlite:///{tmp_path / 'settings.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(app_db, "SessionLocal", make_session_factory(engine))
    session = make_session_factory(engine)()
    yield session
    session.close()


@pytest.fixture
def svc():
    return SystemSettingsService()


def _field(view, key):
    return next(f for f in view["fields"] if f["key"] == key)


# ───────────────── 1. 默认与覆盖 ─────────────────

def test_无覆盖时生效值等于默认(db, svc, monkeypatch):
    monkeypatch.setenv("DECKSMITH_MAX_CONCURRENT_JOBS", "3")
    view = svc.view(db)
    f = _field(view, "max_concurrent_jobs")
    assert f["value"] == 3 and f["default"] == 3
    assert f["overridden"] is False and f["source"] == "default"


def test_覆盖后生效值取覆盖(db, svc):
    out = svc.save(db, {"max_concurrent_jobs": 7}, "admin-1")
    f = _field(out, "max_concurrent_jobs")
    assert f["value"] == 7 and f["overridden"] is True and f["source"] == "override"
    assert runtime_settings.max_concurrent_jobs() == 7


def test_提交null恢复默认(db, svc):
    svc.save(db, {"max_concurrent_jobs": 9}, "admin-1")
    out = svc.save(db, {"max_concurrent_jobs": None}, "admin-1")
    f = _field(out, "max_concurrent_jobs")
    assert f["overridden"] is False
    assert SystemSettingRepo().get(db, "max_concurrent_jobs") is None


def test_部分提交不动其他键(db, svc):
    svc.save(db, {"max_concurrent_jobs": 5}, "admin-1")
    out = svc.save(db, {"job_timeout_seconds": 7200}, "admin-1")
    assert _field(out, "max_concurrent_jobs")["value"] == 5
    assert _field(out, "job_timeout_seconds")["value"] == 7200


# ───────────────── 2. 校验：整次失败，不做部分成功 ─────────────────

def test_越界整次400且一个都不落库(db, svc):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        svc.save(db, {"max_concurrent_jobs": 2, "job_timeout_seconds": 999999}, "admin-1")
    assert exc.value.status_code == 400
    assert "单任务超时" in exc.value.detail
    # 合法的那个也**不该**被写进去
    assert SystemSettingRepo().get(db, "max_concurrent_jobs") is None


def test_未知键被拒(db, svc):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        svc.save(db, {"max_concurrent_job": 3}, "admin-1")   # 少一个 s，拼错
    assert exc.value.status_code == 400
    assert "未知设置项" in exc.value.detail


def test_空提交被拒(db, svc):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        svc.save(db, {}, "admin-1")


def test_类型与边界校验():
    f = BY_KEY["max_concurrent_jobs"]
    assert validate(f, 0)[0] is False and validate(f, 1)[0] is True
    assert validate(f, 33)[0] is False
    assert validate(f, "5")[0] is True and validate(f, "5")[1] == 5
    assert validate(f, True)[0] is False          # bool 不是 int
    assert validate(f, "abc")[0] is False
    s = BY_KEY["scheduler_interval_seconds"]
    assert validate(s, 0.5)[0] is False           # 非整数
    assert validate(s, 1)[0] is True


# ───────────────── 3. 热生效：调度器每拍重读 ─────────────────

def test_调度器每拍读生效值(db, svc, monkeypatch):
    """改完设置、不重启进程，下一拍的放行数量就跟着变（ADR-020 决策 2 的实质证明）。"""
    from app.service.scheduler import Scheduler

    class Eng:
        def __init__(self):
            self.submitted = []

        def submit(self, content=None, title=None, pages=None, engine_params=None,
                   sources=None, job_id=None, engine_type=None):
            self.submitted.append(job_id); return job_id

        def status(self, job_id):
            from engine.core.models import EngineJobStatus
            return EngineJobStatus.RUNNING

    for i in range(10):
        JobRepo().create(
            db, job_id=f"s{i}", project_id="p1", task_type="output", status="queued",
            engine="fake", agent="claude", content_style="marketing_promo",
            engine_params={}, content="c", created_at=_now(), updated_at=_now())

    svc.save(db, {"max_concurrent_jobs": 2}, "admin-1")
    eng = Eng()
    sched = Scheduler(service=None, engine_factory=lambda name: eng, session_factory=lambda: db)
    assert sched.max_concurrent == 2          # 没传覆盖值 → 读生效值
    sched.sweep()
    assert len(eng.submitted) == 2

    svc.save(db, {"max_concurrent_jobs": 5}, "admin-1")
    assert sched.max_concurrent == 5          # 同一个实例，下一拍就变了
    sched.sweep()
    # 再放行的是 3 条而不是 5 条：闸门口径是"在跑的行数"，前两拍那 2 条还占着位置。
    # 这个差值恰好同时证明了热生效与闸门口径两件事。
    assert len(eng.submitted) == 5, eng.submitted


def test_构造时显式给值则钉死(db, svc):
    """测试与特殊部署可以钉死闸门，不受设置页影响。"""
    from app.service.scheduler import Scheduler
    svc.save(db, {"max_concurrent_jobs": 8}, "admin-1")
    sched = Scheduler(session_factory=lambda: db, max_concurrent=1)
    assert sched.max_concurrent == 1
    sched.max_concurrent = None               # 交还给生效值
    assert sched.max_concurrent == 8


# ───────────────── 4. 容错 ─────────────────

def test_脏覆盖值退回默认(db, monkeypatch):
    monkeypatch.setenv("DECKSMITH_MAX_CONCURRENT_JOBS", "3")
    SystemSettingRepo().upsert(db, "max_concurrent_jobs", 99999, "x")   # 越界的存量脏值
    assert runtime_settings.max_concurrent_jobs() == 3                   # 按默认跑，不炸


def test_表不存在时按默认跑(monkeypatch):
    """全新库还没建表 / 库被锁住时，设置面坏掉不该把调度一起拖死。"""
    import app.repository.settings as repo_mod
    from app.core import db as app_db

    def boom(*a, **k):
        raise RuntimeError("no such table: system_settings")

    monkeypatch.setattr(repo_mod.SystemSettingRepo, "load_all", boom)
    monkeypatch.setenv("DECKSMITH_MAX_CONCURRENT_JOBS", "4")
    assert runtime_settings.max_concurrent_jobs() == 4


# ───────────────── 5. secret 掩码 ─────────────────

def test_secret项明文不出接口(db, monkeypatch):
    """本期没有 secret 设置项，所以临时注入一个来验机制（不造假的 agent 配置项）。"""
    secret_field = settings_catalog.SettingField(
        key="_test_token", group="scheduler", label="测试令牌", type="secret",
        description="仅测试", default=lambda: None)
    monkeypatch.setattr(settings_catalog, "FIELDS", FIELDS + (secret_field,))
    monkeypatch.setattr(settings_catalog, "BY_KEY", {**BY_KEY, "_test_token": secret_field})
    # service 与 runtime_settings 都在模块顶部 `from ... import BY_KEY`，
    # 绑的是各自命名空间里的名字 —— 只改 settings_catalog.BY_KEY 对它们无效。
    # （这正是本仓库反复踩过的"改原函数不影响已绑定引用"，测试要按同一套规矩打桩。）
    import app.service.settings as svc_mod
    monkeypatch.setattr(svc_mod, "BY_KEY", settings_catalog.BY_KEY)
    monkeypatch.setattr(svc_mod, "FIELDS", settings_catalog.FIELDS)   # view() 遍历的是它
    monkeypatch.setattr(runtime_settings, "BY_KEY", settings_catalog.BY_KEY)

    svc = SystemSettingsService()
    out = svc.save(db, {"_test_token": "sk-proj-abcdef123456"}, "admin-1")
    f = _field(out, "_test_token")
    assert f["value"] is None
    assert f["configured"] is True
    assert f["masked"] == "sk-…3456"
    assert "sk-proj-abcdef123456" not in json.dumps(out, ensure_ascii=False), \
        "明文出现在响应里 —— 契约的硬约束被破坏"
    # 空串不算配置
    out2 = svc.save(db, {"_test_token": None}, "admin-1")
    assert _field(out2, "_test_token")["configured"] is False


def test_空secret被拒(db, monkeypatch):
    secret_field = settings_catalog.SettingField(
        key="_test_token", group="scheduler", label="测试令牌", type="secret",
        description="仅测试", default=lambda: None)
    monkeypatch.setattr(settings_catalog, "BY_KEY", {**BY_KEY, "_test_token": secret_field})
    from fastapi import HTTPException

    svc = SystemSettingsService()
    with pytest.raises(HTTPException):
        svc.save(db, {"_test_token": "   "}, "admin-1")


# ───────────────── 6. 与契约对齐 ─────────────────

def test_env越界的默认值被夹回注册表边界(monkeypatch):
    """真浏览器复验抓到的：env 写 500 而注册表上限 200，设置页就会显示一个填不出来的值。

    边界只能有注册表一个真源，否则"生效值 500、输入框最大 200"，管理员一碰这个框
    反而把系统调严了。
    """
    monkeypatch.setenv("DECKSMITH_MAX_UNFINISHED_PER_IDENTITY", "500")
    f = BY_KEY["max_unfinished_per_identity"]
    assert f.max == 200
    assert f.default_value() == 200
    assert runtime_settings.max_unfinished_per_identity() == 200

    monkeypatch.setenv("DECKSMITH_MAX_CONCURRENT_JOBS", "0")
    assert BY_KEY["max_concurrent_jobs"].default_value() == 1     # 下限同理


def test_后端产出的字段与分组都在契约枚举内():
    """防"代码偷偷长出一个契约没有的面"——前端按契约渲染，多出来的字段等于没有。"""
    import dataclasses

    import yaml

    root = _REPO / "docs/contracts/openapi.yaml"
    doc = yaml.safe_load(root.read_text(encoding="utf-8"))
    schema = doc["components"]["schemas"]["SystemSettingsField"]["properties"]
    declared = set(schema.keys())

    # catalog 的每个 dataclass 字段都要被契约声明；default 是 callable，
    # 出参里对应的是它的返回值 default_value，故排除。
    for attr in (f.name for f in dataclasses.fields(next(iter(FIELDS)))):
        if attr == "default":
            continue
        assert attr in declared, f"后端吐出的字段 {attr} 契约里没有声明"

    assert {f.type for f in FIELDS} <= set(schema["type"]["enum"]), "出现了契约未声明的设置项类型"
    # group 在契约里刻意不是枚举（分组清单由接口动态下发，见 ADR-020 决策 4），
    # 那不变量就得在后端这侧守：每个字段的分组都必须真的存在于 GROUPS 里，
    # 否则前端按 groups[] 渲染分区时会把它丢进不存在的组。
    group_ids = {g["id"] for g in settings_catalog.GROUPS}
    missing = {f.group for f in FIELDS} - group_ids
    assert not missing, f"设置项挂在了未声明的分组上：{sorted(missing)}"


def test_capacity只回三个数(db, svc):
    cap = svc.capacity(db)
    assert set(cap) == {"max_concurrent_jobs", "running", "queued"}
    assert cap["running"] == 0 and cap["queued"] == 0
