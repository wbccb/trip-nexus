# Agent 框架调研（开源 + 高 Star）

## 选型标准
- GitHub Star 具备规模化社区（>20k 作为“足够热度”参考）
- 开源协议清晰，可用于商业与二次开发
- 支持多 Agent 编排、状态持久化或 HITL（Human-in-the-loop）能力
- 与现有 Python 技术栈兼容

## 主流框架对比

| 框架 | GitHub | Stars（近似） | 主要定位 | 核心能力 | 适配 TripNexus 的价值 |
|---|---|---:|---|---|---|
| LangGraph | https://github.com/langchain-ai/langgraph | 24.1k | 图式编排与状态机 | 节点/边编排、持久化检查点、HITL | 与“顺序链路 + 状态快照”高度匹配，适合做可恢复编排 | 
| AutoGen | https://github.com/microsoft/autogen | 54k | 多 Agent 对话编排 | 角色对话、工具扩展、工作流 | 多 Agent 协同能力成熟，但官方正在向 Microsoft Agent Framework 迁移 | 
| CrewAI | https://github.com/crewAIInc/crewAI | 43.5k | 角色协作与流程编排 | 角色分工、Crew/Flow 双模式 | 适合“角色分工 + 任务流”模式，落地成本较低 | 
| LlamaIndex | https://github.com/run-llama/llama_index | 46.7k | RAG 与 Agent over data | 索引与检索、工具调用、Agent | 对 RAG 质量与证据管理有优势，适合强化 Map/RAG Agent | 
| Semantic Kernel | https://github.com/microsoft/semantic-kernel | 27.1k | 企业级 Agent/编排 | 多语言、插件体系、流程框架 | 适合企业化落地与可观测性，但体系较重 | 
| smolagents | https://github.com/huggingface/smolagents | 25k | 轻量 Agent 工具库 | 代码型 Agent、工具与模型兼容 | 轻量试验与快速原型好，但复杂编排需自建 | 

## 关键观察
- LangGraph 更贴合“状态机 + 可恢复编排 + HITL”的需求特征，适配 v0.0.3 的目标链路。
- AutoGen 仍为高热度开源项目，但官方已宣布向 Microsoft Agent Framework 迁移，短期适合作为对照参考，长期需评估迁移成本。
- CrewAI 更偏“角色协作 + 任务流”，对“Planner/Checker/Optimizer”这种角色化链路较友好。
- LlamaIndex 对 RAG 组件与证据结构支持成熟，可作为 Map/RAG Agent 的底层框架或工具集补充。
- Semantic Kernel 提供企业级能力（多语言、插件、流程框架），但引入成本与依赖较大。
- smolagents 适合轻量流程与快速验证，不适合作为完整状态机编排主框架。

## 与 TripNexus 的落地映射
- 若以“状态机 + 快照 + HITL”为主线：优先 LangGraph
- 若以“多 Agent 对话协作”为主线：AutoGen 或 CrewAI
- 若以“RAG 证据治理”为主线：LlamaIndex 作为补充能力

## 接入成本评估（结合 TripNexus 现有模块）

| 框架 | 接入复杂度 | 关键改动点 | 对现有模块影响 | 主要风险 |
|---|---|---|---|---|
| LangGraph | 中 | 新增 Orchestrator 层，抽象 Planner/Checker/Optimizer/MapRAG 节点 | 复用 LlmManager 与工具注册表；UI 增加状态流展示 | 引入状态持久化与检查点设计成本 |
| CrewAI | 低-中 | 定义角色与任务流，映射现有链路为 Crew/Flow | LlmManager 作为工具执行层；UI 侧增加流程状态展示 | Crew 与 Flow 双模型的学习成本 |
| AutoGen | 中-高 | 采用 AgentChat/Core 建多 Agent 对话流程 | 需要改写现有调用入口与上下文管理 | 官方迁移到 Agent Framework，后续升级成本 |
| LlamaIndex | 中 | 作为 Map/RAG Agent 的检索与工具层 | 与现有 RAG 流程部分重叠，需要权衡替换或补充 | 双 RAG 流程并存导致维护成本 |
| Semantic Kernel | 高 | 引入插件/流程框架与跨语言抽象 | 大幅改造工具与模型调用入口 | 体系重、引入成本高 |
| smolagents | 低 | 适合 PoC 原型，小规模编排 | 需手工实现状态机、快照与流程日志 | 可扩展性不足 |

## 最小 PoC 方案（LangGraph）
- 目标：验证“顺序链路 + 快照 + HITL”的核心闭环
- 复用组件：LlmManager、ToolRegistry、TripMap、RAG Pipeline
- PoC 流程
  - 节点设计：Planner → Checker → Optimizer → Map/RAG
  - 状态结构：current_step、payload（draft/constraints/optimized/map）、logs
  - HITL：Planner 产出草案后暂停，UI 提示确认/修改
  - 快照：每步结束写入持久化存储（短期可先用本地或测试存储）
- 成功标准：草案行程可恢复、HITL 修改生效、地图与证据可输出

## 最小 PoC 方案（CrewAI）
- 目标：验证“角色分工 + 任务流”的可用性与成本
- 复用组件：LlmManager 的工具调用封装、现有工具与 RAG
- PoC 流程
  - 角色：Planner/Checker/Optimizer/MapRAG
  - 任务：草案生成 → 约束校验 → 预算与路线修正 → 地图与证据输出
  - HITL：Checker 输出硬约束后由用户确认是否继续
- 成功标准：角色链路可稳定运行、工具调用可复用、输出结构满足现有 UI 需求

## LangGraph 快速开发教程（面向 TripNexus）

### 1. 安装与最小入口
- 安装：
  - pip install -U langgraph
- 基础概念：
  - StateGraph：用“节点 + 边”描述流程
  - START/END：图的起点与终点
  - state：贯穿全流程的结构化状态
  - compile：将图编译成可 invoke/stream 的运行时

```python
from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, START, END

class TripState(TypedDict):
    user_input: str
    draft: Dict[str, Any]
    constraints: Dict[str, Any]
    optimized: Dict[str, Any]
    map_payload: Dict[str, Any]
    logs: List[str]

def planner(state: TripState):
    return {"draft": state.get("draft", {})}

def checker(state: TripState):
    return {"constraints": state.get("constraints", {})}

def optimizer(state: TripState):
    return {"optimized": state.get("optimized", {})}

def map_rag(state: TripState):
    return {"map_payload": state.get("map_payload", {})}

builder = StateGraph(TripState)
builder.add_node("planner", planner)
builder.add_node("checker", checker)
builder.add_node("optimizer", optimizer)
builder.add_node("map_rag", map_rag)
builder.add_edge(START, "planner")
builder.add_edge("planner", "checker")
builder.add_edge("checker", "optimizer")
builder.add_edge("optimizer", "map_rag")
builder.add_edge("map_rag", END)
graph = builder.compile()
```

### 2. 接入持久化快照（Checkpoint）
- 目的：支持断点恢复、HITL 暂停、稳定重试
- 关键点：调用时必须带 thread_id，作为会话级状态指针

```python
from langgraph.checkpoint.memory import InMemorySaver

checkpointer = InMemorySaver()
graph = builder.compile(checkpointer=checkpointer)
config = {"configurable": {"thread_id": "trip-session-1"}}
result = graph.invoke({"user_input": "杭州三日游"}, config=config)
```

### 3. HITL（Human-in-the-loop）中断与恢复
- 在关键节点调用 interrupt 暂停，返回给 UI 决策
- 通过 Command(resume=...) 将人工输入回灌到暂停点

```python
from langgraph.types import interrupt, Command

def hitl_gate(state: TripState):
    decision = interrupt({"question": "是否接受草案行程？", "draft": state["draft"]})
    return {"logs": state.get("logs", []) + [str(decision)]}

builder.add_node("hitl_gate", hitl_gate)
builder.add_edge("planner", "hitl_gate")
builder.add_edge("hitl_gate", "checker")
graph = builder.compile(checkpointer=checkpointer)

config = {"configurable": {"thread_id": "trip-session-1"}}
first_run = graph.invoke({"user_input": "杭州三日游"}, config=config)
pending = first_run["__interrupt__"]
graph.invoke(Command(resume={"approved": True}), config=config)
```

### 4. 与 TripNexus 现有模块的快速映射
- Planner：复用 LlmManager.generate_trip 输出草案行程
- Checker：复用 call_tool_by_llm 触发天气/POI/地理编码工具
- Optimizer：复用 change_trip 或改写生成提示词进行修正
- Map/RAG：复用 TripMap 与 rag_main.py 的证据输出
- State 结构：沿用 destination/days/daily_plan，新增 constraints/optimized/map_payload

### 5. 输出与 UI 串联建议
- graph.stream 用于流式输出节点进度与中间状态
- UI 侧根据 __interrupt__ 触发“确认/修改”交互，再 resume 执行
- 线程 ID 可直接复用 session_id，形成“会话级可恢复”

## Agent 可视化与可观测性（主流方案调研）

### 目标与能力边界
- 直观展示“节点 → 边”的执行轨迹与状态转移
- 流式调试：实时看到节点开始/结束/中断/错误
- 可重放与断点恢复：结合 checkpoint 与 thread_id 进行时间旅行
- 可观测性指标：耗时、错误、工具调用参数与结果摘要

### 方案一：LangGraph 流式更新（原生轻量）
- 优势：零外部依赖、贴合 StateGraph 执行模型、支持子图与中断
- 用法：graph.stream(..., subgraphs=True)，选择 "updates" 流式模式获取节点事件
- 落地建议：在 Streamlit 中订阅事件并绘制“时间线/泳道图”

```python
# 伪示例：收集节点事件用于前端渲染
config = {"configurable": {"thread_id": "trip-session-2"}}
events = []
for chunk in graph.stream({"user_input": "上海周末游"}, config, subgraphs=True):
    events.append(chunk)  # 每个 chunk 包含节点开始/结束/中断等更新
# 前端将 events 转为可视化（如时间线、Mermaid、Graphviz）
```

### 方案二：LangSmith（托管追踪）
- 能力：调用链路追踪、模型与工具调用监控、数据集评测、对话回放
- 优势：与 LangChain/LangGraph 深度集成、成熟的 UI 与分享能力
- 风险：SaaS 托管（非完全开源），需考虑隐私与成本
- 适配：将 LlmManager 的模型与工具调用接入 LangSmith client，附加 thread_id 元数据

### 方案三：Langfuse（开源可观测性）
- 能力：Trace/Span、提示词与模型输出记录、性能指标可视化
- 优势：开源部署、生态活跃；适合私有化需求
- 适配：在 Orchestrator 层为每个节点生成 span，记录输入/输出摘要、耗时与错误

### 方案四：OpenTelemetry + Grafana/Tempo/Jaeger
- 能力：通用分布式追踪；以 Span/Trace 记录节点执行与工具调用
- 优势：生态标准；可与现有监控体系整合
- 风险：集成成本较高，需要统一上下文与采样策略
- 适配：为 Planner/Checker/Optimizer/MapRAG 创建 span，并以 thread_id 作为 trace_id

### 方案五：Arize Phoenix（开源）
- 能力：LLM 应用可观测性与评估；支持对话与检索质量分析
- 优势：可视化体验完善，适合 RAG/Agent 的质量分析
- 风险：需要对接其 SDK 并适配数据结构

## Streamlit 调试可视化面板（最小实现）

### 设计目标
- 提供“事件总线 + 时间线组件”的最小可用实现，便于在 UI 中快速调试 Agent
- 支持展示节点开始/结束、工具调用、错误与中断（HITL）等关键事件
- 与 LangGraph 的 graph.stream 事件机制兼容，后续可扩展到持久化追踪

### 事件总线（最小实现）

```python
from typing import TypedDict, Optional, List, Dict, Any
import time

class AgentEvent(TypedDict):
    """
    事件数据结构
    - kind：事件类型（node_start/node_end/interrupt/error/tool_call/tool_result/update）
    - ts：事件发生的时间戳（秒）
    - thread_id：会话指针，用于在 Streamlit 按会话过滤展示
    - node：当前节点名称（如 'planner'/'checker' 等）
    - detail：可序列化的事件详情（输入摘要/输出摘要/错误信息/工具参数等）
    """
    kind: str
    ts: float
    thread_id: str
    node: Optional[str]
    detail: Dict[str, Any]

class EventBus:
    """
    事件总线（内存版最小实现）
    - 负责接收与缓存 Agent 执行过程中的事件
    - 实际项目中可替换为 Redis/DB，以支持跨会话协作与历史回放
    """
    def __init__(self):
        self._events: List[AgentEvent] = []

    def emit(self, kind: str, thread_id: str, node: Optional[str], detail: Dict[str, Any]) -> None:
        """
        事件写入方法
        - kind：事件类型
        - thread_id：会话指针
        - node：节点名（可为空，例如全局错误）
        - detail：事件详情（必须是 JSON 可序列化）
        """
        self._events.append({
            "kind": kind,
            "ts": time.time(),
            "thread_id": thread_id,
            "node": node,
            "detail": detail,
        })

    def list(self, thread_id: Optional[str] = None) -> List[AgentEvent]:
        """
        事件读取方法
        - 支持根据 thread_id 过滤，默认返回全部事件
        - 在 UI 中可按会话维度渲染时间线
        """
        if thread_id is None:
            return list(self._events)
        return [e for e in self._events if e["thread_id"] == thread_id]

# 单例总线（示例）
event_bus = EventBus()
```

### 与 LangGraph 的事件对接

```python
from typing import Dict, Any

def collect_graph_events(graph, initial_input: Dict[str, Any], thread_id: str):
    """
    将 LangGraph 的 graph.stream 事件转换为统一的 AgentEvent，并写入事件总线
    - graph：已编译的 StateGraph 实例
    - initial_input：首次运行的输入（如 {"user_input": "杭州三日游"}）
    - thread_id：会话指针，用于后续恢复与 UI 渲染
    """
    config = {"configurable": {"thread_id": thread_id}}
    for chunk in graph.stream(initial_input, config=config, subgraphs=True):
        # 最小兼容：把原始 chunk 作为 detail 存储，便于后续解析与展示
        event_bus.emit(kind="update", thread_id=thread_id, node=None, detail={"raw": chunk})
        # 可选：根据 chunk 的结构进一步识别 node_start/node_end/interrupt 等具体事件
        # 这里保留最小实现，避免与版本细节强耦合
```

### 时间线组件（Streamlit 最小实现）

```python
import streamlit as st

def render_timeline(thread_id: str):
    """
    时间线组件
    - 按 thread_id 拉取事件并可视化
    - 使用图标区分不同事件类型，显示节点名与关键摘要
    """
    st.subheader(f"执行时间线（thread_id={thread_id}）")
    events = event_bus.list(thread_id)

    # 事件类型与图标映射
    ICON = {
        "node_start": "🟢",
        "node_end": "✅",
        "interrupt": "⏸️",
        "error": "❌",
        "tool_call": "🛠️",
        "tool_result": "📦",
        "update": "🔄",
    }

    # 按时间排序渲染
    events = sorted(events, key=lambda e: e["ts"])
    for e in events:
        ts = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
        icon = ICON.get(e["kind"], "•")
        node = e["node"] or "-"
        # 生成简要摘要（最小实现：取 detail 的键列表）
        summary = ", ".join(list(e["detail"].keys()))
        st.markdown(f"{icon} [{ts}] 节点：{node} 事件：{e['kind']} 详情：{summary}")

    # 检测中断事件（示例）：若存在 __interrupt__，在顶部显示交互入口
    interrupted = any(
        (e["kind"] == "update" and "__interrupt__" in e["detail"].get("raw", ({},))[1])
        for e in events
    )
    if interrupted:
        st.warning("检测到中断（HITL）：请在下方选择继续/编辑/拒绝")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.button("继续执行（approve）")
        with col2:
            st.button("编辑参数（edit）")
        with col3:
            st.button("拒绝并给出反馈（reject）")
```

### 使用步骤（最小闭环）
- 在 Orchestrator 入口处，调用 collect_graph_events(graph, initial_input, thread_id) 将事件写入总线
- 在 Streamlit 页面中，调用 render_timeline(thread_id) 渲染时间线
- 若需要响应中断（HITL），在按钮回调中读取最新的中断负载并调用 Command(resume=...) 恢复执行（代码集成留到后续实现）

### 自定义可视化（Streamlit 实现建议）
- 事件模型：node_start、node_end、interrupt、error、tool_call、tool_result
- 映射规则：
  - 线程：thread_id 对应会话
  - 节点：node_id/step 序号
  - 时间：start_ts/end_ts；渲染时生成“泳道图”
- 数据字段：
  - node_id、status、duration_ms、input_summary、output_summary、tool_args、tool_result_digest

```python
# 伪示例：统一事件总线
def emit_event(kind: str, payload: dict):
    pass  # 写入内存/Redis，前端订阅渲染

def wrapped_node(fn, name: str):
    def _inner(state):
        emit_event("node_start", {"name": name, "state": state})
        try:
            out = fn(state)
            emit_event("node_end", {"name": name, "out": out})
            return out
        except Exception as e:
            emit_event("error", {"name": name, "error": str(e)})
            raise
    return _inner
```

### TripNexus 选型建议
- 首选：LangGraph 流式事件 + Streamlit 可视化（轻量、零外部依赖、快速落地）
- 增强：需要团队协作与历史回放时，引入 Langfuse（私有化）或 LangSmith（托管）
- 长期：如需统一监控体系，集成 OpenTelemetry 并汇总到 Grafana/Tempo

### 参考
- LangGraph 文档（Interrupts/Checkpointing/Streaming）
  - https://docs.langchain.com/oss/python/langgraph/interrupts  
  - https://reference.langchain.com/python/langgraph/checkpoints/  
- LangSmith（托管追踪）  
  - https://docs.smith.langchain.com/  
- Langfuse（开源可观测性）  
  - https://langfuse.com/docs  
- OpenTelemetry Python  
  - https://opentelemetry.io/docs/instrumentation/python/  
## 参考与数据来源
- LangGraph Star 数与项目说明：https://github.com/langchain-ai/langgraph/releases  
- AutoGen Star 数与项目状态：https://github.com/microsoft/autogen/releases  
- AutoGen 迁移说明：https://github.com/microsoft/autogen/discussions/7066  
- CrewAI Star 数：https://github.com/crewAIInc  
- LlamaIndex Star 数：https://github.com/run-llama  
- Semantic Kernel Star 数：https://github.com/microsoft/semantic-kernel/releases  
- smolagents Star 数：https://github.com/huggingface/smolagents/releases  
