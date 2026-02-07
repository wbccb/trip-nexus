from typing import Callable, Deque, Dict, List, Optional, Set
from collections import deque
import time

from src.agent.plan_models import Task


class SchedulerError(Exception):
    # 调度器基础异常
    pass


class RateLimitExceeded(SchedulerError):
    # 速率限制异常
    pass


class BudgetExceeded(SchedulerError):
    # 预算超限异常
    pass


class Scheduler:
    def __init__(
        self,
        tasks: List[Task],
        max_concurrency: int = 4,
        rate_limit_per_min: Optional[int] = None,
        max_total_tasks: Optional[int] = None,
        now_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        # 构建 task_id -> Task 映射
        self._tasks: Dict[str, Task] = {task.id: task for task in tasks}
        # 构建依赖集合
        self._dependencies: Dict[str, Set[str]] = {
            task.id: set(task.dependencies) for task in tasks
        }
        # 构建反向依赖集合
        self._dependents: Dict[str, Set[str]] = {task.id: set() for task in tasks}
        # 填充反向依赖关系
        for task_id, deps in self._dependencies.items():
            for dep in deps:
                if dep in self._dependents:
                    self._dependents[dep].add(task_id)
        # 初始化就绪队列
        self._ready: Deque[str] = deque(
            [task_id for task_id, deps in self._dependencies.items() if not deps]
        )
        # 记录已完成任务
        self._completed: Set[str] = set()
        # 记录已下发任务
        self._issued: Set[str] = set()
        # 记录下发时间戳
        self._issued_timestamps: Deque[float] = deque()
        # 设置并发上限
        self._max_concurrency = max(1, int(max_concurrency))
        # 设置速率限制
        self._rate_limit_per_min = rate_limit_per_min
        # 设置总任务预算
        self._max_total_tasks = max_total_tasks
        # 记录已下发数量
        self._issued_count = 0
        # 注入时间函数便于测试
        self._now = now_fn or time.time

    def _prune_rate_window(self) -> None:
        # 无速率限制则跳过
        if not self._rate_limit_per_min:
            return
        # 计算窗口起点
        cutoff = self._now() - 60
        # 移除窗口外的时间戳
        while self._issued_timestamps and self._issued_timestamps[0] < cutoff:
            self._issued_timestamps.popleft()

    def _ensure_rate_limit(self) -> None:
        # 无速率限制则跳过
        if not self._rate_limit_per_min:
            return
        # 清理过期时间戳
        self._prune_rate_window()
        # 触发速率限制异常
        if len(self._issued_timestamps) >= int(self._rate_limit_per_min):
            raise RateLimitExceeded("rate_limit_exceeded")

    def _ensure_budget(self) -> None:
        # 未配置预算则跳过
        if self._max_total_tasks is None:
            return
        # 触发预算超限异常
        if self._issued_count >= int(self._max_total_tasks):
            raise BudgetExceeded("budget_exceeded")

    def next_batch(self) -> List[Task]:
        # 无就绪任务直接返回空列表
        if not self._ready:
            return []
        # 初始化批次容器
        batch: List[Task] = []
        # 直到达到并发上限或队列为空
        while self._ready and len(batch) < self._max_concurrency:
            # 预算校验
            self._ensure_budget()
            # 速率限制校验
            self._ensure_rate_limit()
            # 取出一个就绪任务
            task_id = self._ready.popleft()
            # 过滤已下发或已完成任务
            if task_id in self._issued or task_id in self._completed:
                continue
            # 获取任务对象
            task = self._tasks.get(task_id)
            # 任务不存在则跳过
            if not task:
                continue
            # 标记为已下发
            self._issued.add(task_id)
            # 增加下发计数
            self._issued_count += 1
            # 记录下发时间戳
            self._issued_timestamps.append(self._now())
            # 将任务加入批次
            batch.append(task)
        # 返回批次任务
        return batch

    def mark_done(self, task_id: str) -> None:
        # 已完成则直接返回
        if task_id in self._completed:
            return
        # 标记完成
        self._completed.add(task_id)
        # 遍历依赖该任务的节点
        for dependent in self._dependents.get(task_id, set()):
            # 获取依赖集合
            deps = self._dependencies.get(dependent)
            # 依赖集合不存在则跳过
            if deps is None:
                continue
            # 移除当前依赖
            deps.discard(task_id)
            # 若依赖为空则进入就绪队列
            if not deps:
                self._ready.append(dependent)

    def iterate_ready_batches(self):
        # 持续产出可执行批次
        while True:
            # 获取下一批次
            batch = self.next_batch()
            # 没有任务则结束
            if not batch:
                break
            # 产出批次
            yield batch

    def pending_count(self) -> int:
        # 计算未完成任务数量
        return len(self._tasks) - len(self._completed)
