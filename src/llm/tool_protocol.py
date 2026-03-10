from typing import Dict, Any, Callable, List, Optional
import time  # 用于统计工具调用耗时
from pydantic import BaseModel
from src.config import Config
from src.observability import TTLCache, build_tool_cache_key, normalize_tool_params, ErrorCodes, build_error_payload, MetricsRecorder, normalize_exception, get_global_recorder, CircuitBreaker


class ToolSchema(BaseModel):
    """
    统一工具协议 Schema 定义。

    设计目标：
    1) 用统一的 JSON 结构描述工具能力，便于本地工具与 MCP 工具共享。
    2) 在 LLM 侧可以直接读取 Schema，形成稳定的调用约束。
    3) 明确错误枚举，保证调用链路在失败时可预期。
    """

    # 工具名
    name: str
    # 工具描述
    description: str
    # 参数协议
    params: Dict[str, Any]
    # 返回结构
    returns: Dict[str, Any]
    # 错误字典
    errors: Dict[str, str]


class ToolCallResult(BaseModel):
    """
    统一工具调用结果结构。

    success: 是否调用成功
    data: 成功时返回的结构化结果
    error: 失败时返回的错误信息（包含 code 与 message）
    """

    # 工具名
    tool_name: str
    # 成功标记
    success: bool
    # 成功数据
    data: Optional[Any] = None
    # 失败错误
    error: Optional[Dict[str, Any]] = None


class ToolRegistry:
    """
    工具注册表。

    关键点：
    - 使用 name 作为全局唯一标识。
    - 将 schema 与 handler 绑定，统一走 call 入口。
    - kind 标识工具类型（本地 / MCP），为后续路由做准备。
    """

    def __init__(self):
        # 初始化工具注册表存储
        self._tools: Dict[str, Dict[str, Any]] = {}
        # 初始化配置
        self._config = Config()
        # 初始化工具结果缓存
        self._cache = TTLCache(
            ttl_seconds=self._config.TOOL_CACHE_TTL_SECONDS,
            max_size=self._config.TOOL_CACHE_MAX_SIZE,
        )
        # 初始化指标记录器
        self._metrics = get_global_recorder()
        # 初始化工具级熔断器
        self._circuit_breaker = CircuitBreaker(
            # 读取熔断失败阈值
            failure_threshold=self._config.TOOL_CIRCUIT_FAILURE_THRESHOLD,
            # 读取熔断冷却时间
            cooldown_seconds=self._config.TOOL_CIRCUIT_COOLDOWN_SECONDS,
        )
        # 读取单次调用时间预算
        self._tool_time_budget_seconds = self._config.TOOL_CALL_MAX_DURATION_SECONDS
        # 敏感字段列表
        self._sensitive_keys = {
            "api_key",
            "token",
            "secret",
            "password",
            "access_key",
            "access_token",
        }

    def register(self, schema: ToolSchema, handler: Callable[..., Any], kind: str = "local") -> None:
        """
        注册工具到注册表。

        schema: 工具协议描述
        handler: 实际执行的函数
        kind: 工具类型标识，默认 local
        """
        # 写入注册表
        self._tools[schema.name] = {"schema": schema, "handler": handler, "kind": kind}

    def list_schemas(self) -> List[Dict[str, Any]]:
        """
        返回所有工具的 Schema（可直接暴露给上层能力）。
        """
        # 以 dict 形式返回 schema
        return [tool["schema"].model_dump() for tool in self._tools.values()]

    def _filter_sensitive_fields(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # 初始化过滤结果
        filtered: Dict[str, Any] = {}
        # 遍历参数
        for key, value in (params or {}).items():
            # 统一小写比较
            key_lower = str(key).lower()
            # 命中敏感字段直接跳过
            if key_lower in self._sensitive_keys or any(k in key_lower for k in self._sensitive_keys):
                continue
            # 保留非敏感字段
            filtered[key] = value
        # 返回过滤结果
        return filtered

    def _validate_params(self, schema: ToolSchema, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # 缺少 schema 时不校验
        if not schema or not schema.params:
            return None
        # 校验必填参数
        required = schema.params.get("required") or []
        for key in required:
            if key not in params:
                return build_error_payload(
                    code=ErrorCodes.TOOL_INVALID_PARAMS,
                    message=f"Missing required param: {key}",
                    source="tool_registry",
                )
        # 读取参数类型定义
        properties = schema.params.get("properties") or {}
        # JSON schema 类型映射
        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "object": dict,
            "array": list,
        }
        # 校验参数类型
        for key, value in (params or {}).items():
            prop = properties.get(key) or {}
            expected_type = prop.get("type")
            if expected_type and value is not None:
                py_type = type_map.get(expected_type)
                if py_type and not isinstance(value, py_type):
                    return build_error_payload(
                        code=ErrorCodes.TOOL_INVALID_PARAMS,
                        message=f"Invalid type for param: {key}",
                        source="tool_registry",
                    )
        # 校验通过
        return None

    def _get_shared_context_data(self, shared_context: Optional[Any]) -> Dict[str, Any]:
        # 允许直接传入 dict
        if isinstance(shared_context, dict):
            return shared_context
        # None 返回空
        if shared_context is None:
            return {}
        # 尝试读取 data 字段
        data = getattr(shared_context, "data", None)
        # data 为 dict 则返回
        if isinstance(data, dict):
            return data
        # 兜底空字典
        return {}

    def _resolve_input_mapping(
        self,
        params: Dict[str, Any],
        input_mapping: Optional[Dict[str, str]],
        shared_context: Optional[Any],
    ) -> Dict[str, Any]:
        # 初始化参数副本
        resolved = dict(params or {})
        # 获取共享上下文数据
        context_data = self._get_shared_context_data(shared_context)
        # 解析 input_mapping 引用
        for target_key, ref in (input_mapping or {}).items():
            # 忽略非法引用
            if not isinstance(ref, str) or "." not in ref:
                continue
            # 拆分 task_id 与 output_key
            task_id, output_key = ref.split(".", 1)
            # 读取共享上下文
            value = (context_data.get(task_id) or {}).get(output_key)
            # 写入解析到的值
            if value is not None:
                resolved[target_key] = value
        # 返回解析后的参数
        return resolved

    def call_with_context(
        self,
        name: str,
        params: Optional[Dict[str, Any]],
        shared_context: Optional[Any] = None,
        input_mapping: Optional[Dict[str, str]] = None,
    ) -> ToolCallResult:
        # 根据 input_mapping 与 shared_context 合并参数
        resolved_params = self._resolve_input_mapping(params or {}, input_mapping, shared_context)
        # 继续调用统一入口
        return self.call(name, resolved_params)

    def call(self, name: str, params: Dict[str, Any]) -> ToolCallResult:
        """
        统一的工具调用入口，屏蔽具体工具实现差异。
        """
        # 获取工具定义
        # 获取工具定义
        tool = self._tools.get(name)
        if not tool:
            # 构造统一错误结构
            # 构造统一错误结构
            error = build_error_payload(
                code=ErrorCodes.TOOL_NOT_FOUND,
                message=f"Tool {name} not found",
                source="tool_registry",
            )
            # 返回错误结果
            return ToolCallResult(tool_name=name, success=False, error=error)
        # 判断熔断状态
        if not self._circuit_breaker.allow(name):
            # 构造熔断错误
            error = build_error_payload(
                code=ErrorCodes.TOOL_CIRCUIT_OPEN,
                message=f"Tool {name} circuit open",
                source="tool_registry",
            )
            # 记录熔断指标
            self._metrics.record("tool_call_circuit_open", {"tool": name})
            # 返回熔断结果
            return ToolCallResult(tool_name=name, success=False, error=error)
        # 原始参数
        raw_params = params or {}
        # 参数校验
        validation_error = self._validate_params(tool.get("schema"), raw_params)
        if validation_error:
            # 记录失败指标
            self._metrics.record("tool_call_failed", {"tool": name, "error": validation_error.get("code")})
            # 返回校验失败
            return ToolCallResult(tool_name=name, success=False, error=validation_error)
        # 过滤敏感字段并规范化参数
        safe_params = normalize_tool_params(self._filter_sensitive_fields(raw_params))
        # 构建缓存 key
        cache_key = build_tool_cache_key(name, safe_params)
        # 尝试读取缓存
        cached_value, age_ms = self._cache.get(cache_key)
        if cached_value is not None:
            # 记录缓存命中
            self._metrics.record("tool_cache_hit", {"tool": name, "age_ms": age_ms})
            # 返回缓存结果
            if isinstance(cached_value, ToolCallResult):
                return cached_value
            if isinstance(cached_value, dict):
                return ToolCallResult(**cached_value)
        # 记录缓存未命中
        self._metrics.record("tool_cache_miss", {"tool": name})
        try:
            # 记录调用开始时间
            started_at = time.time()
            # 执行工具逻辑
            # 调用工具 handler
            result = tool["handler"](**raw_params)
            # 计算调用耗时
            duration_seconds = time.time() - started_at
            # 校验时间预算
            if self._tool_time_budget_seconds and duration_seconds > self._tool_time_budget_seconds:
                # 构造预算超限错误
                error = build_error_payload(
                    code=ErrorCodes.BUDGET_EXCEEDED,
                    message=f"tool_time_budget_exceeded:{name}",
                    source="tool_registry",
                )
                # 记录熔断失败
                self._circuit_breaker.record_failure(name)
                # 记录预算超限指标
                self._metrics.record(
                    "tool_call_budget_exceeded",
                    {"tool": name, "duration_ms": int(duration_seconds * 1000)},
                )
                # 返回预算超限结果
                return ToolCallResult(tool_name=name, success=False, error=error)
            # 组装成功结果
            # 组装成功结果
            tool_result = ToolCallResult(tool_name=name, success=True, data=result)
            # 写入缓存
            # 写入缓存
            self._cache.set(cache_key, tool_result)
            # 记录成功指标
            # 记录成功指标
            self._metrics.record("tool_call_success", {"tool": name})
            # 重置熔断计数
            self._circuit_breaker.record_success(name)
            # 返回成功结果
            # 返回成功结果
            return tool_result
        except Exception as e:
            # 组装失败结果
            error = normalize_exception(e, code=ErrorCodes.TOOL_EXECUTION_ERROR, source="tool_registry")
            # 封装失败结果
            tool_result = ToolCallResult(tool_name=name, success=False, error=error)
            # 记录失败指标
            self._metrics.record("tool_call_failed", {"tool": name, "error": error.get("code")})
            # 记录熔断失败
            self._circuit_breaker.record_failure(name)
            # 返回失败结果
            return tool_result
