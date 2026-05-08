import logging
import os
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

def _parse_cors_origins(raw_value: str | None) -> list[str]:
    """解析 CORS 允许的精确来源，自动忽略部署占位符。"""
    if not raw_value:
        return []
    placeholders = {
        "https://<你的-vercel-域名>",
        "http://<你的-vercel-域名>",
        "<你的-vercel-域名>",
    }
    origins: list[str] = []
    for item in raw_value.split(","):
        origin = item.strip()
        if not origin:
            continue
        if "<" in origin or ">" in origin or origin in placeholders:
            continue
        origins.append(origin)
    return origins


def _build_cors_origin_regex() -> str | None:
    """构建 CORS 允许来源正则：优先使用显式配置，否则给 Vercel 生产域名兜底。"""
    raw_regex = os.getenv("CORS_ORIGIN_REGEX", "").strip()
    if raw_regex and "<" not in raw_regex and ">" not in raw_regex:
        return raw_regex

    env_name = os.getenv("ENVIRONMENT", "").strip().lower()
    if env_name and env_name != "production":
        return None
    return r"^https://.*\.vercel\.app$"


# 配置 CORS 跨域
cors_origins_env = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
cors_origins = _parse_cors_origins(cors_origins_env)
cors_origin_regex = _build_cors_origin_regex()

logger.info(
    "CORS 配置已加载: origins=%s regex=%s",
    cors_origins,
    cors_origin_regex,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=cors_origin_regex,
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
