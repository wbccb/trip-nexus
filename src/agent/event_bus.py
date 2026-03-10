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
import json
import os
import sqlite3
import threading
import time

from src.config import PROJECT_ROOT


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
    sequence: int


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
        self._lock = threading.Lock()
        self._db_path = os.path.join(PROJECT_ROOT, "agent_events.db")
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=5)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._init_tables()

    def _init_tables(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                node TEXT,
                kind TEXT NOT NULL,
                ts REAL NOT NULL,
                detail_json TEXT NOT NULL
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_events_thread_id ON agent_events(thread_id)")
        self._conn.commit()

    def emit(self, kind: str, thread_id: str, node: Optional[str], detail: Dict[str, Any]) -> None:
        """
        写入一个事件。

        参数：
        - kind：事件类型
        - thread_id：会话指针（一次 Agent 执行的唯一标识）
        - node：节点名（可以为空，表示全局事件）
        - detail：可序列化详情（建议只放摘要/关键字段，避免过大）
        """

        ts = time.time()
        detail_json = json.dumps(detail or {}, ensure_ascii=False)
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                INSERT INTO agent_events (thread_id, node, kind, ts, detail_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (thread_id, node, kind, ts, detail_json),
            )
            self._conn.commit()
            sequence = int(cursor.lastrowid or 0)
        self._events.append(
            {
                "kind": kind,
                "ts": ts,
                "thread_id": thread_id,
                "node": node,
                "detail": detail,
                "sequence": sequence,
            }
        )

    def list(
        self,
        thread_id: Optional[str] = None,
        after_sequence: Optional[int] = None,
        limit: int = 200,
    ) -> List[AgentEvent]:
        """
        读取事件列表。

        参数：
        - thread_id：为空则返回所有事件；否则只返回该 thread_id 对应事件。

        返回：
        - 事件列表（浅拷贝），调用方可自行排序/切片/摘要展示。
        """

        clauses = []
        params: List[Any] = []
        if thread_id is not None:
            clauses.append("thread_id = ?")
            params.append(thread_id)
        if after_sequence is not None:
            clauses.append("id > ?")
            params.append(int(after_sequence))
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT id, thread_id, node, kind, ts, detail_json
            FROM agent_events
            {where_clause}
            ORDER BY id ASC
            LIMIT ?
        """
        params.append(int(limit))
        cursor = self._conn.cursor()
        rows = cursor.execute(sql, params).fetchall()
        events: List[AgentEvent] = []
        for row in rows:
            detail = {}
            try:
                detail = json.loads(row["detail_json"]) if row["detail_json"] else {}
            except Exception:
                detail = {}
            events.append(
                {
                    "kind": row["kind"],
                    "ts": float(row["ts"]),
                    "thread_id": row["thread_id"],
                    "node": row["node"],
                    "detail": detail,
                    "sequence": int(row["id"]),
                }
            )
        return events

    def clear(self, thread_id: Optional[str] = None) -> None:
        """
        清理事件。

        参数：
        - thread_id：为空则清理全部事件；否则只清理该 thread_id 的事件。
        """

        cursor = self._conn.cursor()
        if thread_id is None:
            cursor.execute("DELETE FROM agent_events")
            self._conn.commit()
            self._events = []
            return
        cursor.execute("DELETE FROM agent_events WHERE thread_id = ?", (thread_id,))
        self._conn.commit()
        self._events = [event for event in self._events if event["thread_id"] != thread_id]

    def latest_sequence(self, thread_id: str) -> int:
        cursor = self._conn.cursor()
        row = cursor.execute(
            "SELECT MAX(id) AS max_id FROM agent_events WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return int(row["max_id"] or 0) if row else 0


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
        self._lock = threading.Lock()
        self._db_path = os.path.join(PROJECT_ROOT, "agent_events.db")
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=5)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._init_tables()

    def _init_tables(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                ts REAL NOT NULL,
                step TEXT,
                duration_ms INTEGER,
                payload_json TEXT,
                state_json TEXT
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_snapshots_thread_id ON agent_snapshots(thread_id)")
        self._conn.commit()

    def add(self, thread_id: str, snapshot: Dict[str, Any]) -> None:
        """
        追加写入一个快照。

        参数：
        - thread_id：会话指针
        - snapshot：快照数据（建议包含 step、duration_ms、payload、state 等）
        """

        if "ts" not in snapshot:
            snapshot["ts"] = time.time()
        payload_json = json.dumps(snapshot.get("payload") or {}, ensure_ascii=False)
        state_json = json.dumps(snapshot.get("state") or {}, ensure_ascii=False)
        step = snapshot.get("step")
        duration_ms = snapshot.get("duration_ms")
        ts_value = float(snapshot.get("ts") or time.time())
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                INSERT INTO agent_snapshots (thread_id, ts, step, duration_ms, payload_json, state_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (thread_id, ts_value, step, duration_ms, payload_json, state_json),
            )
            self._conn.commit()
            sequence = int(cursor.lastrowid or 0)
        snapshot_with_seq = dict(snapshot)
        snapshot_with_seq["sequence"] = sequence
        self._snapshots.setdefault(thread_id, []).append(snapshot_with_seq)

    def list(self, thread_id: str) -> List[Dict[str, Any]]:
        """
        读取指定 thread_id 的全部快照。

        返回：
        - 快照列表（浅拷贝），通常用于 UI 时间线/快照面板展示。
        """

        cursor = self._conn.cursor()
        rows = cursor.execute(
            """
            SELECT id, thread_id, ts, step, duration_ms, payload_json, state_json
            FROM agent_snapshots
            WHERE thread_id = ?
            ORDER BY id ASC
            """,
            (thread_id,),
        ).fetchall()
        snapshots: List[Dict[str, Any]] = []
        for row in rows:
            payload = {}
            state = {}
            try:
                payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            except Exception:
                payload = {}
            try:
                state = json.loads(row["state_json"]) if row["state_json"] else {}
            except Exception:
                state = {}
            snapshots.append(
                {
                    "sequence": int(row["id"]),
                    "thread_id": row["thread_id"],
                    "ts": float(row["ts"]),
                    "step": row["step"],
                    "duration_ms": row["duration_ms"],
                    "payload": payload,
                    "state": state,
                }
            )
        return snapshots

    def latest(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """
        读取最新快照（用于恢复）。

        返回：
        - 最新快照 dict；没有快照则返回 None。
        """

        cursor = self._conn.cursor()
        row = cursor.execute(
            """
            SELECT id, thread_id, ts, step, duration_ms, payload_json, state_json
            FROM agent_snapshots
            WHERE thread_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (thread_id,),
        ).fetchone()
        if not row:
            return None
        payload = {}
        state = {}
        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        except Exception:
            payload = {}
        try:
            state = json.loads(row["state_json"]) if row["state_json"] else {}
        except Exception:
            state = {}
        return {
            "sequence": int(row["id"]),
            "thread_id": row["thread_id"],
            "ts": float(row["ts"]),
            "step": row["step"],
            "duration_ms": row["duration_ms"],
            "payload": payload,
            "state": state,
        }

    def clear(self, thread_id: Optional[str] = None) -> None:
        """
        清理快照。

        参数：
        - thread_id：为空则清理全部快照；否则仅清理该 thread_id。
        """

        cursor = self._conn.cursor()
        if thread_id is None:
            cursor.execute("DELETE FROM agent_snapshots")
            self._conn.commit()
            self._snapshots = {}
            return
        cursor.execute("DELETE FROM agent_snapshots WHERE thread_id = ?", (thread_id,))
        self._conn.commit()
        self._snapshots.pop(thread_id, None)

event_bus = EventBus()
snapshot_store = SnapshotStore()
