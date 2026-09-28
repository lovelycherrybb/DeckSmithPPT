"""FastAPI 入口：装配路由、CORS、数据库。"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .api.agent import router as agent_router
from .api.auth import router as auth_router
from .api.users import router as users_router
from .api.settings import router as settings_router
from .core.bootstrap import seed_default_admin
from .core.db import SessionLocal, init_db
from .engines.runtime import report as report_runtime_readiness
from .service.jobs import JobService
from .repository.jobs import JobRepo
from .engines.factory import get_engine
from .service.scheduler import Scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # 启动时建表
    # 内置管理员播种（幂等，已存在不重置密码）。用户系统 v0.8。
    _db = SessionLocal()
    try:
        seed_default_admin(_db)
    finally:
        _db.close()
    # 引擎运行前置条件预检（ADR-012 决策 2）。复用 runtime 里那一份日志实现，
    # 不在这里再拼一套文案（否则两处口径会漂）。**故意忽略返回码**：
    # 开发环境缺 claude 时也应能打开界面与历史记录；需要"缺前置即拦停"的是容器，
    # 由 deploy/entrypoint.sh 跑同一入口并按退出码决定起不起。
    logging.getLogger("decksmith").debug("引擎前置条件预检退出码：%s", report_runtime_readiness())
    # 任务调度循环（ADR-019）：一条专职线程负责认领 queued、轮询 running、收尸与落终态。
    # 它取代了原先"每条任务在请求线程池里挂一个 _poll_until_done"的形态 —— 那会在
    # 约 36 条并发时把 anyio 的 40 个线程占满，让整个后端停止响应任何请求。
    # 状态权威在 DB，所以进程重启后第一拍就能接管/收尸，不再留下幽灵 running。
    scheduler = Scheduler(service=JobService(JobRepo(), get_engine))
    app.state.scheduler = scheduler
    scheduler.start()
    try:
        yield
    finally:
        # 关停顺序很重要：先让它**不再认领新任务**，在途的那一拍同步跑完，
        # 于是 docker stop 不会留下 fork 到一半的孤儿子进程。
        scheduler.stop()


app = FastAPI(title="DeckSmithPPT API", version="0.6.0", lifespan=lifespan)
# CORS：默认放行本地开发端口；生产用 CORS_ORIGINS 环境变量收紧（逗号分隔）
_origins_env = os.environ.get("CORS_ORIGINS", "")
if _origins_env.strip() == "*":
    _allow_origins = ["*"]
else:
    _allow_origins = [o.strip() for o in _origins_env.split(",") if o.strip()] or [
        "http://localhost:3000",
        "http://localhost:3080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3080",
    ]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(agent_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(settings_router)
