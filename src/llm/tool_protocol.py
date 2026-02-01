from typing import Dict, Any, Callable, List, Optional
from pydantic import BaseModel


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
        self._tools: Dict[str, Dict[str, Any]] = {}

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
        tool = self._tools.get(name)
        if not tool:
            return ToolCallResult(
                tool_name=name,
                success=False,
                error={"code": "TOOL_NOT_FOUND", "message": f"Tool {name} not found"},
            )
        try:
            result = tool["handler"](**params)
            return ToolCallResult(tool_name=name, success=True, data=result)
        except Exception as e:
            return ToolCallResult(
                tool_name=name,
                success=False,
                error={"code": "TOOL_EXECUTION_ERROR", "message": str(e)},
            )
