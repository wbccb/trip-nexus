import logging
from typing import List, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from src.auth.middleware import (
    AuthenticatedUser,
    get_current_user,
)
from src.api.schemas.trip import (
    TripUpdateRequest,
    TripUpdateResponse,
    TripConflictPreviewRequest,
    TripConflictPreviewResponse,
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
from src.observability import log_event

router = APIRouter(prefix="/api/trip", tags=["trip"])
logger = logging.getLogger(__name__)

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
        current_trip = storage.get_trip_data(session_id) or {}
        trip_data = dict(payload.trip_data or {})
        previous_conflict_report = current_trip.get("conflict_report") if isinstance(current_trip, dict) else {}
        incoming_conflict_report = trip_data.get("conflict_report") if isinstance(trip_data.get("conflict_report"), dict) else {}
        inferred_apply_alternative = bool((previous_conflict_report or {}).get("has_conflicts")) and bool(
            (previous_conflict_report or {}).get("alternatives")
        ) and not bool((incoming_conflict_report or {}).get("has_conflicts"))
        update_source = str(payload.update_source or "").strip() or (
            "apply_conflict_alternative" if inferred_apply_alternative else "manual_edit_or_save"
        )
        is_applying_alternative = update_source == "apply_conflict_alternative"
        # 核心步骤：在真正重算约束和冲突前，先明确这次 update_trip 是普通保存还是“采用替代方案”。
        log_event(
            logger,
            logging.INFO,
            "行程更新请求开始",
            {
                "session_id": session_id,
                "更新来源": update_source,
                "原有冲突数": len((previous_conflict_report or {}).get("conflicts") or []),
                "原有替代方案数": len((previous_conflict_report or {}).get("alternatives") or []),
                "新目的地": trip_data.get("destination"),
                "新天数": trip_data.get("days"),
                "采用方案标签": payload.selected_alternative_label,
                "采用方案索引": payload.selected_alternative_index,
            },
        )
        if is_applying_alternative:
            log_event(
                logger,
                logging.INFO,
                "用户已选择冲突替代方案，开始更新正式行程",
                {
                    "session_id": session_id,
                    "方案标签": payload.selected_alternative_label,
                    "方案索引": payload.selected_alternative_index,
                    "说明": "后端将基于用户选中的替代方案重算约束与冲突，并覆盖当前正式行程",
                },
            )
        constraints_used = _normalize_trip_constraints(payload.constraints or trip_data.get("constraints_used") or {})
        constraints_satisfied = _build_constraint_statuses(trip_data, constraints_used)
        if is_applying_alternative:
            log_event(
                logger,
                logging.INFO,
                "采用替代方案后开始冲突复检",
                {
                    "session_id": session_id,
                    "方案标签": payload.selected_alternative_label,
                    "方案索引": payload.selected_alternative_index,
                    "说明": "这里只做冲突复检，不再重复生成新的替代方案",
                },
            )
        conflict_report = llm_manager.build_conflict_report(
            trip_data,
            constraints_used,
            include_alternatives=not is_applying_alternative,
        ).model_dump()
        trip_data["constraints_used"] = constraints_used
        trip_data["constraints_satisfied"] = constraints_satisfied
        trip_data["conflict_report"] = conflict_report
        storage.store_trip_data(session_id, trip_data)
        if is_applying_alternative:
            log_event(
                logger,
                logging.INFO,
                "采用替代方案后的冲突复检完成",
                {
                    "session_id": session_id,
                    "当前阻断冲突": bool(conflict_report.get("has_conflicts")),
                    "当前冲突总数": len(conflict_report.get("conflicts") or []),
                    "当前替代方案数": len(conflict_report.get("alternatives") or []),
                },
            )
        log_event(
            logger,
            logging.INFO,
            "行程更新成功",
            {
                "session_id": session_id,
                "更新来源": update_source,
                "当前阻断冲突": bool(conflict_report.get("has_conflicts")),
                "当前冲突总数": len(conflict_report.get("conflicts") or []),
                "当前替代方案数": len(conflict_report.get("alternatives") or []),
                "约束校验数": len(constraints_satisfied),
                "采用方案标签": payload.selected_alternative_label,
                "采用方案索引": payload.selected_alternative_index,
            },
        )
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


@router.post("/conflict/preview", response_model=TripConflictPreviewResponse)
def preview_conflict_alternative(
    payload: TripConflictPreviewRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> TripConflictPreviewResponse:
    """记录用户当前预览的冲突替代方案，便于在终端还原“预览 -> 采用”的交互链路。"""
    user_id = str(current_user.user_id)
    session_id = _ensure_session_id(user_id, payload.device_id, payload.session_id)
    if payload.session_id:
        _assert_session_owned(session_id, current_user)
    guard_token = _apply_authenticated_request_guard(
        request=request,
        current_user=current_user,
        request_path="/api/trip/conflict/preview",
        bucket="trip_conflict_preview",
        session_id=session_id,
    )
    try:
        log_event(
            logger,
            logging.INFO,
            "用户当前预览冲突替代方案",
            {
                "session_id": session_id,
                "方案标签": payload.alternative_label,
                "方案索引": payload.alternative_index,
            },
        )
        return TripConflictPreviewResponse(session_id=session_id, ok=True)
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
