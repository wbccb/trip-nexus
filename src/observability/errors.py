from typing import Any, Dict, Optional
import traceback


class ErrorCodes:
    # 统一错误码：工具相关
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    # 统一错误码：工具执行失败
    TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"
    # 统一错误码：搜索实例失败
    SEARCH_INSTANCE_FAILED = "SEARCH_INSTANCE_FAILED"
    # 统一错误码：搜索超时
    SEARCH_TIMEOUT = "SEARCH_TIMEOUT"
    # 统一错误码：抓取失败
    CRAWL_FAILED = "CRAWL_FAILED"
    # 统一错误码：抓取超时
    CRAWL_TIMEOUT = "CRAWL_TIMEOUT"
    # 统一错误码：RAG 失败
    RAG_FAILED = "RAG_FAILED"
    # 统一错误码：LLM 调用失败
    LLM_FAILED = "LLM_FAILED"
    # 统一错误码：未知异常
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"


def build_error_payload(
    code: str,
    message: str,
    detail: Optional[Dict[str, Any]] = None,
    retryable: bool = False,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    # 构造统一错误结构，便于 UI 与日志消费
    payload: Dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    # 可选补充错误来源，便于定位链路
    if source:
        payload["source"] = source
    # 可选补充详细信息，便于调试与回放
    if detail:
        payload["detail"] = detail
    # 返回结构化错误信息
    return payload


def normalize_exception(
    exc: Exception,
    code: str = ErrorCodes.UNEXPECTED_ERROR,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    # 提取异常类型名称，便于故障聚类
    exc_type = type(exc).__name__
    # 提取异常消息文本，便于快速定位
    exc_message = str(exc)
    # 抽取堆栈信息，便于排查问题根因
    trace_text = traceback.format_exc()
    # 组织 detail 字段用于调试
    detail = {
        "exception_type": exc_type,
        "exception_message": exc_message,
        "traceback": trace_text,
    }
    # 返回统一错误结构
    return build_error_payload(code=code, message=exc_message, detail=detail, retryable=False, source=source)
