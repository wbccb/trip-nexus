import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import streamlit as st

from src.agent.event_bus import event_bus, snapshot_store
from src.agent.orchestrator import AgentOrchestrator
from src.llm.llm_manager import LlmManager
from src.observability import ErrorCodes, normalize_exception


class AgentUI:
    def __init__(
        self,
        llm_manager: LlmManager,
        agent_orchestrator: AgentOrchestrator,
        metrics,
        render_rag_evidence_panel: Callable[[Dict[str, Any], str], None],
    ) -> None:
        self.llm_manager = llm_manager
        self.agent_orchestrator = agent_orchestrator
        self._metrics = metrics
        self._render_rag_evidence_panel = render_rag_evidence_panel

    def ensure_thread_id(self) -> str:
        if not st.session_state.get("agent_thread_id"):
            st.session_state.agent_thread_id = self.agent_orchestrator.create_thread_id()
        return st.session_state.agent_thread_id

    def _resolve_intent(self, query: str) -> Tuple[Optional[Dict[str, Any]], List[str], Optional[str]]:
        intent_data = self.llm_manager.analyze_user_message(
            query=query,
            context=[],
            current_trip=None,
        )
        trip_request = self.llm_manager.prepare_trip_request_from_intent(
            intent_data,
            context=[],
        )
        if trip_request.get("needs_more_info"):
            missing_info = trip_request.get("missing_info") or []
            missing_text = "、".join([str(item) for item in missing_info if item])
            return None, [], missing_text
        return trip_request.get("user_input"), trip_request.get("context_texts") or [], None

    def _build_agent_input(
        self,
        input_text: str,
        trip_data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[Dict[str, Any]], List[str], Optional[str]]:
        user_input: Dict[str, Any] = {}
        context_texts: List[str] = []
        if input_text:
            try:
                parsed = json.loads(input_text)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                user_input = parsed
                if not user_input.get("days") or not user_input.get("budget"):
                    resolved_input, resolved_context, missing_text = self._resolve_intent(input_text)
                    if missing_text:
                        return None, [], missing_text
                    if isinstance(resolved_input, dict):
                        user_input = resolved_input or user_input
                    context_texts = resolved_context or []
            elif isinstance(parsed, str):
                parsed_value = parsed.strip()
                if parsed_value:
                    resolved_input, resolved_context, missing_text = self._resolve_intent(parsed_value)
                    if missing_text:
                        return None, [], missing_text
                    if isinstance(resolved_input, dict):
                        user_input = resolved_input or {"destination": parsed_value}
                    else:
                        user_input = {"destination": parsed_value}
                    context_texts = resolved_context or []
            elif parsed is not None:
                return None, [], "JSON 输入必须为对象或字符串"
            else:
                resolved_input, resolved_context, missing_text = self._resolve_intent(input_text)
                if missing_text:
                    return None, [], missing_text
                if isinstance(resolved_input, dict):
                    user_input = resolved_input or {"destination": input_text}
                else:
                    user_input = {"destination": input_text}
                context_texts = resolved_context or []
        elif trip_data:
            user_input = {
                "destination": trip_data.get("destination"),
                "days": trip_data.get("days"),
                "budget": trip_data.get("budget"),
            }
        if not user_input:
            return None, [], "请输入目的地或提供包含 destination 的 JSON"
        if isinstance(user_input, dict) and not user_input.get("destination"):
            return None, [], "JSON 输入缺少 destination 字段"
        return user_input, context_texts, None

    def render_debug_panel(self) -> None:
        st.subheader("Agent 调试")
        thread_id = self.ensure_thread_id()
        st.text_input("Thread ID", value=thread_id, key="agent_thread_display", disabled=True)

        input_text = st.text_area("输入(JSON 或目的地文本)", value=st.session_state.agent_user_input or "上海到广州旅游3天，预算1000元", height=120)
        st.session_state.agent_user_input = input_text

        enable_checker = st.checkbox("启用 Checker", value=st.session_state.agent_config.get("enable_checker", True))
        enable_optimizer = st.checkbox("启用 Optimizer", value=st.session_state.agent_config.get("enable_optimizer", True))
        enable_rag = st.checkbox("启用 RAG", value=st.session_state.agent_config.get("enable_rag", True))

        budget_cap_value = st.number_input(
            "预算上限(0 为不限)",
            min_value=0,
            max_value=20000,
            value=int(st.session_state.agent_config.get("budget_cap") or 0),
        )
        trip_density = st.selectbox(
            "行程密度",
            ["low", "medium", "high"],
            index=["low", "medium", "high"].index(st.session_state.agent_config.get("trip_density", "medium")),
        )
        prefer_indoor = st.checkbox("室内优先", value=st.session_state.agent_config.get("prefer_indoor", False))

        poi_top_k = st.number_input(
            "POI 结果数量",
            min_value=1,
            max_value=10,
            value=int(st.session_state.agent_config.get("poi_top_k") or 5),
        )
        weather_days = st.number_input(
            "天气预报天数",
            min_value=1,
            max_value=7,
            value=int(st.session_state.agent_config.get("weather_days") or 3),
        )
        rag_top_k = st.number_input(
            "检索 TopK",
            min_value=1,
            max_value=10,
            value=int(st.session_state.agent_config.get("rag_top_k") or 3),
        )
        poi_query = st.text_input("POI 查询关键词", value=st.session_state.agent_config.get("poi_query") or "热门景点")

        pause_option = st.selectbox("暂停点", ["不暂停", "planner", "checker"], index=0)
        pause_at = None if pause_option == "不暂停" else pause_option

        st.session_state.agent_config = {
            "enable_checker": enable_checker,
            "enable_optimizer": enable_optimizer,
            "enable_rag": enable_rag,
            "budget_cap": None if budget_cap_value == 0 else budget_cap_value,
            "trip_density": trip_density,
            "prefer_indoor": prefer_indoor,
            "poi_top_k": poi_top_k,
            "poi_query": poi_query,
            "rag_top_k": rag_top_k,
            "weather_days": weather_days,
        }

        col1, col2, col3 = st.columns(3)
        with col1:
            run_agent = st.button("运行 Agent", use_container_width=True)
        with col2:
            resume_agent = st.button("继续执行", use_container_width=True)
        with col3:
            clear_agent = st.button("清空事件", use_container_width=True)

        if clear_agent:
            event_bus.clear(thread_id)
            snapshot_store.clear(thread_id)

        if run_agent:
            event_bus.clear(thread_id)
            snapshot_store.clear(thread_id)
            trip_data = st.session_state.get("trip_data") or {}
            user_input, context_texts, error_text = self._build_agent_input(input_text, trip_data)
            if error_text:
                st.error(error_text)
                return
            try:
                state = self.agent_orchestrator.run_stream(
                    user_input=user_input,
                    thread_id=thread_id,
                    agent_config=st.session_state.agent_config,
                    context=context_texts or None,
                    pause_at=pause_at,
                    resume_state=None,
                    retry_limit=1,
                )
                st.session_state.agent_last_state = state
            except Exception as error:
                error_payload = normalize_exception(
                    error,
                    code=ErrorCodes.UNEXPECTED_ERROR,
                    source="ui_agent",
                )
                self._metrics.record("ui_agent_error", {"error": error_payload})
                st.error(f"系统处理失败（{error_payload.get('code')}）：{error_payload.get('message')}")
                st.session_state.agent_last_state = {"status": "failed", "error": error_payload}
                return

        if resume_agent:
            state = self.agent_orchestrator.resume_from_latest(thread_id, st.session_state.agent_config)
            if state:
                st.session_state.agent_last_state = state

        last_state = st.session_state.agent_last_state or {}
        if isinstance(last_state, dict):
            map_payload = last_state.get("map_payload") or {}
            if isinstance(map_payload, dict):
                rag_answer = map_payload.get("rag_answer")
                if rag_answer:
                    st.markdown("#### RAG 回答")
                    st.markdown(str(rag_answer))

                rag_query = map_payload.get("rag_query")
                rag_evidence = (
                    map_payload.get("rag_evidence")
                    or map_payload.get("evidence")
                    or map_payload.get("rag_result", {}).get("evidence")
                )
                if isinstance(rag_evidence, dict) and rag_evidence:
                    evidence_view = dict(rag_evidence)
                    if rag_query:
                        evidence_view["_query"] = rag_query
                    self._render_rag_evidence_panel(evidence_view, panel_key=f"agent::{thread_id}")

        events = event_bus.list(thread_id)
        if events:
            st.markdown("#### 事件流")
            for event in sorted(events, key=lambda e: e["ts"]):
                ts = datetime.fromtimestamp(event["ts"]).strftime("%H:%M:%S")
                node = event.get("node") or "-"
                kind = event.get("kind")
                detail_keys = ", ".join(list((event.get("detail") or {}).keys()))
                st.markdown(f"- {ts} | {kind} | {node} | {detail_keys}")

        snapshots = snapshot_store.list(thread_id)
        if snapshots:
            st.markdown("#### 快照面板")
            for snap in snapshots:
                ts = datetime.fromtimestamp(snap["ts"]).strftime("%H:%M:%S")
                step = snap.get("step")
                duration_ms = snap.get("duration_ms")
                payload_keys = ", ".join(list((snap.get("payload") or {}).keys()))
                st.markdown(f"- {ts} | {step} | {duration_ms}ms | {payload_keys}")

    def _build_node_status(self, thread_id: str) -> Tuple[Dict[str, str], Dict[str, str]]:
        node_labels = {
            "planner": "Planner",
            "checker": "Checker",
            "optimizer": "Optimizer",
            "map_rag": "Map/RAG",
        }
        node_desc = {
            "planner": "生成草案与意图解析",
            "checker": "调用工具校验约束",
            "optimizer": "预算与偏好修正",
            "map_rag": "地图渲染与检索",
        }
        status_map: Dict[str, str] = {}
        activity_map: Dict[str, str] = {}
        events = event_bus.list(thread_id)
        for node in node_labels.keys():
            node_events = [event for event in events if event.get("node") == node]
            if not node_events:
                status_map[node] = "pending"
                activity_map[node] = "未开始"
                continue
            last_event = node_events[-1]
            kind = last_event.get("kind")
            detail = last_event.get("detail") or {}
            if kind == "node_start":
                status_map[node] = "running"
                activity_map[node] = node_desc.get(node, "执行中")
            elif kind == "tool_call":
                status_map[node] = "running"
                tool_name = detail.get("tool") or detail.get("name") or ""
                activity_map[node] = f"调用工具 {tool_name}".strip()
            elif kind == "tool_result":
                status_map[node] = "running"
                activity_map[node] = "工具返回处理中"
            elif kind == "node_end":
                status_map[node] = "done"
                activity_map[node] = "完成"
            elif kind == "interrupt":
                status_map[node] = "paused"
                activity_map[node] = "已暂停"
            elif kind == "error":
                status_map[node] = "failed"
                activity_map[node] = str(detail.get("error") or "失败")
            else:
                status_map[node] = str(kind or "unknown")
                activity_map[node] = str(kind or "unknown")
        return status_map, activity_map

    def render_status_panel(self, thread_id: Optional[str] = None, floating: bool = True) -> None:
        active_thread_id = thread_id or st.session_state.get("agent_thread_id") or ""
        if not active_thread_id:
            return
        if not event_bus.list(active_thread_id):
            return
        status_map, activity_map = self._build_node_status(active_thread_id)
        node_order = ["planner", "checker", "optimizer", "map_rag"]
        status_color = {
            "pending": "#9aa0a6",
            "running": "#1a73e8",
            "done": "#1e8e3e",
            "paused": "#f9ab00",
            "failed": "#d93025",
        }
        rows_html = []
        for node in node_order:
            status = status_map.get(node, "pending")
            color = status_color.get(status, "#5f6368")
            activity = activity_map.get(node, "")
            label = node.replace("_", " ").title()
            rows_html.append(
                f"""
                <div class="agent-node-row">
                    <div class="agent-node-title">
                        <span class="agent-dot" style="background:{color}"></span>
                        <span class="agent-node-label">{label}</span>
                        <span class="agent-node-status">{status}</span>
                    </div>
                    <div class="agent-node-activity">{activity}</div>
                </div>
                """
            )
        panel_html = f"""
        <div id="agent-status-panel">
            <div class="agent-panel-title">Agent 运行状态</div>
            {''.join(rows_html)}
        </div>
        <style>
        #agent-status-panel {{
            background: #ffffff;
            border: 1px solid #e0e0e0;
            border-radius: 12px;
            padding: 12px 14px;
            box-shadow: 0 8px 18px rgba(0,0,0,0.08);
            width: 320px;
            font-size: 13px;
            line-height: 1.4;
        }}
        .agent-panel-title {{
            font-weight: 600;
            margin-bottom: 8px;
            color: #202124;
        }}
        .agent-node-row {{
            padding: 6px 0;
            border-bottom: 1px dashed #ececec;
        }}
        .agent-node-row:last-child {{
            border-bottom: none;
        }}
        .agent-node-title {{
            display: flex;
            align-items: center;
            gap: 6px;
            margin-bottom: 4px;
        }}
        .agent-dot {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
        }}
        .agent-node-label {{
            font-weight: 600;
            color: #202124;
        }}
        .agent-node-status {{
            margin-left: auto;
            color: #5f6368;
            text-transform: capitalize;
        }}
        .agent-node-activity {{
            color: #5f6368;
        }}
        </style>
        """
        if floating:
            st.markdown(
                """
                <style>
                div.element-container:has(div#agent-status-marker) + div.element-container {
                    position: fixed !important;
                    right: 20px;
                    bottom: 24px;
                    z-index: 1000002;
                    width: auto !important;
                }
                </style>
                <div id="agent-status-marker"></div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown(panel_html, unsafe_allow_html=True)
