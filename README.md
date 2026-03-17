# TripNexus ✈️

TripNexus 是一个基于大语言模型（LLM）与多智能体（Agent）架构构建的智能旅行规划系统。本项目通过结合流式交互、FunctionCall 工具调用、LangGraph 状态编排以及 RAG 检索增强技术，为用户提供从意图识别、路线规划、私有知识库参考到地图可视化的端到端 AI 智能管家体验。

## 🌟 核心能力

* **智能行程规划与编辑**：支持结构化行程的生成，以及基于自然语言的增、删、改、重新排布。
* **原生工具调用 (FunctionCall)**：统一协议调用天气查询、地理编码、POI（兴趣点）搜索等本地/外部 API。
* **高阶 RAG 检索增强**：结合多源搜索引擎与本地知识库，引入“证据预算 (Evidence Budget)”机制，精准防范上下文溢出。
* **Agent 状态编排**：基于 LangGraph 实现 `Planner -> Scheduler -> Executor -> Reflector` 的计划与执行循环，支持断点续传（Checkpoint）与人工介入（HITL）。
* **极致流式交互**：采用 SSE (Server-Sent Events) 协议，支持大模型文本与前端 UI 状态树的增量渲染与断线重连。

---

## 🏗️ 系统架构

项目采用前后端分离架构设计，主链路为 **React 前端 + Python API 后端**。

* **前端 (Frontend)**：`/web`，基于 React + Vite 构建。负责 UI 交互、地图渲染（高德/CartoDB）、流式事件解析与状态可视化（如 Agent 节点状态、知识库管理等）。
* **后端 (Backend)**：`/src`，基于 Python 构建。API 入口为 `src/api/app.py`，负责 LLM 路由、上下文管理、RAG 检索与 Agent 执行逻辑。
* **存储层 (Storage)**：Redis（2小时短期记忆/缓存） + MySQL（会话与行程持久化） + Chroma（向量知识库）。

---

## 🚀 主要运行流程

1.  **用户输入**：前端 UI 收集用户聊天消息或指令。
2.  **意图识别**：LLM 对输入进行意图分类与核心参数抽取。
3.  **记忆融合**：系统检索历史对话，维护短期/长期记忆，优化 Token 预算。
4.  **检索增强**：触发 RAG 链路，检索私有知识库或进行外网多源搜索获取辅助信息。
5.  **工具与生成**：模型通过 FunctionCall 调用天气/POI等工具，生成或修改结构化行程。
6.  **前端渲染**：通过 SSE 将生成过程流式推送到前端，同步渲染聊天气泡与地图路线。
7.  **数据持久化**：会话状态、行程数据落库保存。

---

## 🧩 核心模块解析

### 1. LLM 管理与 FunctionCall (`src/llm/`)
负责管理 Ollama 与 OpenAI 兼容模型，处理结构化提示词与清洗 JSON 输出。

* **动态路由与工具调用**：云端模型优先走原生 `bind_tools` (FunctionCall)，本地模型安全降级为大模型意图路由（`decide_tool_call`）。模型仅负责“选择工具并生成参数”，实际由后端本地代码执行后注入上下文。
* **流式输出适配 (`LlmStreamingAdapter`)**：将模型原生 `stream` 输出封装为标准化的 `start/delta/end` 事件序列，失败时自动降级为 `invoke`，保障链路高可用。

### 2. Agent 计划与执行循环 (`src/agent/`)
摒弃了简单的线性链，采用基于 LangGraph 的可控状态机编排。

* **执行拓扑**：Planner（规划） -> Scheduler（调度） -> Executor（执行） -> Reflector（反思）。
* **控制机制**：支持通过 Checkpoint 按 `thread_id` 恢复状态；在工具执行失败等场景支持 Human-in-the-loop（人工介入），前端可选择跳过（skip）、覆盖（override）或重试（retry）。

### 3. RAG 检索链路 (`src/rag/`)
构建了端到端的 `AIRetrievalPipeline`，无需搜索时直接绕过，提升响应速度。

* **多源聚合**：SearXNG 聚合搜索 + DuckDuckGo 备选，结合 ThreadPool 并发抓取网页正文。
* **证据预算 (Evidence Budget)**：对摘要 (Summary) 和正文 (Body) 进行 Top K 截断与字符预算控制，动态回填分数与元数据。
* **私有知识库**：支持 PDF/Markdown/TXT 文件的高维向量化入库（Chroma），支持与行程生成请求联动。

### 4. 对话上下文与存储 (`src/frontend/context/`)
* **分级记忆**：核心实体提取（最高优先级） -> 最近3轮对话 -> 长期摘要 -> 早期对话。
* **存储实现**：提供 `prod_storage` (Redis+MySQL) 与 `test_storage` (内存+SQLite) 两套实现，保证测试与生产环境接口一致。

### 5. 可观测性与稳定性保障 (`src/observability/`)
* 针对多源搜索与抓取链路加入并发限制、全局超时与熔断机制。
* 实现全链路日志与关键步骤打点（耗时、Token消耗、错误码），并具备统一的错误规范用于前端 UI 可视化提示。

---

## 🔌 协议与状态流转

* **SSE 传输协议**：前后端通过 Server-Sent Events 连接。后端推送包含 `id/event/data` 的标准帧，其中 `data` 承载自定义 JSON 状态树（包含 sequence、nodes、payload 等）。
* **断线续传**：基于 `last_sequence` 与数据库事件表，前端在重连时携带最后序号，后端按序恢复下发，确保状态不乱序。
* **单向数据流**：前端**完全事件驱动**，不进行本地推断。例如 `replan` 动作由后端下发事件清空前端队列，将节点状态重置为 `planned`，等待新计划填充。

---

## 📂 目录导航参考

* 前端布局与交互：[`web/README.md`](web/README.md)
* 后端 API 入口：[`src/api/app.py`](src/api/app.py)
* 大模型核心逻辑：[`src/llm/llm_manager.py`](src/llm/llm_manager.py)
* RAG 检索主流程：[`src/rag/rag_main.py`](src/rag/rag_main.py)
* Agent 编排逻辑：[`src/agent/agent_loop.py`](src/agent/agent_loop.py)

---


## 项目运行
```shell
# 后端
PYTHONPATH=. uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload

# 前端
cd web
pnpm run dev
```