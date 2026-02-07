from typing import Dict, Any, Optional, List, TypedDict
import time
import uuid

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from src.llm.llm_manager import LlmManager
from src.map.map_renderer import TripMap
from src.rag.rag_main import AIRetrievalPipeline
from src.agent.event_bus import event_bus, snapshot_store


class TripState(TypedDict, total=False):
    """
    LangGraph 状态机在 TripNexus 中使用的“全局状态结构”。

    设计原则：
    - 一个 thread_id 对应一次完整的行程生成链路；
    - 所有中间产物（draft/constraints/optimized/map_payload）都挂在同一个 state 上；
    - 运行过程元信息（logs/snapshots/status/pause_at 等）也统一放在 state 中，方便快照和调试。
    """

    thread_id: str
    user_input: Dict[str, Any]
    context: List[str]
    draft: Dict[str, Any]
    constraints: Dict[str, Any]
    optimized: Dict[str, Any]
    map_payload: Dict[str, Any]
    logs: List[str]
    snapshots: List[Dict[str, Any]]
    status: str
    pause_at: Optional[str]
    paused: bool
    cancelled: bool
    error: Optional[str]
    retry_limit: int
    retry_count: int
    agent_config: Dict[str, Any]


class AgentOrchestrator:
    """
    基于 LangGraph 的行程编排 Orchestrator。

    职责：
    1) 把 Planner/Checker/Optimizer/Map-RAG 抽象为图节点，按顺序编排；
    2) 在每个节点前后打点并写入事件总线 + 快照存储；
    3) 暴露 run_stream/resume_from_latest，供 UI 触发执行或从最近快照恢复。
    """

    def __init__(self, llm_manager: LlmManager, map_renderer: Optional[TripMap] = None) -> None:
        """
        初始化编排器。

        参数：
        - llm_manager：复用现有 LlmManager，避免重复封装工具/模型调用；
        - map_renderer：地图渲染器，默认使用 TripMap；

        内部成员：
        - rag_pipeline：RAG 管线，用于 Map/RAG 节点补充证据；
        - checkpointer：LangGraph 的内存检查点对象（供未来扩展使用，本版本主要用自建快照）；
        - graph：编译完成的 StateGraph，可 invoke/stream。
        """

        self.llm_manager = llm_manager
        self.map_renderer = map_renderer or TripMap()
        self.rag_pipeline = AIRetrievalPipeline(llm_manager.get_llm())
        self.checkpointer = InMemorySaver()
        self.graph = self._build_graph()

    def create_thread_id(self) -> str:
        """
        生成一次 Agent 执行的唯一 thread_id。

        返回：
        - UUID 字符串，UI 会把它保存在 session_state 中用作过滤键。
        """

        return str(uuid.uuid4())

    def run_stream(
        self,
        user_input: Dict[str, Any],
        thread_id: str,
        agent_config: Dict[str, Any],
        context: Optional[List[str]] = None,
        pause_at: Optional[str] = None,
        resume_state: Optional[Dict[str, Any]] = None,
        retry_limit: int = 1,
    ) -> Dict[str, Any]:
        """
        以流式方式运行一次完整编排链路。

        调用方（通常是 Streamlit）只需要：
        - 提供结构化 user_input（目的地/天数/预算等）；
        - 指定 thread_id（用于事件过滤与快照归属）；
        - 传入 agent_config（是否启用 Checker/Optimizer/RAG、预算上限等）。

        实现要点：
        - 先通过 _build_initial_state 构造初始 TripState；
        - graph.stream(...) 会按照顺序依次执行各节点，并在每个节点结束时返回一段“状态增量”；
        - 每个 chunk 都会写入 EventBus（kind=update），并叠加到 current_state 中，最终返回最新 state。
        """

        initial_state = self._build_initial_state(
            user_input=user_input,
            thread_id=thread_id,
            agent_config=agent_config,
            context=context,
            pause_at=pause_at,
            resume_state=resume_state,
            retry_limit=retry_limit,
        )
        config = {"configurable": {"thread_id": thread_id}}
        current_state: Dict[str, Any] = dict(initial_state)

        for chunk in self.graph.stream(initial_state, config=config, subgraphs=True):
            # 1. 将 LangGraph 原生的 chunk 全量写入事件总线，供前端做更细节的解析或调试
            event_bus.emit("update", thread_id, None, {"raw": chunk})
            # 2. 如果 chunk 是 {node_name: state_delta} 结构，则遍历 value 并累加到 current_state 上
            if isinstance(chunk, dict):
                for update in chunk.values():
                    if isinstance(update, dict):
                        current_state.update(update)

        return current_state

    def resume_from_latest(self, thread_id: str, agent_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        从最新业务快照恢复执行。

        恢复策略：
        - 从 SnapshotStore 里拿到 latest 快照，取出其中的 state 子集；
        - 把 paused/pause_at 重置成运行状态；
        - 使用 run_stream 再次进入图执行，graph 会从头跑，但由于节点函数都做了“幂等判断 + 复用已有结果”，
          因此能够快速跳过已完成步骤。
        """

        latest = snapshot_store.latest(thread_id)
        if not latest:
            return None
        resume_state = latest.get("state") or {}
        resume_state["paused"] = False
        resume_state["pause_at"] = None
        user_input = resume_state.get("user_input") or {}
        return self.run_stream(
            user_input=user_input,
            thread_id=thread_id,
            agent_config=agent_config,
            context=resume_state.get("context"),
            pause_at=None,
            resume_state=resume_state,
            retry_limit=resume_state.get("retry_limit", 1),
        )

    def _build_graph(self):
        """
        构建 LangGraph StateGraph。

        节点：
        - planner：生成草案行程（复用 generate_trip）；
        - checker：预调用天气/POI/地理编码工具，构建约束；
        - optimizer：在预算/偏好/约束下修正草案；
        - map_rag：渲染地图并附加 RAG 证据摘要。

        边：
        - START -> planner -> checker/optimizer/map_rag（由 _route_after_* 决定）；
        - checker -> optimizer/map_rag；
        - optimizer -> map_rag -> END。
        """

        builder = StateGraph(TripState)
        builder.add_node("planner", self._wrap_node("planner", self._planner))
        builder.add_node("checker", self._wrap_node("checker", self._checker))
        builder.add_node("optimizer", self._wrap_node("optimizer", self._optimizer))
        builder.add_node("map_rag", self._wrap_node("map_rag", self._map_rag))
        builder.add_edge(START, "planner")
        builder.add_conditional_edges(
            "planner",
            self._route_after_planner,
            {"checker": "checker", "optimizer": "optimizer", "map_rag": "map_rag", "stop": END},
        )
        builder.add_conditional_edges(
            "checker",
            self._route_after_checker,
            {"optimizer": "optimizer", "map_rag": "map_rag", "stop": END},
        )
        builder.add_conditional_edges(
            "optimizer",
            self._route_after_optimizer,
            {"map_rag": "map_rag", "stop": END},
        )
        builder.add_edge("map_rag", END)
        return builder.compile(checkpointer=self.checkpointer)

    def _route_after_planner(self, state: TripState) -> str:
        """
        决定 planner 执行完后跳转到哪个节点。

        规则：
        - 若已暂停/取消/失败，则直接 stop；
        - 否则优先进入 Checker（若开启），再进入 Optimizer，否则直接进入 Map/RAG。
        """

        if state.get("paused") or state.get("cancelled") or state.get("status") in {"failed", "cancelled"}:
            return "stop"
        config = state.get("agent_config") or {}
        if config.get("enable_checker", True):
            return "checker"
        if config.get("enable_optimizer", True):
            return "optimizer"
        return "map_rag"

    def _route_after_checker(self, state: TripState) -> str:
        """
        决定 checker 执行完后跳转到哪个节点。

        规则：
        - 若已暂停/取消/失败，则 stop；
        - 否则若开启 Optimizer 则进入 Optimizer，否则直接进入 Map/RAG。
        """

        if state.get("paused") or state.get("cancelled") or state.get("status") in {"failed", "cancelled"}:
            return "stop"
        config = state.get("agent_config") or {}
        if config.get("enable_optimizer", True):
            return "optimizer"
        return "map_rag"

    def _route_after_optimizer(self, state: TripState) -> str:
        """
        决定 optimizer 执行完后跳转到哪个节点。

        规则：
        - 若已暂停/取消/失败，则 stop；
        - 否则直接进入 Map/RAG。
        """

        if state.get("paused") or state.get("cancelled") or state.get("status") in {"failed", "cancelled"}:
            return "stop"
        return "map_rag"

    def _wrap_node(self, name: str, fn):
        """
        统一包装所有节点函数，注入日志/事件/重试/快照能力。

        包装后行为：
        - node_start：节点开始时写入事件（包含当前 state key 摘要）；
        - retry：按 retry_limit 重试 fn，记录错误并在 EventBus 中打点；
        - node_end：节点结束时记录耗时和结果摘要；
        - snapshot：在 SnapshotStore 中追加一条快照（含 step/payload/logs/duration_ms/state 子集）；
        - interrupt：若 pause_at 与当前节点名一致，则设置 paused/status，并发出 interrupt 事件。
        """

        def _inner(state: TripState) -> Dict[str, Any]:
            thread_id = state.get("thread_id") or "default"
            if state.get("cancelled"):
                # 如果上游已经标记为取消，直接打点并返回取消状态
                event_bus.emit("cancelled", thread_id, name, {"reason": "cancelled"})
                return {"status": "cancelled"}
            logs, snapshots = self._ensure_lists(state)
            event_bus.emit("node_start", thread_id, name, {"state_keys": list(state.keys())})
            start_ts = time.time()
            retry_limit = int(state.get("retry_limit") or 0)
            attempts = 0
            last_error: Optional[str] = None
            result: Dict[str, Any] = {}
            while attempts <= retry_limit:
                try:
                    result = fn(state)
                    break
                except Exception as exc:
                    # 节点执行失败：记录错误并尝试重试，超过重试次数则退出循环
                    attempts += 1
                    last_error = str(exc)
                    event_bus.emit("error", thread_id, name, {"error": last_error, "attempt": attempts})
                    if attempts > retry_limit:
                        break
            duration_ms = int((time.time() - start_ts) * 1000)
            updates = dict(result)
            if last_error and attempts > retry_limit:
                # 重试后仍失败，则将状态标记为 failed，并记录错误信息
                updates["status"] = "failed"
                updates["error"] = last_error
            updates["retry_count"] = attempts
            # 记录本节点耗时到日志列表，供快照/可视化使用
            logs.append(f"{name}:{duration_ms}ms")
            event_bus.emit("node_end", thread_id, name, {"duration_ms": duration_ms, "updates": self._summarize(updates)})
            # 构造快照并写入内存存储，用于后续恢复或 UI 时间线展示
            snapshot = self._build_snapshot(name, updates, logs, duration_ms, state)
            snapshots.append(snapshot)
            snapshot_store.add(thread_id, snapshot)
            pause_at = state.get("pause_at")
            if pause_at == name:
                # 命中暂停点：标记为 paused，并发出 interrupt 事件，供 HITL 面板展示
                updates["paused"] = True
                updates["status"] = "paused"
                event_bus.emit("interrupt", thread_id, name, {"pause_at": pause_at})
            updates["logs"] = logs
            updates["snapshots"] = snapshots
            return updates

        return _inner

    def _ensure_lists(self, state: TripState) -> tuple[list, list]:
        """
        确保日志与快照列表存在，避免在节点内部处理 None。

        返回：
        - logs：当前已有的日志列表（副本）
        - snapshots：当前已有的快照列表（副本）
        """

        logs = list(state.get("logs") or [])
        snapshots = list(state.get("snapshots") or [])
        return logs, snapshots

    def _summarize(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        将节点返回的 payload 做摘要，避免在事件中存放过大的结构。

        规则：
        - dict：只保留 key 列表；
        - list：只保留长度；
        - 基本类型：直接保留原值。
        """

        summary = {}
        for key, value in payload.items():
            if isinstance(value, dict):
                summary[key] = list(value.keys())
            elif isinstance(value, list):
                summary[key] = len(value)
            else:
                summary[key] = value
        return summary

    def _build_snapshot(
        self,
        step: str,
        payload: Dict[str, Any],
        logs: List[str],
        duration_ms: int,
        state: TripState,
    ) -> Dict[str, Any]:
        """
        构造一条快照记录。

        快照内容：
        - step：当前节点名（planner/checker/optimizer/map_rag）；
        - payload：本节点新增/更新的状态字段；
        - logs：当前累计日志列表；
        - duration_ms：本节点耗时；
        - ts：快照时间戳；
        - state：恢复所需的最小状态子集（thread_id/user_input/context/draft/constraints/optimized/agent_config/retry_limit/pause_at/paused）。
        """

        return {
            "step": step,
            "payload": payload,
            "logs": list(logs),
            "duration_ms": duration_ms,
            "ts": time.time(),
            "state": {
                "thread_id": state.get("thread_id"),
                "user_input": state.get("user_input"),
                "context": state.get("context"),
                "draft": state.get("draft"),
                "constraints": state.get("constraints"),
                "optimized": state.get("optimized"),
                "agent_config": state.get("agent_config"),
                "retry_limit": state.get("retry_limit"),
                "pause_at": state.get("pause_at"),
                "paused": state.get("paused"),
            },
        }

    def _build_initial_state(
        self,
        user_input: Dict[str, Any],
        thread_id: str,
        agent_config: Dict[str, Any],
        context: Optional[List[str]],
        pause_at: Optional[str],
        resume_state: Optional[Dict[str, Any]],
        retry_limit: int,
    ) -> TripState:
        """
        构造初始 TripState。

        逻辑：
        - base_state：根据控制平面参数（user_input/agent_config/pause_at/retry_limit 等）构建；
        - 若传入 resume_state，则以它为基础进行更新，用于从快照恢复。
        """

        base_state = {
            "thread_id": thread_id,
            "user_input": user_input,
            "context": context or [],
            "agent_config": agent_config,
            "pause_at": pause_at,
            "paused": False,
            "cancelled": False,
            "status": "running",
            "retry_limit": retry_limit,
            "retry_count": 0,
        }
        if resume_state:
            base_state.update(resume_state)
            base_state["paused"] = False
            base_state["status"] = "running"
            base_state["agent_config"] = agent_config
        return base_state

    def _planner(self, state: TripState) -> Dict[str, Any]:
        """
        Planner 节点：负责生成草案行程。

        策略：
        - 若 state 中已经有 draft，则直接复用（保证幂等性与恢复能力）；
        - 否则调用 LlmManager.generate_trip 基于 user_input/context 生成新的行程草案。
        """

        if state.get("draft"):
            return {"draft": state.get("draft")}
        user_input = state.get("user_input") or {}
        context = state.get("context") or []
        draft = self.llm_manager.generate_trip(user_input, context) or {}
        return {"draft": draft}

    def _call_tool(self, thread_id: str, node: str, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        统一工具调用封装，自动打点 tool_call/tool_result 事件。

        参数：
        - thread_id：会话指针
        - node：当前节点名
        - tool_name：工具名（如 weather.get_daily）
        - params：工具参数
        """

        event_bus.emit("tool_call", thread_id, node, {"tool": tool_name, "params": params})
        result = self.llm_manager.call_tool(tool_name, params)
        event_bus.emit("tool_result", thread_id, node, {"tool": tool_name, "result": result})
        return result

    def _checker(self, state: TripState) -> Dict[str, Any]:
        """
        Checker 节点：调用天气/POI/地理编码工具，补全行程约束信息。

        核心逻辑：
        - 若已有 constraints，则直接复用（支持恢复与多次调用）；
        - city：优先取 user_input.destination，其次 draft.destination；
        - weather：调用 weather.get_daily(city)，用于后续做“晴雨天分配”；
        - poi：调用 poi.search(query, city, top_k)，query 可在 Agent 配置中调整；
        - geocode：基于草案中第一天第一条行程的地址/景点做地理编码，作为地图起点；
        - 最终返回 constraints dict，并挂载到全局 state。
        """

        if state.get("constraints"):
            return {"constraints": state.get("constraints")}
        thread_id = state.get("thread_id") or "default"
        user_input = state.get("user_input") or {}
        draft = state.get("draft") or {}
        agent_config = state.get("agent_config") or {}
        city = user_input.get("destination") or draft.get("destination") or ""
        poi_top_k = int(agent_config.get("poi_top_k") or 5)
        poi_query = agent_config.get("poi_query") or "热门景点"
        weather = self._call_tool(thread_id, "checker", "weather.get_daily", {"city": city})
        poi = self._call_tool(thread_id, "checker", "poi.search", {"query": poi_query, "city": city, "top_k": poi_top_k})
        address = city or "市中心"
        attraction = None
        daily_plan = draft.get("daily_plan") or {}
        if isinstance(daily_plan, dict):
            first_day = next(iter(daily_plan.values()), [])
        elif isinstance(daily_plan, list):
            first_day = daily_plan
        else:
            first_day = []
        if first_day:
            first_item = first_day[0] or {}
            address = first_item.get("address") or address
            attraction = first_item.get("attraction")
        geocode = self._call_tool(
            thread_id,
            "checker",
            "geo.geocode",
            {"address": address, "city": city, "attraction": attraction},
        )
        constraints = {"weather": weather, "poi": poi, "geocode": geocode}
        return {"constraints": constraints}

    def _optimizer(self, state: TripState) -> Dict[str, Any]:
        """
        Optimizer 节点：根据预算与偏好，对草案行程做轻量修正。

        当前实现：
        - 若已有 optimized，则直接复用；
        - 若配置了 budget_cap 且草案预算超出，则将预算压到上限；
        - constraint_summary：把 weather/poi 的 key 列表摘要出来，方便上层使用；
        - preferences：把密度/室内优先等偏好打平到 optimized 中，便于后续渲染或调试。

        注意：这里没有对 daily_plan 做复杂重排，只做“可观测的最小优化”，便于 PoC。
        """

        if state.get("optimized"):
            return {"optimized": state.get("optimized")}
        draft = state.get("draft") or {}
        constraints = state.get("constraints") or {}
        agent_config = state.get("agent_config") or {}
        optimized = dict(draft)
        budget_cap = agent_config.get("budget_cap")
        if budget_cap is not None and isinstance(draft.get("budget"), (int, float)):
            if draft.get("budget") > budget_cap:
                optimized["budget"] = budget_cap
        optimized["constraint_summary"] = {
            "weather": list((constraints.get("weather") or {}).keys()),
            "poi": list((constraints.get("poi") or {}).keys()),
        }
        optimized["preferences"] = {
            "trip_density": agent_config.get("trip_density"),
            "prefer_indoor": agent_config.get("prefer_indoor"),
        }
        return {"optimized": optimized}

    def _map_rag(self, state: TripState) -> Dict[str, Any]:
        """
        Map/RAG 节点：渲染地图并拼接 RAG 证据摘要。

        处理流程：
        - trip_data：优先使用 optimized，其次回退到 draft；
        - 地图：调用 TripMap.render_map(trip_data)，并将渲染结果转为 HTML（供前端悬浮地图使用）；
        - RAG：若 enable_rag=True，则构造 destination 相关查询并走 AIRetrievalPipeline，
          将 evidence（Summary/Body + Budget）与 answer 写入 map_payload，供前端做证据可视化与调试。
        """

        draft = state.get("draft") or {}
        optimized = state.get("optimized") or {}
        agent_config = state.get("agent_config") or {}
        trip_data = optimized or draft
        map_payload: Dict[str, Any] = {}
        print(f"【Map/RAG】节点开始，启用RAG：{agent_config.get('enable_rag', True)}")
        if trip_data:
            map_obj = self.map_renderer.render_map(trip_data)
            map_payload["map_html"] = map_obj.get_root().render()
        if agent_config.get("enable_rag", True):
            destination = trip_data.get("destination") or state.get("user_input", {}).get("destination") or ""
            rag_query = agent_config.get("rag_query") or f"{destination} 行程 旅行 建议"
            print(f"【Map/RAG】开始检索，查询：{rag_query}")
            rag_result = self.rag_pipeline.run(rag_query)
            map_payload["rag_query"] = rag_query
            map_payload["rag_answer"] = rag_result.get("answer")
            map_payload["rag_evidence"] = rag_result.get("evidence", {})
            map_payload["evidence_summary"] = rag_result.get("evidence", {}).get("summary", {})
            map_payload["rag_processing_time"] = rag_result.get("processing_time")
            evidence = map_payload.get("rag_evidence") or {}
            summary_items = (evidence.get("summary") or {}).get("items") or []
            body_items = (evidence.get("body") or {}).get("items") or []
            print(f"【Map/RAG】检索完成，摘要/正文条目数：{len(summary_items)}/{len(body_items)}")
        else:
            print("【Map/RAG】已关闭RAG，跳过检索")
        return {"map_payload": map_payload}
