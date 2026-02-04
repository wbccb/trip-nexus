from typing import Any, Dict, List, Optional
import threading
import time


class MetricsRecorder:
    # 初始化指标采集器
    def __init__(self) -> None:
        # 用于记录指标事件列表
        self._events: List[Dict[str, Any]] = []
        # 线程锁，避免并发写入冲突
        self._lock = threading.Lock()

    # 记录一次指标事件
    def record(self, name: str, payload: Optional[Dict[str, Any]] = None) -> None:
        # 生成事件结构
        event = {
            "ts": time.time(),
            "name": name,
            "payload": payload or {},
        }
        # 加锁后追加事件
        with self._lock:
            self._events.append(event)

    # 读取指标事件快照
    def snapshot(self) -> List[Dict[str, Any]]:
        # 加锁后复制事件列表
        with self._lock:
            return list(self._events)

    # 清空指标事件
    def clear(self) -> None:
        # 加锁后清空列表
        with self._lock:
            self._events.clear()


# 全局指标采集器实例
_GLOBAL_RECORDER = MetricsRecorder()


def get_global_recorder() -> MetricsRecorder:
    # 返回全局指标采集器
    return _GLOBAL_RECORDER
