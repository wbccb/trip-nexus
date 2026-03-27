import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Iterable, Optional


class ZhLogFormatter(logging.Formatter):
    _LEVEL_MAP = {
        logging.DEBUG: "调试",
        logging.INFO: "信息",
        logging.WARNING: "警告",
        logging.ERROR: "错误",
        logging.CRITICAL: "严重",
    }

    def format(self, record: logging.LogRecord) -> str:
        record.levelname_cn = self._LEVEL_MAP.get(record.levelno, "日志")
        return super().format(record)


def setup_logging(level: Optional[str] = None) -> None:
    root = logging.getLogger()
    if getattr(root, "_tripnexus_logging_ready", False):
        return
    resolved_level = str(level or os.getenv("TRIPNEXUS_LOG_LEVEL", "WARNING")).upper()
    handler = logging.StreamHandler()
    handler.setFormatter(
        ZhLogFormatter("%(asctime)s [%(levelname_cn)s] %(name)s - %(message)s")
    )
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, resolved_level, logging.INFO))
    root._tripnexus_logging_ready = True  # type: ignore[attr-defined]


def summarize_text(text: Any, head: int = 24, tail: int = 24) -> str:
    if text is None:
        return ""
    value = str(text).strip()
    if len(value) <= head + tail + 3:
        return value
    return f"{value[:head]}...{value[-tail:]}"


def summarize_value(value: Any, head: int = 24, tail: int = 24) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        try:
            return summarize_text(
                json.dumps(value, ensure_ascii=False, default=str),
                head=head,
                tail=tail,
            )
        except Exception:
            return summarize_text(str(value), head=head, tail=tail)
    return summarize_text(value, head=head, tail=tail)


def format_kv(data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> str:
    merged: Dict[str, Any] = {}
    if data:
        merged.update(data)
    merged.update({k: v for k, v in kwargs.items() if v not in [None, ""]})
    parts = []
    for key, value in merged.items():
        if isinstance(value, float):
            parts.append(f"{key}={value:.2f}")
        else:
            parts.append(f"{key}={summarize_value(value)}")
    return " ".join(parts)


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    data: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> None:
    kv_text = format_kv(data, **kwargs)
    if kv_text:
        logger.log(level, "%s | %s", message, kv_text)
        return
    logger.log(level, message)


def log_llm_start(
    logger: logging.Logger,
    *,
    stage: str,
    model: str,
    prompt: Any,
    extra: Optional[Dict[str, Any]] = None,
) -> datetime:
    log_event(
        logger,
        logging.INFO,
        f"LLM 调用开始: {stage}",
        {
            "模型": model,
            "提示词长度": len(str(prompt or "")),
            "提示词预览": summarize_value(prompt),
            **(extra or {}),
        },
    )
    return datetime.now()


def log_llm_end(
    logger: logging.Logger,
    *,
    stage: str,
    started_at: datetime,
    output: Any,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    cost = (datetime.now() - started_at).total_seconds()
    log_event(
        logger,
        logging.INFO,
        f"LLM 调用结束: {stage}",
        {
            "耗时秒": cost,
            "输出长度": len(str(output or "")),
            "输出预览": summarize_value(output),
            **(extra or {}),
        },
    )


def summarize_iterable_size(items: Optional[Iterable[Any]]) -> int:
    if items is None:
        return 0
    try:
        return len(items)  # type: ignore[arg-type]
    except Exception:
        return sum(1 for _ in items)
