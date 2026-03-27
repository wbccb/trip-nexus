import asyncio
import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from src.auth.middleware import (
    AuthenticatedUser,
    get_current_user,
)
from src.api.schemas.flow import (
    FlowMetricItem,
    FlowMetricsListResponse,
    FlowMetricsSummaryResponse,
    FlowControlRequest,
    FlowControlResponse,
    FlowStatusResponse,
    ReleaseGateResponse,
)
from src.api.schemas.trip import FlowStreamRequest
from src.api.dependencies import (
    _get_llm_manager,
    _apply_authenticated_request_guard,
    _reset_observability_context,
    _record_audit_log,
    _assert_within_quota,
    _assert_session_owned,
    _ensure_session_id,
)
from src.api.logic.flow import (
    _flow_streams,
    _flow_streams_lock,
    _cleanup_flow_streams,
    _run_flow_stream,
    _get_flow_state,
    _to_int,
    _query_flow_metrics_rows,
    _query_flow_metrics_summary,
    _load_latest_replay_report,
    _build_release_gate_from_data,
)

router = APIRouter(prefix="/api/flow", tags=["flow"])

@router.post("/stream")
async def stream_main_flow(
    payload: FlowStreamRequest,
    request: Request,
    message_id: Optional[str] = Query(None, description="流式消息ID"),
    last_sequence: Optional[int] = Query(None, description="断线续传序号"),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """启动或续传主流程流式任务，输出统一的 SSE 事件序列"""
    _assert_within_quota(current_user)
    if not payload.destination or payload.days <= 0:
        raise HTTPException(status_code=400, detail="destination 和 days 为必填且 days 必须大于 0")
    user_id = str(current_user.user_id)
    session_id = _ensure_session_id(user_id, payload.device_id, payload.session_id)
    if payload.session_id:
        _assert_session_owned(session_id, current_user)
    guard_token = _apply_authenticated_request_guard(
        request=request,
        current_user=current_user,
        request_path="/api/flow/stream",
        bucket="flow_stream",
        session_id=session_id,
        message_id=str(message_id or ""),
    )
    llm_manager = _get_llm_manager()
    try:
        await _cleanup_flow_streams()
        async with _flow_streams_lock:
            stream_id = message_id or f"flow-{datetime.now().strftime('%H%M%S%f')}"
            stream_state = _flow_streams.get(stream_id)
            if not stream_state:
                stream_state = {
                    "message_id": stream_id,
                    "session_id": session_id,
                    "events": [],
                    "done": False,
                    "running": False,
                    "pause_requested": False,
                    "last_status": "running",
                    "last_error": "",
                    "retry_count": 0,
                    "parent_message_id": "",
                    "last_payload": payload.model_dump(),
                    "created_at": datetime.now().timestamp(),
                    "updated_at": datetime.now().timestamp(),
                }
                _flow_streams[stream_id] = stream_state
            else:
                session_id = stream_state.get("session_id") or session_id
                stream_state["last_payload"] = payload.model_dump()
            if not stream_state.get("running") and not stream_state.get("done"):
                stream_state["running"] = True
                stream_state["pause_requested"] = False
                asyncio.create_task(
                    _run_flow_stream(
                        stream_id,
                        session_id,
                        user_id,
                        llm_manager,
                        payload,
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
                async with _flow_streams_lock:
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
                        if bool(event.get("is_final")):
                            return
                else:
                    if done:
                        return
                    await asyncio.sleep(0.2)

        _record_audit_log(
            action="flow_stream_started",
            status="accepted",
            user_id=current_user.user_id,
            user_email=current_user.email,
            session_id=session_id,
            message_id=stream_id,
            detail={"session_id": session_id},
        )
        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    finally:
        _reset_observability_context(guard_token)


@router.get("/status", response_model=FlowStatusResponse)
async def get_flow_status(
    message_id: str = Query(..., description="流式消息ID"),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> FlowStatusResponse:
    """查询指定流式任务的当前运行状态与统计信息"""
    stream_state = await _get_flow_state(message_id)
    if not stream_state:
        raise HTTPException(status_code=404, detail="未找到对应主流程消息")
    events = stream_state.get("events") if isinstance(stream_state.get("events"), list) else []
    latest_sequence = 0
    if events:
        latest_sequence = _to_int(events[-1].get("sequence"), 0) if isinstance(events[-1], dict) else 0
    status_name = str(stream_state.get("last_status") or "running")
    if bool(stream_state.get("pause_requested")) and not bool(stream_state.get("done")):
        status_name = "paused"
    if str(stream_state.get("session_id") or ""):
        _assert_session_owned(str(stream_state.get("session_id") or ""), current_user)
    return FlowStatusResponse(
        message_id=str(stream_state.get("message_id") or message_id),
        session_id=str(stream_state.get("session_id") or ""),
        running=bool(stream_state.get("running")),
        done=bool(stream_state.get("done")),
        paused=bool(stream_state.get("pause_requested")),
        status=status_name,
        retry_count=_to_int(stream_state.get("retry_count"), 0),
        has_error=bool(str(stream_state.get("last_error") or "").strip()),
        last_error=str(stream_state.get("last_error") or "") or None,
        latest_sequence=latest_sequence,
        event_count=len(events),
        created_at=float(stream_state.get("created_at") or 0.0),
        updated_at=float(stream_state.get("updated_at") or 0.0),
    )


@router.post("/control", response_model=FlowControlResponse)
async def control_flow(
    payload: FlowControlRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> FlowControlResponse:
    """控制主流程执行，支持暂停、恢复或重试"""
    guard_token = _apply_authenticated_request_guard(
        request=request,
        current_user=current_user,
        request_path="/api/flow/control",
        bucket="flow_control",
        message_id=str(payload.message_id or ""),
    )
    action = str(payload.action or "").strip().lower()
    if action not in {"pause", "resume", "retry"}:
        raise HTTPException(status_code=400, detail="action 仅支持 pause/resume/retry")
    try:
        await _cleanup_flow_streams()
        async with _flow_streams_lock:
            stream_state = _flow_streams.get(payload.message_id)
            if not stream_state:
                raise HTTPException(status_code=404, detail="未找到对应主流程消息")
            if str(stream_state.get("session_id") or ""):
                _assert_session_owned(str(stream_state.get("session_id") or ""), current_user)
            if action == "pause":
                if bool(stream_state.get("done")):
                    return FlowControlResponse(
                        message_id=payload.message_id,
                        action=action,
                        accepted=False,
                        status=str(stream_state.get("last_status") or "done"),
                        detail="流程已结束，无法暂停",
                    )
                stream_state["pause_requested"] = True
                stream_state["updated_at"] = datetime.now().timestamp()
                _record_audit_log(action="flow_control", status="success", user_id=current_user.user_id, user_email=current_user.email, detail={"action": action, "message_id": payload.message_id})
                return FlowControlResponse(
                    message_id=payload.message_id,
                    action=action,
                    accepted=True,
                    status="paused",
                    detail="已标记暂停，执行线程将在检查点暂停",
                )
            if action == "resume":
                if bool(stream_state.get("done")):
                    return FlowControlResponse(
                        message_id=payload.message_id,
                        action=action,
                        accepted=False,
                        status=str(stream_state.get("last_status") or "done"),
                        detail="流程已结束，无法恢复",
                    )
                stream_state["pause_requested"] = False
                stream_state["updated_at"] = datetime.now().timestamp()
                _record_audit_log(action="flow_control", status="success", user_id=current_user.user_id, user_email=current_user.email, detail={"action": action, "message_id": payload.message_id})
                return FlowControlResponse(
                    message_id=payload.message_id,
                    action=action,
                    accepted=True,
                    status="running",
                    detail="已恢复执行",
                )
            if bool(stream_state.get("running")):
                return FlowControlResponse(
                    message_id=payload.message_id,
                    action=action,
                    accepted=False,
                    status="running",
                    detail="流程运行中，暂不支持并发重试，请先暂停或等待结束",
                )
            last_payload = stream_state.get("last_payload")
            if not isinstance(last_payload, dict):
                return FlowControlResponse(
                    message_id=payload.message_id,
                    action=action,
                    accepted=False,
                    status=str(stream_state.get("last_status") or "failed"),
                    detail="缺少重试请求参数，无法重试",
                )
            retry_message_id = f"{payload.message_id}-retry-{datetime.now().strftime('%H%M%S%f')}"
            stream_state["retry_count"] = _to_int(stream_state.get("retry_count"), 0) + 1
            retry_payload = FlowStreamRequest(**last_payload)
            retry_state = {
                "message_id": retry_message_id,
                "session_id": str(stream_state.get("session_id") or ""),
                "events": [],
                "done": False,
                "running": True,
                "pause_requested": False,
                "last_status": "running",
                "last_error": "",
                "retry_count": _to_int(stream_state.get("retry_count"), 0),
                "parent_message_id": payload.message_id,
                "last_payload": retry_payload.model_dump(),
                "created_at": datetime.now().timestamp(),
                "updated_at": datetime.now().timestamp(),
            }
            _flow_streams[retry_message_id] = retry_state
            llm_manager = _get_llm_manager()
            session_id = str(stream_state.get("session_id") or retry_payload.session_id or "")
            asyncio.create_task(
                _run_flow_stream(
                    retry_message_id,
                    session_id,
                    str(current_user.user_id),
                    llm_manager,
                    retry_payload,
                )
            )
            _record_audit_log(action="flow_control", status="success", user_id=current_user.user_id, user_email=current_user.email, detail={"action": action, "message_id": payload.message_id, "next_message_id": retry_message_id})
            return FlowControlResponse(
                message_id=payload.message_id,
                action=action,
                accepted=True,
                status="running",
                next_message_id=retry_message_id,
                detail="已创建重试任务，请使用 next_message_id 继续拉流",
            )
    finally:
        _reset_observability_context(guard_token)


@router.get("/metrics", response_model=FlowMetricsListResponse)
def list_flow_metrics(
    start_time: Optional[str] = Query(None, description="起始时间（ISO8601）"),
    end_time: Optional[str] = Query(None, description="结束时间（ISO8601）"),
    mode: Optional[str] = Query(None, description="执行模式 fast/deep"),
    intent: Optional[str] = Query(None, description="意图筛选"),
    status: Optional[str] = Query(None, description="状态筛选 done/failed"),
    device_id: Optional[str] = Query(None, description="设备ID"),
    session_id: Optional[str] = Query(None, description="会话ID"),
    agent_escalated: Optional[bool] = Query(None, description="是否升级Agent"),
    rag_hit: Optional[bool] = Query(None, description="是否命中RAG"),
    limit: int = Query(50, ge=1, le=500, description="分页条数"),
    offset: int = Query(0, ge=0, description="分页偏移"),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> FlowMetricsListResponse:
    """查询主流程执行指标明细列表"""
    total, items = _query_flow_metrics_rows(
        start_time=start_time,
        end_time=end_time,
        mode=mode,
        intent=intent,
        status=status,
        user_id=str(current_user.user_id),
        device_id=device_id,
        session_id=session_id,
        agent_escalated=agent_escalated,
        rag_hit=rag_hit,
        limit=limit,
        offset=offset,
    )
    return FlowMetricsListResponse(total=total, items=[FlowMetricItem(**item) for item in items])


@router.get("/metrics/summary", response_model=FlowMetricsSummaryResponse)
def summary_flow_metrics(
    start_time: Optional[str] = Query(None, description="起始时间（ISO8601）"),
    end_time: Optional[str] = Query(None, description="结束时间（ISO8601）"),
    mode: Optional[str] = Query(None, description="执行模式 fast/deep"),
    intent: Optional[str] = Query(None, description="意图筛选"),
    status: Optional[str] = Query(None, description="状态筛选 done/failed"),
    device_id: Optional[str] = Query(None, description="设备ID"),
    session_id: Optional[str] = Query(None, description="会话ID"),
    agent_escalated: Optional[bool] = Query(None, description="是否升级Agent"),
    rag_hit: Optional[bool] = Query(None, description="是否命中RAG"),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> FlowMetricsSummaryResponse:
    """获取主流程执行指标的聚合统计摘要"""
    summary = _query_flow_metrics_summary(
        start_time=start_time,
        end_time=end_time,
        mode=mode,
        intent=intent,
        status=status,
        user_id=str(current_user.user_id),
        device_id=device_id,
        session_id=session_id,
        agent_escalated=agent_escalated,
        rag_hit=rag_hit,
    )
    return FlowMetricsSummaryResponse(**summary)


@router.get("/release_gate", response_model=ReleaseGateResponse)
def flow_release_gate(current_user: AuthenticatedUser = Depends(get_current_user)) -> ReleaseGateResponse:
    """判定当前系统状态是否满足发布门槛要求"""
    metrics_summary = _query_flow_metrics_summary(
        start_time=None, end_time=None, mode=None, intent=None, status=None,
        user_id=None, device_id=None, session_id=None, agent_escalated=None, rag_hit=None,
    )
    replay_report = _load_latest_replay_report()
    return _build_release_gate_from_data(metrics_summary, replay_report)
