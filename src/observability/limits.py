from typing import Dict, Optional
import threading
import time
from urllib.parse import urlparse


class CircuitBreaker:
    # 初始化熔断器配置
    def __init__(self, failure_threshold: int = 3, cooldown_seconds: int = 30) -> None:
        # 失败阈值，超过后进入熔断状态
        self._failure_threshold = max(1, int(failure_threshold))
        # 熔断冷却时间
        self._cooldown_seconds = max(1, int(cooldown_seconds))
        # 存储状态：key -> {"fail_count": int, "open_until": float}
        self._state: Dict[str, Dict[str, float]] = {}
        # 线程锁，保证并发安全
        self._lock = threading.Lock()

    # 判断是否允许请求通过
    def allow(self, key: str) -> bool:
        # 记录当前时间
        now = time.time()
        # 加锁读取状态
        with self._lock:
            # 获取当前 key 的状态
            state = self._state.get(key)
            # 无状态则允许通过
            if not state:
                return True
            # 若未达到熔断时间则拒绝
            if state.get("open_until", 0) > now:
                return False
            # 熔断窗口已过，允许通过
            return True

    # 记录一次成功请求，重置失败计数
    def record_success(self, key: str) -> None:
        # 加锁更新状态
        with self._lock:
            # 重置失败计数与熔断窗口
            self._state[key] = {"fail_count": 0, "open_until": 0.0}

    # 记录一次失败请求，可能触发熔断
    def record_failure(self, key: str) -> None:
        # 记录当前时间
        now = time.time()
        # 加锁更新状态
        with self._lock:
            # 获取当前状态
            state = self._state.get(key) or {"fail_count": 0, "open_until": 0.0}
            # 增加失败计数
            state["fail_count"] = float(state.get("fail_count", 0)) + 1
            # 判断是否触发熔断
            if state["fail_count"] >= self._failure_threshold:
                # 设置熔断截止时间
                state["open_until"] = now + self._cooldown_seconds
            # 回写状态
            self._state[key] = state


class DomainConcurrencyLimiter:
    # 初始化并发限制器
    def __init__(self, max_global: int = 10, max_per_domain: int = 2) -> None:
        # 全局并发上限
        self._max_global = max(1, int(max_global))
        # 单域名并发上限
        self._max_per_domain = max(1, int(max_per_domain))
        # 全局信号量
        self._global_semaphore = threading.Semaphore(self._max_global)
        # 域名级信号量映射
        self._domain_semaphores: Dict[str, threading.Semaphore] = {}
        # 线程锁，保证域名信号量创建安全
        self._lock = threading.Lock()

    # 解析域名信息
    def _get_domain(self, url: str) -> str:
        # 解析 URL 获取 hostname
        parsed = urlparse(url or "")
        # 使用 hostname 或默认占位符
        return parsed.hostname or "unknown"

    # 获取或创建域名信号量
    def _get_domain_semaphore(self, domain: str) -> threading.Semaphore:
        # 加锁防止并发创建
        with self._lock:
            # 若已存在则直接返回
            if domain in self._domain_semaphores:
                return self._domain_semaphores[domain]
            # 创建新的域名级信号量
            semaphore = threading.Semaphore(self._max_per_domain)
            # 缓存并返回
            self._domain_semaphores[domain] = semaphore
            return semaphore

    # 获取并发许可
    def acquire(self, url: str, timeout: Optional[float] = None) -> bool:
        # 获取域名
        domain = self._get_domain(url)
        # 获取域名级信号量
        domain_semaphore = self._get_domain_semaphore(domain)
        # 先获取全局信号量，失败则直接返回
        if not self._global_semaphore.acquire(timeout=timeout):
            return False
        # 再获取域名级信号量
        if domain_semaphore.acquire(timeout=timeout):
            return True
        # 域名获取失败需释放全局信号量
        self._global_semaphore.release()
        return False

    # 释放并发许可
    def release(self, url: str) -> None:
        # 获取域名
        domain = self._get_domain(url)
        # 获取域名级信号量
        domain_semaphore = self._get_domain_semaphore(domain)
        # 释放域名级信号量
        domain_semaphore.release()
        # 释放全局信号量
        self._global_semaphore.release()


class BudgetManager:
    # 初始化预算管理器
    def __init__(
        self,
        token_budget: Optional[int] = None,
        cost_budget: Optional[float] = None,
        time_budget_seconds: Optional[float] = None,
    ) -> None:
        # 初始化 token 预算上限
        self._token_budget = None if token_budget is None else max(0, int(token_budget))
        # 初始化成本预算上限
        self._cost_budget = None if cost_budget is None else max(0.0, float(cost_budget))
        # 初始化时间预算字段
        self._time_budget_seconds = None
        # 写入时间预算上限
        if time_budget_seconds is not None:
            self._time_budget_seconds = max(0.0, float(time_budget_seconds))

    # 检查 token 预算
    def check_tokens(self, token_estimate: Optional[int]) -> bool:
        # 未配置预算直接通过
        if self._token_budget is None:
            return True
        # 未提供估算值直接通过
        if token_estimate is None:
            return True
        # 判断估算值是否在预算内
        return int(token_estimate) <= self._token_budget

    # 检查成本预算
    def check_cost(self, cost_estimate: Optional[float]) -> bool:
        # 未配置预算直接通过
        if self._cost_budget is None:
            return True
        # 未提供估算值直接通过
        if cost_estimate is None:
            return True
        # 判断估算值是否在预算内
        return float(cost_estimate) <= self._cost_budget

    # 检查时间预算
    def check_time(self, elapsed_seconds: float) -> bool:
        # 未配置预算直接通过
        if self._time_budget_seconds is None:
            return True
        # 判断耗时是否在预算内
        return float(elapsed_seconds) <= self._time_budget_seconds


class ConstraintChecker:
    # 初始化约束检查器
    def __init__(self, budget_manager: BudgetManager) -> None:
        # 记录预算管理器
        self._budget = budget_manager

    # 统一校验预算约束
    def ensure_within_budget(
        self,
        elapsed_seconds: float,
        token_estimate: Optional[int] = None,
        cost_estimate: Optional[float] = None,
    ) -> bool:
        # 检查时间预算
        if not self._budget.check_time(elapsed_seconds):
            return False
        # 检查 token 预算
        if not self._budget.check_tokens(token_estimate):
            return False
        # 检查成本预算
        if not self._budget.check_cost(cost_estimate):
            return False
        # 全部满足预算
        return True
