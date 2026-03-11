# API 服务入口，提供行程生成、会话列表、知识库检索的 HTTP 接口
from datetime import datetime
import asyncio
import json
import time
import re
from io import BytesIO
from functools import lru_cache  # 用于缓存全局单例对象，减少重复初始化
from typing import Any, Dict, List, Optional  # 提供类型注解，便于接口契约清晰

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile  # FastAPI 框架与错误处理
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware  # 允许前端跨域访问
from pydantic import BaseModel, Field  # 数据模型与字段校验
from pypdf import PdfReader

from src.config import Config  # 读取项目配置
from src.frontend.context.conversation_manager import ConversationManager
from src.frontend.context.entity import Message, MessageType
from src.frontend.context.storage import get_conversation_storage  # 获取会话存储实现
from src.llm.llm_manager import LlmManager  # 行程生成核心管理器
from src.map.map_renderer import TripMap
from src.rag.rag_main import AIRetrievalPipeline  # 知识库检索流水线
from src.rag.store.vector_store import VectorStore
from src.agent import run_agent_loop_sync
from src.agent.event_bus import event_bus
from src.agent.plan_models import TripState


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
    knowledge_base_id: Optional[str] = Field(None, description="知识库ID（可选）")
    knowledge_query: Optional[str] = Field(None, description="知识库检索查询（可选）")


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


class KnowledgeAnswerRequest(BaseModel):
    query: str = Field(..., description="检索问题")
    evidence: Dict[str, Any] = Field(..., description="证据结构化结果")


class KnowledgeAnswerResponse(BaseModel):
    query: str = Field(..., description="检索问题")
    evidence: Dict[str, Any] = Field(..., description="证据结构化结果")
    answer: str = Field(..., description="生成回答")


class KnowledgeBaseCreateRequest(BaseModel):
    """创建知识库请求体"""
    name: str = Field(..., description="知识库名称")


class KnowledgeBaseCreateResponse(BaseModel):
    """创建知识库响应体"""
    knowledge_base_id: str = Field(..., description="知识库ID")
    name: str = Field(..., description="知识库名称")
    collection_name: str = Field(..., description="向量集合名")


class KnowledgeBaseDeleteResponse(BaseModel):
    """删除知识库响应体"""
    knowledge_base_id: str = Field(..., description="知识库ID")
    success: bool = Field(..., description="是否删除成功")


class KnowledgeBaseItem(BaseModel):
    """知识库列表项"""
    knowledge_base_id: str = Field(..., description="知识库ID")
    name: str = Field(..., description="知识库名称")
    collection_name: str = Field(..., description="向量集合名")


class KnowledgeBaseListResponse(BaseModel):
    """知识库列表响应体"""
    items: List[KnowledgeBaseItem] = Field(default_factory=list, description="知识库列表")


class KnowledgeUploadResponse(BaseModel):
    """知识库文档上传响应体"""
    knowledge_base_id: str = Field(..., description="知识库ID")
    filename: str = Field(..., description="原始文件名")
    chunks: int = Field(..., description="入库分块数")


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


class MapGeoJsonRequest(BaseModel):
    trip_data: Dict[str, Any] = Field(..., description="结构化行程数据")


class MapGeoJsonResponse(BaseModel):
    points: Dict[str, Any] = Field(..., description="POI 点位 GeoJSON")
    routes: Dict[str, Any] = Field(..., description="路线 GeoJSON")
    center: Dict[str, float] = Field(..., description="地图中心点")
    bounds: List[float] = Field(..., description="地图边界")
    total_points: int = Field(..., description="POI 数量")


class TripUpdateRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一ID")
    device_id: str = Field(..., description="设备唯一ID")
    session_id: Optional[str] = Field(None, description="会话ID，可为空以便新建")
    trip_data: Dict[str, Any] = Field(..., description="结构化行程数据")


class TripUpdateResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    trip_data: Dict[str, Any] = Field(..., description="结构化行程数据")


class TripReplanDayRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一ID")
    device_id: str = Field(..., description="设备唯一ID")
    session_id: Optional[str] = Field(None, description="会话ID，可为空以便新建")
    day: int = Field(..., description="需要重新规划的天数")


class TripReplanDayResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    trip_data: Dict[str, Any] = Field(..., description="结构化行程数据")


class AgentRunRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一ID")
    device_id: str = Field(..., description="设备唯一ID")
    thread_id: Optional[str] = Field(None, description="执行线程ID")
    user_intent: Optional[str] = Field("generate_trip", description="用户意图")
    user_input: Dict[str, Any] = Field(default_factory=dict, description="用户输入")
    agent_config: Dict[str, Any] = Field(default_factory=dict, description="Agent 配置")
    resume: bool = Field(False, description="是否从快照恢复")


class AgentRunResponse(BaseModel):
    thread_id: str = Field(..., description="执行线程ID")
    status: str = Field(..., description="启动状态")


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


KNOWLEDGE_COLLECTION_PREFIX = "kb_"
KNOWLEDGE_REGISTRY_COLLECTION = "knowledge_registry"


@lru_cache(maxsize=1)
def _get_knowledge_store() -> VectorStore:
    """缓存知识库向量实例，统一管理 collection 生命周期。"""
    return VectorStore(collection_name=KNOWLEDGE_REGISTRY_COLLECTION)


def _normalize_knowledge_base_id(raw_id: str) -> str:
    """将知识库ID标准化为可持久化命名。"""
    cleaned = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fa5]", "_", str(raw_id or "").strip())
    cleaned = cleaned.strip("_")
    if not cleaned:
        raise HTTPException(status_code=400, detail="知识库名称不能为空")
    return cleaned[:48]


def _to_collection_name(knowledge_base_id: str) -> str:
    """将业务知识库ID映射为向量集合名。"""
    normalized_id = _normalize_knowledge_base_id(knowledge_base_id).lower()
    safe_collection_id = re.sub(r"[^a-z0-9_\-]", "_", normalized_id)
    return f"{KNOWLEDGE_COLLECTION_PREFIX}{safe_collection_id}"[:63]


def _build_kb_query(destination: str, days: int, budget: Optional[str], preference: Optional[str], override_query: Optional[str]) -> str:
    """构造知识库检索查询，支持用户自定义覆盖。"""
    if override_query and override_query.strip():
        return override_query.strip()
    query_parts = [
        f"目的地:{destination}",
        f"天数:{days}",
        f"预算:{budget or '未指定'}",
        f"偏好:{preference or '未指定'}",
        "行程建议",
    ]
    return " ".join(query_parts)


def _load_knowledge_base_registry() -> List[Dict[str, str]]:
    """从 registry 集合加载知识库定义列表。"""
    store = _get_knowledge_store()
    store.switch_collection(KNOWLEDGE_REGISTRY_COLLECTION, create_if_missing=True)
    payload = store.vector_db.get(include=["metadatas"])
    metadata_list = payload.get("metadatas") if isinstance(payload, dict) else []
    rows: List[Dict[str, str]] = []
    if not isinstance(metadata_list, list):
        return rows
    for metadata in metadata_list:
        if not isinstance(metadata, dict):
            continue
        knowledge_base_id = str(metadata.get("knowledge_base_id") or "").strip()
        collection_name = str(metadata.get("collection_name") or "").strip()
        name = str(metadata.get("name") or "").strip()
        if not knowledge_base_id or not collection_name or not name:
            continue
        rows.append(
            {
                "knowledge_base_id": knowledge_base_id,
                "name": name,
                "collection_name": collection_name,
            }
        )
    unique_map: Dict[str, Dict[str, str]] = {}
    for row in rows:
        unique_map[row["knowledge_base_id"]] = row
    return list(unique_map.values())


def _upsert_knowledge_base_registry(knowledge_base_id: str, name: str, collection_name: str) -> None:
    """写入或更新知识库 registry 记录。"""
    store = _get_knowledge_store()
    store.switch_collection(KNOWLEDGE_REGISTRY_COLLECTION, create_if_missing=True)
    existing = store.vector_db.get(where={"knowledge_base_id": knowledge_base_id}, include=["metadatas"])
    existing_ids = existing.get("ids") if isinstance(existing, dict) else []
    if isinstance(existing_ids, list) and existing_ids:
        store.vector_db.delete(ids=existing_ids)
    store.add_documents(
        [
            {
                "content": f"knowledge_base:{knowledge_base_id}",
                "metadata": {
                    "record_type": "knowledge_base",
                    "knowledge_base_id": knowledge_base_id,
                    "name": name,
                    "collection_name": collection_name,
                },
            }
        ]
    )


def _delete_knowledge_base_registry(knowledge_base_id: str) -> None:
    """删除知识库 registry 记录。"""
    store = _get_knowledge_store()
    store.switch_collection(KNOWLEDGE_REGISTRY_COLLECTION, create_if_missing=True)
    existing = store.vector_db.get(where={"knowledge_base_id": knowledge_base_id}, include=["metadatas"])
    existing_ids = existing.get("ids") if isinstance(existing, dict) else []
    if isinstance(existing_ids, list) and existing_ids:
        store.vector_db.delete(ids=existing_ids)


def _extract_text_from_upload(filename: str, content_bytes: bytes) -> str:
    """按文件后缀解析上传文档文本，支持 PDF/Markdown/纯文本。"""
    lower_name = str(filename or "").lower()
    if lower_name.endswith(".pdf"):
        reader = PdfReader(BytesIO(content_bytes))
        page_text_list: List[str] = []
        for page in reader.pages:
            page_text_list.append(str(page.extract_text() or ""))
        return "\n".join(page_text_list).strip()
    if lower_name.endswith(".md") or lower_name.endswith(".markdown") or lower_name.endswith(".txt"):
        for encoding in ["utf-8", "utf-8-sig", "gbk"]:
            try:
                return content_bytes.decode(encoding).strip()
            except Exception:
                continue
        raise HTTPException(status_code=400, detail="文本文件编码不支持，请使用 UTF-8/GBK")
    raise HTTPException(status_code=400, detail="仅支持 PDF/Markdown/纯文本文件")


def _build_knowledge_context_texts(
    knowledge_base_id: Optional[str],
    destination: str,
    days: int,
    budget: Optional[str],
    preference: Optional[str],
    knowledge_query: Optional[str],
) -> List[str]:
    """从指定知识库检索与本次行程相关的上下文片段并转为提示词上下文。"""
    if not knowledge_base_id:
        return []
    store = _get_knowledge_store()
    collection_name = _to_collection_name(knowledge_base_id)
    all_collections = set(store.list_collections())
    if collection_name not in all_collections:
        raise HTTPException(status_code=404, detail="指定知识库不存在")
    store.switch_collection(collection_name, create_if_missing=False)
    query_text = _build_kb_query(destination, days, budget, preference, knowledge_query)
    related_docs = store.similarity_search(query_text, k=4)
    context_texts: List[str] = []
    for doc in related_docs:
        source = str((doc.metadata or {}).get("source") or "私有知识库")
        snippet = str(doc.page_content or "").strip()
        if not snippet:
            continue
        context_texts.append(f"私有知识库参考（来源:{source}）：{snippet[:800]}")
    return context_texts


def _ensure_session_id(user_id: str, device_id: str, session_id: Optional[str]) -> str:
    """确保存在会话ID，不存在则创建并落库"""
    storage = _get_storage()
    if session_id:
        return session_id
    new_session_id = storage.generate_session_id(user_id, device_id)
    storage.store_session(user_id, new_session_id)
    return new_session_id


def _normalize_daily_plan(trip_data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    daily_plan_raw = trip_data.get("daily_plan")
    if isinstance(daily_plan_raw, dict):
        return {str(key): value for key, value in daily_plan_raw.items() if isinstance(value, list)}
    if isinstance(daily_plan_raw, list):
        return {"1": daily_plan_raw}
    return {}


def _build_agent_thread_id(user_id: str, device_id: str) -> str:
    timestamp_ms = int(datetime.now().timestamp() * 1000)
    return f"{user_id}-{device_id}-{timestamp_ms}"


def _resolve_initial_state(thread_id: str, resume: bool) -> Optional[TripState]:
    return None


def _build_stream_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    kind = event.get("kind") or ""
    detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
    status = "running"
    if kind == "task_end":
        status = "done" if detail.get("success", True) else "failed"
    elif kind in ["paused"]:
        status = "paused"
    elif kind in ["error"]:
        status = "failed"
    elif kind in ["loop_end"]:
        status = str(detail.get("status") or "done")
    return {
        "event": kind,
        "sequence": int(event.get("sequence") or 0),
        "thread_id": event.get("thread_id") or "",
        "node": event.get("node"),
        "status": status,
        "payload": detail or {},
    }


def _format_sse(event_payload: Dict[str, Any]) -> str:
    payload_text = json.dumps(event_payload, ensure_ascii=False)
    return f"id: {event_payload.get('sequence')}\nevent: {event_payload.get('event')}\ndata: {payload_text}\n\n"


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

_trip_streams: Dict[str, Dict[str, Any]] = {}
_trip_streams_lock = asyncio.Lock()
_TRIP_STREAM_TTL_SECONDS = 600


async def _cleanup_trip_streams() -> None:
    now = time.time()
    async with _trip_streams_lock:
        expired = []
        for key, payload in _trip_streams.items():
            updated_at = float(payload.get("updated_at") or now)
            done = bool(payload.get("done"))
            running = bool(payload.get("running"))
            if done and not running and now - updated_at > _TRIP_STREAM_TTL_SECONDS:
                expired.append(key)
        for key in expired:
            _trip_streams.pop(key, None)


async def _append_trip_event(message_id: str, event_payload: Dict[str, Any]) -> None:
    async with _trip_streams_lock:
        stream_state = _trip_streams.get(message_id)
        if not stream_state:
            return
        stream_state["events"].append(event_payload)
        stream_state["updated_at"] = time.time()
        if event_payload.get("event") in ("trip_data", "error"):
            stream_state["done"] = True
            stream_state["running"] = False


async def _run_trip_stream(
    message_id: str,
    session_id: str,
    llm_manager: LlmManager,
    user_input: Dict[str, Any],
    context_texts: List[str],
) -> None:
    response_chunks: List[str] = []
    last_sequence = 0
    try:
        stream = llm_manager.stream_trip_generation(user_input, context_texts)
        for event in llm_manager.build_stream_events_from_stream(stream, message_id):
            delta_text = event.get("content_delta") or ""
            response_chunks.append(delta_text)
            last_sequence = int(event.get("sequence") or last_sequence)
            event_payload = {
                "event": event.get("event"),
                "sequence": event.get("sequence"),
                "message_id": event.get("message_id"),
                "content_delta": delta_text,
                "is_final": event.get("is_final"),
                "session_id": session_id,
            }
            await _append_trip_event(message_id, event_payload)
            await asyncio.sleep(0)
        response_text = "".join(response_chunks)
        trip_data = llm_manager.parse_trip_from_response_text(response_text)
        if trip_data:
            storage = _get_storage()
            storage.store_trip_data(session_id, trip_data)
        final_payload = {
            "event": "trip_data",
            "sequence": last_sequence + 1,
            "message_id": message_id,
            "session_id": session_id,
            "trip_data": trip_data,
            "content": response_text,
        }
        await _append_trip_event(message_id, final_payload)
    except Exception as exc:
        error_payload = {
            "event": "error",
            "sequence": last_sequence + 1,
            "message_id": message_id,
            "session_id": session_id,
            "error": str(exc),
        }
        await _append_trip_event(message_id, error_payload)


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
    kb_context_texts = _build_knowledge_context_texts(
        payload.knowledge_base_id,
        payload.destination,
        payload.days,
        payload.budget,
        payload.preference,
        payload.knowledge_query,
    )
    merged_context_texts = list(payload.context_texts or []) + kb_context_texts
    trip_data = llm_manager.generate_trip(user_input, merged_context_texts)
    if trip_data:
        storage = _get_storage()
        storage.store_trip_data(session_id, trip_data)
    return TripGenerateResponse(session_id=session_id, trip_data=trip_data)


@app.post("/api/trip/stream")
async def stream_trip_generation(
    payload: TripGenerateRequest,
    request: Request,
    message_id: Optional[str] = Query(None, description="流式消息ID"),
    last_sequence: Optional[int] = Query(None, description="断线续传序号"),
):
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
    kb_context_texts = _build_knowledge_context_texts(
        payload.knowledge_base_id,
        payload.destination,
        payload.days,
        payload.budget,
        payload.preference,
        payload.knowledge_query,
    )
    merged_context_texts = list(payload.context_texts or []) + kb_context_texts
    await _cleanup_trip_streams()
    async with _trip_streams_lock:
        stream_id = message_id or f"trip-{datetime.now().strftime('%H%M%S%f')}"
        stream_state = _trip_streams.get(stream_id)
        if not stream_state:
            stream_state = {
                "message_id": stream_id,
                "session_id": session_id,
                "events": [],
                "done": False,
                "running": False,
                "created_at": time.time(),
                "updated_at": time.time(),
            }
            _trip_streams[stream_id] = stream_state
        else:
            session_id = stream_state.get("session_id") or session_id
        if not stream_state.get("running") and not stream_state.get("done"):
            stream_state["running"] = True
            asyncio.create_task(
                _run_trip_stream(
                    stream_id,
                    session_id,
                    llm_manager,
                    user_input,
                    merged_context_texts,
                )
            )
    header_sequence = request.headers.get("Last-Event-ID")
    try:
        header_sequence_value = int(header_sequence) if header_sequence else None
    except Exception:
        header_sequence_value = None
    start_sequence = header_sequence_value if header_sequence_value is not None else last_sequence

    async def event_generator():
        current_sequence = int(start_sequence or 0)
        while True:
            if await request.is_disconnected():
                break
            async with _trip_streams_lock:
                events = [
                    event
                    for event in stream_state.get("events", [])
                    if int(event.get("sequence") or 0) > current_sequence
                ]
                done = bool(stream_state.get("done"))
            if events:
                for event in events:
                    current_sequence = int(event.get("sequence") or current_sequence)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("event") in ("trip_data", "error"):
                        return
            else:
                if done:
                    return
                await asyncio.sleep(0.2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


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


@app.post("/api/map/geojson", response_model=MapGeoJsonResponse)
def render_map_geojson(payload: MapGeoJsonRequest) -> MapGeoJsonResponse:
    trip_data = payload.trip_data or {}
    map_renderer = _get_map_renderer()
    geo_payload = map_renderer.build_geojson(trip_data)
    return MapGeoJsonResponse(**geo_payload)


@app.post("/api/trip/update", response_model=TripUpdateResponse)
def update_trip(payload: TripUpdateRequest) -> TripUpdateResponse:
    session_id = _ensure_session_id(payload.user_id, payload.device_id, payload.session_id)
    storage = _get_storage()
    storage.store_trip_data(session_id, payload.trip_data)
    return TripUpdateResponse(session_id=session_id, trip_data=payload.trip_data)


@app.post("/api/trip/replan_day", response_model=TripReplanDayResponse)
def replan_trip_day(payload: TripReplanDayRequest) -> TripReplanDayResponse:
    session_id = _ensure_session_id(payload.user_id, payload.device_id, payload.session_id)
    storage = _get_storage()
    current_trip = storage.get_trip_data(session_id)
    if not current_trip:
        raise HTTPException(status_code=404, detail="当前会话未找到行程数据")
    day_value = int(payload.day)
    llm_manager = _get_llm_manager()
    user_input = {
        "destination": current_trip.get("destination", "成都"),
        "days": current_trip.get("days", 3),
        "budget": current_trip.get("budget", ""),
        "preference": current_trip.get("preference", ""),
    }
    edit_cmd = {
        "type": "modify",
        "msg": f"仅重新规划第{day_value}天行程，其余天保持不变",
    }
    replanned_trip = llm_manager.generate_trip(user_input, [], edit_cmd)
    if not replanned_trip:
        raise HTTPException(status_code=500, detail="重新规划失败")
    current_daily_plan = _normalize_daily_plan(current_trip)
    replanned_daily_plan = _normalize_daily_plan(replanned_trip)
    day_key = str(day_value)
    if day_key in replanned_daily_plan:
        current_daily_plan[day_key] = replanned_daily_plan[day_key]
    merged_trip = dict(current_trip)
    merged_trip["daily_plan"] = current_daily_plan
    storage.store_trip_data(session_id, merged_trip)
    return TripReplanDayResponse(session_id=session_id, trip_data=merged_trip)


def _run_agent_background(
    thread_id: str,
    user_input: Dict[str, Any],
    agent_config: Dict[str, Any],
    user_intent: str,
    resume: bool,
) -> None:
    llm_manager = _get_llm_manager()
    run_agent_loop_sync(
        llm_manager=llm_manager,
        user_input=user_input,
        thread_id=thread_id,
        agent_config=agent_config,
        user_intent=user_intent,
        resume=resume,
    )


@app.post("/api/agent/run", response_model=AgentRunResponse)
def run_agent(payload: AgentRunRequest, background_tasks: BackgroundTasks) -> AgentRunResponse:
    thread_id = payload.thread_id or _build_agent_thread_id(payload.user_id, payload.device_id)
    background_tasks.add_task(
        _run_agent_background,
        thread_id,
        payload.user_input or {},
        payload.agent_config or {},
        payload.user_intent or "generate_trip",
        payload.resume,
    )
    return AgentRunResponse(thread_id=thread_id, status="started")


@app.get("/api/agent/stream")
async def stream_agent_events(
    request: Request,
    thread_id: str = Query(..., description="执行线程ID"),
    last_sequence: Optional[int] = Query(None, description="断线续传序号"),
):
    header_sequence = request.headers.get("Last-Event-ID")
    try:
        header_sequence_value = int(header_sequence) if header_sequence else None
    except Exception:
        header_sequence_value = None
    start_sequence = header_sequence_value if header_sequence_value is not None else last_sequence

    async def event_generator():
        current_sequence = int(start_sequence or 0)
        while True:
            if await request.is_disconnected():
                break
            events = event_bus.list(thread_id=thread_id, after_sequence=current_sequence, limit=200)
            if events:
                for event in events:
                    payload = _build_stream_payload(event)
                    current_sequence = int(payload.get("sequence") or current_sequence)
                    yield _format_sse(payload)
                    if payload.get("event") == "loop_end":
                        return
            else:
                await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/knowledge/bases", response_model=KnowledgeBaseListResponse)
def list_knowledge_bases() -> KnowledgeBaseListResponse:
    records = _load_knowledge_base_registry()
    items = [KnowledgeBaseItem(**row) for row in records]
    return KnowledgeBaseListResponse(items=items)


@app.post("/api/knowledge/bases", response_model=KnowledgeBaseCreateResponse)
def create_knowledge_base(payload: KnowledgeBaseCreateRequest) -> KnowledgeBaseCreateResponse:
    normalized_id = _normalize_knowledge_base_id(payload.name)
    collection_name = _to_collection_name(normalized_id)
    store = _get_knowledge_store()
    created_collection_name = store.create_collection(collection_name)
    _upsert_knowledge_base_registry(
        knowledge_base_id=normalized_id,
        name=str(payload.name).strip(),
        collection_name=created_collection_name,
    )
    return KnowledgeBaseCreateResponse(
        knowledge_base_id=normalized_id,
        name=str(payload.name).strip(),
        collection_name=created_collection_name,
    )


@app.delete("/api/knowledge/bases/{knowledge_base_id}", response_model=KnowledgeBaseDeleteResponse)
def delete_knowledge_base(knowledge_base_id: str) -> KnowledgeBaseDeleteResponse:
    normalized_id = _normalize_knowledge_base_id(knowledge_base_id)
    collection_name = _to_collection_name(normalized_id)
    store = _get_knowledge_store()
    existing = set(store.list_collections())
    if collection_name not in existing:
        raise HTTPException(status_code=404, detail="知识库不存在")
    delete_success = store.delete_collection(collection_name)
    if not delete_success:
        raise HTTPException(status_code=500, detail="知识库删除失败")
    _delete_knowledge_base_registry(normalized_id)
    return KnowledgeBaseDeleteResponse(knowledge_base_id=normalized_id, success=True)


@app.post("/api/knowledge/bases/{knowledge_base_id}/upload", response_model=KnowledgeUploadResponse)
async def upload_knowledge_document(knowledge_base_id: str, file: UploadFile = File(...)) -> KnowledgeUploadResponse:
    normalized_id = _normalize_knowledge_base_id(knowledge_base_id)
    collection_name = _to_collection_name(normalized_id)
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="文件内容不能为空")
    extracted_text = _extract_text_from_upload(file.filename, file_bytes)
    if not extracted_text:
        raise HTTPException(status_code=400, detail="文档中未提取到可用文本")
    store = _get_knowledge_store()
    existing = set(store.list_collections())
    if collection_name not in existing:
        raise HTTPException(status_code=404, detail="知识库不存在")
    store.switch_collection(collection_name, create_if_missing=False)
    added_chunks = store.add_documents(
        [
            {
                "content": extracted_text,
                "metadata": {
                    "source": str(file.filename),
                    "knowledge_base_id": normalized_id,
                    "uploaded_at": datetime.now().isoformat(),
                    "file_type": str(file.content_type or ""),
                },
            }
        ]
    )
    if added_chunks <= 0:
        raise HTTPException(status_code=500, detail="文档入库失败")
    return KnowledgeUploadResponse(knowledge_base_id=normalized_id, filename=str(file.filename), chunks=added_chunks)


@app.post("/api/knowledge/search", response_model=KnowledgeSearchResponse)
def knowledge_search(payload: KnowledgeSearchRequest) -> KnowledgeSearchResponse:
    """知识库检索接口（最小闭环）"""
    rag_pipeline = _get_rag_pipeline()
    result = rag_pipeline.run(payload.query, generate_answer=payload.generate_answer)
    evidence = result.get("evidence") or {}
    answer = result.get("answer")
    return KnowledgeSearchResponse(query=payload.query, evidence=evidence, answer=answer)


@app.post("/api/knowledge/answer_from_evidence", response_model=KnowledgeAnswerResponse)
def knowledge_answer_from_evidence(payload: KnowledgeAnswerRequest) -> KnowledgeAnswerResponse:
    rag_pipeline = _get_rag_pipeline()
    if not payload.query or not isinstance(payload.evidence, dict):
        raise HTTPException(status_code=400, detail="证据或问题不能为空")
    answer = rag_pipeline.generate_answer_from_evidence(payload.query, payload.evidence)
    return KnowledgeAnswerResponse(query=payload.query, evidence=payload.evidence, answer=answer)
