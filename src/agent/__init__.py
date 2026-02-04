"""
Agent 编排模块对外导出。

用途：
- 将 Orchestrator（编排器）与事件/快照单例统一导出，便于 UI 或其他入口直接引用；
- 保持 import 路径稳定，避免上层到处引用子模块实现细节。
"""

from src.agent.orchestrator import AgentOrchestrator
from src.agent.event_bus import event_bus, snapshot_store
