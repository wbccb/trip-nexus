# TripNexus

[English](README.en.md) | 简体中文

TripNexus 是一个面向旅行规划场景的 AI 应用，结合大语言模型、Function Calling、RAG 检索增强、LangGraph Agent 编排和地图可视化，提供从旅行灵感收集、意图识别、结构化行程生成、局部重排到路线展示的端到端体验。

## 目录

- [核心能力](#核心能力)
- [技术栈](#技术栈)
- [系统架构](#系统架构)
- [快速开始](#快速开始)
- [环境变量](#环境变量)
- [常用 API](#常用-api)
- [项目结构](#项目结构)
- [开发脚本](#开发脚本)
- [相关文档](#相关文档)
- [许可证](#许可证)

## 核心能力

- **AI 行程生成与编辑**：根据目的地、天数、预算、节奏、偏好和特殊约束生成结构化行程，并支持自然语言修改、单日重排和局部重排。
- **旅行灵感收纳箱**：支持上传 PDF、Markdown、TXT，导入社交 URL，进行 URL 预处理、风险分级、失败降级和原位重试。
- **RAG 检索增强**：支持私有知识库和公网多源搜索，使用 Evidence Budget 控制摘要与正文证据，降低上下文溢出风险。
- **Function Calling 工具调用**：统一调用天气、地理编码、POI 等工具，并支持工具缓存、超时、熔断和错误规范化。
- **Agent 编排与流式交互**：基于 LangGraph 管理计划与执行状态，通过 SSE 增量输出主流程事件，支持状态查询、暂停、恢复和重试。
- **地图可视化**：前端展示行程 POI、路线和地图图层，支持高德街道、高德卫星与 CartoDB 图层。
- **多租户模型配置**：支持用户级 LLM 配置隔离，保存前进行连通性验证，并在配置变更后刷新用户模型实例。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 前端 | React 19、Vite、Ant Design、MapLibre、Deck.gl、React Map GL |
| 后端 | Python 3.12、FastAPI、Pydantic、Uvicorn |
| LLM / Agent | LangChain、LangGraph、OpenAI 兼容接口、Ollama 兼容接口 |
| RAG / 知识库 | Chroma、Sentence Transformers、SearXNG、BeautifulSoup、Unstructured |
| 存储 | SQLite 测试存储、Redis 短期缓存、MySQL 生产存储 |
| 地图 | Folium、高德地图瓦片、CartoDB |
| 可观测性 | 结构化日志、流程指标、错误码、超时与熔断 |

## 系统架构

```text
React + Vite Web
  |-- Chat / Trip / Knowledge / Map UI
  |-- SSE consumer and local state rendering
  v
FastAPI Backend
  |-- routes: auth, flow, trip, knowledge, map
  |-- logic: main flow, trip normalization, knowledge orchestration
  |-- llm: prompt building, model routing, function calling, streaming adapter
  |-- rag: intent recognition, search, crawl, validation, vector retrieval
  |-- agent: LangGraph plan-and-execute loop
  |-- observability: metrics, limits, cache, errors
  v
Storage and External Services
  |-- SQLite / Redis / MySQL
  |-- Chroma vector store
  |-- OpenAI-compatible or Ollama-compatible models
  |-- SearXNG and web sources
```

主业务入口是 `/api/flow/stream`。前端提交用户消息、行程约束和可选知识库参数后，后端依次完成意图识别、上下文整理、RAG 检索、工具调用、行程生成、冲突检测和最终结果透传。

## 快速开始

### 1. 克隆项目

```bash
git clone <your-repository-url>
cd TripNexus
```

### 2. 准备 Python 环境

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. 准备前端依赖

```bash
cd web
pnpm install
cd ..
```

### 4. 配置环境变量

后端会根据 `ENVIRONMENT` 加载 `.env.development` 或 `.env.production`，系统环境变量优先级更高。本地开发时请编辑 `.env.development`，或在启动命令前导出同名环境变量。

至少需要确认以下后端配置：

```dotenv
ENVIRONMENT=development
JWT_SECRET_KEY=replace-with-a-local-secret

ANALYSIS_PROVIDER=openai_compatible
ANALYSIS_BASE_URL=http://localhost:11434/v1
ANALYSIS_MODEL_NAME=your-analysis-model
ANALYSIS_API_KEY=your-api-key

GENERATION_PROVIDER=openai_compatible
GENERATION_BASE_URL=http://localhost:11434/v1
GENERATION_MODEL_NAME=your-generation-model
GENERATION_API_KEY=your-api-key

EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_BASE_URL=http://localhost:11434/v1
EMBEDDING_MODEL_NAME=your-embedding-model
EMBEDDING_API_KEY=your-api-key

SEARXNG_URL=http://localhost:8080
TRIP_CONTEXT_STORAGE_TYPE=test
AUTH_DB_BACKEND=sqlite
```

前端默认连接 `http://127.0.0.1:8000`。如需修改前端 API 地址，可创建 `web/.env.local`：

```bash
cd web
printf 'VITE_API_BASE=http://127.0.0.1:8000\n' > .env.local
```

### 5. 启动后端

```bash
source venv/bin/activate
PYTHONPATH=. uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload
```

### 6. 启动前端

```bash
cd web
pnpm run dev
```

打开 Vite 输出的本地地址，通常是 `http://localhost:5173`。

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `ENVIRONMENT` | 运行环境，常用值为 `development` 或 `production` |
| `JWT_SECRET_KEY` | JWT 签名密钥 |
| `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD` | 初始化超级管理员账号 |
| `ANALYSIS_*` | 意图识别与参数抽取模型配置 |
| `GENERATION_*` | 行程生成与修改模型配置 |
| `EMBEDDING_*` | 向量化模型配置 |
| `SEARXNG_URL` | SearXNG 聚合搜索服务地址 |
| `CHROMA_DB_PATH` | Chroma 向量库目录 |
| `TRIP_CONTEXT_STORAGE_TYPE` | 行程上下文存储类型，`test` 或 `prod` |
| `AUTH_DB_BACKEND` | 鉴权数据库后端，`sqlite` 或 `mysql` |
| `REDIS_*` | Redis 短期缓存配置 |
| `MYSQL_*` | MySQL 生产存储配置 |
| `CORS_ORIGINS` | 允许访问后端的前端源 |

## 常用 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/flow/stream` | 主流程 SSE 入口，生成或修改行程 |
| `GET` | `/api/flow/status` | 查询主流程状态 |
| `POST` | `/api/flow/control` | 暂停、恢复或重试主流程 |
| `GET` | `/api/flow/metrics` | 查询流程指标明细 |
| `GET` | `/api/flow/metrics/summary` | 查询流程指标汇总 |
| `GET` | `/api/knowledge/bases` | 获取知识库列表 |
| `POST` | `/api/knowledge/bases` | 创建知识库 |
| `POST` | `/api/knowledge/bases/{knowledge_base_id}/upload` | 上传文件到知识库 |
| `POST` | `/api/knowledge/bases/{knowledge_base_id}/ingest/url` | 导入 URL 来源 |
| `POST` | `/api/knowledge/preprocess/url` | URL 预处理与解析预判 |
| `GET` | `/api/knowledge/bases/{knowledge_base_id}/sources` | 查询知识来源列表 |
| `PATCH` | `/api/knowledge/bases/{knowledge_base_id}/sources/{source_id}` | 修改来源正文并重建分块 |
| `DELETE` | `/api/knowledge/bases/{knowledge_base_id}/sources/{source_id}` | 删除知识来源 |

## 项目结构

```text
TripNexus/
├── src/
│   ├── api/              # FastAPI 入口、路由、请求模型与业务编排
│   ├── auth/             # 用户、JWT、管理员初始化与数据库访问
│   ├── llm/              # LLM 管理、提示词、工具调用、流式适配
│   ├── rag/              # 搜索、抓取、质量门禁、向量检索
│   ├── agent/            # LangGraph Agent 状态编排
│   ├── map/              # 地图 HTML 渲染
│   ├── frontend/context/ # 对话上下文与存储抽象
│   ├── models/           # 领域模型
│   └── observability/    # 指标、缓存、并发限制、错误规范
├── web/
│   ├── src/api/          # 前端 API 封装与 SSE 消费
│   ├── src/components/   # React 页面组件
│   ├── src/hooks/        # 会话、行程、知识库、自定义鉴权状态
│   └── src/utils/        # 行程数据归一化与调试工具
├── scripts/              # 回放、报告与辅助脚本
├── sql/                  # 数据库初始化脚本
├── searxng/              # SearXNG 相关配置说明
├── requirements.txt      # Python 依赖
└── pyproject.toml        # Python 项目元数据
```

## 开发脚本

后端：

```bash
source venv/bin/activate
PYTHONPATH=. uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload
```

前端：

```bash
cd web
pnpm run dev
pnpm run build
pnpm run preview
```

## 相关文档

- [前端说明](web/README.md)
- [SearXNG 说明](searxng/README.md)
- [SQL 说明](sql/README.md)
- [后端入口](src/api/app.py)
- [主流程路由](src/api/routes/flow.py)
- [知识库路由](src/api/routes/knowledge.py)
- [行程逻辑](src/api/logic/trip.py)
- [LLM 管理](src/llm/llm_manager.py)
- [RAG 主流程](src/rag/rag_main.py)

## 许可证

本项目使用 [MIT License](LICENSE)。
