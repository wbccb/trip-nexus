# API 服务入口，提供行程生成、会话列表、知识库检索的 HTTP 接口
from datetime import datetime
from functools import lru_cache  # 用于缓存全局单例对象，减少重复初始化
from typing import Any, Dict, List, Optional  # 提供类型注解，便于接口契约清晰

from fastapi import FastAPI, HTTPException, Query  # FastAPI 框架与错误处理
from fastapi.middleware.cors import CORSMiddleware  # 允许前端跨域访问
from pydantic import BaseModel, Field  # 数据模型与字段校验

from src.config import Config  # 读取项目配置
from src.frontend.context.conversation_manager import ConversationManager
from src.frontend.context.entity import Message, MessageType
from src.frontend.context.storage import get_conversation_storage  # 获取会话存储实现
from src.llm.llm_manager import LlmManager  # 行程生成核心管理器
from src.map.map_renderer import TripMap
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


class TripDataResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    trip_data: Optional[Dict[str, Any]] = Field(None, description="结构化行程数据")


class DeleteSessionResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    success: bool = Field(..., description="是否删除成功")


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


class ChatSendRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一ID")
    device_id: str = Field(..., description="设备唯一ID")
    session_id: Optional[str] = Field(None, description="会话ID，可为空以便新建")
    message: str = Field(..., description="用户输入消息")


class ChatSendResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    response: str = Field(..., description="助手回复")
    trip_data: Optional[Dict[str, Any]] = Field(None, description="结构化行程数据")
    intent: Optional[str] = Field(None, description="意图类型")
    needs_more_info: bool = Field(False, description="是否需要补充信息")


class MapRenderRequest(BaseModel):
    trip_data: Dict[str, Any] = Field(..., description="结构化行程数据")
    batch_index: Optional[int] = Field(0, description="批次序号，从 0 开始")
    batch_size: Optional[int] = Field(4, description="每批 POI 数量")


class MapRenderResponse(BaseModel):
    map_html: str = Field(..., description="地图 HTML 字符串")
    sequence: int = Field(..., description="当前批次序号")
    day: Optional[str] = Field(None, description="当前批次所属天数")
    is_final: bool = Field(False, description="是否最终批次")


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
def _get_conversation_manager() -> ConversationManager:
    storage = _get_storage()
    llm_manager = _get_llm_manager()
    return ConversationManager(storage, llm_manager)


@lru_cache(maxsize=1)
def _get_rag_pipeline() -> AIRetrievalPipeline:
    """缓存 RAG Pipeline 实例，提供知识库检索能力"""
    llm_manager = _get_llm_manager()
    return AIRetrievalPipeline(llm_manager.get_analysis_llm())


@lru_cache(maxsize=1)
def _get_map_renderer() -> TripMap:
    return TripMap()


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


def _get_context_messages(storage, session_id: str) -> List[Dict[str, Any]]:
    short_term_context = storage.get_short_term_context(session_id)
    if isinstance(short_term_context, dict):
        messages = short_term_context.get("messages") or []
        if messages:
            return messages[-10:]
    history_messages = storage.get_session_chat_list(session_id)
    normalized_messages: List[Dict[str, Any]] = []
    if history_messages:
        for message_json in history_messages[-10:]:
            try:
                message_obj = Message.model_validate_json(message_json)
                normalized_messages.append(_normalize_message_payload(message_obj))
            except Exception:
                continue
    return normalized_messages


CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app = FastAPI(title="TripNexus API", version="0.0.4")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
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
    try:
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
    except Exception:
        return []


@app.get("/api/sessions/trip", response_model=TripDataResponse)
def session_trip(session_id: str = Query(..., description="会话ID")) -> TripDataResponse:
    storage = _get_storage()
    trip_data = storage.get_trip_data(session_id)
    return TripDataResponse(session_id=session_id, trip_data=trip_data)


@app.delete("/api/sessions/delete", response_model=DeleteSessionResponse)
def delete_session(session_id: str = Query(..., description="会话ID")) -> DeleteSessionResponse:
    storage = _get_storage()
    storage.delete_session(session_id)
    return DeleteSessionResponse(session_id=session_id, success=True)


@app.post("/api/chat/send", response_model=ChatSendResponse)
def send_chat(payload: ChatSendRequest) -> ChatSendResponse:
    if not payload.message:
        raise HTTPException(status_code=400, detail="message 不能为空")
    session_id = _ensure_session_id(payload.user_id, payload.device_id, payload.session_id)
    storage = _get_storage()
    llm_manager = _get_llm_manager()
    conversation_manager = _get_conversation_manager()
    context_messages = _get_context_messages(storage, session_id)
    current_trip = storage.get_trip_data(session_id)
    intent_data = llm_manager.analyze_user_message(payload.message, context_messages, current_trip)
    user_message = Message(
        role=MessageType.USER,
        content=payload.message,
        timestamp=datetime.now(),
        metadata={},
    )
    conversation_manager.process_new_message(
        payload.user_id,
        payload.device_id,
        user_message,
        session_id,
        intent_data=intent_data,
    )
    intent = intent_data.get("intent")
    response_text = ""
    trip_data = None
    needs_more_info = False
    if intent == "general_conversation":
        tool_call = llm_manager.call_tool_by_llm(payload.message, context_messages)
        if tool_call.get("needs_tool") and tool_call.get("result"):
            result_payload = tool_call.get("result")
            if isinstance(result_payload, dict) and result_payload.get("success"):
                response_text = f"工具结果：{result_payload.get('data')}"
        if not response_text:
            response_stream = llm_manager.stream_chat_response(payload.message, context_messages, current_trip)
            response_text = "".join([str(delta) for delta in response_stream])
    elif intent == "generate_trip":
        result = llm_manager._handle_trip_generation(intent_data, context_messages)
        response_text = result.get("response") or ""
        trip_data = result.get("trip_data")
        needs_more_info = bool(result.get("needs_more_info"))
    elif intent in ["modify_trip", "add_attraction", "delete_attraction", "reorder_trip"]:
        if current_trip:
            result = llm_manager._handle_trip_modification(intent_data, current_trip, context_messages)
            response_text = result.get("response") or ""
            trip_data = result.get("trip_data")
        else:
            response_text = "我需要先为您生成一个基础行程，然后才能进行调整。请先提供目的地、天数和预算信息。"
    else:
        response_text = f"我理解您想{intent_data.get('summary', '进一步讨论行程')}. 请告诉我更多细节，比如目的地、旅行天数和您的偏好，我可以为您规划具体的行程。"
    if trip_data:
        storage.store_trip_data(session_id, trip_data)
    assistant_message = Message(
        role=MessageType.ASSISTANT,
        content=response_text,
        timestamp=datetime.now(),
        metadata={
            "intent": intent,
            "needs_more_info": needs_more_info,
            "has_trip_data": bool(trip_data),
        },
    )
    conversation_manager.process_new_message(
        payload.user_id,
        payload.device_id,
        assistant_message,
        session_id,
    )
    return ChatSendResponse(
        session_id=session_id,
        response=response_text,
        trip_data=trip_data,
        intent=intent,
        needs_more_info=needs_more_info,
    )


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


@app.post("/api/map/render", response_model=MapRenderResponse)
def render_map(payload: MapRenderRequest) -> MapRenderResponse:
    trip_data = payload.trip_data or {}
    map_renderer = _get_map_renderer()
    batch_index = payload.batch_index or 0
    batch_size = payload.batch_size or 4
    selected_event = None
    last_event = None
    for idx, event in enumerate(map_renderer.render_map_batches(trip_data, batch_size=batch_size)):
        last_event = event
        if idx == batch_index:
            selected_event = event
            break
    if selected_event is None:
        selected_event = last_event or {}
    map_html = selected_event.get("html") or ""
    sequence = selected_event.get("sequence")
    day = selected_event.get("day")
    is_final = bool(selected_event.get("is_final"))
    return MapRenderResponse(
        map_html=map_html,
        sequence=sequence if isinstance(sequence, int) else max(batch_index, 0),
        day=day if isinstance(day, str) or day is None else str(day),
        is_final=is_final,
    )


@app.post("/api/knowledge/search", response_model=KnowledgeSearchResponse)
def knowledge_search(payload: KnowledgeSearchRequest) -> KnowledgeSearchResponse:
    """知识库检索接口（最小闭环）"""
    rag_pipeline = _get_rag_pipeline()
    result = rag_pipeline.run(payload.query, generate_answer=payload.generate_answer)
    evidence = result.get("evidence") or {}
    answer = result.get("answer")
    return KnowledgeSearchResponse(query=payload.query, evidence=evidence, answer=answer)
