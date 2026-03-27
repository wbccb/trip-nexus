from typing import List, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.auth.middleware import (
    AuthenticatedUser,
    get_current_user,
)
from src.frontend.context.entity import Message
from src.api.schemas.session import (
    StartSessionRequest,
    StartSessionResponse,
    SessionItem,
    ChatHistoryItem,
    DeleteSessionResponse,
)
from src.api.schemas.trip import TripDataResponse
from src.api.dependencies import (
    _get_storage,
    _apply_authenticated_audit_context,
    _reset_observability_context,
    _record_audit_log,
    _assert_session_owned,
    _row_to_session_item,
    _normalize_message_payload,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

@router.post("/start", response_model=StartSessionResponse)
def start_session(
    request: Request,
    payload: StartSessionRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> StartSessionResponse:
    """创建新会话并返回会话ID"""
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path="/api/sessions/start",
    )
    try:
        storage = _get_storage()
        user_id = str(current_user.user_id)
        session_id = storage.generate_session_id(user_id, payload.device_id)
        storage.store_session(user_id, session_id)
        _record_audit_log(
            action="session_start",
            status="success",
            user_id=current_user.user_id,
            user_email=current_user.email,
            session_id=session_id,
            detail={"device_id": payload.device_id},
        )
        return StartSessionResponse(session_id=session_id)
    finally:
        _reset_observability_context(guard_token)


@router.get("/list", response_model=List[SessionItem])
def list_sessions(request: Request, current_user: AuthenticatedUser = Depends(get_current_user)) -> List[SessionItem]:
    """获取指定用户的会话列表"""
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path="/api/sessions/list",
    )
    try:
        storage = _get_storage()
        rows = storage.get_session_list(str(current_user.user_id))
        items = [SessionItem(**_row_to_session_item(row)) for row in rows]
        _record_audit_log(
            action="session_list",
            status="success",
            user_id=current_user.user_id,
            user_email=current_user.email,
            detail={"session_count": len(items)},
        )
        return items
    finally:
        _reset_observability_context(guard_token)


@router.get("/history", response_model=List[ChatHistoryItem])
def session_history(
    request: Request,
    session_id: str = Query(..., description="会话ID"),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> List[ChatHistoryItem]:
    """获取指定会话的聊天历史记录"""
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path="/api/sessions/history",
        session_id=session_id,
    )
    try:
        _assert_session_owned(session_id, current_user)
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
                _record_audit_log(
                    action="session_history",
                    status="success",
                    user_id=current_user.user_id,
                    user_email=current_user.email,
                    session_id=session_id,
                    detail={"message_count": len(parsed_messages)},
                )
                return parsed_messages
            short_term_context = storage.get_short_term_context(session_id)
            if isinstance(short_term_context, dict):
                messages = short_term_context.get("messages") or []
                result = [ChatHistoryItem(**item) for item in messages if isinstance(item, dict)]
                _record_audit_log(
                    action="session_history",
                    status="success",
                    user_id=current_user.user_id,
                    user_email=current_user.email,
                    session_id=session_id,
                    detail={"message_count": len(result)},
                )
                return result
            _record_audit_log(
                action="session_history",
                status="success",
                user_id=current_user.user_id,
                user_email=current_user.email,
                session_id=session_id,
                detail={"message_count": 0},
            )
            return []
        except Exception:
            _record_audit_log(
                action="session_history",
                status="fallback_empty",
                user_id=current_user.user_id,
                user_email=current_user.email,
                session_id=session_id,
            )
            return []
    finally:
        _reset_observability_context(guard_token)


@router.get("/trip", response_model=TripDataResponse)
def session_trip(
    request: Request,
    session_id: str = Query(..., description="会话ID"),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> TripDataResponse:
    """获取指定会话的行程数据"""
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path="/api/sessions/trip",
        session_id=session_id,
    )
    try:
        _assert_session_owned(session_id, current_user)
        storage = _get_storage()
        trip_data = storage.get_trip_data(session_id)
        _record_audit_log(
            action="session_trip",
            status="success",
            user_id=current_user.user_id,
            user_email=current_user.email,
            session_id=session_id,
            detail={"has_trip_data": bool(trip_data)},
        )
        return TripDataResponse(session_id=session_id, trip_data=trip_data)
    finally:
        _reset_observability_context(guard_token)


@router.delete("/delete", response_model=DeleteSessionResponse)
def delete_session(
    request: Request,
    session_id: str = Query(..., description="会话ID"),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> DeleteSessionResponse:
    """删除指定会话及其所有相关数据"""
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path="/api/sessions/delete",
        session_id=session_id,
    )
    try:
        _assert_session_owned(session_id, current_user)
        storage = _get_storage()
        storage.delete_session(session_id)
        _record_audit_log(
            action="session_delete",
            status="success",
            user_id=current_user.user_id,
            user_email=current_user.email,
            session_id=session_id,
        )
        return DeleteSessionResponse(session_id=session_id, success=True)
    finally:
        _reset_observability_context(guard_token)
