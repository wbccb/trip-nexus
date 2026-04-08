import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.auth.middleware import init_auth_tables
from src.api.routes import auth, admin, health, session, chat, flow, trip, knowledge, map
from src.api.dependencies import _get_storage

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 生命周期管理：启动时初始化数据库，关闭时清理资源。
    """
    logger.info("TripNexus API 正在启动...")
    # 初始化认证相关的数据库表，保证登录注册与管理员链路可直接使用。
    init_auth_tables()
    # 初始化会话/聊天/实体/摘要/行程等业务存储，确保生产环境首启时自动建表。
    _get_storage()
    yield
    logger.info("TripNexus API 正在关闭...")

# 创建 FastAPI 应用实例
app = FastAPI(
    title="TripNexus API",
    description="旅游路线规划助手核心接口，支持多轮对话、RAG 增强与 Agent 深度编排。",
    version="0.0.7",
    lifespan=lifespan,
)

import os

# 配置 CORS 跨域
cors_origins_env = os.getenv("CORS_ORIGINS", "http://localhost:5173")
cors_origins = [
    item.strip()
    for item in cors_origins_env.split(",")
    if item.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册各个模块的路由
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(health.router)
app.include_router(session.router)
app.include_router(chat.router)
app.include_router(flow.router)
app.include_router(trip.router)
app.include_router(knowledge.router)
# 注册地图相关路由，解决前端请求 /api/map/geojson 404 的问题
app.include_router(map.router)

if __name__ == "__main__":
    import uvicorn
    # 本地直接运行入口
    uvicorn.run(app, host="0.0.0.0", port=8000)
