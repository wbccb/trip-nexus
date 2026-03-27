from typing import List, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from src.auth.middleware import (
    AuthenticatedUser,
    get_current_user,
)
from src.api.schemas.trip import (
    TripUpdateRequest,
    TripUpdateResponse,
    TripReplanDayRequest,
    TripReplanDayResponse,
    AgentEscalationInfo,
)
from src.api.dependencies import (
    _get_storage,
    _get_llm_manager,
    _apply_authenticated_request_guard,
    _reset_observability_context,
    _record_audit_log,
    _assert_within_quota,
    _assert_session_owned,
    _ensure_session_id,
)
from src.api.logic.trip import (
    _normalize_trip_constraints,
    _build_constraint_statuses,
    _normalize_daily_plan,
    _normalize_locked_days,
    _build_replan_context,
    _detect_replan_escalation,
    _merge_day_items_by_scope,
)

router = APIRouter(prefix="/api/trip", tags=["trip"])

@router.put("/update", response_model=TripUpdateResponse)
def update_trip(
    payload: TripUpdateRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> TripUpdateResponse:
    """更新行程数据并重新校验冲突与约束"""
    user_id = str(current_user.user_id)
    session_id = _ensure_session_id(user_id, payload.device_id, payload.session_id)
    if payload.session_id:
        _assert_session_owned(session_id, current_user)
    guard_token = _apply_authenticated_request_guard(
        request=request,
        current_user=current_user,
        request_path="/api/trip/update",
        bucket="trip_update",
        session_id=session_id,
    )
    try:
        storage = _get_storage()
        llm_manager = _get_llm_manager()
        trip_data = dict(payload.trip_data or {})
        constraints_used = _normalize_trip_constraints(payload.constraints or trip_data.get("constraints_used") or {})
        constraints_satisfied = _build_constraint_statuses(trip_data, constraints_used)
        conflict_report = llm_manager.build_conflict_report(trip_data, constraints_used).model_dump()
        trip_data["constraints_used"] = constraints_used
        trip_data["constraints_satisfied"] = constraints_satisfied
        trip_data["conflict_report"] = conflict_report
        storage.store_trip_data(session_id, trip_data)
        _record_audit_log(
            action="trip_update",
            status="success",
            user_id=current_user.user_id,
            user_email=current_user.email,
            session_id=session_id,
            detail={"has_conflicts": bool(conflict_report.get("has_conflicts"))},
        )
        return TripUpdateResponse(session_id=session_id, trip_data=trip_data)
    finally:
        _reset_observability_context(guard_token)


@router.post("/replan_day", response_model=TripReplanDayResponse)
def replan_trip_day(
    payload: TripReplanDayRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> TripReplanDayResponse:
    """局部重排特定天数或时段的行程，支持跨天联动检测"""
    _assert_within_quota(current_user)
    user_id = str(current_user.user_id)
    session_id = _ensure_session_id(user_id, payload.device_id, payload.session_id)
    if payload.session_id:
        _assert_session_owned(session_id, current_user)
    guard_token = _apply_authenticated_request_guard(
        request=request,
        current_user=current_user,
        request_path="/api/trip/replan_day",
        bucket="trip_replan",
        session_id=session_id,
    )
    try:
        storage = _get_storage()
        llm_manager = _get_llm_manager()
        current_trip = storage.get_trip_data(session_id)
        if not current_trip:
            raise HTTPException(status_code=404, detail="未找到现有行程，请先生成行程")
        target_day = int(payload.scope.day if payload.scope else payload.day)
        time_range = str(payload.scope.time_range if payload.scope else "").strip().lower() or None
        locked_days = _normalize_locked_days(payload.locked_days, target_day)
        replan_instruction = str(payload.replan_instruction or "").strip()
        constraints_used = _normalize_trip_constraints(payload.constraints or current_trip.get("constraints_used") or {})
        escalation_info = _detect_replan_escalation(current_trip, target_day, time_range, locked_days)
        replan_context = _build_replan_context(current_trip, target_day, time_range, locked_days, replan_instruction)
        replan_result = llm_manager.replan_trip_day(
            target_day=target_day,
            context=replan_context,
            constraints=constraints_used,
            escalate=bool(escalation_info.get("escalated")),
        )
        new_daily_plan = replan_result.get("daily_plan") or {}
        merged_daily_plan = _normalize_daily_plan(current_trip)
        for day_str, items in new_daily_plan.items():
            day_idx = int(day_str)
            if day_idx == target_day:
                merged_daily_plan[day_str] = _merge_day_items_by_scope(
                    original_items=merged_daily_plan.get(day_str, []),
                    replanned_items=items,
                    time_range=time_range,
                )
            else:
                merged_daily_plan[day_str] = items
        next_trip_data = dict(current_trip)
        next_trip_data["daily_plan"] = merged_daily_plan
        next_trip_data["constraints_used"] = constraints_used
        next_trip_data["constraints_satisfied"] = _build_constraint_statuses(next_trip_data, constraints_used)
        conflict_report = llm_manager.build_conflict_report(next_trip_data, constraints_used).model_dump()
        next_trip_data["conflict_report"] = conflict_report
        storage.store_trip_data(session_id, next_trip_data)
        _record_audit_log(
            action="trip_replan",
            status="success",
            user_id=current_user.user_id,
            user_email=current_user.email,
            session_id=session_id,
            detail={
                "target_day": target_day,
                "time_range": time_range,
                "escalated": bool(escalation_info.get("escalated")),
            },
        )
        return TripReplanDayResponse(
            session_id=session_id,
            trip_data=next_trip_data,
            replanned_scope={"day": target_day, "time_range": time_range},
            agent_escalation=AgentEscalationInfo(
                escalated=bool(escalation_info.get("escalated")),
                reasons=list(escalation_info.get("reasons") or []),
                message=str(escalation_info.get("message") or ""),
            ),
            conflict_report=conflict_report,
        )
    finally:
        _reset_observability_context(guard_token)
