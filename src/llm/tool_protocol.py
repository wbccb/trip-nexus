from typing import Dict, Any, Callable, List, Optional
from pydantic import BaseModel
from src.config import Config
from src.observability import TTLCache, build_tool_cache_key, normalize_tool_params, ErrorCodes, build_error_payload, MetricsRecorder, normalize_exception, get_global_recorder


class ToolSchema(BaseModel):
    """
    统一工具协议 Schema 定义。

    设计目标：
    1) 用统一的 JSON 结构描述工具能力，便于本地工具与 MCP 工具共享。
    2) 在 LLM 侧可以直接读取 Schema，形成稳定的调用约束。
    3) 明确错误枚举，保证调用链路在失败时可预期。
    """

    name: str
    description: str
    params: Dict[str, Any]
    returns: Dict[str, Any]
    errors: Dict[str, str]


class ToolCallResult(BaseModel):
    """
    统一工具调用结果结构。

    success: 是否调用成功
    data: 成功时返回的结构化结果
    error: 失败时返回的错误信息（包含 code 与 message）
    """

    tool_name: str
    success: bool
    data: Optional[Any] = None
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

    def register(self, schema: ToolSchema, handler: Callable[..., Any], kind: str = "local") -> None:
        """
        注册工具到注册表。

        schema: 工具协议描述
        handler: 实际执行的函数
        kind: 工具类型标识，默认 local
        """
        self._tools[schema.name] = {"schema": schema, "handler": handler, "kind": kind}

    def list_schemas(self) -> List[Dict[str, Any]]:
        """
        返回所有工具的 Schema（可直接暴露给上层能力）。
        """
        return [tool["schema"].model_dump() for tool in self._tools.values()]

    def call(self, name: str, params: Dict[str, Any]) -> ToolCallResult:
        """
        统一的工具调用入口，屏蔽具体工具实现差异。
        """
        # 先规范化参数，保证缓存键稳定
        safe_params = normalize_tool_params(params or {})
        # 构建工具缓存 key
        cache_key = build_tool_cache_key(name, safe_params)
        # 尝试读取缓存
        cached_value, age_ms = self._cache.get(cache_key)
        # 如果缓存命中，则直接返回
        if cached_value is not None:
            # 记录缓存命中指标
            self._metrics.record("tool_cache_hit", {"tool": name, "age_ms": age_ms})
            # 若缓存结果为 ToolCallResult，直接返回
            if isinstance(cached_value, ToolCallResult):
                return cached_value
            # 若缓存结果为 dict，则尝试转换为 ToolCallResult
            if isinstance(cached_value, dict):
                return ToolCallResult(**cached_value)
        # 记录缓存未命中指标
        self._metrics.record("tool_cache_miss", {"tool": name})
        # 获取工具定义
        tool = self._tools.get(name)
        if not tool:
            # 构造统一错误结构
            error = build_error_payload(
                code=ErrorCodes.TOOL_NOT_FOUND,
                message=f"Tool {name} not found",
                source="tool_registry",
            )
            # 返回错误结果
            return ToolCallResult(tool_name=name, success=False, error=error)
        try:
            # 执行工具逻辑
            result = tool["handler"](**safe_params)
            # 组装成功结果
            tool_result = ToolCallResult(tool_name=name, success=True, data=result)
            # 写入缓存
            self._cache.set(cache_key, tool_result)
            # 记录成功指标
            self._metrics.record("tool_call_success", {"tool": name})
            # 返回成功结果
            return tool_result
        except Exception as e:
            # 组装失败结果
            error = normalize_exception(e, code=ErrorCodes.TOOL_EXECUTION_ERROR, source="tool_registry")
            tool_result = ToolCallResult(tool_name=name, success=False, error=error)
            # 记录失败指标
            self._metrics.record("tool_call_failed", {"tool": name, "error": error.get("code")})
            # 返回失败结果
            return tool_result
