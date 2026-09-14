"""FastAPI 入口：装配路由、CORS、数据库。"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .api.agent import router as agent_router
from .core.db import init_db
from .engines.runtime import report as report_runtime_readiness


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # 启动时建表
    # 引擎运行前置条件预检（ADR-012 决策 2）。复用 runtime 里那一份日志实现，
    # 不在这里再拼一套文案（否则两处口径会漂）。**故意忽略返回码**：
    # 开发环境缺 claude 时也应能打开界面与历史记录；需要"缺前置即拦停"的是容器，
    # 由 deploy/entrypoint.sh 跑同一入口并按退出码决定起不起。
    logging.getLogger("decksmith").debug("引擎前置条件预检退出码：%s", report_runtime_readiness())
    yield


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
