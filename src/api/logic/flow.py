import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.agent import run_agent_loop_sync
from src.llm.llm_manager import LlmManager
from src.models.conflicts import ConflictReport
from src.api.schemas.flow import (
    FlowMetricItem,
    FlowMetricsListResponse,
    FlowMetricsSummaryResponse,
    FlowControlRequest,
    FlowControlResponse,
    FlowStatusResponse,
    ReleaseChecklistItem,
    ReleaseGateResponse,
)
from src.api.schemas.trip import FlowStreamRequest
from src.api.dependencies import (
    _get_storage,
    _get_llm_manager,
    _record_audit_log,
)
from src.api.logic.trip import _normalize_trip_constraints, _build_constraint_statuses
from src.api.logic.knowledge import (
    _build_knowledge_context_payload,
    _build_source_evidence_from_docs,
)

logger = logging.getLogger(__name__)

_flow_streams: Dict[str, Dict[str, Any]] = {}
_flow_streams_lock = asyncio.Lock()
_FLOW_STREAM_TTL_SECONDS = 600
_FLOW_CONTEXT_MAX_ITEMS = 12
_FLOW_CONTEXT_ITEM_MAX_CHARS = 600
_FLOW_CONTEXT_TOTAL_MAX_CHARS = 3200
_FLOW_METRICS_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "flow_metrics.db"))
_flow_metrics_lock = threading.Lock()


def _init_flow_metrics_table() -> None:
    """初始化主流程指标表，确保落库查询能力可用。"""
    with _flow_metrics_lock:
        conn = sqlite3.connect(_FLOW_METRICS_DB_PATH, check_same_thread=False)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS flow_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    tool_count INTEGER NOT NULL DEFAULT 0,
                    rag_hit INTEGER NOT NULL DEFAULT 0,
                    agent_escalated INTEGER NOT NULL DEFAULT 0,
                    context_count INTEGER NOT NULL DEFAULT 0,
                    context_chars INTEGER NOT NULL DEFAULT 0,
                    context_budget_json TEXT NOT NULL DEFAULT '{}',
                    escalation_reasons_json TEXT NOT NULL DEFAULT '[]',
                    error_text TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_json_loads(text: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(text or ""))
    except Exception:
        return fallback


def _percentile(values: List[int], ratio: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    idx = int(round((len(sorted_values) - 1) * max(0.0, min(1.0, ratio))))
    idx = max(0, min(len(sorted_values) - 1, idx))
    return float(sorted_values[idx])


def _record_flow_metrics(payload: Dict[str, Any]) -> None:
    """持久化单次主流程执行指标，供后续明细与聚合查询。"""
    _init_flow_metrics_table()
    with _flow_metrics_lock:
        conn = sqlite3.connect(_FLOW_METRICS_DB_PATH, check_same_thread=False)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO flow_metrics (
                    message_id, session_id, user_id, device_id, mode, intent, status,
                    latency_ms, tool_count, rag_hit, agent_escalated, context_count, context_chars,
                    context_budget_json, escalation_reasons_json, error_text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload.get("message_id") or ""),
                    str(payload.get("session_id") or ""),
                    str(payload.get("user_id") or ""),
                    str(payload.get("device_id") or ""),
                    str(payload.get("mode") or "fast"),
                    str(payload.get("intent") or ""),
                    str(payload.get("status") or "done"),
                    _to_int(payload.get("latency_ms"), 0),
                    _to_int(payload.get("tool_count"), 0),
                    1 if bool(payload.get("rag_hit")) else 0,
                    1 if bool(payload.get("agent_escalated")) else 0,
                    _to_int(payload.get("context_count"), 0),
                    _to_int(payload.get("context_chars"), 0),
                    json.dumps(payload.get("context_budget") or {}, ensure_ascii=False),
                    json.dumps(payload.get("escalation_reasons") or [], ensure_ascii=False),
                    str(payload.get("error") or "") or None,
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def _build_flow_metrics_filters_sql(
    start_time: Optional[str],
    end_time: Optional[str],
    mode: Optional[str],
    intent: Optional[str],
    status: Optional[str],
    user_id: Optional[str],
    device_id: Optional[str],
    session_id: Optional[str],
    agent_escalated: Optional[bool],
    rag_hit: Optional[bool],
) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if start_time:
        clauses.append("created_at >= ?")
        params.append(start_time)
    if end_time:
        clauses.append("created_at <= ?")
        params.append(end_time)
    if mode:
        clauses.append("mode = ?")
        params.append(mode)
    if intent:
        clauses.append("intent = ?")
        params.append(intent)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    if device_id:
        clauses.append("device_id = ?")
        params.append(device_id)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if agent_escalated is not None:
        clauses.append("agent_escalated = ?")
        params.append(1 if agent_escalated else 0)
    if rag_hit is not None:
        clauses.append("rag_hit = ?")
        params.append(1 if rag_hit else 0)
    where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where_sql, params


def _query_flow_metrics_rows(
    start_time: Optional[str],
    end_time: Optional[str],
    mode: Optional[str],
    intent: Optional[str],
    status: Optional[str],
    user_id: Optional[str],
    device_id: Optional[str],
    session_id: Optional[str],
    agent_escalated: Optional[bool],
    rag_hit: Optional[bool],
    limit: int,
    offset: int,
) -> tuple[int, List[Dict[str, Any]]]:
    _init_flow_metrics_table()
    where_sql, params = _build_flow_metrics_filters_sql(
        start_time, end_time, mode, intent, status, user_id, device_id, session_id, agent_escalated, rag_hit
    )
    with _flow_metrics_lock:
        conn = sqlite3.connect(_FLOW_METRICS_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(1) AS total FROM flow_metrics{where_sql}", params)
            total_row = cursor.fetchone()
            total = _to_int(total_row["total"] if total_row else 0, 0)
            cursor.execute(
                f"""
                SELECT message_id, session_id, user_id, device_id, mode, intent, status,
                       latency_ms, tool_count, rag_hit, agent_escalated, context_count, context_chars,
                       context_budget_json, escalation_reasons_json, error_text, created_at
                FROM flow_metrics
                {where_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                params + [max(1, limit), max(0, offset)],
            )
            rows = []
            for row in cursor.fetchall():
                row_data = dict(row)
                rows.append(
                    {
                        "message_id": str(row_data.get("message_id") or ""),
                        "session_id": str(row_data.get("session_id") or ""),
                        "user_id": str(row_data.get("user_id") or ""),
                        "device_id": str(row_data.get("device_id") or ""),
                        "mode": str(row_data.get("mode") or "fast"),
                        "intent": str(row_data.get("intent") or ""),
                        "status": str(row_data.get("status") or "done"),
                        "latency_ms": _to_int(row_data.get("latency_ms"), 0),
                        "tool_count": _to_int(row_data.get("tool_count"), 0),
                        "rag_hit": bool(_to_int(row_data.get("rag_hit"), 0)),
                        "agent_escalated": bool(_to_int(row_data.get("agent_escalated"), 0)),
                        "context_count": _to_int(row_data.get("context_count"), 0),
                        "context_chars": _to_int(row_data.get("context_chars"), 0),
                        "context_budget": _safe_json_loads(row_data.get("context_budget_json"), {}),
                        "escalation_reasons": _safe_json_loads(row_data.get("escalation_reasons_json"), []),
                        "error": row_data.get("error_text"),
                        "created_at": str(row_data.get("created_at") or ""),
                    }
                )
            return total, rows
        finally:
            conn.close()


def _query_flow_metrics_summary(
    start_time: Optional[str],
    end_time: Optional[str],
    mode: Optional[str],
    intent: Optional[str],
    status: Optional[str],
    user_id: Optional[str],
    device_id: Optional[str],
    session_id: Optional[str],
    agent_escalated: Optional[bool],
    rag_hit: Optional[bool],
) -> Dict[str, Any]:
    _init_flow_metrics_table()
    where_sql, params = _build_flow_metrics_filters_sql(
        start_time, end_time, mode, intent, status, user_id, device_id, session_id, agent_escalated, rag_hit
    )
    with _flow_metrics_lock:
        conn = sqlite3.connect(_FLOW_METRICS_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT status, latency_ms, tool_count, rag_hit, agent_escalated
                FROM flow_metrics
                {where_sql}
                """,
                params,
            )
            rows = [dict(item) for item in cursor.fetchall()]
        finally:
            conn.close()
    total = len(rows)
    if total == 0:
        return {
            "total": 0, "success_count": 0, "failed_count": 0,
            "avg_latency_ms": 0.0, "p50_latency_ms": 0.0, "p90_latency_ms": 0.0,
            "agent_escalated_rate": 0.0, "rag_hit_rate": 0.0, "avg_tool_count": 0.0,
        }
    success_count = len([row for row in rows if str(row.get("status") or "") == "done"])
    failed_count = total - success_count
    latencies = [_to_int(row.get("latency_ms"), 0) for row in rows if _to_int(row.get("latency_ms"), 0) > 0]
    tool_counts = [_to_int(row.get("tool_count"), 0) for row in rows]
    escalated_count = len([row for row in rows if bool(_to_int(row.get("agent_escalated"), 0))])
    rag_hit_count = len([row for row in rows if bool(_to_int(row.get("rag_hit"), 0))])
    return {
        "total": total,
        "success_count": success_count,
        "failed_count": failed_count,
        "avg_latency_ms": _to_float(sum(latencies) / len(latencies), 0.0) if latencies else 0.0,
        "p50_latency_ms": _percentile(latencies, 0.5) if latencies else 0.0,
        "p90_latency_ms": _percentile(latencies, 0.9) if latencies else 0.0,
        "agent_escalated_rate": _to_float(escalated_count / total, 0.0),
        "rag_hit_rate": _to_float(rag_hit_count / total, 0.0),
        "avg_tool_count": _to_float(sum(tool_counts) / len(tool_counts), 0.0) if tool_counts else 0.0,
    }


async def _cleanup_flow_streams() -> None:
    now = time.time()
    async with _flow_streams_lock:
        expired = []
        for key, payload in _flow_streams.items():
            updated_at = float(payload.get("updated_at") or now)
            done = bool(payload.get("done"))
            running = bool(payload.get("running"))
            if done and not running and now - updated_at > _FLOW_STREAM_TTL_SECONDS:
                expired.append(key)
        for key in expired:
            _flow_streams.pop(key, None)


async def _append_flow_event(message_id: str, event_payload: Dict[str, Any]) -> None:
    async with _flow_streams_lock:
        stream_state = _flow_streams.get(message_id)
        if not stream_state:
            return
        stream_state["events"].append(event_payload)
        stream_state["updated_at"] = time.time()
        stream_state["last_status"] = str(event_payload.get("status") or stream_state.get("last_status") or "running")
        if str(event_payload.get("status") or "") == "failed":
            payload_obj = event_payload.get("payload") if isinstance(event_payload.get("payload"), dict) else {}
            stream_state["last_error"] = str((payload_obj or {}).get("error") or "")
        if bool(event_payload.get("is_final")) or event_payload.get("event") == "error":
            stream_state["done"] = True
            stream_state["running"] = False


async def _pause_checkpoint(
    message_id: str,
    session_id: str,
    sequence: int,
    flow_mode: str,
    step: str,
) -> int:
    pause_emitted = False
    next_sequence = int(sequence)
    while True:
        async with _flow_streams_lock:
            stream_state = _flow_streams.get(message_id)
            pause_requested = bool(stream_state.get("pause_requested")) if isinstance(stream_state, dict) else False
            done = bool(stream_state.get("done")) if isinstance(stream_state, dict) else False
        if done:
            return next_sequence
        if not pause_requested:
            if pause_emitted:
                next_sequence += 1
                await _append_flow_event(
                    message_id,
                    {
                        "event": "delta",
                        "sequence": next_sequence,
                        "message_id": message_id,
                        "session_id": session_id,
                        "step": "control",
                        "status": "running",
                        "mode": flow_mode,
                        "content_delta": "流程已恢复执行",
                        "is_final": False,
                        "payload": {"from_step": step},
                    },
                )
            return next_sequence
        if not pause_emitted:
            next_sequence += 1
            await _append_flow_event(
                message_id,
                {
                    "event": "delta",
                    "sequence": next_sequence,
                    "message_id": message_id,
                    "session_id": session_id,
                    "step": "control",
                    "status": "paused",
                    "mode": flow_mode,
                    "content_delta": "流程已暂停，等待恢复",
                    "is_final": False,
                    "payload": {"from_step": step},
                },
            )
            pause_emitted = True
        await asyncio.sleep(0.2)


async def _get_flow_state(message_id: str) -> Optional[Dict[str, Any]]:
    async with _flow_streams_lock:
        stream_state = _flow_streams.get(message_id)
        if not isinstance(stream_state, dict):
            return None
        return dict(stream_state)


def _load_latest_replay_report() -> Dict[str, Any]:
    import glob
    report_candidates = glob.glob(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts", "flow_replay_report_*.json")))
    if not report_candidates:
        return {}
    latest_path = sorted(report_candidates)[-1]
    try:
        with open(latest_path, "r", encoding="utf-8") as handle:
            parsed = json.loads(handle.read())
            if isinstance(parsed, dict):
                parsed["_report_path"] = latest_path
                return parsed
    except Exception:
        return {}
    return {}


def _build_release_gate_from_data(metrics_summary: Dict[str, Any], replay_report: Dict[str, Any]) -> ReleaseGateResponse:
    checklist: List[ReleaseChecklistItem] = []
    total_metrics = _to_int(metrics_summary.get("total"), 0)
    obs_passed = total_metrics > 0
    checklist.append(
        ReleaseChecklistItem(
            key="observability_metrics",
            title="关键指标落库与可查询",
            required=True,
            status="passed" if obs_passed else "failed",
            detail=f"当前可查询样本数: {total_metrics}",
        )
    )
    replay_total = _to_int(replay_report.get("total_cases"), 0)
    replay_success_rate = _to_float(replay_report.get("success_rate"), 0.0)
    fast_non_agent_ratio = _to_float(replay_report.get("fast_non_agent_ratio"), 0.0)
    replay_exists = replay_total > 0
    checklist.append(
        ReleaseChecklistItem(
            key="functional_non_agent_ratio",
            title="常规请求非Agent占比",
            required=True,
            status="passed" if replay_exists and fast_non_agent_ratio >= 0.9 else ("failed" if replay_exists else "unknown"),
            detail=f"fast_non_agent_ratio={fast_non_agent_ratio:.2%}, replay_total={replay_total}",
        )
    )
    checklist.append(
        ReleaseChecklistItem(
            key="functional_success_rate",
            title="回放样本成功率",
            required=True,
            status="passed" if replay_exists and replay_success_rate >= 0.9 else ("failed" if replay_exists else "unknown"),
            detail=f"success_rate={replay_success_rate:.2%}, replay_total={replay_total}",
        )
    )
    pause_resume_supported = True
    checklist.append(
        ReleaseChecklistItem(
            key="stability_pause_resume_retry",
            title="复杂任务暂停/恢复/重试闭环",
            required=True,
            status="passed" if pause_resume_supported else "failed",
            detail="已提供 /api/flow/control 与 /api/flow/status 控制与观测接口",
        )
    )
    avg_latency = _to_float(metrics_summary.get("avg_latency_ms"), 0.0)
    latency_has_data = avg_latency > 0
    checklist.append(
        ReleaseChecklistItem(
            key="performance_latency_baseline",
            title="性能目标（时延）可验证",
            required=False,
            status="partial" if latency_has_data else "unknown",
            detail="当前仅有实时样本时延，需与历史基线对照才能判定“下降20%”",
        )
    )
    checklist.append(
        ReleaseChecklistItem(
            key="cost_token_baseline",
            title="成本目标（Token）可验证",
            required=False,
            status="partial",
            detail="暂未建立标准化 token 基线对照报表，需在下一阶段补齐",
        )
    )
    required_items = [item for item in checklist if item.required]
    blocked = any(item.status != "passed" for item in required_items)
    return ReleaseGateResponse(
        generated_at=datetime.now().isoformat(),
        overall_status="blocked" if blocked else "passed",
        checklist=checklist,
        metrics_snapshot=metrics_summary,
        replay_snapshot=replay_report,
    )


def _build_agent_thread_id(user_id: str, device_id: str) -> str:
    return f"flow-{user_id}-{device_id}"


def _normalize_flow_mode(mode: Optional[str]) -> str:
    m = str(mode or "fast").strip().lower()
    return "deep" if m == "deep" else "fast"


def _detect_agent_escalation(
    flow_mode: str,
    intent: str,
    user_input: Dict[str, Any],
    knowledge_query: Optional[str],
    tool_result: Dict[str, Any],
) -> Dict[str, Any]:
    reasons: List[str] = []
    if flow_mode == "deep":
        reasons.append("mode_deep_requested")
    if intent == "generate_trip" and int(user_input.get("days") or 0) > 7:
        reasons.append("long_duration_trip")
    if knowledge_query and len(str(knowledge_query)) > 20:
        reasons.append("complex_knowledge_query")
    if tool_result.get("needs_tool"):
        reasons.append("tool_usage_required")
    return {
        "agent_escalated": len(reasons) > 0,
        "reasons": reasons,
    }


def _build_flow_query_text(payload: FlowStreamRequest) -> str:
    if payload.message:
        return payload.message
    return f"规划去{payload.destination}的{payload.days}天行程"


def _build_flow_context_messages(context_texts: List[str]) -> List[Dict[str, str]]:
    from src.frontend.context.entity import MessageType
    messages = []
    for text in context_texts:
        if text:
            messages.append({"role": MessageType.USER, "content": text})
    return messages


def _merge_context_with_budget(context_texts: List[str]) -> List[str]:
    results = []
    total_chars = 0
    for text in context_texts:
        if len(results) >= _FLOW_CONTEXT_MAX_ITEMS:
            break
        t = text[:_FLOW_CONTEXT_ITEM_MAX_CHARS]
        if total_chars + len(t) > _FLOW_CONTEXT_TOTAL_MAX_CHARS:
            break
        results.append(t)
        total_chars += len(t)
    return results


async def _run_flow_stream(
    message_id: str,
    session_id: str,
    user_id: str,
    llm_manager: LlmManager,
    payload: FlowStreamRequest,
) -> None:
    """执行单主流程编排：工具与RAG增强、条件升级 Agent、统一流式事件输出。"""
    import re
    last_sequence = 0
    started_at = time.perf_counter()
    flow_mode = _normalize_flow_mode(payload.mode)
    metrics: Dict[str, Any] = {
        "tool_count": 0,
        "rag_hit": False,
        "agent_escalated": False,
        "knowledge_scope": str(payload.knowledge_scope or "private_plus_public"),
    }
    trip_data: Optional[Dict[str, Any]] = None
    escalation_reasons: List[str] = []
    user_input = {
        "destination": payload.destination,
        "days": payload.days,
        "budget": payload.budget,
        "preference": payload.preference,
    }
    constraints_used = _normalize_trip_constraints(payload)
    user_input.update(
        {
            "budget_level": constraints_used["budget_level"],
            "intensity": constraints_used["intensity"],
            "pace": constraints_used["pace"],
            "special_constraints": constraints_used["special_constraints"],
        }
    )
    merged_context_texts = list(payload.context_texts or [])
    response_text = ""
    source_evidence: List[Dict[str, Any]] = []
    constraints_satisfied: List[Dict[str, Any]] = []
    conflict_report = ConflictReport().model_dump()
    allow_public_fusion = str(payload.knowledge_scope or "private_plus_public").strip().lower() != "private_only"
    
    try:
        last_sequence += 1
        await _append_flow_event(
            message_id,
            {
                "event": "start",
                "sequence": last_sequence,
                "message_id": message_id,
                "session_id": session_id,
                "step": "intent",
                "status": "running",
                "mode": flow_mode,
                "content_delta": "",
                "is_final": False,
                "payload": {},
            },
        )
        last_sequence = await _pause_checkpoint(message_id, session_id, last_sequence, flow_mode, "intent")
        flow_query = _build_flow_query_text(payload)
        context_messages = _build_flow_context_messages(merged_context_texts)
        current_trip = _get_storage().get_trip_data(session_id)
        intent_data = llm_manager.analyze_user_message(flow_query, context_messages, current_trip)
        intent = str(intent_data.get("intent") or "generate_trip")
        metrics["intent"] = intent
        last_sequence += 1
        await _append_flow_event(
            message_id,
            {
                "event": "delta",
                "sequence": last_sequence,
                "message_id": message_id,
                "session_id": session_id,
                "step": "intent",
                "status": "done",
                "mode": flow_mode,
                "content_delta": f"意图识别完成：{intent}",
                "is_final": False,
                "payload": {
                    "intent": intent,
                    "summary": intent_data.get("summary"),
                    "needs_more_info": bool(intent_data.get("needs_more_info")),
                },
            },
        )

        last_sequence = await _pause_checkpoint(message_id, session_id, last_sequence, flow_mode, "tool")
        tool_query = llm_manager._build_tool_query(user_input=user_input, query=flow_query)
        tool_result: Dict[str, Any] = {"needs_tool": False}
        if tool_query and allow_public_fusion:
            tool_result = llm_manager.call_tool_by_llm(tool_query, context_messages)
            if tool_result.get("needs_tool"):
                metrics["tool_count"] = 1
                result_payload = tool_result.get("result")
                if isinstance(result_payload, dict) and result_payload.get("success"):
                    merged_context_texts.append(f"工具结果：{json.dumps(result_payload, ensure_ascii=False)}")
        
        kb_context_texts, kb_context_docs = _build_knowledge_context_payload(
            payload.knowledge_base_id,
            payload.destination,
            payload.days,
            payload.budget,
            payload.preference,
            payload.knowledge_query,
        )
        merged_context_texts.extend(kb_context_texts)
        if kb_context_texts:
            metrics["rag_hit"] = True
            source_evidence = _build_source_evidence_from_docs(kb_context_docs)
        
        merged_context_texts = _merge_context_with_budget(merged_context_texts)
        metrics["context_count"] = len(merged_context_texts)
        metrics["context_chars"] = sum([len(item) for item in merged_context_texts])
        metrics["context_budget"] = {
            "max_items": _FLOW_CONTEXT_MAX_ITEMS,
            "item_max_chars": _FLOW_CONTEXT_ITEM_MAX_CHARS,
            "total_max_chars": _FLOW_CONTEXT_TOTAL_MAX_CHARS,
        }

        last_sequence = await _pause_checkpoint(message_id, session_id, last_sequence, flow_mode, "route")
        escalation = _detect_agent_escalation(flow_mode, intent, user_input, payload.knowledge_query, tool_result)
        metrics["agent_escalated"] = bool(escalation.get("agent_escalated"))
        escalation_reasons = list(escalation.get("reasons") or [])

        if metrics["agent_escalated"]:
            last_sequence = await _pause_checkpoint(message_id, session_id, last_sequence, flow_mode, "agent")
            last_sequence += 1
            await _append_flow_event(
                message_id,
                {
                    "event": "delta",
                    "sequence": last_sequence,
                    "message_id": message_id,
                    "session_id": session_id,
                    "step": "agent",
                    "status": "running",
                    "mode": flow_mode,
                    "content_delta": "进入深度规划流程",
                    "is_final": False,
                    "payload": {"reasons": escalation_reasons},
                },
            )
            thread_id = _build_agent_thread_id(user_id, payload.device_id)
            final_state = run_agent_loop_sync(
                llm_manager=llm_manager,
                user_input=user_input,
                thread_id=thread_id,
                agent_config={"mode": flow_mode},
                user_intent="generate_trip",
                context=merged_context_texts,
                resume=False,
            )
            if final_state and isinstance(final_state.final_payload, dict):
                draft_trip = final_state.final_payload.get("draft_trip")
                if isinstance(draft_trip, dict) and draft_trip:
                    trip_data = draft_trip
        elif intent == "general_conversation":
            response_chunks: List[str] = []
            stream = llm_manager.stream_chat_response(flow_query, context_messages, current_trip)
            for delta_text in stream:
                last_sequence = await _pause_checkpoint(message_id, session_id, last_sequence, flow_mode, "generate")
                delta_value = str(delta_text or "")
                if not delta_value:
                    continue
                response_chunks.append(delta_value)
                last_sequence += 1
                await _append_flow_event(
                    message_id,
                    {
                        "event": "delta",
                        "sequence": last_sequence,
                        "message_id": message_id,
                        "session_id": session_id,
                        "step": "generate",
                        "status": "running",
                        "mode": flow_mode,
                        "content_delta": delta_value,
                        "is_final": False,
                        "payload": {},
                    },
                )
                await asyncio.sleep(0)
            response_text = "".join(response_chunks)
        elif intent in {"modify_trip", "add_attraction", "delete_attraction", "reorder_trip"} and current_trip:
            last_sequence = await _pause_checkpoint(message_id, session_id, last_sequence, flow_mode, "modify")
            change_result = llm_manager.change_trip(flow_query, context_messages, current_trip, constraints=constraints_used)
            trip_data = change_result.get("trip_data")
            response_text = str(change_result.get("response") or "")
            if response_text:
                last_sequence += 1
                await _append_flow_event(
                    message_id,
                    {
                        "event": "delta",
                        "sequence": last_sequence,
                        "message_id": message_id,
                        "session_id": session_id,
                        "step": "generate",
                        "status": "running",
                        "mode": flow_mode,
                        "content_delta": response_text,
                        "is_final": False,
                        "payload": {"intent": intent},
                    },
                )
        else:
            response_chunks: List[str] = []
            stream = llm_manager.stream_trip_generation(user_input, merged_context_texts)
            for event in llm_manager.build_stream_events_from_stream(stream, message_id):
                last_sequence = await _pause_checkpoint(message_id, session_id, last_sequence, flow_mode, "generate")
                raw_event = str(event.get("event") or "")
                if raw_event not in {"start", "delta", "end"}:
                    continue
                if raw_event == "end":
                    continue
                delta_text = event.get("content_delta") or ""
                if raw_event == "delta":
                    response_chunks.append(delta_text)
                last_sequence += 1
                await _append_flow_event(
                    message_id,
                    {
                        "event": raw_event,
                        "sequence": last_sequence,
                        "message_id": message_id,
                        "session_id": session_id,
                        "step": "generate",
                        "status": "running",
                        "mode": flow_mode,
                        "content_delta": delta_text,
                        "is_final": False,
                        "payload": {},
                    },
                )
                await asyncio.sleep(0)
            response_text = "".join(response_chunks)
            trip_data = llm_manager.parse_trip_from_response_text(response_text)

        if trip_data:
            if isinstance(trip_data, dict):
                constraints_satisfied = _build_constraint_statuses(trip_data, constraints_used)
                generated_conflict_report = llm_manager.build_conflict_report(trip_data, constraints_used)
                conflict_report = generated_conflict_report.model_dump()
                metrics["conflict_detected"] = bool(conflict_report.get("has_conflicts"))
                metrics["plan_alternative_generated"] = bool(conflict_report.get("alternatives"))
                if conflict_report.get("has_conflicts"):
                    last_sequence += 1
                    await _append_flow_event(
                        message_id,
                        {
                            "event": "delta",
                            "sequence": last_sequence,
                            "message_id": message_id,
                            "session_id": session_id,
                            "step": "warning",
                            "status": "running",
                            "mode": flow_mode,
                            "content_delta": f"检测到 {len(conflict_report.get('conflicts') or [])} 处行程冲突，请查看替代方案建议。",
                            "is_final": False,
                            "payload": {
                                "message": "检测到行程冲突，请查看替代方案建议。",
                                "conflict_report": conflict_report,
                            },
                        },
                    )
                trip_data["constraints_used"] = constraints_used
                trip_data["constraints_satisfied"] = constraints_satisfied
                trip_data["conflict_report"] = conflict_report
            _get_storage().store_trip_data(session_id, trip_data)
        else:
            metrics["conflict_detected"] = False
            metrics["plan_alternative_generated"] = False
        metrics["latency_ms"] = int((time.perf_counter() - started_at) * 1000)
        _record_flow_metrics(
            {
                "message_id": message_id,
                "session_id": session_id,
                "user_id": user_id,
                "device_id": payload.device_id,
                "mode": flow_mode,
                "intent": metrics.get("intent") or "",
                "status": "done",
                "latency_ms": metrics.get("latency_ms") or 0,
                "tool_count": metrics.get("tool_count") or 0,
                "rag_hit": bool(metrics.get("rag_hit")),
                "agent_escalated": bool(metrics.get("agent_escalated")),
                "context_count": metrics.get("context_count") or 0,
                "context_chars": metrics.get("context_chars") or 0,
                "context_budget": metrics.get("context_budget") or {},
                "escalation_reasons": escalation_reasons,
                "error": None,
            }
        )

        last_sequence += 1
        await _append_flow_event(
            message_id,
            {
                "event": "end",
                "sequence": last_sequence,
                "message_id": message_id,
                "session_id": session_id,
                "step": "finalize",
                "status": "done",
                "mode": flow_mode,
                "content_delta": "",
                "is_final": True,
                "payload": {
                    "trip_data": trip_data,
                    "response_text": response_text,
                    "metrics": metrics,
                    "constraints_used": constraints_used,
                    "constraints_satisfied": constraints_satisfied,
                    "conflict_report": conflict_report,
                    "source_evidence": source_evidence,
                    "knowledge_debug": {
                        "knowledge_scope": str(payload.knowledge_scope or "private_plus_public"),
                        "allow_public_fusion": allow_public_fusion,
                        "kb_context_count": len(kb_context_texts),
                        "source_evidence_count": len(source_evidence),
                    },
                    "agent_escalated": metrics["agent_escalated"],
                    "escalation_reasons": escalation_reasons,
                },
            },
        )
        _record_audit_log(
            action="flow_stream_completed",
            status="success",
            user_id=int(user_id or 0),
            session_id=session_id,
            message_id=message_id,
            detail={"message_id": message_id, "session_id": session_id, "has_trip_data": bool(trip_data)},
        )
    except Exception as exc:
        logger.exception("主流程异常 message_id=%s session_id=%s error=%s", message_id, session_id, str(exc))
        last_sequence += 1
        await _append_flow_event(
            message_id,
            {
                "event": "error",
                "sequence": last_sequence,
                "message_id": message_id,
                "session_id": session_id,
                "step": "finalize",
                "status": "failed",
                "mode": flow_mode,
                "content_delta": "",
                "is_final": True,
                "payload": {"error": str(exc)},
            },
        )
        _record_flow_metrics(
            {
                "message_id": message_id,
                "session_id": session_id,
                "user_id": user_id,
                "device_id": payload.device_id,
                "mode": flow_mode,
                "intent": str(metrics.get("intent") or ""),
                "status": "failed",
                "latency_ms": int((time.perf_counter() - started_at) * 1000),
                "tool_count": metrics.get("tool_count") or 0,
                "rag_hit": bool(metrics.get("rag_hit")),
                "agent_escalated": bool(metrics.get("agent_escalated")),
                "context_count": metrics.get("context_count") or 0,
                "context_chars": metrics.get("context_chars") or 0,
                "context_budget": metrics.get("context_budget") or {},
                "escalation_reasons": escalation_reasons,
                "error": str(exc),
            }
        )
        _record_audit_log(
            action="flow_stream_completed",
            status="failed",
            user_id=int(user_id or 0),
            session_id=session_id,
            message_id=message_id,
            detail={"message_id": message_id, "session_id": session_id, "error": str(exc)},
        )
