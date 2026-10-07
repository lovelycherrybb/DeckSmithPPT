"""跨层共用的测试装置：把整套后端挂到一个**临时目录**上跑起来。

**为什么必须有这一层，而不是各测试文件自己拼环境变量**。`app/core/db.py` 在
**导入期**就绑定了模块级单例：

    DATABASE_URL = get_database_url()
    ENGINE = make_engine(DATABASE_URL)
    SessionLocal = make_session_factory(ENGINE)
    JOBS_ROOT = get_jobs_root()

事后 monkeypatch 只能补一部分调用点，已经绑定的 `SessionLocal` / `JOBS_ROOT` 仍指向
开发库 `outputs/decksmith.db` 与开发任务目录。调度器那段注释（ADR-019 的两个注入点）
明令禁止这个后果：跑一拍 sweep 就会真去认领开发库里的任务。

conftest 的导入时机早于任何测试模块，所以在这里设 env 是唯一可靠的位置。
pytest 的 `tests/contract` 与 `tests/e2e` 各自是独立进程（见 scripts/test.sh 的分层），
互不污染。
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ── 沙箱：库、任务目录、上传目录全在这里，退出即删 ──
_SANDBOX = Path(tempfile.mkdtemp(prefix="decksmith-pytest-"))
atexit.register(shutil.rmtree, _SANDBOX, True)

# 必须早于任何 app 模块导入 —— 这是本文件存在的唯一理由
os.environ["DATABASE_URL"] = f"sqlite:///{(_SANDBOX / 'decksmith.db').as_posix()}"
os.environ["DECKSMITH_JOBS_ROOT"] = str(_SANDBOX / "jobs")
# 节拍压到下限（1s），让 e2e 的闭环不必干等默认的 5s
os.environ["DECKSMITH_SCHEDULER_INTERVAL"] = "1"
# 离线钩子：凡注册过的真实引擎一律落到 stub。绝不起 claude 子进程 —— 不烧额度，
# 且无网的 CI 里也能跑完（factory 的判据取自注册表本身，不会漏掉新引擎）。
os.environ["DECKSMITH_FORCE_STUB"] = "1"
# 版本是"已发布安装包"的属性；测试里给一个显式值，免得 /api/version 的 null 分支
# 与"未注入"这一真实语义混在一起被误读
os.environ["DECKSMITH_VERSION"] = "0.0.0-pytest"

for _p in (REPO / "packages", REPO / "packages" / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest  # noqa: E402

SANDBOX = _SANDBOX


@pytest.fixture(scope="session")
def sandbox() -> Path:
    """本次测试跑用的临时根（库在这里、任务目录在这里）。用于断言"没写到别处"。"""
    return _SANDBOX


@pytest.fixture(scope="session")
def client():
    """带 lifespan 的测试客户端。

    用 `with TestClient(...)` 而不是裸对象：只有进上下文才会跑 lifespan，于是
    建表、管理员播种、调度线程这三件事都按**生产同一顺序**发生。不起 lifespan 的
    客户端等于测另一个系统。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def visitor(client):
    """走一遍真实的匿名入口：/api/auth/me 顺带下发访客令牌。

    之后同一 client 上的请求都带着 `ds_visitor` Cookie，新建项目的归属才打得上去，
    `_guard_job` / `_guard_project` 的归属校验也才认这条任务。
    """
    r = client.get("/api/auth/me")
    assert r.status_code == 200, f"访客身份入口失败：{r.status_code} {r.text[:200]}"
    return r.json()
