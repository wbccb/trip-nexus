import contextvars
import json
import logging
import time
from functools import lru_cache
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request

from src.config import Config
from src.auth.jwt_handler import build_access_token
from src.auth.middleware import (
    AuthenticatedUser,
    get_auth_db_connection,
    init_auth_tables,
)
from src.frontend.context.conversation_manager import ConversationManager
from src.frontend.context.storage import get_conversation_storage
from src.llm.llm_manager import LlmManager
from src.map.map_renderer import TripMap
from src.rag.rag_main import AIRetrievalPipeline
from src.rag.store.vector_store import VectorStore
from src.models.user import PublicUserProfile
from src.api.schemas.auth import AuthResponse
from src.frontend.context.entity import Message

logger = logging.getLogger(__name__)

_REQUEST_OBSERVABILITY_CONTEXT: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "tripnexus_request_observability_context",
    default={},
)


@lru_cache(maxsize=1)
def _get_config() -> Config:
    """缓存 Config 实例，避免重复读取环境变量"""
    return Config()


@lru_cache(maxsize=1)
def _get_storage():
    """缓存会话存储实例，用于会话列表与行程数据持久化"""
    config = _get_config()
    return get_conversation_storage(config)


@lru_cache(maxsize=128)
def _get_llm_manager_for_user(user_id: int) -> LlmManager:
    """缓存 LlmManager 实例，按用户级别隔离以支持独立配置"""
    config = _get_config()
    is_prod = config.ENVIRONMENT.lower() == "production"

    base_llm_config = {
        "provider": config.GENERATION_PROVIDER,
        "base_url": config.GENERATION_BASE_URL,
        "model_name": config.GENERATION_MODEL_NAME,
        "api_key": config.GENERATION_API_KEY,
        "temperature": config.GENERATION_TEMPERATURE,
        "analysis_provider": config.ANALYSIS_PROVIDER,
        "analysis_base_url": config.ANALYSIS_BASE_URL,
        "analysis_model_name": config.ANALYSIS_MODEL_NAME,
        "analysis_api_key": config.ANALYSIS_API_KEY,
        "analysis_temperature": config.ANALYSIS_TEMPERATURE,
        "generation_provider": config.GENERATION_PROVIDER,
        "generation_base_url": config.GENERATION_BASE_URL,
        "generation_model_name": config.GENERATION_MODEL_NAME,
        "generation_api_key": config.GENERATION_API_KEY,
        "generation_temperature": config.GENERATION_TEMPERATURE,
    }

    user_configured = False
    if user_id > 0:
        user_row = _get_user_row(user_id)
        if user_row and user_row.get("llm_config"):
            try:
                user_llm_config = json.loads(user_row.get("llm_config"))
                if isinstance(user_llm_config, dict) and user_llm_config:
                    base_llm_config.update(user_llm_config)
                    user_configured = True
            except Exception as e:
                logger.warning(f"解析用户 {user_id} 的 LLM 配置失败: {e}")

    if is_prod and not user_configured:
        raise HTTPException(status_code=400, detail="生产环境必须先配置大语言模型才能使用。请在界面左上角进行 LLM 配置。")

    llm_manager = LlmManager(
        model_name=base_llm_config["generation_model_name"],
        ollama_base_url=base_llm_config["generation_base_url"],
        provider=base_llm_config["generation_provider"],
        base_url=base_llm_config["generation_base_url"],
        api_key=base_llm_config["generation_api_key"],
        temperature=base_llm_config["generation_temperature"],
    )
    llm_manager.update_llm_config(base_llm_config)
    llm_manager.set_usage_observer(_llm_usage_observer)
    return llm_manager

def _get_llm_manager() -> LlmManager:
    """获取当前上下文用户的 LlmManager 实例，统一行程生成入口"""
    ctx = _REQUEST_OBSERVABILITY_CONTEXT.get({})
    user_id = int(ctx.get("user_id") or 0)
    return _get_llm_manager_for_user(user_id)


def _get_conversation_manager() -> ConversationManager:
    storage = _get_storage()
    llm_manager = _get_llm_manager()
    return ConversationManager(storage, llm_manager)


def _get_rag_pipeline() -> AIRetrievalPipeline:
    """每次获取最新的 LLM 实例用于 RAG"""
    llm_manager = _get_llm_manager()
    return AIRetrievalPipeline(llm_manager.get_analysis_llm())


@lru_cache(maxsize=1)
def _get_map_renderer() -> TripMap:
    return TripMap()


def _get_knowledge_store() -> VectorStore:
    return VectorStore()


def _normalize_knowledge_base_id(raw_id: str) -> str:
    if not raw_id:
        return ""
    return str(raw_id).strip().lower()


def _row_to_public_user_profile(row: Dict[str, Any]) -> PublicUserProfile:
    llm_config_str = str(row.get("llm_config") or "{}")
    has_llm_config = False
    if llm_config_str and llm_config_str != "{}":
        try:
            parsed = json.loads(llm_config_str)
            has_llm_config = bool(parsed and isinstance(parsed, dict))
        except Exception:
            pass

    return PublicUserProfile(
        user_id=int(row.get("id") or 0),
        email=str(row.get("email") or ""),
        nickname=str(row.get("nickname") or ""),
        role=str(row.get("role") or "user"),
        status=str(row.get("status") or "active"),
        token_quota=int(row.get("token_quota") or 0),
        token_used=int(row.get("token_used") or 0),
        has_llm_config=has_llm_config,
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
    )


def _build_auth_response(user_row: Dict[str, Any]) -> AuthResponse:
    config = _get_config()
    token = build_access_token(
        user_id=int(user_row.get("id") or 0),
        email=str(user_row.get("email") or ""),
        role=str(user_row.get("role") or "user"),
        token_version=int(user_row.get("token_version") or 0),
        secret_key=config.JWT_SECRET_KEY,
        expire_minutes=config.JWT_EXPIRE_MINUTES,
        algorithm=config.JWT_ALGORITHM,
    )
    return AuthResponse(
        user_id=int(user_row.get("id") or 0),
        token=token,
        role=str(user_row.get("role") or "user"),
        profile=_row_to_public_user_profile(user_row),
    )


def _estimate_tokens_text(text: Any) -> int:
    try:
        return _get_llm_manager().estimate_tokens(text)
    except Exception:
        return 0


def _set_observability_context(
    *,
    user_id: int,
    user_email: str,
    request_path: str,
    session_id: str = "",
    message_id: str = "",
    ip_address: str = "",
) -> contextvars.Token:
    return _REQUEST_OBSERVABILITY_CONTEXT.set(
        {
            "user_id": int(user_id or 0),
            "user_email": str(user_email or ""),
            "request_path": str(request_path or ""),
            "session_id": str(session_id or ""),
            "message_id": str(message_id or ""),
            "ip_address": str(ip_address or ""),
        }
    )


def _reset_observability_context(token: contextvars.Token) -> None:
    _REQUEST_OBSERVABILITY_CONTEXT.reset(token)


def _record_audit_log(
    *,
    action: str,
    status: str,
    detail: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
    user_email: str = "",
    request_path: str = "",
    ip_address: str = "",
    session_id: str = "",
    message_id: str = "",
) -> None:
    init_auth_tables()
    ctx = _REQUEST_OBSERVABILITY_CONTEXT.get({})
    conn = get_auth_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audit_log (
                user_id, user_email, action, session_id, message_id,
                request_path, status, detail_json, ip_address
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id if user_id is not None else (ctx.get("user_id") or 0)) or None,
                str(user_email or ctx.get("user_email") or ""),
                str(action or ""),
                str(session_id or ctx.get("session_id") or ""),
                str(message_id or ctx.get("message_id") or ""),
                str(request_path or ctx.get("request_path") or ""),
                str(status or ""),
                json.dumps(detail or {}, ensure_ascii=False),
                str(ip_address or ctx.get("ip_address") or ""),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _record_token_usage(
    *,
    user_id: int,
    request_path: str,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    stage: str,
    session_id: str = "",
    message_id: str = "",
) -> None:
    if int(total_tokens or 0) <= 0:
        return
    init_auth_tables()
    conn = get_auth_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO token_usage_log (
                user_id, session_id, request_path, model_name,
                prompt_tokens, completion_tokens, total_tokens, stage, message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                str(session_id or ""),
                str(request_path or ""),
                str(model_name or ""),
                int(prompt_tokens or 0),
                int(completion_tokens or 0),
                int(total_tokens or 0),
                str(stage or ""),
                str(message_id or ""),
            ),
        )
        cursor.execute(
            "UPDATE users SET token_used = token_used + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (int(total_tokens or 0), int(user_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _llm_usage_observer(payload: Dict[str, Any]) -> None:
    ctx = _REQUEST_OBSERVABILITY_CONTEXT.get({})
    if not ctx or not ctx.get("user_id"):
        return
    _record_token_usage(
        user_id=int(ctx.get("user_id") or 0),
        session_id=str(ctx.get("session_id") or ""),
        request_path=str(ctx.get("request_path") or ""),
        message_id=str(ctx.get("message_id") or ""),
        model_name=str(payload.get("model_name") or ""),
        prompt_tokens=int(payload.get("prompt_tokens") or 0),
        completion_tokens=int(payload.get("completion_tokens") or 0),
        total_tokens=int(payload.get("total_tokens") or 0),
        stage=str(payload.get("stage") or ""),
    )


def _get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    init_auth_tables()
    conn = get_auth_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ?", (email.lower(),))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_user_row(user_id: int) -> Optional[Dict[str, Any]]:
    init_auth_tables()
    conn = get_auth_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _count_all_users() -> int:
    init_auth_tables()
    conn = get_auth_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(1) AS total FROM users")
        row = cursor.fetchone()
        return int((dict(row) if row else {}).get("total") or 0)
    finally:
        conn.close()


def _ensure_session_id(user_id: str, device_id: str, payload_session_id: Optional[str]) -> str:
    if payload_session_id:
        return payload_session_id
    storage = _get_storage()
    session_id = storage.generate_session_id(user_id, device_id)
    storage.store_session(user_id, session_id)
    return session_id


def _get_context_messages(storage, session_id: str) -> List[Dict[str, Any]]:
    """获取指定会话的上下文消息列表，用于 LLM 交互。"""
    try:
        history_jsons = storage.get_session_chat_list(session_id)
        if history_jsons:
            parsed = []
            for h in history_jsons:
                try:
                    m = Message.model_validate_json(h)
                    parsed.append({"role": m.role, "content": m.content})
                except Exception:
                    continue
            return parsed
        short_term = storage.get_short_term_context(session_id)
        if isinstance(short_term, dict):
            return short_term.get("messages") or []
    except Exception:
        return []
    return []


def _row_to_session_item(row: Any) -> Dict[str, str]:
    """将存储层的会话行数据转换为 API 响应模型格式。"""
    data = dict(row) if row else {}
    return {
        "session_id": str(data.get("session_id") or ""),
        "user_id": str(data.get("user_id") or ""),
        "name": str(data.get("name") or "新会话"),
        "update_time": str(data.get("update_time") or ""),
    }


def _normalize_message_payload(message: Message) -> Dict[str, Any]:
    """将内部 Message 对象转换为 API 响应中的历史消息格式。"""
    return {
        "role": str(message.role.value if hasattr(message.role, "value") else message.role),
        "content": str(message.content or ""),
        "timestamp": str(message.timestamp.isoformat() if hasattr(message.timestamp, "isoformat") else message.timestamp),
        "metadata": dict(message.metadata or {}),
        "is_redundant": bool((message.metadata or {}).get("is_redundant")),
    }


def _get_request_ip(request: Optional[Request]) -> str:
    if request is None or request.client is None:
        return ""
    return str(request.client.host or "")


def _enforce_rate_limit(
    *,
    subject_key: str,
    bucket: str,
    request_path: str,
    ip_address: str,
    max_requests: int,
    window_seconds: int,
) -> None:
    init_auth_tables()
    now_ts = int(time.time())
    window_start = now_ts - max(1, int(window_seconds))
    conn = get_auth_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rate_limit_log WHERE created_at < ?", (window_start,))
        cursor.execute(
            """
            SELECT COUNT(1) AS total FROM rate_limit_log
            WHERE subject_key = ? AND bucket = ? AND created_at >= ?
            """,
            (str(subject_key or ""), str(bucket or ""), window_start),
        )
        row = cursor.fetchone()
        current_count = int((dict(row) if row else {}).get("total") or 0)
        if current_count >= int(max_requests):
            _record_audit_log(
                action="rate_limit_blocked",
                status="blocked",
                request_path=request_path,
                ip_address=ip_address,
                detail={
                    "bucket": bucket,
                    "subject_key": subject_key,
                    "window_seconds": int(window_seconds),
                    "max_requests": int(max_requests),
                },
            )
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        cursor.execute(
            """
            INSERT INTO rate_limit_log (subject_key, bucket, request_path, ip_address, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(subject_key or ""), str(bucket or ""), str(request_path or ""), str(ip_address or ""), now_ts),
        )
        conn.commit()
    finally:
        conn.close()


def _assert_within_quota(current_user: AuthenticatedUser) -> None:
    if int(current_user.token_used or 0) >= int(current_user.token_quota or 0):
        _record_audit_log(
            action="quota_exceeded_blocked",
            status="blocked",
            user_id=current_user.user_id,
            user_email=current_user.email,
            detail={
                "token_quota": current_user.token_quota,
                "token_used": current_user.token_used,
            },
        )
        raise HTTPException(
            status_code=403,
            detail=f"您的 Token 额度已耗尽 ({current_user.token_used}/{current_user.token_quota})，请联系管理员增加额度。",
        )


def _resolve_effective_user_id(payload_user_id: Optional[str], current_user: AuthenticatedUser) -> str:
    if current_user and current_user.user_id:
        return str(current_user.user_id)
    return str(payload_user_id or "")


def _assert_session_owned(session_id: str, current_user: AuthenticatedUser) -> None:
    if not session_id:
        return
    storage = _get_storage()
    meta = storage.get_session_meta(session_id)
    if meta and meta.get("user_id"):
        if str(meta.get("user_id")) != str(current_user.user_id):
            raise HTTPException(status_code=403, detail="无权访问该会话")


def _apply_authenticated_request_guard(
    *,
    request: Optional[Request],
    request_path: str,
    bucket: str,
    current_user: AuthenticatedUser,
    session_id: str = "",
    message_id: str = "",
) -> contextvars.Token:
    ip_address = _get_request_ip(request)
    _enforce_rate_limit(
        subject_key=str(current_user.user_id),
        bucket=bucket,
        request_path=request_path,
        ip_address=ip_address,
        max_requests=100,
        window_seconds=60,
    )
    _assert_within_quota(current_user)
    if session_id:
        _assert_session_owned(session_id, current_user)

    return _set_observability_context(
        user_id=current_user.user_id,
        user_email=current_user.email,
        request_path=request_path,
        session_id=session_id,
        message_id=message_id,
        ip_address=ip_address,
    )


def _apply_authenticated_audit_context(
    *,
    request: Optional[Request],
    request_path: str,
    current_user: AuthenticatedUser,
    session_id: str = "",
    message_id: str = "",
) -> contextvars.Token:
    return _set_observability_context(
        user_id=current_user.user_id,
        user_email=current_user.email,
        request_path=request_path,
        session_id=session_id,
        message_id=message_id,
        ip_address=_get_request_ip(request),
    )


def _apply_auth_request_guard(request: Optional[Request], request_path: str, bucket: str) -> contextvars.Token:
    ip_address = _get_request_ip(request)
    _enforce_rate_limit(
        subject_key=ip_address,
        bucket=bucket,
        request_path=request_path,
        ip_address=ip_address,
        max_requests=20,
        window_seconds=60,
    )
    return _set_observability_context(
        user_id=0,
        user_email="anonymous",
        request_path=request_path,
        ip_address=ip_address,
    )
