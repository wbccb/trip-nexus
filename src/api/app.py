# API 服务入口，提供行程生成、会话列表、知识库检索的 HTTP 接口
from functools import lru_cache  # 用于缓存全局单例对象，减少重复初始化
from typing import Any, Dict, List, Optional  # 提供类型注解，便于接口契约清晰

from fastapi import FastAPI, HTTPException, Query  # FastAPI 框架与错误处理
from fastapi.middleware.cors import CORSMiddleware  # 允许前端跨域访问
from pydantic import BaseModel, Field  # 数据模型与字段校验

from src.config import Config  # 读取项目配置
from src.frontend.context.entity import Message
from src.frontend.context.storage import get_conversation_storage  # 获取会话存储实现
from src.llm.llm_manager import LlmManager  # 行程生成核心管理器
from src.rag.rag_main import AIRetrievalPipeline  # 知识库检索流水线


class StartSessionRequest(BaseModel):
    """创建新会话的请求体"""
    user_id: str = Field(..., description="用户唯一ID")
    device_id: str = Field(..., description="设备唯一ID")


class StartSessionResponse(BaseModel):
    """创建新会话的响应体"""
    session_id: str = Field(..., description="新会话ID")


class SessionItem(BaseModel):
    """会话列表展示结构"""
    session_id: str = Field(..., description="会话ID")
    user_id: str = Field(..., description="用户ID")
    name: str = Field(..., description="会话名称")
    update_time: str = Field(..., description="最后更新时间")


class TripGenerateRequest(BaseModel):
    """行程生成请求体"""
    user_id: str = Field(..., description="用户唯一ID")
    device_id: str = Field(..., description="设备唯一ID")
    session_id: Optional[str] = Field(None, description="会话ID，可为空以便新建")
    destination: str = Field(..., description="目的地城市")
    days: int = Field(..., description="行程天数")
    budget: Optional[str] = Field(None, description="预算（可选）")
    preference: Optional[str] = Field(None, description="偏好（可选）")
    context_texts: List[str] = Field(default_factory=list, description="上下文文本列表")


class TripGenerateResponse(BaseModel):
    """行程生成响应体"""
    session_id: str = Field(..., description="会话ID")
    trip_data: Optional[Dict[str, Any]] = Field(None, description="结构化行程数据")


class KnowledgeSearchRequest(BaseModel):
    """知识库检索请求体"""
    query: str = Field(..., description="检索问题或主题")
    generate_answer: bool = Field(False, description="是否直接生成回答")


class KnowledgeSearchResponse(BaseModel):
    """知识库检索响应体"""
    query: str = Field(..., description="检索问题")
    evidence: Dict[str, Any] = Field(..., description="证据结构化结果")
    answer: Optional[str] = Field(None, description="可选回答")


class ChatHistoryItem(BaseModel):
    role: str = Field(..., description="消息角色")
    content: str = Field(..., description="消息内容")
    timestamp: str = Field(..., description="消息时间")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="消息元数据")
    is_redundant: bool = Field(False, description="是否为冗余消息")


@lru_cache(maxsize=1)
def _get_config() -> Config:
    """缓存 Config 实例，避免重复读取环境变量"""
    return Config()


@lru_cache(maxsize=1)
def _get_storage():
    """缓存会话存储实例，用于会话列表与行程数据持久化"""
    config = _get_config()
    return get_conversation_storage(config)


@lru_cache(maxsize=1)
def _get_llm_manager() -> LlmManager:
    """缓存 LlmManager 实例，统一行程生成入口"""
    config = _get_config()
    llm_manager = LlmManager(
        model_name=config.GENERATION_MODEL_NAME,  # 使用生成模型名称初始化
        ollama_base_url=config.GENERATION_BASE_URL,  # 兼容旧命名，作为 base_url 兜底
        provider=config.GENERATION_PROVIDER,  # 生成模型提供方
        base_url=config.GENERATION_BASE_URL,  # 生成模型 Base URL
        api_key=config.GENERATION_API_KEY,  # 生成模型 API Key
        temperature=config.GENERATION_TEMPERATURE,  # 生成温度
    )
    llm_manager.update_llm_config(
        {
            "provider": config.GENERATION_PROVIDER,  # 通用 provider
            "base_url": config.GENERATION_BASE_URL,  # 通用 base_url
            "model_name": config.GENERATION_MODEL_NAME,  # 通用 model_name
            "api_key": config.GENERATION_API_KEY,  # 通用 api_key
            "temperature": config.GENERATION_TEMPERATURE,  # 通用温度
            "analysis_provider": config.ANALYSIS_PROVIDER,  # 分析阶段 provider
            "analysis_base_url": config.ANALYSIS_BASE_URL,  # 分析阶段 base_url
            "analysis_model_name": config.ANALYSIS_MODEL_NAME,  # 分析阶段模型
            "analysis_api_key": config.ANALYSIS_API_KEY,  # 分析阶段 key
            "analysis_temperature": config.ANALYSIS_TEMPERATURE,  # 分析阶段温度
            "generation_provider": config.GENERATION_PROVIDER,  # 生成阶段 provider
            "generation_base_url": config.GENERATION_BASE_URL,  # 生成阶段 base_url
            "generation_model_name": config.GENERATION_MODEL_NAME,  # 生成阶段模型
            "generation_api_key": config.GENERATION_API_KEY,  # 生成阶段 key
            "generation_temperature": config.GENERATION_TEMPERATURE,  # 生成阶段温度
        }
    )
    return llm_manager


@lru_cache(maxsize=1)
def _get_rag_pipeline() -> AIRetrievalPipeline:
    """缓存 RAG Pipeline 实例，提供知识库检索能力"""
    llm_manager = _get_llm_manager()
    return AIRetrievalPipeline(llm_manager.get_analysis_llm())


def _ensure_session_id(user_id: str, device_id: str, session_id: Optional[str]) -> str:
    """确保存在会话ID，不存在则创建并落库"""
    storage = _get_storage()
    if session_id:
        return session_id
    new_session_id = storage.generate_session_id(user_id, device_id)
    storage.store_session(user_id, new_session_id)
    return new_session_id


def _row_to_session_item(row: Any) -> Dict[str, str]:
    """将数据库行转换为可序列化字典"""
    return {
        "session_id": str(row["session_id"]) if isinstance(row, dict) or hasattr(row, "__getitem__") else "",
        "user_id": str(row["user_id"]) if isinstance(row, dict) or hasattr(row, "__getitem__") else "",
        "name": str(row["name"]) if isinstance(row, dict) or hasattr(row, "__getitem__") else "",
        "update_time": str(row["update_time"]) if isinstance(row, dict) or hasattr(row, "__getitem__") else "",
    }


def _normalize_message_payload(message: Message) -> Dict[str, Any]:
    payload = message.model_dump()
    payload["timestamp"] = message.timestamp.isoformat()
    return payload


app = FastAPI(title="TripNexus API", version="0.0.4")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源，便于前端本地开发调试
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有方法
    allow_headers=["*"],  # 允许所有请求头
)


@app.get("/api/health")
def health_check() -> Dict[str, str]:
    """健康检查接口"""
    return {"status": "ok"}


@app.post("/api/sessions/start", response_model=StartSessionResponse)
def start_session(payload: StartSessionRequest) -> StartSessionResponse:
    """创建新会话并返回会话ID"""
    storage = _get_storage()
    session_id = storage.generate_session_id(payload.user_id, payload.device_id)
    storage.store_session(payload.user_id, session_id)
    return StartSessionResponse(session_id=session_id)


@app.get("/api/sessions/list", response_model=List[SessionItem])
def list_sessions(user_id: str = Query(..., description="用户ID")) -> List[SessionItem]:
    """获取指定用户的会话列表"""
    storage = _get_storage()
    rows = storage.get_session_list(user_id)
    return [SessionItem(**_row_to_session_item(row)) for row in rows]


@app.get("/api/sessions/history", response_model=List[ChatHistoryItem])
def session_history(session_id: str = Query(..., description="会话ID")) -> List[ChatHistoryItem]:
    storage = _get_storage()
    history_messages = storage.get_session_chat_list(session_id)
    if history_messages:
        parsed_messages = []
        for message_json in history_messages:
            try:
                message_obj = Message.model_validate_json(message_json)
                parsed_messages.append(ChatHistoryItem(**_normalize_message_payload(message_obj)))
            except Exception:
                continue
        return parsed_messages
    short_term_context = storage.get_short_term_context(session_id)
    if isinstance(short_term_context, dict):
        messages = short_term_context.get("messages") or []
        return [ChatHistoryItem(**item) for item in messages if isinstance(item, dict)]
    return []


@app.post("/api/trip/generate", response_model=TripGenerateResponse)
def generate_trip(payload: TripGenerateRequest) -> TripGenerateResponse:
    """行程生成主接口"""
    if not payload.destination or payload.days <= 0:
        raise HTTPException(status_code=400, detail="destination 和 days 为必填且 days 必须大于 0")
    session_id = _ensure_session_id(payload.user_id, payload.device_id, payload.session_id)
    user_input = {
        "destination": payload.destination,
        "days": payload.days,
        "budget": payload.budget,
        "preference": payload.preference,
    }
    llm_manager = _get_llm_manager()
    trip_data = llm_manager.generate_trip(user_input, payload.context_texts or [])
    if trip_data:
        storage = _get_storage()
        storage.store_trip_data(session_id, trip_data)
    return TripGenerateResponse(session_id=session_id, trip_data=trip_data)


@app.post("/api/knowledge/search", response_model=KnowledgeSearchResponse)
def knowledge_search(payload: KnowledgeSearchRequest) -> KnowledgeSearchResponse:
    """知识库检索接口（最小闭环）"""
    rag_pipeline = _get_rag_pipeline()
    result = rag_pipeline.run(payload.query, generate_answer=payload.generate_answer)
    evidence = result.get("evidence") or {}
    answer = result.get("answer")
    return KnowledgeSearchResponse(query=payload.query, evidence=evidence, answer=answer)
