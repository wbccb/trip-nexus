from typing import Any, Dict, Optional, Tuple
import json
import threading
import time


def normalize_tool_params(params: Dict[str, Any]) -> Dict[str, Any]:
    # 参数规范化：移除 None 与空字符串，保持 JSON 可序列化
    if not isinstance(params, dict):
        return {}
    # 用于承载清洗后的参数
    normalized: Dict[str, Any] = {}
    # 遍历原始参数键值对
    for key, value in params.items():
        # 过滤空键名
        if key is None or str(key).strip() == "":
            continue
        # 过滤 None 值
        if value is None:
            continue
        # 过滤空字符串
        if isinstance(value, str) and value.strip() == "":
            continue
        # 过滤空容器
        if isinstance(value, (list, dict)) and len(value) == 0:
            continue
        # 递归清洗字典
        if isinstance(value, dict):
            normalized[key] = normalize_tool_params(value)
            continue
        # 原样保留其他类型
        normalized[key] = value
    # 返回规范化后的参数
    return normalized


def build_tool_cache_key(tool_name: str, params: Dict[str, Any]) -> str:
    # 工具名为空时提供默认占位符
    safe_tool = tool_name or "unknown_tool"
    # 先清洗参数，避免无意义差异导致缓存失效
    normalized = normalize_tool_params(params or {})
    # 使用稳定序列化生成缓存 key 片段
    serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    # 拼接形成最终缓存键
    return f"{safe_tool}:{serialized}"


class TTLCache:
    # 初始化缓存结构与策略参数
    def __init__(self, ttl_seconds: int = 300, max_size: int = 512) -> None:
        # 统一 TTL 时间配置
        self._ttl_seconds = max(1, int(ttl_seconds))
        # 缓存最大条目数，避免内存无限增长
        self._max_size = max(1, int(max_size))
        # 存储结构：key -> (created_ts, expires_ts, value)
        self._store: Dict[str, Tuple[float, float, Any]] = {}
        # 线程锁，保证多线程安全
        self._lock = threading.Lock()

    # 获取缓存值与 age_ms
    def get(self, key: str) -> Tuple[Optional[Any], Optional[int]]:
        # 记录当前时间戳
        now = time.time()
        # 加锁，确保线程安全
        with self._lock:
            # 获取缓存条目
            item = self._store.get(key)
            # 缓存未命中时直接返回
            if not item:
                return None, None
            # 解析缓存条目
            created_ts, expires_ts, value = item
            # 判断是否过期
            if expires_ts <= now:
                # 删除过期条目
                self._store.pop(key, None)
                return None, None
            # 计算缓存年龄
            age_ms = int((now - created_ts) * 1000)
            # 返回缓存值与年龄
            return value, age_ms

    # 写入缓存值
    def set(self, key: str, value: Any) -> None:
        # 记录当前时间戳
        now = time.time()
        # 计算过期时间戳
        expires_ts = now + self._ttl_seconds
        # 加锁，确保线程安全
        with self._lock:
            # 写入缓存条目
            self._store[key] = (now, expires_ts, value)
            # 清理过期条目与多余条目
            self._prune_locked(now)

    # 在已加锁状态下执行清理逻辑
    def _prune_locked(self, now: float) -> None:
        # 收集过期的 key 列表
        expired_keys = [k for k, (_, exp, _) in self._store.items() if exp <= now]
        # 删除过期条目
        for key in expired_keys:
            self._store.pop(key, None)
        # 若未超过最大容量则直接返回
        if len(self._store) <= self._max_size:
            return
        # 按创建时间排序，优先移除最旧条目
        ordered = sorted(self._store.items(), key=lambda item: item[1][0])
        # 计算需要移除的数量
        to_remove = len(self._store) - self._max_size
        # 删除多余条目
        for idx in range(max(0, to_remove)):
            key = ordered[idx][0]
            self._store.pop(key, None)
