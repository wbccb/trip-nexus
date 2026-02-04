"""
Agent 调试事件与快照存储（最小实现）。

设计目标：
1) 给 LangGraph 编排提供“可消费的事件流”，让 Streamlit 能实时/回放展示执行轨迹；
2) 给“取消/重试/暂停/恢复”提供恢复基点（Snapshot），避免强耦合 LangGraph 内部 checkpoint 结构；
3) 保持零外部依赖：内存实现即可跑通 v0.0.3 的闭环，后续可替换为 Redis/DB。

注意：
- 本模块的 EventBus/SnapshotStore 都是“进程内”状态：刷新页面或重启服务会丢失。
- 生产化时建议将其替换为持久化存储，并把 thread_id 作为会话/任务的主键。
"""

from typing import TypedDict, Optional, List, Dict, Any
import time


class AgentEvent(TypedDict):
    """
    统一事件结构（供 UI 消费）。

    字段含义：
    - kind：事件类型（node_start/node_end/interrupt/error/tool_call/tool_result/update/cancelled 等）
    - ts：事件发生时间戳（秒）
    - thread_id：一次 Agent 执行链路的会话指针（用于过滤与恢复）
    - node：当前节点名（planner/checker/optimizer/map_rag），也允许为 None（全局事件）
    - detail：事件详情（必须可 JSON 序列化，UI 仅做摘要/回放展示）
    """

    kind: str
    ts: float
    thread_id: str
    node: Optional[str]
    detail: Dict[str, Any]


class EventBus:
    """
    事件总线（内存版）。

    关键点：
    - emit 写入事件；list 按 thread_id 读取事件；clear 清理事件；
    - 事件是 append-only，天然按时间顺序增长（UI 可按 ts 排序）。
    """

    def __init__(self) -> None:
        """
        初始化事件容器。

        _events：按时间追加的事件列表；同一 thread_id 的事件会交错在列表中，
        所以读取时需要过滤 thread_id。
        """

        self._events: List[AgentEvent] = []

    def emit(self, kind: str, thread_id: str, node: Optional[str], detail: Dict[str, Any]) -> None:
        """
        写入一个事件。

        参数：
        - kind：事件类型
        - thread_id：会话指针（一次 Agent 执行的唯一标识）
        - node：节点名（可以为空，表示全局事件）
        - detail：可序列化详情（建议只放摘要/关键字段，避免过大）
        """

        self._events.append(
            {
                "kind": kind,
                "ts": time.time(),
                "thread_id": thread_id,
                "node": node,
                "detail": detail,
            }
        )

    def list(self, thread_id: Optional[str] = None) -> List[AgentEvent]:
        """
        读取事件列表。

        参数：
        - thread_id：为空则返回所有事件；否则只返回该 thread_id 对应事件。

        返回：
        - 事件列表（浅拷贝），调用方可自行排序/切片/摘要展示。
        """

        if thread_id is None:
            return list(self._events)
        return [event for event in self._events if event["thread_id"] == thread_id]

    def clear(self, thread_id: Optional[str] = None) -> None:
        """
        清理事件。

        参数：
        - thread_id：为空则清理全部事件；否则只清理该 thread_id 的事件。
        """

        if thread_id is None:
            self._events = []
            return
        self._events = [event for event in self._events if event["thread_id"] != thread_id]


class SnapshotStore:
    """
    快照存储（内存版）。

    快照定位：
    - 与 LangGraph checkpoint 互补：这里存的是“业务恢复所需的最小状态”
      （step/payload/logs/耗时 + 可 resume 的 state 子集）。
    - 由于是“每步结束落快照”，因此从 latest 快照恢复可以满足“从最近成功快照继续”的诉求。
    """

    def __init__(self) -> None:
        """
        初始化快照容器。

        结构：
        - _snapshots[thread_id] = [snapshot1, snapshot2, ...]
        """

        self._snapshots: Dict[str, List[Dict[str, Any]]] = {}

    def add(self, thread_id: str, snapshot: Dict[str, Any]) -> None:
        """
        追加写入一个快照。

        参数：
        - thread_id：会话指针
        - snapshot：快照数据（建议包含 step、duration_ms、payload、state 等）
        """

        self._snapshots.setdefault(thread_id, []).append(snapshot)

    def list(self, thread_id: str) -> List[Dict[str, Any]]:
        """
        读取指定 thread_id 的全部快照。

        返回：
        - 快照列表（浅拷贝），通常用于 UI 时间线/快照面板展示。
        """

        return list(self._snapshots.get(thread_id, []))

    def latest(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """
        读取最新快照（用于恢复）。

        返回：
        - 最新快照 dict；没有快照则返回 None。
        """

        snapshots = self._snapshots.get(thread_id, [])
        return snapshots[-1] if snapshots else None

    def clear(self, thread_id: Optional[str] = None) -> None:
        """
        清理快照。

        参数：
        - thread_id：为空则清理全部快照；否则仅清理该 thread_id。
        """

        if thread_id is None:
            self._snapshots = {}
            return
        self._snapshots.pop(thread_id, None)


# 单例实例：
# - event_bus：供 orchestrator 与 UI 写入/读取事件
# - snapshot_store：供 orchestrator 每步写入快照，UI 用于恢复与展示
event_bus = EventBus()
snapshot_store = SnapshotStore()
