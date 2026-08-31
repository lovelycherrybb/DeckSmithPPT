"""FastAPI 入口：装配路由、CORS、数据库。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .core.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # 启动时建表
    yield


app = FastAPI(title="DeckSmithPPT API", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
