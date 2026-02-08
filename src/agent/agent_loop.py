from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from src.agent.plan_models import Plan, Task, TripState, SharedContext
from src.agent.scheduler import Scheduler, RateLimitExceeded, BudgetExceeded
from src.agent.event_bus import event_bus, snapshot_store
from src.llm.llm_manager import LlmManager
from src.llm.tool_protocol import ToolCallResult
from src.map.map_renderer import TripMap
from src.observability import ErrorCodes, build_error_payload, normalize_exception


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
                params={"query": poi_query, "city": destination, "top_k": poi_top_k},
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
        print(f"\n[{time.strftime('%H:%M:%S')}] [PlannerAgent] 生成 SOP 计划")
        # 判断是否需要 SOP
        if force_sop:
            sop_plan.validate_plan(tool_whitelist=set(tool_whitelist))
            print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] 强制使用固定流程的 SOP 计划\n")
            return sop_plan
        # 读取工具清单
        tool_registry = self._llm_manager.list_tools()
        # 构建 SOP 模板列表
        sop_templates = [{"name": "default_trip", "tasks": [task.model_dump() for task in sop_plan.tasks]}]
        # 构建 prompt
        prompt = self._build_plan_prompt(user_intent, user_input, tool_registry, sop_templates, error_context=error_context)
        try:
            # [LOG] 调用大模型前打印信息
            print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] 即将调用大模型......进行任务规划")
            print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] 用户意图: {user_intent}")
            print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] 用户输入: {json.dumps(user_input, ensure_ascii=False)}")
            
            # 调用分析模型
            # 这里使用 analysis_llm 进行意图理解和计划生成
            raw_response = self._llm_manager.get_analysis_llm().invoke(prompt)
            
            # 读取响应文本
            response_text = raw_response.content if hasattr(raw_response, "content") else raw_response
            
            # [LOG] 调用大模型后打印信息
            print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] 大模型-任务规划完成")
            # print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] 原始响应长度: {len(str(response_text))}")
            # print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] 原始响应内容: {response_text}") # 内容可能较长，按需开启

            # 解析计划
            plan = self._parse_plan_response(str(response_text))
            # 校验计划合法性
            plan.validate_plan(tool_whitelist=set(tool_whitelist))

            print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] 大模型-任务规划完成并完成校验，返回计划\n")
            return plan
        except Exception as e:
            # [LOG] 大模型调用或解析失败
            print(f"[{time.strftime('%H:%M:%S')}] [PlannerAgent] 计划生成失败: {e}，回退到 SOP 默认计划")
            # 失败时回退 SOP
            sop_plan.validate_plan(tool_whitelist=set(tool_whitelist))
            return sop_plan


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


class AgentExecutor:
    def __init__(self, llm_manager: LlmManager, map_renderer: Optional[TripMap] = None) -> None:
        # 复用 LLM 管理器
        self._llm_manager = llm_manager
        # 初始化地图渲染器
        self._map_renderer = map_renderer or TripMap()
        # 初始化 Planner
        self._planner = PlannerAgent(llm_manager)

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
            return {"query": data.get("query"), "results": data.get("results")}
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

    def _execute_task(
        self,
        task: Task,
        state: TripState,
        agent_config: Dict[str, Any],
    ) -> Tuple[Task, Dict[str, Any]]:
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
            # 返回结果
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
            draft = self._llm_manager.generate_trip(state.user_input, context_payload) or {}
            
            # [LOG] 调用大模型生成行程后
            trip_days = draft.get("days") if isinstance(draft, dict) else "Unknown"
            print(f"[{time.strftime('%H:%M:%S')}] [AgentExecutor] 行程生成完成")
            print(f"[{time.strftime('%H:%M:%S')}] [AgentExecutor] 生成行程天数: {trip_days}")

            # 写入共享上下文
            output_key = task.output_key or "draft_trip"
            state.shared_context.write(task.id, output_key, draft)
            # 返回统一结果
            return task, {"success": True, "data": draft}
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
            # 返回结果
            return task, {"success": True, "data": map_payload}
        # 行程摘要任务
        if task.type == "trip_summarize":
            # 读取行程数据
            trip_data = state.shared_context.resolve(task.input_mapping.get("trip", ""), default={})
            # 构造简要摘要
            summary = f"{trip_data.get('destination', '')} 行程，共 {trip_data.get('days', '')} 天"
            # 写入共享上下文
            output_key = task.output_key or "summary"
            state.shared_context.write(task.id, output_key, {"summary": summary})
            # 返回结果
            return task, {"success": True, "data": {"summary": summary}}
        # 默认未知任务类型
        return task, {"success": False, "error": {"code": "UNKNOWN_TASK_TYPE", "message": task.type}}

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
    ) -> TripState:
        # 初始化状态
        state = TripState(
            user_intent=user_intent or "",
            user_input=user_input or {},
            plan=[],
            plan_history=[],
            execution_queue=[],
            completed_tasks=set(),
            failed_tasks=set(),
            task_summaries={},
            shared_context=SharedContext(),
            task_results={},
            context_revisions=[],
            final_payload={},
            status="running",
            error=None,
            stop_reason=None,
            retry_counts={},
            replan_depth=0,
            max_replan_depth=max_replan_depth,
        )
        # 初始化反思器
        reflector = Reflector(max_replan_depth=max_replan_depth)
        # 读取工具白名单
        tool_whitelist = self._tool_whitelist()
        # 生成计划
        if plan_override:
            plan = plan_override
            plan.validate_plan(tool_whitelist=set(tool_whitelist))
        else:
            plan = self._planner.plan(user_intent, user_input, agent_config, tool_whitelist)
        # 写入计划
        state.plan = plan.tasks
        # 初始化调度器
        scheduler = Scheduler(
            plan.tasks,
            max_concurrency=int(agent_config.get("max_concurrency") or 4),
            rate_limit_per_min=agent_config.get("rate_limit_per_min"),
            max_total_tasks=agent_config.get("max_total_tasks"),
        )
        print(f"\n[{time.strftime('%H:%M:%S')}] [AgentExecutor] 初始化调度器")
        # 主循环
        while True:
            # 获取就绪批次
            batch = scheduler.next_batch()
            # 无就绪任务则结束
            if not batch:
                break
            # 记录执行队列
            state.execution_queue = [task.id for task in batch]
            # 发出批次开始事件
            event_bus.emit("batch_start", thread_id, "executor", {"tasks": state.execution_queue})
            # 执行批次任务
            try:
                print(f"\n[{time.strftime('%H:%M:%S')}] [AgentExecutor] 执行批次任务")
                results = await self._execute_batch(batch, state, agent_config)
            except (RateLimitExceeded, BudgetExceeded) as e:
                # 记录预算/速率错误并终止
                state.status = "failed"
                state.error = str(e)
                state.stop_reason = "budget_or_rate_limit"
                event_bus.emit("error", thread_id, "executor", {"error": state.error})
                break
            # 处理任务结果
            replan_triggered = False
            error_context = {}
            for task, result in results:
                # 缓存原始结果
                state.task_results[task.id] = result
                # 判定成功
                success = bool(result.get("success"))
                # 记录摘要
                state.task_summaries[task.id] = self._summarize_task(task, success)
                # 更新完成/失败集合
                if success:
                    state.completed_tasks.add(task.id)
                    scheduler.mark_done(task.id)
                else:
                    state.failed_tasks.add(task.id)
                    # 处理重试
                    retry_count = state.retry_counts.get(task.id, 0)
                    if retry_count < retry_limit:
                        state.retry_counts[task.id] = retry_count + 1
                        # 直接重试一次
                        retry_task, retry_result = self._execute_task(task, state, agent_config)
                        state.task_results[retry_task.id] = retry_result
                        success = bool(retry_result.get("success"))
                        state.task_summaries[retry_task.id] = self._summarize_task(retry_task, success)
                        if success:
                            state.completed_tasks.add(retry_task.id)
                            scheduler.mark_done(retry_task.id)
                        else:
                            state.failed_tasks.add(retry_task.id)
                # 反思器判断是否触发重规划
                if reflector.should_replan(task, result) and reflector.can_replan(state):
                    replan_triggered = True
                    error_context = {"task_id": task.id, "result": result}
                    print(f"\n[{time.strftime('%H:%M:%S')}] [AgentExecutor] 反思器判断触发重规划，任务 ID: {task.id}")
                else:
                    print(f"[{time.strftime('%H:%M:%S')}] [AgentExecutor] 反思器判断不触发重规划")
            # 落快照
            snapshot_store.add(
                thread_id,
                {
                    "step": "executor_batch",
                    "payload": {"completed": list(state.completed_tasks), "failed": list(state.failed_tasks)},
                    "state": state.model_dump(),
                    "duration_ms": 0,
                },
            )
            # 触发重规划
            if replan_triggered:
                print(f"[{time.strftime('%H:%M:%S')}] [AgentExecutor] 开始重规划")
                state.plan_history.append(list(state.plan))
                state.replan_depth = int(state.replan_depth) + 1
                new_plan = self._planner.plan(
                    user_intent,
                    user_input,
                    agent_config,
                    tool_whitelist,
                    force_sop=False,
                    error_context=error_context,
                )
                print(f"[{time.strftime('%H:%M:%S')}] [AgentExecutor] 重规划完成，新计划任务数: {new_plan.tasks}，重新初始化scheduler，重新回到循环中\n")
                state.plan = new_plan.tasks
                scheduler = Scheduler(new_plan.tasks)
                event_bus.emit("replan", thread_id, "executor", {"depth": state.replan_depth})
                continue
            # 失败任务且无法重规划则终止
            if state.failed_tasks:
                state.status = "failed"
                state.error = "task_failed"
                state.stop_reason = "task_failed"
                break
        # 生成最终聚合输出
        state.final_payload = {
            "draft_trip": state.shared_context.resolve("t4.draft_trip", default={}),
            "map_payload": state.shared_context.resolve("t5.map_payload", default={}),
            "summary": state.shared_context.resolve("t6.summary", default={}),
        }
        # 设置完成状态
        if state.status == "running":
            state.status = "done"
        # 发出结束事件
        event_bus.emit("loop_end", thread_id, "executor", {"status": state.status})
        # 返回最终状态
        return state


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
) -> TripState:
    # 初始化执行器
    executor = AgentExecutor(llm_manager)
    # 构建协程对象
    coroutine = executor.run_agent_loop(
        user_input=user_input,
        thread_id=thread_id,
        agent_config=agent_config,
        user_intent=user_intent,
        context=context,
        plan_override=plan_override,
        retry_limit=retry_limit,
        max_replan_depth=max_replan_depth,
    )
    # 尝试获取当前事件循环
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    # 无运行中的事件循环直接运行
    if not loop or not loop.is_running():
        return asyncio.run(coroutine)
    # 存在运行中的事件循环则创建临时事件循环
    temp_loop = asyncio.new_event_loop()
    try:
        return temp_loop.run_until_complete(coroutine)
    finally:
        temp_loop.close()
