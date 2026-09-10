"""FastAPI 入口：装配路由、CORS、数据库。"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .api.agent import router as agent_router
from .core.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # 启动时建表
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
