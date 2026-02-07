import os
import sys
import asyncio

# 将项目根目录添加到 Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 引入执行器与计划
from src.agent.agent_loop import AgentExecutor, PlannerAgent
# 引入计划模型
from src.agent.plan_models import Plan, Task
# 引入工具注册表
from src.llm.tool_protocol import ToolRegistry, ToolSchema
# 引入错误码
from src.observability import ErrorCodes


class FakeLLM:
    def invoke(self, prompt: str):
        # 返回一个简单的计划 JSON
        return {"tasks": []}


class FakeMapRoot:
    def render(self) -> str:
        # 返回固定 HTML
        return "<div>map</div>"


class FakeMapObj:
    def get_root(self) -> FakeMapRoot:
        # 返回根节点
        return FakeMapRoot()


class FakeMapRenderer:
    def render_map(self, trip_data):
        # 返回伪造地图对象
        return FakeMapObj()


class FakeLlmManager:
    def __init__(self, empty_weather: bool = False) -> None:
        # 初始化工具注册表
        self.tool_registry = ToolRegistry()
        # 注册天气工具
        self.tool_registry.register(
            ToolSchema(
                name="weather.get_daily",
                description="weather",
                params={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
                returns={"type": "object"},
                errors={
                    ErrorCodes.TOOL_NOT_FOUND: "not_found",
                    ErrorCodes.TOOL_EXECUTION_ERROR: "failed",
                    ErrorCodes.TOOL_INVALID_PARAMS: "invalid",
                },
            ),
            lambda city, days=None: {"city": city, "daily": [] if empty_weather else [{"date": "2025-01-01"}]},
        )
        # 注册 POI 工具
        self.tool_registry.register(
            ToolSchema(
                name="poi.search",
                description="poi",
                params={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                returns={"type": "object"},
                errors={
                    ErrorCodes.TOOL_NOT_FOUND: "not_found",
                    ErrorCodes.TOOL_EXECUTION_ERROR: "failed",
                    ErrorCodes.TOOL_INVALID_PARAMS: "invalid",
                },
            ),
            lambda query, city=None, top_k=5: {"query": query, "results": [{"name": "spot"}]},
        )
        # 注册地理编码工具
        self.tool_registry.register(
            ToolSchema(
                name="geo.geocode",
                description="geo",
                params={"type": "object", "properties": {"address": {"type": "string"}}, "required": ["address"]},
                returns={"type": "object"},
                errors={
                    ErrorCodes.TOOL_NOT_FOUND: "not_found",
                    ErrorCodes.TOOL_EXECUTION_ERROR: "failed",
                    ErrorCodes.TOOL_INVALID_PARAMS: "invalid",
                },
            ),
            lambda address, city=None: {"address": address, "latitude": 1.0, "longitude": 2.0},
        )

    def list_tools(self):
        # 返回工具 Schema
        return self.tool_registry.list_schemas()

    def get_analysis_llm(self):
        # 返回伪造 LLM
        return FakeLLM()

    def generate_trip(self, user_input, context):
        # 返回固定行程
        return {"destination": user_input.get("destination"), "days": user_input.get("days"), "daily_plan": {}}

    def extract_json_from_string(self, text: str) -> str:
        # 直接返回输入
        return text


def test_planner_sop() -> None:
    # 初始化 Planner
    llm_manager = FakeLlmManager()
    planner = PlannerAgent(llm_manager)
    # 构造输入
    user_input = {"destination": "上海", "days": 2, "budget": 1000}
    # 生成计划
    plan = planner.plan(
        user_intent="generate_trip",
        user_input=user_input,
        agent_config={"poi_query": "热门景点", "poi_top_k": 3, "weather_days": 2},
        tool_whitelist=[schema.get("name") for schema in llm_manager.list_tools()],
        force_sop=True,
    )
    # 校验计划任务数量
    assert isinstance(plan, Plan)
    assert len(plan.tasks) >= 4


def test_executor_loop_success() -> None:
    # 初始化执行器
    llm_manager = FakeLlmManager()
    executor = AgentExecutor(llm_manager, map_renderer=FakeMapRenderer())
    # 执行主循环
    state = asyncio.run(
        executor.run_agent_loop(
            user_input={"destination": "上海", "days": 2, "budget": 1000},
            thread_id="t-1",
            agent_config={"poi_query": "热门景点", "poi_top_k": 3, "weather_days": 2},
            user_intent="generate_trip",
            context=None,
            plan_override=None,
            retry_limit=1,
            max_replan_depth=1,
        )
    )
    # 校验执行完成
    assert state.status == "done"
    assert "t1" in state.completed_tasks
    assert state.final_payload.get("map_payload") is not None


def test_reflector_replan() -> None:
    # 初始化执行器（天气返回空）
    llm_manager = FakeLlmManager(empty_weather=True)
    executor = AgentExecutor(llm_manager, map_renderer=FakeMapRenderer())
    # 执行主循环
    state = asyncio.run(
        executor.run_agent_loop(
            user_input={"destination": "上海", "days": 2, "budget": 1000},
            thread_id="t-2",
            agent_config={"poi_query": "热门景点", "poi_top_k": 3, "weather_days": 2},
            user_intent="generate_trip",
            context=None,
            plan_override=None,
            retry_limit=0,
            max_replan_depth=1,
        )
    )
    # 校验触发重规划
    assert state.replan_depth >= 1


def main() -> None:
    # 运行 SOP 计划测试
    test_planner_sop()
    # 运行执行器成功测试
    test_executor_loop_success()
    # 运行反思器重规划测试
    test_reflector_replan()
    # 输出测试完成信息
    print("agent_phase2_phase3_test passed")


if __name__ == "__main__":
    # 执行测试入口
    main()
