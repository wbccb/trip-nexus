import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from src.auth.middleware import (
    AuthenticatedUser,
    get_current_user,
)
from src.frontend.context.entity import Message, MessageType
from src.api.schemas.chat import ChatSendRequest, ChatSendResponse
from src.api.dependencies import (
    _get_storage,
    _get_llm_manager,
    _get_conversation_manager,
    _apply_authenticated_request_guard,
    _reset_observability_context,
    _record_audit_log,
    _assert_within_quota,
    _assert_session_owned,
    _ensure_session_id,
    _get_context_messages,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])

@router.post("/send", response_model=ChatSendResponse)
def send_chat(
    payload: ChatSendRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> ChatSendResponse:
    """发送聊天消息并获取助手回复，支持意图识别与行程生成/修改"""
    _assert_within_quota(current_user)
    if not payload.message:
        raise HTTPException(status_code=400, detail="message 不能为空")
    user_id = str(current_user.user_id)
    session_id = _ensure_session_id(user_id, payload.device_id, payload.session_id)
    if payload.session_id:
        _assert_session_owned(session_id, current_user)
    guard_token = _apply_authenticated_request_guard(
        request=request,
        current_user=current_user,
        request_path="/api/chat/send",
        bucket="chat_send",
        session_id=session_id,
    )
    try:
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
            user_id,
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
        cleaned_response = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL).strip()
        assistant_message = Message(
            role=MessageType.ASSISTANT,
            content=cleaned_response,
            timestamp=datetime.now(),
            metadata={
                "intent": intent,
                "needs_more_info": needs_more_info,
                "has_trip_data": bool(trip_data),
            },
        )
        conversation_manager.process_new_message(
            user_id,
            payload.device_id,
            assistant_message,
            session_id,
        )
        _record_audit_log(
            action="chat_send",
            status="success",
            user_id=current_user.user_id,
            user_email=current_user.email,
            session_id=session_id,
            detail={"intent": intent, "has_trip_data": bool(trip_data)},
        )
        return ChatSendResponse(
            session_id=session_id,
            response=response_text,
            trip_data=trip_data,
            intent=intent,
            needs_more_info=needs_more_info,
        )
    finally:
        _reset_observability_context(guard_token)
