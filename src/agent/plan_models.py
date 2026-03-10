from typing import Any, Dict, List, Optional, Set
import time
from pydantic import BaseModel, Field, field_validator


class Task(BaseModel):
    # 任务唯一标识
    id: str
    # 任务类型（tool_call / trip_generate 等）
    type: str
    # 当任务为 tool_call 时使用的工具名
    tool: Optional[str] = None
    # 任务执行参数
    params: Dict[str, Any] = Field(default_factory=dict)
    # 任务依赖列表（上游 task id）
    dependencies: List[str] = Field(default_factory=list)
    # 输入映射（target_key -> "task_id.output_key"）
    input_mapping: Dict[str, str] = Field(default_factory=dict)
    # 输出写入 shared_context 的 key
    output_key: Optional[str] = None
    # 任务描述，用于可视化或审计
    description: Optional[str] = None
    # 条件表达式（可选）
    condition: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        # 校验 task id 必须存在
        if not value or not str(value).strip():
            # 抛出约束错误
            raise ValueError("task_id_required")
        # 返回合法 id
        return value


class SharedContext(BaseModel):
    # 共享上下文数据（task_id -> {output_key: value}）
    data: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    # 上下文写入版本记录
    revisions: List[Dict[str, Any]] = Field(default_factory=list)

    def write(self, task_id: str, key: str, value: Any) -> None:
        # 获取或创建 task_id 的命名空间
        bucket = self.data.setdefault(task_id, {})
        # 写入 key/value
        bucket[key] = value
        # 记录写入版本
        self.revisions.append(
            {"task_id": task_id, "key": key, "ts": time.time()}
        )

    def read(self, task_id: str, key: str, default: Any = None) -> Any:
        # 从指定 task_id 读取 key
        return (self.data.get(task_id) or {}).get(key, default)

    def resolve(self, reference: str, default: Any = None) -> Any:
        # 处理非法引用
        if not reference or "." not in reference:
            return default
        # 解析 task_id 与 key
        task_id, key = reference.split(".", 1)
        # 读取并返回值
        return self.read(task_id, key, default=default)


class Plan(BaseModel):
    # 任务列表
    tasks: List[Task] = Field(default_factory=list)

    def task_map(self) -> Dict[str, Task]:
        # 构建 task_id -> Task 的映射
        return {task.id: task for task in self.tasks}

    def validate_dependencies(self) -> None:
        # 收集全部 task_id
        task_ids = {task.id for task in self.tasks}
        # 遍历每个任务的依赖
        for task in self.tasks:
            for dep in task.dependencies:
                # 发现未知依赖时抛错
                if dep not in task_ids:
                    raise ValueError(f"unknown_dependency:{task.id}:{dep}")

    def validate_no_cycles(self) -> None:
        # 收集任务集合
        task_ids = {task.id for task in self.tasks}
        # 构建依赖图
        deps_map = {task.id: set(task.dependencies) for task in self.tasks}
        # 记录已访问节点
        visited: Set[str] = set()
        # 记录递归路径上的节点
        visiting: Set[str] = set()

        def dfs(node: str) -> None:
            # 命中递归栈则存在环
            if node in visiting:
                raise ValueError(f"dependency_cycle:{node}")
            # 已访问直接返回
            if node in visited:
                return
            # 标记进入栈
            visiting.add(node)
            # 深度优先遍历依赖
            for dep in deps_map.get(node, set()):
                if dep in task_ids:
                    dfs(dep)
            # 退出栈并标记完成
            visiting.remove(node)
            visited.add(node)

        # 对每个节点执行 DFS
        for node in task_ids:
            dfs(node)

    def validate_input_mapping(self) -> None:
        # 收集任务集合
        task_ids = {task.id for task in self.tasks}
        # 遍历每个 input_mapping 引用
        for task in self.tasks:
            for _, ref in (task.input_mapping or {}).items():
                # 校验引用格式
                if not isinstance(ref, str) or "." not in ref:
                    raise ValueError(f"invalid_input_mapping:{task.id}:{ref}")
                # 校验引用来源存在
                ref_task, _ = ref.split(".", 1)
                if ref_task not in task_ids:
                    raise ValueError(f"unknown_input_mapping_source:{task.id}:{ref_task}")

    def validate_tool_whitelist(self, whitelist: Optional[Set[str]]) -> None:
        # 无白名单则跳过校验
        if not whitelist:
            return
        # 校验工具任务是否在白名单内
        for task in self.tasks:
            if task.type == "tool_call":
                tool_name = task.tool or ""
                if tool_name not in whitelist:
                    raise ValueError(f"tool_not_allowed:{task.id}:{tool_name}")

    def validate_plan(self, tool_whitelist: Optional[Set[str]] = None) -> None:
        # 校验依赖存在性
        self.validate_dependencies()
        # 校验依赖无环
        self.validate_no_cycles()
        # 校验输入映射合法性
        self.validate_input_mapping()
        # 校验工具白名单
        self.validate_tool_whitelist(tool_whitelist)


class TripState(BaseModel):
    # 用户意图
    user_intent: str = ""
    # 结构化用户输入
    user_input: Dict[str, Any] = Field(default_factory=dict)
    thread_id: str = ""
    # 当前计划任务
    plan: List[Task] = Field(default_factory=list)
    # 计划历史，用于重规划回溯
    plan_history: List[List[Task]] = Field(default_factory=list)
    # 执行队列
    execution_queue: List[str] = Field(default_factory=list)
    # 已完成任务
    completed_tasks: Set[str] = Field(default_factory=set)
    # 失败任务
    failed_tasks: Set[str] = Field(default_factory=set)
    # 任务摘要
    task_summaries: Dict[str, str] = Field(default_factory=dict)
    # 共享上下文
    shared_context: SharedContext = Field(default_factory=SharedContext)
    # 原始任务结果
    task_results: Dict[str, Any] = Field(default_factory=dict)
    # 上下文版本记录
    context_revisions: List[Dict[str, Any]] = Field(default_factory=list)
    # 聚合输出
    final_payload: Dict[str, Any] = Field(default_factory=dict)
    # 当前状态
    status: str = "pending"
    # 错误信息
    error: Optional[str] = None
    # 停止原因
    stop_reason: Optional[str] = None
    # 任务重试次数
    retry_counts: Dict[str, int] = Field(default_factory=dict)
    # 已触发重规划次数
    replan_depth: int = 0
    # 最大重规划次数
    max_replan_depth: int = 0
