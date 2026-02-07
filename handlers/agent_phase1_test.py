import os
import sys

# 将项目根目录添加到 Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 引入计划模型
from src.agent.plan_models import Task, Plan
# 引入调度器
from src.agent.scheduler import Scheduler, RateLimitExceeded, BudgetExceeded
# 引入工具注册表
from src.llm.tool_protocol import ToolRegistry, ToolSchema
# 引入错误码
from src.observability import ErrorCodes


def _build_registry() -> ToolRegistry:
    # 构建工具注册表实例
    registry = ToolRegistry()

    # 定义简单的加法工具
    def add(a: int, b: int):
        # 返回求和结果
        return {"sum": a + b}

    # 构建工具协议
    schema = ToolSchema(
        name="math.add",
        description="add",
        params={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
        returns={"type": "object"},
        errors={
            ErrorCodes.TOOL_NOT_FOUND: "not_found",
            ErrorCodes.TOOL_EXECUTION_ERROR: "failed",
            ErrorCodes.TOOL_INVALID_PARAMS: "invalid",
        },
    )
    # 注册工具
    registry.register(schema, add)
    # 返回注册表
    return registry


def test_plan_validation() -> None:
    # 构造合法任务
    tasks = [
        Task(id="t1", type="tool_call", tool="weather.get_daily"),
        Task(id="t2", type="tool_call", tool="poi.search"),
        Task(id="t3", type="trip_generate", dependencies=["t1", "t2"], input_mapping={"w": "t1.weather"}),
    ]
    # 构造计划
    plan = Plan(tasks=tasks)
    # 校验计划合法性
    plan.validate_plan(tool_whitelist={"weather.get_daily", "poi.search"})

    # 构造非法依赖任务
    invalid_tasks = [
        Task(id="t1", type="tool_call", tool="weather.get_daily"),
        Task(id="t2", type="trip_generate", dependencies=["t9"]),
    ]
    # 构造非法计划
    invalid_plan = Plan(tasks=invalid_tasks)
    try:
        # 期望抛出依赖错误
        invalid_plan.validate_plan()
        raise AssertionError("expected_dependency_error")
    except ValueError:
        # 捕获错误即通过
        pass

    # 构造非法工具计划
    invalid_tool_plan = Plan(
        tasks=[Task(id="t1", type="tool_call", tool="unknown.tool")]
    )
    try:
        # 期望抛出白名单错误
        invalid_tool_plan.validate_plan(tool_whitelist={"weather.get_daily"})
        raise AssertionError("expected_tool_whitelist_error")
    except ValueError:
        # 捕获错误即通过
        pass


def test_scheduler_batches() -> None:
    # 构造 DAG 任务
    tasks = [
        Task(id="t1", type="tool_call"),
        Task(id="t2", type="tool_call"),
        Task(id="t3", type="trip_generate", dependencies=["t1", "t2"]),
        Task(id="t4", type="trip_summarize", dependencies=["t3"]),
    ]
    # 初始化调度器
    scheduler = Scheduler(tasks, max_concurrency=2)
    # 取首批任务
    batch1 = scheduler.next_batch()
    # 校验并发批次包含 t1/t2
    assert {task.id for task in batch1} == {"t1", "t2"}
    # 标记 t1/t2 完成
    for task in batch1:
        scheduler.mark_done(task.id)
    # 取第二批任务
    batch2 = scheduler.next_batch()
    # 校验顺序进入 t3
    assert [task.id for task in batch2] == ["t3"]
    # 标记 t3 完成
    scheduler.mark_done("t3")
    # 取第三批任务
    batch3 = scheduler.next_batch()
    # 校验进入 t4
    assert [task.id for task in batch3] == ["t4"]
    # 标记 t4 完成
    scheduler.mark_done("t4")
    # 任务耗尽返回空
    assert scheduler.next_batch() == []


def test_scheduler_limits() -> None:
    # 构造简单任务列表
    tasks = [Task(id="t1", type="tool_call"), Task(id="t2", type="tool_call")]
    # 模拟时间函数
    clock = {"t": 0.0}

    # 定义测试时钟
    def now() -> float:
        # 返回固定时间
        return clock["t"]

    # 速率限制测试
    scheduler = Scheduler(tasks, max_concurrency=2, rate_limit_per_min=1, now_fn=now)
    try:
        # 期望触发速率限制
        scheduler.next_batch()
        raise AssertionError("expected_rate_limit_error")
    except RateLimitExceeded:
        # 捕获异常即通过
        pass

    # 预算限制测试
    scheduler = Scheduler(tasks, max_concurrency=2, max_total_tasks=1, now_fn=now)
    try:
        # 期望触发预算限制
        scheduler.next_batch()
        raise AssertionError("expected_budget_error")
    except BudgetExceeded:
        # 捕获异常即通过
        pass


def test_tool_registry_context() -> None:
    # 初始化注册表
    registry = _build_registry()
    # 构造共享上下文
    shared_context = {"t1": {"x": 2}, "t2": {"y": 3}}
    # 通过上下文注入参数
    result = registry.call_with_context(
        "math.add",
        {},
        shared_context=shared_context,
        input_mapping={"a": "t1.x", "b": "t2.y"},
    )
    # 校验调用成功
    assert result.success is True
    # 校验结果值
    assert result.data.get("sum") == 5

    # 构造缺参调用
    bad_result = registry.call("math.add", {"a": 1})
    # 校验失败
    assert bad_result.success is False
    # 校验错误码
    assert bad_result.error.get("code") == ErrorCodes.TOOL_INVALID_PARAMS


def main() -> None:
    # 运行计划校验测试
    test_plan_validation()
    # 运行调度器批次测试
    test_scheduler_batches()
    # 运行调度器限制测试
    test_scheduler_limits()
    # 运行工具上下文测试
    test_tool_registry_context()
    # 输出测试完成信息
    print("agent_phase1_test passed")


if __name__ == "__main__":
    # 执行测试入口
    main()
