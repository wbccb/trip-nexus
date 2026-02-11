import json
import uuid
import textwrap
import hashlib
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import streamlit as st

from src.agent.event_bus import event_bus, snapshot_store
from src.agent.agent_loop import PlannerAgent, run_agent_loop_sync, TripState
from src.agent.plan_models import Plan, Task
from src.llm.llm_manager import LlmManager
from src.observability import ErrorCodes, normalize_exception


class AgentUI:
    def __init__(
        self,
        llm_manager: LlmManager,
        metrics,
        render_rag_evidence_panel: Callable[[Dict[str, Any], str], None],
    ) -> None:
        self.llm_manager = llm_manager
        self._metrics = metrics
        self._render_rag_evidence_panel = render_rag_evidence_panel
        self._planner_agent = PlannerAgent(llm_manager)

    def ensure_thread_id(self) -> str:
        if not st.session_state.get("agent_thread_id"):
            st.session_state.agent_thread_id = str(uuid.uuid4())
        return st.session_state.agent_thread_id

    def _render_plan_preview(self, plan: Plan) -> None:
        st.markdown("#### 计划预览")
        task_names = {
            "tool_call": "工具调用",
            "trip_generate": "生成行程",
            "map_render": "渲染地图",
            "trip_summarize": "输出摘要",
        }
        tool_names = {
            "weather.get_daily": "查询天气",
            "poi.search": "查询景点",
            "geo.geocode": "查询地理编码",
        }
        for index, task in enumerate(plan.tasks, start=1):
            if task.type == "tool_call":
                title = tool_names.get(task.tool or "", "工具调用")
            else:
                title = task_names.get(task.type, task.type or "任务")
            deps_text = "、".join(task.dependencies or [])
            suffix = f"（依赖：{deps_text}）" if deps_text else ""
            st.markdown(f"- {index}. {title}{suffix}")

    def _task_title(self, task: Task) -> str:
        task_names = {
            "tool_call": "工具调用",
            "trip_generate": "生成行程",
            "map_render": "渲染地图",
            "trip_summarize": "输出摘要",
        }
        tool_names = {
            "weather.get_daily": "查询天气",
            "poi.search": "查询景点",
            "geo.geocode": "查询地理编码",
        }
        if task.type == "tool_call":
            return tool_names.get(task.tool or "", "工具调用")
        return task_names.get(task.type, task.type or "任务")

    def _extract_plan_tasks(self, plan_payload: Any) -> List[Task]:
        if isinstance(plan_payload, Plan):
            return list(plan_payload.tasks)
        if isinstance(plan_payload, dict):
            items = plan_payload.get("tasks") or []
        elif isinstance(plan_payload, list):
            items = plan_payload
        else:
            items = []
        tasks: List[Task] = []
        for item in items:
            if isinstance(item, Task):
                tasks.append(item)
                continue
            if not isinstance(item, dict):
                continue
            try:
                tasks.append(Task(**item))
            except Exception:
                continue
        return tasks

    def _build_task_title_map(self, plan_payload: Any) -> Dict[str, str]:
        title_map: Dict[str, str] = {}
        for task in self._extract_plan_tasks(plan_payload):
            if task.id:
                title_map[task.id] = self._task_title(task)
        return title_map

    def _truncate_text(self, text: str, max_len: int = 40) -> str:
        if not text:
            return ""
        return text[:max_len] + "..." if len(text) > max_len else text

    def _render_task_summaries(
        self,
        task_summaries: Dict[str, Any],
        task_title_map: Dict[str, str],
        task_results: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not task_summaries:
            return
        st.markdown("#### 任务摘要")
        status_map = {"success": "成功", "failed": "失败"}
        for key, value in task_summaries.items():
            text = str(value or "")
            parts = text.split(":")
            task_id = parts[0] if parts else str(key)
            status_key = parts[-1] if len(parts) >= 3 else ""
            status_text = status_map.get(status_key, status_key or "未知")
            title = task_title_map.get(task_id, "任务")
            reason_text = ""
            if task_results and isinstance(task_results.get(task_id), dict):
                error = task_results.get(task_id, {}).get("error")
                if isinstance(error, dict):
                    reason_text = error.get("message") or error.get("code") or ""
                elif isinstance(error, str):
                    reason_text = error
            reason_suffix = f"，原因：{self._truncate_text(str(reason_text))}" if reason_text else ""
            st.markdown(f"- {title}（{task_id}）：{status_text}{reason_suffix}")

    def _format_event_line(
        self,
        event: Dict[str, Any],
        task_title_map: Dict[str, str],
        task_summaries: Dict[str, Any],
    ) -> str:
        ts_value = event.get("ts")
        ts = datetime.fromtimestamp(ts_value).strftime("%H:%M:%S") if ts_value else "--:--:--"
        kind = event.get("kind") or ""
        detail = event.get("detail") or {}
        if kind == "batch_start":
            tasks = detail.get("tasks") or []
            task_names = [task_title_map.get(task_id, str(task_id)) for task_id in tasks]
            if len(task_names) > 3:
                task_text = "、".join(task_names[:3]) + "…"
            else:
                task_text = "、".join(task_names)
            task_text = task_text or "暂无任务"
            return f"{ts} 开始批量执行任务：{task_text}"
        if kind == "replan":
            depth = detail.get("depth")
            depth_text = f"第{depth}次" if depth is not None else "重新规划"
            result_text = ""
            if task_summaries:
                sample = list(task_summaries.values())[:2]
                result_text = "；执行结果：" + "、".join([self._truncate_text(str(item)) for item in sample])
                if len(task_summaries) > 2:
                    result_text += "…"
            return f"{ts} 重新规划（{depth_text}），原因：上一步工具结果为空或失败{result_text}"
        if kind == "loop_end":
            status = detail.get("status") or ""
            status_text = {"done": "完成", "failed": "失败", "paused": "暂停"}.get(status, status or "结束")
            return f"{ts} 执行结束：{status_text}"
        if kind == "error":
            error_text = ""
            error_detail = detail.get("error")
            if isinstance(error_detail, dict):
                error_text = error_detail.get("message") or error_detail.get("code") or ""
            elif isinstance(error_detail, str):
                error_text = error_detail
            error_text = self._truncate_text(str(error_text)) if error_text else "未知错误"
            return f"{ts} 执行失败：{error_text}"
        return f"{ts} 事件：{kind}"

    def _resolve_intent(self, query: str) -> Tuple[Optional[Dict[str, Any]], List[str], Optional[str], Optional[str]]:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [AgentUI] 开始意图识别: {self._truncate_text(query)}")
        intent_data = self.llm_manager.analyze_user_message(
            query=query,
            context=[],
            current_trip=None,
        )
        intent_type = intent_data.get("intent")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [AgentUI] 意图识别完成: {intent_type} \n")
        trip_request = self.llm_manager.prepare_trip_request_from_intent(
            intent_data,
            context=[],
        )
        if trip_request.get("needs_more_info"):
            missing_info = trip_request.get("missing_info") or []
            missing_text = "、".join([str(item) for item in missing_info if item])
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [AgentUI] 信息缺失: {missing_text}")
            return None, [], missing_text, intent_type
        return trip_request.get("user_input"), trip_request.get("context_texts") or [], None, intent_type

    def _build_evidence_item_id(self, section: str, item: Dict[str, Any]) -> str:
        source = str(item.get("source") or "")
        title = str(item.get("title") or "")
        text = str(item.get("text") or "")
        raw = f"{section}||{source}||{title}||{text}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _reconstruct_evidence(self, original_evidence: Dict[str, Any], panel_key: str) -> Dict[str, Any]:
        """
        根据 UI 交互状态（rag_evidence_ui）重建 evidence 对象。
        """
        ui_state = st.session_state.get("rag_evidence_ui", {}).get(panel_key, {})
        # 如果没有 UI 状态，直接返回原始数据
        if not ui_state:
            return original_evidence

        new_evidence = original_evidence.copy()
        
        for section in ["summary", "body"]:
            section_data = new_evidence.get(section, {})
            # 获取原始列表（优先 candidates，其次 items）
            candidates = section_data.get("candidates") or []
            items = section_data.get("items") or []
            base_list = candidates if candidates else items
            
            if not base_list:
                continue

            new_items = []
            for item in base_list:
                item_id = self._build_evidence_item_id(section, item)
                item_state = ui_state.get(item_id, {})
                
                # 检查是否保留
                # 注意：如果 item_state 为空（未交互且未渲染？），则默认不保留？
                # 不，render_rag_evidence_panel 会初始化所有 items。
                keep = item_state.get("keep", False)
                
                if keep:
                    new_item = item.copy()
                    if item_state.get("edited_text"):
                        new_item["text"] = item_state["edited_text"]
                    new_items.append(new_item)
            
            # 更新 section items
            # 只有当确实有选中项时才更新，防止误操作清空
            # 或者如果用户全不选，那就是空。
            section_data["items"] = new_items
            # 清空 candidates，表示已确认
            section_data["candidates"] = []
            new_evidence[section] = section_data
        
        return new_evidence

    def _build_agent_input(
        self,
        input_text: str,
        trip_data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[Dict[str, Any]], List[str], Optional[str], Optional[str]]:
        user_input: Dict[str, Any] = {}
        context_texts: List[str] = []
        intent_type: Optional[str] = None
        if input_text:
            try:
                parsed = json.loads(input_text)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                user_input = parsed
                if not user_input.get("days") or not user_input.get("budget"):
                    resolved_input, resolved_context, missing_text, intent_type = self._resolve_intent(input_text)
                    if missing_text:
                        return None, [], missing_text, intent_type
                    if isinstance(resolved_input, dict):
                        user_input = resolved_input or user_input
                    context_texts = resolved_context or []
            elif isinstance(parsed, str):
                parsed_value = parsed.strip()
                if parsed_value:
                    resolved_input, resolved_context, missing_text, intent_type = self._resolve_intent(parsed_value)
                    if missing_text:
                        return None, [], missing_text, intent_type
                    if isinstance(resolved_input, dict):
                        user_input = resolved_input or {"destination": parsed_value}
                    else:
                        user_input = {"destination": parsed_value}
                    context_texts = resolved_context or []
            elif parsed is not None:
                return None, [], "JSON 输入必须为对象或字符串", None
            else:
                resolved_input, resolved_context, missing_text, intent_type = self._resolve_intent(input_text)
                if missing_text:
                    return None, [], missing_text, intent_type
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
            return None, [], "请输入目的地或提供包含 destination 的 JSON", intent_type
        if isinstance(user_input, dict) and not user_input.get("destination"):
            return None, [], "JSON 输入缺少 destination 字段", intent_type
        return user_input, context_texts, None, intent_type

    def render_debug_panel(self) -> None:
        print(f"[AgentUI] render_debug_panel rerun_ts={datetime.now().strftime('%H:%M:%S')}")
        st.subheader("Agent 调试")
        thread_id = self.ensure_thread_id()
        st.text_input("Thread ID", value=thread_id, key="agent_thread_display", disabled=True)

        input_text = st.text_area("输入(JSON 或目的地文本)", value=st.session_state.agent_user_input or "上海到广州旅游2天，预算1000元, 2025年11月从上海飞机出发，在广州侧重于地铁交通，住宿预算每晚100元", height=120)
        st.session_state.agent_user_input = input_text

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
        manual_rag_review = st.checkbox("启用 RAG 人工复核", value=st.session_state.agent_config.get("manual_rag_review", False))

        st.session_state.agent_config = {
            "budget_cap": None if budget_cap_value == 0 else budget_cap_value,
            "trip_density": trip_density,
            "prefer_indoor": prefer_indoor,
            "poi_top_k": poi_top_k,
            "poi_query": poi_query,
            "rag_top_k": rag_top_k,
            "manual_rag_review": manual_rag_review,
            "weather_days": weather_days,
            "max_concurrency": 4,
            "rate_limit_per_min": 60,
            "max_total_tasks": None,
        }

        # 检查是否处于暂停状态
        last_state_data = st.session_state.get("agent_last_state")
        last_state = None
        if last_state_data:
            if isinstance(last_state_data, dict):
                try:
                    last_state = TripState(**last_state_data)
                except Exception as e:
                    print(f"[AgentUI] Failed to restore TripState: {e}")
            else:
                last_state = last_state_data

        is_paused = last_state and last_state.status == "paused"

        if is_paused:
            st.info(f"⚠️ Agent 已暂停，原因: {last_state.stop_reason}")
            if last_state.stop_reason == "rag_review":
                st.markdown("### RAG 结果人工复核")
                tasks_to_review = []
                # print(f"[AgentUI] 进入 RAG 复核流程，Completed: {last_state.completed_tasks}")
                # print(f"[AgentUI] task_results keys: {list(last_state.task_results.keys())}")
                # print(f"[AgentUI] shared_context keys: {list((last_state.shared_context.data or {}).keys())}")
                for task_id in last_state.completed_tasks:
                    res = last_state.task_results.get(task_id, {})
                    # print(f"[AgentUI] 任务 {task_id} 原始结果类型: {type(res)}")
                    if hasattr(res, "data"):
                        data = res.data
                    else:
                        data = res.get("data", {})
                    # print(f"[AgentUI] 任务 {task_id} data 类型: {type(data)}")
                    if hasattr(data, "model_dump"):
                        data = data.model_dump()
                    # if isinstance(data, dict):
                        # print(f"[AgentUI] 任务 {task_id} data keys: {list(data.keys())}")
                    evidence = data.get("evidence") if isinstance(data, dict) else None
                    query = data.get("query") if isinstance(data, dict) else None
                    results = data.get("results") if isinstance(data, dict) else None
                    if not evidence:
                        target_task = next((t for t in last_state.plan if t.id == task_id), None)
                        output_key = target_task.output_key if target_task else "result"
                        ctx = last_state.shared_context.read(task_id, output_key)
                        # print(f"[AgentUI] 任务 {task_id} shared_context({output_key}) 类型: {type(ctx)}")
                        if isinstance(ctx, dict):
                            # print(f"[AgentUI] 任务 {task_id} shared_context keys: {list(ctx.keys())}")
                            evidence = ctx.get("evidence")
                            query = query or ctx.get("query")
                            results = results or ctx.get("results")
                    if isinstance(evidence, dict) and evidence:
                        # print(f"[AgentUI] 任务 {task_id} Evidence keys: {list(evidence.keys())}")
                        # print(f"[AgentUI] 任务 {task_id} 发现 Evidence")
                        tasks_to_review.append(
                            {
                                "task_id": task_id,
                                "evidence": evidence,
                                "query": query,
                                "results": results,
                            }
                        )
                    # else:
                    #     print(f"[AgentUI] 任务 {task_id} 无 Evidence")
                
                if not tasks_to_review:
                     st.warning("触发了 RAG 复核暂停，但未找到包含 Evidence 的任务。")

                for task_info in tasks_to_review:
                    task_id = task_info.get("task_id")
                    evidence = task_info.get("evidence") or {}
                    query = task_info.get("query")
                    st.markdown(f"**任务 {task_id} 的 RAG 证据**")
                    evidence_view = dict(evidence)
                    if query:
                        evidence_view["_query"] = query
                    #print(f"[AgentUI] 渲染 Evidence 面板 task={task_id} keys={list(evidence_view.keys())}")
                    # 渲染交互面板 (状态保存在 session_state.rag_evidence_ui)
                    self._render_rag_evidence_panel(evidence_view, f"review_{task_id}")

                if tasks_to_review and st.button("确认复核并继续执行", type="primary"):
                    with st.spinner("正在应用修改并恢复执行..."):
                        # 更新 SharedContext 中的 evidence
                        for task_info in tasks_to_review:
                            task_id = task_info.get("task_id")
                            res = last_state.task_results.get(task_id, {})
                            # 兼容提取
                            if hasattr(res, "data"):
                                data = res.data
                            else:
                                data = res.get("data", {})
                            if hasattr(data, "model_dump"):
                                data = data.model_dump()

                            original_evidence = task_info.get("evidence") or data.get("evidence")
                            
                            # 重建 evidence
                            new_evidence = self._reconstruct_evidence(original_evidence, f"review_{task_id}")

                            # 更新 state.task_results (用于历史记录)
                            # 注意：如果是 dict 直接更新，如果是对象则无法直接 setitem?
                            # 假设 task_results 存储的是 dict (因为 _execute_task 返回的是 dict)
                            if isinstance(last_state.task_results.get(task_id), dict):
                                if not isinstance(last_state.task_results[task_id].get("data"), dict):
                                    last_state.task_results[task_id]["data"] = {}
                                last_state.task_results[task_id]["data"]["evidence"] = new_evidence
                            elif hasattr(last_state.task_results[task_id], "data"):
                                # 如果是对象，尝试修改属性（可能不可变）
                                # 但 _execute_task 返回的是 dict，所以应该是 dict。
                                pass
                            
                            # 更新 SharedContext (用于下游任务)
                            # poi.search 写入的是 {"query": ..., "results": ..., "evidence": ...}
                            # 我们需要读取 context 中现有的值并更新
                            target_task = next((t for t in last_state.plan if t.id == task_id), None)
                            output_key = target_task.output_key if target_task else "result"
                            current_ctx = last_state.shared_context.read(task_id, output_key)
                            
                            # 更新 Context
                            if current_ctx:
                                current_ctx["evidence"] = new_evidence
                                last_state.shared_context.write(task_id, output_key, current_ctx)
                            else:
                                # 如果 Context 中没有，可能是写入失败或者 key 不对。
                                # 尝试直接覆盖
                                update_data = {
                                    "evidence": new_evidence,
                                    "results": task_info.get("results") or data.get("results"),
                                    "query": task_info.get("query") or data.get("query"),
                                }
                                last_state.shared_context.write(task_id, output_key, update_data)

                        # 恢复执行
                        final_state = run_agent_loop_sync(
                            llm_manager=self.llm_manager,
                            user_input=st.session_state.get("agent_user_input_resolved") or {},
                            thread_id=thread_id,
                            agent_config=st.session_state.agent_config,
                            user_intent=st.session_state.get("agent_plan_intent", ""),
                            context=st.session_state.get("agent_context_texts"),
                            plan_override=None,
                            initial_state=last_state
                        )
                        st.session_state.agent_last_state = final_state
                        st.rerun()

        col1, col2 = st.columns(2)
        with col1:
            build_plan = st.button("生成计划 & 执行计划", use_container_width=True)
        with col2:
            clear_agent = st.button("清空事件", use_container_width=True)

        if clear_agent:
            event_bus.clear(thread_id)
            snapshot_store.clear(thread_id)

        if build_plan:
            event_bus.clear(thread_id)
            snapshot_store.clear(thread_id)
            trip_data = st.session_state.get("trip_data") or {}
            user_input, context_texts, error_text, user_intent = self._build_agent_input(input_text, trip_data)
            if error_text:
                st.error(error_text)
                return
            print(f"\n\n[{datetime.now().strftime('%H:%M:%S')}] [AgentUI] 用户点击生成计划，开始处理...")
            user_intent = user_intent or "generate_trip"
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [AgentUI] 确认用户意图: {user_intent}")
            tool_whitelist = [schema.get("name") for schema in self.llm_manager.list_tools()]
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [AgentUI] 调用 PlannerAgent 生成计划...调用 LLM 生成 DAG 任务图")
            plan = self._planner_agent.plan(
                user_intent=user_intent,
                user_input=user_input,
                agent_config=st.session_state.agent_config,
                tool_whitelist=tool_whitelist,
                force_sop=True,
            )
            tools = [t.tool for t in plan.tasks if t.type == "tool_call" and t.tool]
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [AgentUI] plan生成完成，工具序列: {', '.join(tools)} \n\n")
            st.session_state.agent_plan_preview = plan.model_dump()
            st.session_state.agent_user_input_resolved = user_input
            st.session_state.agent_context_texts = context_texts
            st.session_state.agent_plan_intent = user_intent
            st.session_state.agent_plan_confirmed = False

            print(f"[{datetime.now().strftime('%H:%M:%S')}] [AgentUI] 开始执行计划！")
            plan_payload = st.session_state.get("agent_plan_preview")
            if not plan_payload:
                st.warning("请先生成计划")
                return
            user_input = st.session_state.get("agent_user_input_resolved") or {}
            context_texts = st.session_state.get("agent_context_texts") or []
            plan_tasks = [Task(**item) for item in (plan_payload.get("tasks") or [])]
            plan = Plan(tasks=plan_tasks)
            try:
                state = run_agent_loop_sync(
                    llm_manager=self.llm_manager,
                    user_input=user_input,
                    thread_id=thread_id,
                    agent_config=st.session_state.agent_config,
                    user_intent=st.session_state.get("agent_plan_intent") or "generate_trip",
                    context=context_texts or None,
                    plan_override=plan,
                    retry_limit=1,
                    max_replan_depth=2,
                )
                st.session_state.agent_last_state = state.model_dump()
                st.session_state.agent_plan_confirmed = True
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [AgentUI] Agent 执行结束，status={state.status}")
                if getattr(state, "status", "") == "paused":
                    st.rerun()
            except Exception as error:
                error_payload = normalize_exception(
                    error,
                    code=ErrorCodes.UNEXPECTED_ERROR,
                    source="ui_agent_loop",
                )
                self._metrics.record("ui_agent_error", {"error": error_payload})
                st.error(f"系统处理失败（{error_payload.get('code')}）：{error_payload.get('message')}")
                st.session_state.agent_last_state = {"status": "failed", "error": error_payload}
                return

        last_state_raw = st.session_state.agent_last_state or {}
        if hasattr(last_state_raw, "model_dump"):
            last_state = last_state_raw.model_dump()
        elif isinstance(last_state_raw, dict):
            last_state = last_state_raw
        else:
            last_state = {}
        plan_preview = st.session_state.get("agent_plan_preview")
        plan_payload_for_map = plan_preview
        if isinstance(last_state, dict) and last_state.get("plan"):
            plan_payload_for_map = {"tasks": last_state.get("plan")}
        task_title_map = self._build_task_title_map(plan_payload_for_map)
        if plan_preview:
            try:
                preview_plan = Plan(tasks=[Task(**item) for item in (plan_preview.get("tasks") or [])])
                self._render_plan_preview(preview_plan)
            except Exception:
                st.warning("计划预览解析失败")
        if isinstance(last_state, dict):
            map_payload = last_state.get("map_payload") or {}
            final_payload = last_state.get("final_payload") or {}
            plan_items = last_state.get("plan") or []
            shared_context = last_state.get("shared_context") or {}
            shared_data = shared_context.get("data") if isinstance(shared_context, dict) else {}
            map_task_id = None
            map_output_key = None
            summary_task_id = None
            summary_output_key = None
            if isinstance(plan_items, list):
                for item in plan_items:
                    if not isinstance(item, dict):
                        continue
                    task_type = item.get("type")
                    if task_type == "map_render" and not map_task_id:
                        map_task_id = item.get("id")
                        map_output_key = item.get("output_key") or "map_payload"
                    if task_type == "trip_summarize" and not summary_task_id:
                        summary_task_id = item.get("id")
                        summary_output_key = item.get("output_key") or "summary"
            map_payload_from_final = final_payload.get("map_payload") if isinstance(final_payload, dict) else {}
            summary_payload_from_final = final_payload.get("summary") if isinstance(final_payload, dict) else {}
            if not map_payload:
                if isinstance(map_payload_from_final, dict):
                    map_payload = map_payload_from_final
                elif map_task_id and isinstance(shared_data, dict):
                    map_payload = (shared_data.get(map_task_id) or {}).get(map_output_key or "map_payload") or {}
            summary_payload = summary_payload_from_final if isinstance(summary_payload_from_final, dict) else {}
            if not summary_payload:
                if summary_task_id and isinstance(shared_data, dict):
                    summary_payload = (shared_data.get(summary_task_id) or {}).get(summary_output_key or "summary") or {}
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
                map_html = map_payload.get("map_html")
                if map_html:
                    st.markdown("#### 行程地图")
                    st.components.v1.html(map_html, height=520, width=1000, scrolling=True)

            # [Added] 渲染任务中的 RAG 回答 (e.g. POI)
            task_results = last_state.get("task_results") or {}
            for task_id, res in task_results.items():
                if not isinstance(res, dict): continue
                data = res.get("data")
                if isinstance(data, dict) and data.get("rag_answer"):
                    st.markdown(f"#### RAG 回答 ({task_id})")
                    st.markdown(str(data.get("rag_answer")))

            if summary_payload:
                summary_text = summary_payload.get("summary") if isinstance(summary_payload, dict) else summary_payload
                if summary_text:
                    st.markdown("#### 行程摘要")
                    st.markdown(str(summary_text))

            self._render_task_summaries(
                last_state.get("task_summaries") or {},
                task_title_map,
                last_state.get("task_results") or {},
            )

        events = event_bus.list(thread_id)
        if events:
            st.markdown("#### 事件流")
            for event in sorted(events, key=lambda e: e["ts"]):
                st.markdown(f"- {self._format_event_line(event, task_title_map, last_state.get('task_summaries') or {})}")

        snapshots = snapshot_store.list(thread_id)
        if snapshots:
            st.markdown("#### 快照面板")
            for snap in snapshots:
                ts_value = snap.get("ts")
                ts = datetime.fromtimestamp(ts_value).strftime("%H:%M:%S") if ts_value else "-"
                step = snap.get("step")
                duration_ms = snap.get("duration_ms")
                payload_keys = ", ".join(list((snap.get("payload") or {}).keys()))
                st.markdown(f"- {ts} | {step} | {duration_ms}ms | {payload_keys}")

        agent_status = last_state.get("status") if isinstance(last_state, dict) else getattr(last_state, "status", None)
        autorefresh = getattr(st, "autorefresh", None)
        if callable(autorefresh) and agent_status in ("running", "paused"):
            autorefresh(interval=1000, key="agent_debug_autorefresh")

    def _build_task_status_from_plan(
        self,
        plan: List[Dict[str, Any]],
        completed_tasks: List[str],
        failed_tasks: List[str],
    ) -> Tuple[Dict[str, str], Dict[str, str], List[str]]:
        # 初始化状态映射
        status_map: Dict[str, str] = {}
        activity_map: Dict[str, str] = {}
        # 构建顺序列表
        node_order: List[str] = []
        # 读取完成/失败集合
        completed_set = set(completed_tasks or [])
        failed_set = set(failed_tasks or [])
        # 遍历计划任务
        for task in plan:
            task_id = str(task.get("id") or "")
            if not task_id:
                continue
            node_order.append(task_id)
            # 计算状态
            if task_id in failed_set:
                status_map[task_id] = "failed"
            elif task_id in completed_set:
                status_map[task_id] = "done"
            else:
                status_map[task_id] = "pending"
            # 填充活动文本
            activity_map[task_id] = str(task.get("description") or task.get("type") or "")
        # 返回状态结果
        return status_map, activity_map, node_order

    def render_status_panel(self, thread_id: Optional[str] = None, floating: bool = True) -> None:
        active_thread_id = thread_id or st.session_state.get("agent_thread_id") or ""
        if not active_thread_id:
            return
        last_state = st.session_state.get("agent_last_state") or {}
        plan = last_state.get("plan") if isinstance(last_state, dict) else None
        if plan:
            status_map, activity_map, node_order = self._build_task_status_from_plan(
                plan=plan,
                completed_tasks=list(last_state.get("completed_tasks") or []),
                failed_tasks=list(last_state.get("failed_tasks") or []),
            )
        else:
            return
        status_color = {
            "pending": "#9aa0a6",
            "running": "#1a73e8",
            "done": "#1e8e3e",
            "paused": "#f9ab00",
            "failed": "#d93025",
        }
        # 初始化 HTML 行列表
        rows_html = []
        for node in node_order:
            # 获取节点状态、颜色、活动信息和标签
            status = status_map.get(node, "pending")
            color = status_color.get(status, "#5f6368")
            activity = activity_map.get(node, "")
            label = node.replace("_", " ").title()
            
            # 使用 textwrap.dedent 去除缩进，防止 Markdown 将其渲染为代码块
            # 并使用 strip() 去除首尾空白
            row_content = textwrap.dedent(
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
            ).strip()
            rows_html.append(row_content)

        # 构建面板头部 HTML，使用 textwrap.dedent 确保无缩进
        overall_status = last_state.get("status") if isinstance(last_state, dict) else getattr(last_state, "status", "")
        overall_status_text = {"done": "完成", "failed": "失败", "paused": "暂停", "running": "运行中"}.get(
            overall_status,
            overall_status or "未知",
        )
        panel_header = textwrap.dedent(
            f"""
            <div id="agent-status-panel">
                <div class="agent-panel-title">Agent 运行状态</div>
                <div class="agent-panel-overall">总状态：{overall_status_text}</div>
            """
        ).strip()

        # 构建面板样式 CSS
        # 注意：此处使用普通字符串而非 f-string，因此 CSS 中的大括号不需要转义
        panel_css = textwrap.dedent(
            """
            <style>
            #agent-status-panel {
                background: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 12px;
                padding: 12px 14px;
                box-shadow: 0 8px 18px rgba(0,0,0,0.08);
                width: 320px;
                font-size: 13px;
                line-height: 1.4;
            }
            .agent-panel-title {
                font-weight: 600;
                margin-bottom: 8px;
                color: #202124;
            }
            .agent-panel-overall {
                font-size: 12px;
                color: #5f6368;
                margin-bottom: 8px;
            }
            .agent-node-row {
                padding: 6px 0;
                border-bottom: 1px dashed #ececec;
            }
            .agent-node-row:last-child {
                border-bottom: none;
            }
            .agent-node-title {
                display: flex;
                align-items: center;
                gap: 6px;
                margin-bottom: 4px;
            }
            .agent-dot {
                width: 10px;
                height: 10px;
                border-radius: 50%;
                display: inline-block;
            }
            .agent-node-label {
                font-weight: 600;
                color: #202124;
            }
            .agent-node-status {
                margin-left: auto;
                color: #5f6368;
                text-transform: capitalize;
            }
            .agent-node-activity {
                color: #5f6368;
            }
            </style>
            """
        ).strip()

        # 拼接最终的 HTML 字符串：头部 + 所有行 + 闭合标签 + CSS
        panel_html = f"{panel_header}\n{''.join(rows_html)}\n</div>\n{panel_css}"
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

    def render_live_panel(self, floating: bool = True) -> None:
        thread_id = st.session_state.get("agent_thread_id") or ""
        last_state = st.session_state.get("agent_last_state") or {}
        plan_preview = st.session_state.get("agent_plan_preview")
        events = event_bus.list(thread_id) if thread_id else []
        if not plan_preview and not last_state and not events:
            return
        plan_payload_for_map = plan_preview
        if isinstance(last_state, dict) and last_state.get("plan"):
            plan_payload_for_map = {"tasks": last_state.get("plan")}
        task_title_map = self._build_task_title_map(plan_payload_for_map)
        lines: List[str] = []
        if plan_preview:
            tasks = self._extract_plan_tasks(plan_preview)
            if tasks:
                plan_text = " → ".join([self._task_title(task) for task in tasks])
                lines.append(f"计划预览：{self._truncate_text(plan_text, 60)}")
        if isinstance(last_state, dict):
            summaries = last_state.get("task_summaries") or {}
            if summaries:
                summary_lines = []
                for key, value in list(summaries.items())[:3]:
                    text = str(value or "")
                    parts = text.split(":")
                    task_id = parts[0] if parts else str(key)
                    status_key = parts[-1] if len(parts) >= 3 else ""
                    status_text = {"success": "成功", "failed": "失败"}.get(status_key, status_key or "未知")
                    title = task_title_map.get(task_id, "任务")
                    summary_lines.append(f"{title}（{task_id}）{status_text}")
                summary_text = "、".join(summary_lines)
                if len(summaries) > 3:
                    summary_text += "…"
                lines.append(f"任务摘要：{summary_text}")
        if events:
            event_lines = [self._format_event_line(event, task_title_map, last_state.get("task_summaries") or {}) for event in sorted(events, key=lambda e: e["ts"])]
            if len(event_lines) > 3:
                event_lines = event_lines[-3:]
            lines.append("事件：")
            lines.extend([f"- {line}" for line in event_lines])
        content_html = "".join([f"<div class='agent-live-line'>{line}</div>" for line in lines])
        panel_html = textwrap.dedent(
            f"""
        <div id="agent-live-panel">
            <div class="agent-live-title">Agent 实时</div>
            {content_html}
        </div>
        <style>
        #agent-live-panel {{
            background: #ffffff;
            border: 1px solid #e0e0e0;
            border-radius: 12px;
            padding: 12px 14px;
            box-shadow: 0 8px 18px rgba(0,0,0,0.08);
            width: 320px;
            font-size: 13px;
            line-height: 1.4;
        }}
        .agent-live-title {{
            font-weight: 600;
            margin-bottom: 8px;
            color: #202124;
        }}
        .agent-live-line {{
            margin-bottom: 6px;
            color: #5f6368;
        }}
        </style>
        """
        ).strip()
        if floating:
            st.markdown(
                """
                <style>
                div.element-container:has(div#agent-live-marker) + div.element-container {
                    position: fixed !important;
                    right: 20px;
                    bottom: 120px;
                    z-index: 1000001;
                    width: auto !important;
                }
                </style>
                <div id="agent-live-marker"></div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown(panel_html, unsafe_allow_html=True)
