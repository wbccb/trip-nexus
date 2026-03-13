from __future__ import annotations

import asyncio
import json
import time
import re
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from src.agent.plan_models import Plan, Task, TripState, SharedContext
from src.agent.event_bus import event_bus
from src.llm.llm_manager import LlmManager
from src.llm.tool_protocol import ToolCallResult
from src.map.map_renderer import TripMap
from src.observability import ErrorCodes, build_error_payload, normalize_exception, BudgetManager, ConstraintChecker
from src.config import Config  # 读取全局配置预算


def _strip_think_content(text: Any) -> str:
    if text is None:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", str(text), flags=re.DOTALL)
    cleaned = re.sub(r"</?think>", "", cleaned)
    return cleaned.strip()


def _format_log_text(text: str, head: int = 180, tail: int = 180) -> str:
    if text is None:
        return ""
    text_value = str(text)
    if len(text_value) <= head + tail + 5:
        return text_value
    return f"{text_value[:head]}....{text_value[-tail:]}"


def _log_llm_output(tag: str, cleaned_text: str) -> None:
    preview = _format_log_text(cleaned_text)
    print(f"[PlannerAgent] {tag} cleaned_len={len(cleaned_text)} cleaned_preview={preview}")


class AgentGraphState(TypedDict):
    user_intent: str
    user_input: Dict[str, Any]
    thread_id: str
    agent_config: Dict[str, Any]
    plan: List[Dict[str, Any]]
    plan_history: List[List[Dict[str, Any]]]
    execution_queue: List[str]
    completed_tasks: List[str]
    failed_tasks: List[str]
    pending_count: int
    task_summaries: Dict[str, str]
    shared_context: Dict[str, Any]
    task_results: Dict[str, Any]
    context_revisions: List[Dict[str, Any]]
    final_payload: Dict[str, Any]
    status: str
    error: Optional[str]
    stop_reason: Optional[str]
    retry_counts: Dict[str, int]
    replan_depth: int
    max_replan_depth: int
    loop_started_at: float
    last_task_id: Optional[str]
    last_task_result: Dict[str, Any]


_AGENT_CHECKPOINTER = InMemorySaver()


class PlannerAgent:
    def __init__(self, llm_manager: LlmManager) -> None:
        # 复用 LLM 管理器
        self._llm_manager = llm_manager

    def _build_sop_plan(self, user_input: Dict[str, Any], agent_config: Dict[str, Any]) -> Plan:
        # 获取目的地
        destination = user_input.get("destination") or ""
        # 获取 POI 查询关键词
        poi_query = agent_config.get("poi_query") or "热门景点"
        # 获取 POI 数量
        poi_top_k = int(agent_config.get("poi_top_k") or 5)
        # 获取天气天数
        weather_days = int(agent_config.get("weather_days") or 3)
        # 组装任务列表
        defer_answer = bool(agent_config.get("manual_rag_review"))
        tasks: List[Task] = [
            Task(
                id="t1",
                type="tool_call",
                tool="weather.get_daily",
                params={"city": destination, "days": weather_days},
                output_key="weather",
                description="查询目的地天气",
            ),
            Task(
                id="t2",
                type="tool_call",
                tool="poi.search",
                params={"query": poi_query, "city": destination, "top_k": poi_top_k, "defer_answer": defer_answer},
                output_key="poi",
                description="查询目的地 POI",
            ),
            Task(
                id="t3",
                type="tool_call",
                tool="geo.geocode",
                params={"address": destination, "city": destination},
                output_key="geocode",
                description="查询目的地地理编码",
            ),
            Task(
                id="t4",
                type="trip_generate",
                dependencies=["t1", "t2", "t3"],
                input_mapping={
                    "weather_context": "t1.weather",
                    "poi_context": "t2.poi",
                    "geocode_context": "t3.geocode",
                },
                output_key="draft_trip",
                description="生成行程草案",
            ),
            Task(
                id="t5",
                type="map_render",
                dependencies=["t4"],
                input_mapping={"trip": "t4.draft_trip"},
                output_key="map_payload",
                description="渲染地图",
            ),
            Task(
                id="t6",
                type="trip_summarize",
                dependencies=["t4"],
                input_mapping={"trip": "t4.draft_trip"},
                output_key="summary",
                description="生成行程摘要",
            ),
        ]
        # 返回 SOP 计划
        return Plan(tasks=tasks)

    def _build_plan_prompt(
        self,
        user_intent: str,
        user_input: Dict[str, Any],
        tool_registry: List[Dict[str, Any]],
        sop_templates: List[Dict[str, Any]],
        error_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        # 组织错误上下文
        error_text = json.dumps(error_context or {}, ensure_ascii=False)
        # 构建提示词
        prompt = f"""
你是一个任务规划器，请根据用户意图输出 JSON 任务计划。

用户意图: {user_intent}
用户输入: {json.dumps(user_input, ensure_ascii=False)}
工具清单: {json.dumps(tool_registry, ensure_ascii=False)}
SOP 模板: {json.dumps(sop_templates, ensure_ascii=False)}
错误上下文: {error_text}

请输出 JSON，结构如下：
{{"tasks":[{{"id":"t1","type":"tool_call","tool":"weather.get_daily","params":{{}},"dependencies":[],"input_mapping":{{}},"output_key":"weather","description":"..."}}]}}

只输出 JSON，不要包含其他文本。
"""
        # 返回提示词
        return prompt

    def _parse_plan_response(self, response_text: str) -> Plan:
        # 清洗并提取 JSON
        cleaned = self._llm_manager.extract_json_from_string(response_text)
        # 解析 JSON
        parsed = json.loads(cleaned)
        # 读取任务列表
        task_items = parsed.get("tasks") or []
        # 构建 Task 列表
        tasks = [Task(**item) for item in task_items if isinstance(item, dict)]
        # 返回计划
        return Plan(tasks=tasks)

    def plan(
        self,
        user_intent: str,
        user_input: Dict[str, Any],
        agent_config: Dict[str, Any],
        tool_whitelist: List[str],
        force_sop: bool = False,
        error_context: Optional[Dict[str, Any]] = None,
    ) -> Plan:
        # 生成 SOP 计划
        sop_plan = self._build_sop_plan(user_input, agent_config)
        # print(f"\n[{time.strftime('%H:%M:%S')}] [PlannerAgent] 生成 SOP 计划")
        # 判断是否需要 SOP
        if force_sop:
            sop_plan.validate_plan(tool_whitelist=set(tool_whitelist))
            tools = [t.tool for t in sop_plan.tasks if t.type == "tool_call"]
            print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] 使用 SOP 计划，工具序列: {', '.join(tools)}\n")
            return sop_plan
        # 读取工具清单
        tool_registry = self._llm_manager.list_tools()
        # 构建 SOP 模板列表
        sop_templates = [{"name": "default_trip", "tasks": [task.model_dump() for task in sop_plan.tasks]}]
        # 构建 prompt
        prompt = self._build_plan_prompt(user_intent, user_input, tool_registry, sop_templates, error_context=error_context)
        print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] 即将调用大模型......进行任务规划")
        print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] 用户意图: {user_intent}")
        print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] 用户输入: {json.dumps(user_input, ensure_ascii=False)}")
        print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] prompt\n: {prompt}")
        raw_response = self._llm_manager.get_analysis_llm().invoke(prompt)
        
        response_text = raw_response.content if hasattr(raw_response, "content") else raw_response
        cleaned_response_text = _strip_think_content(response_text)
        _log_llm_output("plan_response", cleaned_response_text)
        
        print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] 大模型-任务规划完成")

        plan = self._parse_plan_response(str(cleaned_response_text))
        plan.validate_plan(tool_whitelist=set(tool_whitelist))

        tools = [t.tool for t in plan.tasks if t.type == "tool_call"]
        print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] 规划完成，工具序列: {', '.join(tools)}\n")
        return plan


class Reflector:
    def __init__(self, max_replan_depth: int = 2) -> None:
        # 最大重规划深度
        self._max_replan_depth = max(0, int(max_replan_depth))

    def should_replan(self, task: Task, result: Dict[str, Any]) -> bool:
        # 非工具任务直接跳过
        if task.type != "tool_call":
            return False
        # 失败结果触发重规划
        if not result.get("success"):
            return True
        # 工具返回为空触发重规划
        data = result.get("data")
        if data is None:
            return True
        if isinstance(data, dict) and not data:
            return True
        if isinstance(data, list) and len(data) == 0:
            return True
        # 针对已知工具做细粒度空结果判定
        if task.tool == "weather.get_daily":
            daily = data.get("daily") if isinstance(data, dict) else None
            if isinstance(daily, list) and len(daily) == 0:
                return True
        if task.tool == "poi.search":
            results = data.get("results") if isinstance(data, dict) else None
            if isinstance(results, list) and len(results) == 0:
                return True
        if task.tool == "geo.geocode":
            lat = data.get("latitude") if isinstance(data, dict) else None
            lng = data.get("longitude") if isinstance(data, dict) else None
            if lat in [None, 0, 0.0] or lng in [None, 0, 0.0]:
                return True
        # 默认不触发重规划
        return False

    def can_replan(self, state: TripState) -> bool:
        # 判断是否超过最大重规划深度
        return int(state.replan_depth) < self._max_replan_depth


def _serialize_shared_context(shared_context: SharedContext) -> Dict[str, Any]:
    # 将 SharedContext 转成可序列化结构，便于 checkpoint 持久化
    return {
        "data": shared_context.data or {},
        "revisions": shared_context.revisions or [],
    }


def _deserialize_shared_context(payload: Any) -> SharedContext:
    # 从 checkpoint payload 还原 SharedContext
    if isinstance(payload, SharedContext):
        return payload
    if not isinstance(payload, dict):
        return SharedContext()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    revisions = payload.get("revisions") if isinstance(payload.get("revisions"), list) else []
    return SharedContext(data=data, revisions=revisions)


def _serialize_plan_items(tasks: List[Task]) -> List[Dict[str, Any]]:
    # 将 Task 列表转成 JSON 友好的结构
    return [task.model_dump() for task in tasks]


def _deserialize_plan_items(payload: Any) -> List[Task]:
    # 将 checkpoint 中的任务结构还原为 Task 列表
    if not isinstance(payload, list):
        return []
    tasks: List[Task] = []
    for item in payload:
        if isinstance(item, Task):
            tasks.append(item)
            continue
        if isinstance(item, dict):
            tasks.append(Task(**item))
    return tasks


def _graph_state_from_trip_state(state: TripState, agent_config: Dict[str, Any]) -> AgentGraphState:
    # 将 TripState 映射为 LangGraph State
    return {
        "user_intent": state.user_intent,
        "user_input": state.user_input,
        "thread_id": state.thread_id,
        "agent_config": agent_config,
        "plan": _serialize_plan_items(state.plan),
        "plan_history": [
            _serialize_plan_items(history) for history in (state.plan_history or [])
        ],
        "execution_queue": list(state.execution_queue or []),
        "completed_tasks": list(state.completed_tasks or []),
        "failed_tasks": list(state.failed_tasks or []),
        "pending_count": 0,
        "task_summaries": dict(state.task_summaries or {}),
        "shared_context": _serialize_shared_context(state.shared_context),
        "task_results": dict(state.task_results or {}),
        "context_revisions": list(state.context_revisions or []),
        "final_payload": dict(state.final_payload or {}),
        "status": state.status,
        "error": state.error,
        "stop_reason": state.stop_reason,
        "retry_counts": dict(state.retry_counts or {}),
        "replan_depth": int(state.replan_depth),
        "max_replan_depth": int(state.max_replan_depth),
        "loop_started_at": float(time.time()),
        "last_task_id": None,
        "last_task_result": {},
    }


def _trip_state_from_graph_state(payload: Dict[str, Any]) -> TripState:
    # 将 LangGraph State 还原为 TripState
    plan_items = _deserialize_plan_items(payload.get("plan"))
    plan_history_payload = payload.get("plan_history") if isinstance(payload.get("plan_history"), list) else []
    plan_history = [
        _deserialize_plan_items(history) for history in plan_history_payload if isinstance(history, list)
    ]
    shared_context = _deserialize_shared_context(payload.get("shared_context"))
    return TripState(
        user_intent=payload.get("user_intent") or "",
        user_input=payload.get("user_input") or {},
        thread_id=payload.get("thread_id") or "",
        plan=plan_items,
        plan_history=plan_history,
        execution_queue=list(payload.get("execution_queue") or []),
        completed_tasks=set(payload.get("completed_tasks") or []),
        failed_tasks=set(payload.get("failed_tasks") or []),
        task_summaries=payload.get("task_summaries") or {},
        shared_context=shared_context,
        task_results=payload.get("task_results") or {},
        context_revisions=payload.get("context_revisions") or [],
        final_payload=payload.get("final_payload") or {},
        status=payload.get("status") or "pending",
        error=payload.get("error"),
        stop_reason=payload.get("stop_reason"),
        retry_counts=payload.get("retry_counts") or {},
        replan_depth=int(payload.get("replan_depth") or 0),
        max_replan_depth=int(payload.get("max_replan_depth") or 0),
    )


class AgentExecutor:
    def __init__(self, llm_manager: LlmManager, map_renderer: Optional[TripMap] = None) -> None:
        # 复用 LLM 管理器
        self._llm_manager = llm_manager
        # 初始化地图渲染器
        self._map_renderer = map_renderer or TripMap()
        # 初始化 Planner
        self._planner = PlannerAgent(llm_manager)
        self._node_alias = {
            "tool_call": "checker",
            "trip_generate": "optimizer",
            "map_render": "map_rag",
            "trip_summarize": "map_rag",
        }

    def _tool_whitelist(self) -> List[str]:
        # 读取工具清单
        schemas = self._llm_manager.list_tools()
        # 提取工具名称
        return [schema.get("name") for schema in schemas if isinstance(schema, dict)]

    def _extract_tool_result(self, tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
        # 仅返回成功结果中的关键字段
        data = result.get("data")
        if not isinstance(data, dict):
            return {"raw": data}
        # 天气结果提取
        if tool_name == "weather.get_daily":
            return {"city": data.get("city"), "daily": data.get("daily")}
        # POI 结果提取
        if tool_name == "poi.search":
            return {
                "query": data.get("query"),
                "results": data.get("results"),
                "evidence": data.get("evidence"),
                "rag_answer": data.get("rag_answer"),
            }
        # 地理编码结果提取
        if tool_name == "geo.geocode":
            return {
                "address": data.get("address"),
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
            }
        # 兜底返回
        return data

    def _summarize_task(self, task: Task, success: bool) -> str:
        # 拼接任务摘要文本
        status_text = "success" if success else "failed"
        return f"{task.id}:{task.type}:{status_text}"

    def _resolve_node(self, task: Task) -> str:
        return self._node_alias.get(task.type, task.type or "executor")

    # 发送共享上下文的 JSON Patch 事件
    def _emit_context_patch(self, state: TripState, task: Task, output_key: str, value: Any) -> None:
        # 构造 JSON Patch 列表
        patch_ops = [
            # 追加共享上下文变更
            {"op": "add", "path": f"/shared_context/{task.id}/{output_key}", "value": value}
        ]
        # 发送上下文补丁事件
        event_bus.emit(
            "context_patch",
            state.thread_id,
            self._resolve_node(task),
            {"task_id": task.id, "key": output_key, "value": value, "patch": patch_ops},
        )

    def _execute_task(
        self,
        task: Task,
        state: TripState,
        agent_config: Dict[str, Any],
    ) -> Tuple[Task, Dict[str, Any]]:
        node_name = self._resolve_node(task)
        event_bus.emit(
            "task_start",
            state.thread_id,
            node_name,
            {
                "task_id": task.id,
                "task_type": task.type,
                "tool": task.tool,
                "description": task.description,
            },
        )
        # 工具任务执行
        if task.type == "tool_call":
            # 调用工具注册表
            result = self._llm_manager.tool_registry.call_with_context(
                task.tool or "",
                task.params or {},
                shared_context=state.shared_context,
                input_mapping=task.input_mapping,
            )
            # 转换结果为 dict
            result_dict = result.model_dump() if hasattr(result, "model_dump") else dict(result)
            # 提取关键事实写入上下文
            if result_dict.get("success"):
                output_key = task.output_key or "result"
                cleaned = self._extract_tool_result(task.tool or "", result_dict)
                state.shared_context.write(task.id, output_key, cleaned)
                self._emit_context_patch(state, task, output_key, cleaned)
            # 返回结果
            event_bus.emit(
                "task_end",
                state.thread_id,
                node_name,
                {
                    "task_id": task.id,
                    "task_type": task.type,
                    "success": bool(result_dict.get("success")),
                },
            )
            return task, result_dict
        # 行程生成任务
        if task.type == "trip_generate":
            # 构建上下文摘要
            context_payload = []
            for key, bucket in (state.shared_context.data or {}).items():
                context_payload.append(f"{key}:{json.dumps(bucket, ensure_ascii=False)}")
            
            # [LOG] 调用大模型生成行程前
            print(f"[{time.strftime('%H:%M:%S')}] [AgentExecutor] 正在执行行程生成任务: {task.id}")
            print(f"[{time.strftime('%H:%M:%S')}] [AgentExecutor] 上下文条目数: {len(context_payload)}")
            
            # 调用行程生成
            # generate_trip 内部会构建 prompt 并调用 LLM 生成详细行程 JSON
            draft = self._llm_manager.generate_trip_pure(state.user_input, context_payload) or {}
            
            # [LOG] 调用大模型生成行程后
            trip_days = draft.get("days") if isinstance(draft, dict) else "Unknown"
            print(f"[{time.strftime('%H:%M:%S')}] [AgentExecutor] 行程生成完成")
            print(f"[{time.strftime('%H:%M:%S')}] [AgentExecutor] 生成行程天数: {trip_days}")

            # 写入共享上下文
            output_key = task.output_key or "draft_trip"
            state.shared_context.write(task.id, output_key, draft)
            self._emit_context_patch(state, task, output_key, draft)
            # 返回统一结果
            result_payload = {"success": True, "data": draft}
            event_bus.emit(
                "task_end",
                state.thread_id,
                node_name,
                {
                    "task_id": task.id,
                    "task_type": task.type,
                    "success": True,
                },
            )
            return task, result_payload
        # 地图渲染任务
        if task.type == "map_render":
            # 读取行程数据
            trip_data = state.shared_context.resolve(task.input_mapping.get("trip", ""), default={})
            # 渲染地图
            map_obj = self._map_renderer.render_map(trip_data or {})
            # 生成 map_html
            map_payload = {"map_html": map_obj.get_root().render()} if map_obj else {}
            # 写入共享上下文
            output_key = task.output_key or "map_payload"
            state.shared_context.write(task.id, output_key, map_payload)
            self._emit_context_patch(state, task, output_key, map_payload)
            # 返回结果
            result_payload = {"success": True, "data": map_payload}
            event_bus.emit(
                "task_end",
                state.thread_id,
                node_name,
                {
                    "task_id": task.id,
                    "task_type": task.type,
                    "success": True,
                },
            )
            return task, result_payload
        # 行程摘要任务
        if task.type == "trip_summarize":
            print("\n")
            # 读取行程数据
            trip_data = state.shared_context.resolve(task.input_mapping.get("trip", ""), default={})
            # 构造简要摘要
            summary = f"{trip_data.get('destination', '')} 行程，共 {trip_data.get('days', '')} 天"
            # 写入共享上下文
            output_key = task.output_key or "summary"
            state.shared_context.write(task.id, output_key, {"summary": summary})
            self._emit_context_patch(state, task, output_key, {"summary": summary})
            print(f"生成摘要完成:{summary}")
            print("\n")
            # 返回结果
            result_payload = {"success": True, "data": {"summary": summary}}
            event_bus.emit(
                "task_end",
                state.thread_id,
                node_name,
                {
                    "task_id": task.id,
                    "task_type": task.type,
                    "success": True,
                },
            )
            return task, result_payload
        # 默认未知任务类型
        result_payload = {"success": False, "error": {"code": "UNKNOWN_TASK_TYPE", "message": task.type}}
        event_bus.emit(
            "task_end",
            state.thread_id,
            node_name,
            {
                "task_id": task.id,
                "task_type": task.type,
                "success": False,
            },
        )
        return task, result_payload

    async def _execute_batch(
        self,
        batch: List[Task],
        state: TripState,
        agent_config: Dict[str, Any],
    ) -> List[Tuple[Task, Dict[str, Any]]]:
        # 构建并发任务
        tasks = [
            asyncio.to_thread(self._execute_task, task, state, agent_config)
            for task in batch
        ]
        print(f"[{time.strftime('%H:%M:%S')}] [AgentExecutor] 并发执行任务: {tasks}")
        # 并发执行并返回结果
        return await asyncio.gather(*tasks)

    async def run_agent_loop(
        self,
        user_input: Dict[str, Any],
        thread_id: str,
        agent_config: Dict[str, Any],
        user_intent: str,
        context: Optional[List[str]] = None,
        plan_override: Optional[Plan] = None,
        retry_limit: int = 1,
        max_replan_depth: int = 2,
        initial_state: Optional[TripState] = None,
        resume: bool = False,
    ) -> TripState:
        return await asyncio.to_thread(
            run_agent_loop_sync,
            self._llm_manager,
            user_input,
            thread_id,
            agent_config,
            user_intent,
            context,
            plan_override,
            retry_limit,
            max_replan_depth,
            initial_state,
            resume,
        )


def _build_initial_graph_state(
    user_input: Dict[str, Any],
    thread_id: str,
    agent_config: Dict[str, Any],
    user_intent: str,
    plan_override: Optional[Plan],
    retry_limit: int,
    max_replan_depth: int,
) -> AgentGraphState:
    plan_items = _serialize_plan_items(plan_override.tasks) if plan_override else []
    merged_config = dict(agent_config or {})
    merged_config["retry_limit"] = int(retry_limit)
    merged_config["max_replan_depth"] = int(max_replan_depth)
    return {
        "user_intent": user_intent or "",
        "user_input": user_input or {},
        "thread_id": thread_id,
        "agent_config": merged_config,
        "plan": plan_items,
        "plan_history": [],
        "execution_queue": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "pending_count": 0,
        "task_summaries": {},
        "shared_context": _serialize_shared_context(SharedContext()),
        "task_results": {},
        "context_revisions": [],
        "final_payload": {},
        "status": "running",
        "error": None,
        "stop_reason": None,
        "retry_counts": {},
        "replan_depth": 0,
        "max_replan_depth": int(max_replan_depth),
        "loop_started_at": float(time.time()),
        "last_task_id": None,
        "last_task_result": {},
    }


def _build_agent_graph(llm_manager: LlmManager) -> Any:
    planner_agent = PlannerAgent(llm_manager)
    executor = AgentExecutor(llm_manager)

    def _planner_node(state: AgentGraphState) -> Dict[str, Any]:
        if state.get("plan"):
            return {}
        tool_whitelist = executor._tool_whitelist()
        plan = planner_agent.plan(
            state.get("user_intent") or "",
            state.get("user_input") or {},
            state.get("agent_config") or {},
            tool_whitelist,
        )
        plan_items = _serialize_plan_items(plan.tasks)
        event_bus.emit(
            "plan_created",
            state.get("thread_id") or "",
            "planner",
            {
                "tasks": [
                    {
                        "task_id": task.id,
                        "task_type": task.type,
                        "tool": task.tool,
                        "description": task.description,
                    }
                    for task in plan.tasks
                ],
            },
        )
        return {"plan": plan_items}

    def _scheduler_node(state: AgentGraphState) -> Dict[str, Any]:
        if state.get("status") in {"paused", "failed"}:
            return {"execution_queue": [], "pending_count": 0}
        agent_config = state.get("agent_config") or {}
        config = Config()
        time_budget_seconds = agent_config.get("time_budget_seconds")
        if time_budget_seconds is None:
            time_budget_seconds = config.AGENT_TIME_BUDGET_SECONDS
        if time_budget_seconds is not None and float(time_budget_seconds) <= 0:
            time_budget_seconds = None
        budget_checker = ConstraintChecker(BudgetManager(time_budget_seconds=time_budget_seconds))
        elapsed_seconds = time.time() - float(state.get("loop_started_at") or time.time())
        if not budget_checker.ensure_within_budget(elapsed_seconds=elapsed_seconds):
            error_payload = build_error_payload(
                code=ErrorCodes.BUDGET_EXCEEDED,
                message="agent_time_budget_exceeded",
                source="agent_executor",
            )
            event_bus.emit("error", state.get("thread_id") or "", "executor", {"error": error_payload})
            return {
                "status": "failed",
                "error": error_payload.get("message"),
                "stop_reason": "budget_exceeded",
                "execution_queue": [],
                "pending_count": 0,
            }
        plan_items = _deserialize_plan_items(state.get("plan"))
        completed = set(state.get("completed_tasks") or [])
        failed = set(state.get("failed_tasks") or [])
        pending_tasks = [task for task in plan_items if task.id not in completed and task.id not in failed]
        ready_tasks = [
            task.id
            for task in pending_tasks
            if all(dep in completed for dep in (task.dependencies or []))
        ]
        max_concurrency = int(agent_config.get("max_concurrency") or 4)
        execution_queue = ready_tasks[:max_concurrency]
        if not execution_queue and pending_tasks:
            error_payload = build_error_payload(
                code=ErrorCodes.UNEXPECTED_ERROR,
                message="scheduler_no_ready_tasks",
                source="agent_scheduler",
            )
            event_bus.emit("error", state.get("thread_id") or "", "scheduler", {"error": error_payload})
            return {
                "status": "failed",
                "error": error_payload.get("message"),
                "stop_reason": "no_ready_tasks",
                "execution_queue": [],
                "pending_count": len(pending_tasks),
            }
        if execution_queue:
            event_bus.emit(
                "batch_start",
                state.get("thread_id") or "",
                "executor",
                {"tasks": execution_queue},
            )
        return {"execution_queue": execution_queue, "pending_count": len(pending_tasks)}

    def _executor_node(state: AgentGraphState) -> Dict[str, Any]:
        execution_queue = state.get("execution_queue") or []
        if not execution_queue:
            return {}
        plan_items = _deserialize_plan_items(state.get("plan"))
        task_id = execution_queue[0]
        task = next((item for item in plan_items if item.id == task_id), None)
        if not task:
            return {"execution_queue": []}
        trip_state = _trip_state_from_graph_state(state)
        result_task, result_payload = executor._execute_task(task, trip_state, state.get("agent_config") or {})
        success = bool(result_payload.get("success"))
        final_success = success
        retry_limit = int((state.get("agent_config") or {}).get("retry_limit") or 1)
        if not success:
            retry_count = (state.get("retry_counts") or {}).get(task.id, 0)
            if retry_count < retry_limit:
                event_bus.emit(
                    "task_retry",
                    state.get("thread_id") or "",
                    executor._resolve_node(task),
                    {"task_id": task.id, "retry_count": retry_count + 1},
                )
                retry_task, retry_result = executor._execute_task(task, trip_state, state.get("agent_config") or {})
                result_task, result_payload = retry_task, retry_result
                final_success = bool(retry_result.get("success"))
        completed = set(state.get("completed_tasks") or [])
        failed = set(state.get("failed_tasks") or [])
        if final_success:
            completed.add(task.id)
        else:
            failed.add(task.id)
        if not final_success and task.type == "tool_call":
            event_bus.emit(
                "paused",
                state.get("thread_id") or "",
                executor._resolve_node(task),
                {"reason": "tool_failed", "task_id": task.id},
            )
            decision = interrupt(
                {
                    "reason": "tool_failed",
                    "task_id": task.id,
                    "task_type": task.type,
                    "tool": task.tool,
                    "result": result_payload,
                }
            )
            action = decision.get("action") if isinstance(decision, dict) else "retry"
            if action == "skip":
                output_key = task.output_key or "result"
                trip_state.shared_context.write(task.id, output_key, {})
                executor._emit_context_patch(trip_state, task, output_key, {})
            elif action == "override" and isinstance(decision, dict):
                override_result = decision.get("override_result")
                output_key = task.output_key or "result"
                trip_state.shared_context.write(task.id, output_key, override_result or {})
                executor._emit_context_patch(trip_state, task, output_key, override_result or {})
                completed.add(task.id)
                failed.discard(task.id)
            else:
                retry_task, retry_result = executor._execute_task(task, trip_state, state.get("agent_config") or {})
                result_task, result_payload = retry_task, retry_result
                final_success = bool(retry_result.get("success"))
                if final_success:
                    completed.add(task.id)
                    failed.discard(task.id)
        data_payload = result_payload.get("data") if isinstance(result_payload, dict) else None
        if final_success and (state.get("agent_config") or {}).get("manual_rag_review"):
            if isinstance(data_payload, dict) and data_payload.get("evidence"):
                event_bus.emit(
                    "paused",
                    state.get("thread_id") or "",
                    "map_rag",
                    {"reason": "rag_review", "task_id": task.id},
                )
                return {
                    "status": "paused",
                    "stop_reason": "rag_review",
                    "execution_queue": [],
                    "completed_tasks": list(completed),
                    "failed_tasks": list(failed),
                    "task_results": trip_state.task_results,
                    "shared_context": _serialize_shared_context(trip_state.shared_context),
                }
        task_summaries = dict(state.get("task_summaries") or {})
        task_summaries[task.id] = executor._summarize_task(task, final_success)
        updated_retry_counts = dict(state.get("retry_counts") or {})
        if not final_success:
            updated_retry_counts[task.id] = updated_retry_counts.get(task.id, 0) + 1
        return {
            "execution_queue": [],
            "completed_tasks": list(completed),
            "failed_tasks": list(failed),
            "task_results": trip_state.task_results,
            "task_summaries": task_summaries,
            "shared_context": _serialize_shared_context(trip_state.shared_context),
            "last_task_id": task.id,
            "last_task_result": result_payload,
            "retry_counts": updated_retry_counts,
        }

    def _reflector_node(state: AgentGraphState) -> Dict[str, Any]:
        last_task_id = state.get("last_task_id")
        if not last_task_id:
            return {}
        plan_items = _deserialize_plan_items(state.get("plan"))
        last_task = next((task for task in plan_items if task.id == last_task_id), None)
        if not last_task:
            return {}
        trip_state = _trip_state_from_graph_state(state)
        reflector = Reflector(max_replan_depth=int(state.get("max_replan_depth") or 0))
        last_result = state.get("last_task_result") or {}
        if reflector.should_replan(last_task, last_result) and reflector.can_replan(trip_state):
            tool_whitelist = executor._tool_whitelist()
            new_plan = planner_agent.plan(
                state.get("user_intent") or "",
                state.get("user_input") or {},
                state.get("agent_config") or {},
                tool_whitelist,
                force_sop=False,
                error_context={"task_id": last_task.id, "result": last_result},
            )
            plan_history = list(state.get("plan_history") or [])
            plan_history.append(state.get("plan") or [])
            event_bus.emit(
                "plan_created",
                state.get("thread_id") or "",
                "planner",
                {
                    "tasks": [
                        {
                            "task_id": task.id,
                            "task_type": task.type,
                            "tool": task.tool,
                            "description": task.description,
                        }
                        for task in new_plan.tasks
                    ],
                },
            )
            event_bus.emit(
                "replan",
                state.get("thread_id") or "",
                "executor",
                {"depth": int(state.get("replan_depth") or 0) + 1},
            )
            return {
                "plan": _serialize_plan_items(new_plan.tasks),
                "plan_history": plan_history,
                "replan_depth": int(state.get("replan_depth") or 0) + 1,
                "execution_queue": [],
            }
        return {}

    def _finalize_node(state: AgentGraphState) -> Dict[str, Any]:
        shared_context = _deserialize_shared_context(state.get("shared_context"))
        final_payload = {
            "draft_trip": shared_context.resolve("t4.draft_trip", default={}),
            "map_payload": shared_context.resolve("t5.map_payload", default={}),
            "summary": shared_context.resolve("t6.summary", default={}),
        }
        status = state.get("status") or "running"
        if status == "running":
            status = "done"
        event_bus.emit("loop_end", state.get("thread_id") or "", "executor", {"status": status})
        return {"final_payload": final_payload, "status": status}

    planner_builder = StateGraph(AgentGraphState)
    planner_builder.add_node("planner", _planner_node)
    planner_builder.set_entry_point("planner")
    planner_builder.add_edge("planner", END)
    planner_graph = planner_builder.compile()

    executor_builder = StateGraph(AgentGraphState)
    executor_builder.add_node("executor", _executor_node)
    executor_builder.set_entry_point("executor")
    executor_builder.add_edge("executor", END)
    executor_graph = executor_builder.compile()

    reflector_builder = StateGraph(AgentGraphState)
    reflector_builder.add_node("reflector", _reflector_node)
    reflector_builder.set_entry_point("reflector")
    reflector_builder.add_edge("reflector", END)
    reflector_graph = reflector_builder.compile()

    workflow = StateGraph(AgentGraphState)
    workflow.add_node("planner", planner_graph)
    workflow.add_node("scheduler", _scheduler_node)
    workflow.add_node("executor", executor_graph)
    workflow.add_node("reflector", reflector_graph)
    workflow.add_node("finalize", _finalize_node)
    workflow.set_entry_point("planner")
    workflow.add_edge("planner", "scheduler")

    def _route_from_scheduler(state: AgentGraphState) -> str:
        if state.get("status") in {"paused", "failed"}:
            return "end"
        if not state.get("execution_queue") and not state.get("pending_count"):
            return "end"
        if not state.get("execution_queue"):
            return "end"
        return "execute"

    workflow.add_conditional_edges(
        "scheduler",
        _route_from_scheduler,
        {"execute": "executor", "end": "finalize"},
    )
    workflow.add_edge("executor", "reflector")
    workflow.add_edge("reflector", "scheduler")
    workflow.add_edge("finalize", END)
    return workflow.compile(checkpointer=_AGENT_CHECKPOINTER)


def run_agent_loop_sync(
    llm_manager: LlmManager,
    user_input: Dict[str, Any],
    thread_id: str,
    agent_config: Dict[str, Any],
    user_intent: str,
    context: Optional[List[str]] = None,
    plan_override: Optional[Plan] = None,
    retry_limit: int = 1,
    max_replan_depth: int = 2,
    initial_state: Optional[TripState] = None,
    resume: bool = False,
) -> TripState:
    agent_graph = _build_agent_graph(llm_manager)
    config = {"configurable": {"thread_id": thread_id}}
    resume_payload = agent_config.get("human_decision") or {"approved": True}
    if resume or (initial_state and initial_state.status == "paused"):
        if initial_state:
            state_payload = _graph_state_from_trip_state(initial_state, agent_config)
            try:
                agent_graph.update_state(config, state_payload)
            except Exception:
                pass
        try:
            result = agent_graph.invoke(Command(resume=resume_payload), config=config)
        except Exception:
            result = agent_graph.invoke(
                _build_initial_graph_state(
                    user_input=user_input,
                    thread_id=thread_id,
                    agent_config=agent_config,
                    user_intent=user_intent,
                    plan_override=plan_override,
                    retry_limit=retry_limit,
                    max_replan_depth=max_replan_depth,
                ),
                config=config,
            )
    else:
        result = agent_graph.invoke(
            _build_initial_graph_state(
                user_input=user_input,
                thread_id=thread_id,
                agent_config=agent_config,
                user_intent=user_intent,
                plan_override=plan_override,
                retry_limit=retry_limit,
                max_replan_depth=max_replan_depth,
            ),
            config=config,
        )
    if isinstance(result, dict) and result.get("__interrupt__") is not None:
        result["status"] = "paused"
        result["stop_reason"] = "human_intervention"
        result.setdefault("final_payload", {})
        result["final_payload"]["interrupt"] = result.get("__interrupt__")
    return _trip_state_from_graph_state(result if isinstance(result, dict) else {})
