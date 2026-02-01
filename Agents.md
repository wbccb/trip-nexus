# 目录文件逻辑概述

## 项目目标与能力

- v0.0.1：最小化 MVP，基于 Streamlit + Folium + LLM + RAG 生成行程与地图联动
- v0.0.2：多轮对话式更新行程、RAG 爬虫稳定性、LLM 双模式、本地/云端切换、地图多图层与 POI 优化

## 主要运行流程

1. 前端 UI 收集用户输入或聊天消息
2. LLM 进行意图识别与参数抽取
3. 对话上下文更新与短/长期记忆维护
4. 触发行程生成或修改
5. 地图渲染行程 POI 与路线
6. 会话与行程数据持久化
7. RAG 搜索链路在需要时进行检索增强

## 模块与文件逻辑（排除 __init__.py）

### 配置与基础能力

- src/config.py
  - 统一加载环境变量配置
  - 搜索、向量存储、LLM、Redis/MySQL、业务参数集中管理

- src/utils/console.py
  - Streamlit 内嵌 console.log 便捷调试输出
  - 支持 Pydantic 或普通对象的序列化

### LLM 管理与行程生成

- src/llm/llm_manager.py
  - LlmManager 负责初始化与切换 Ollama / OpenAI 兼容模型
  - build_prompt 构建严格 JSON 结构的行程生成提示词
  - analyze_user_message：实体抽取 + 意图识别
  - generate_trip：调用模型生成结构化行程并解析
  - change_trip / _handle_trip_generation / _handle_trip_modification：统一处理生成与修改逻辑
  - extract_json_from_string：清洗 LLM 输出并解析 JSON

### 对话上下文管理

- src/frontend/context/entity.py
  - Message / CoreEntity / SessionContext 数据结构定义
  - tiktoken 作为 token 计数基础

- src/frontend/context/conversation_manager.py
  - 对话生命周期管理：冗余检测、实体抽取和实体合并、摘要压缩
  - process_new_message：处理新消息，更新上下文的核心方法，主要是各种方法的调用，包括
    - 根据session_id获取当前短期会话列表数据：短期缓存未命中（Redis Miss），尝试从持久化存储（DB）恢复
    - 从用户消息中提取核心实体
    - 添加新消息到短期窗口
    - 检查是否需要压缩早期对话
    - 更新短期存储
    - 存储数据到数据库中
  - optimize_context_for_llm：基于 token 预算构建 LLM 上下文，包括摘要融合、构建基础上下文、按照优先级：核心实体 > 最近3轮 > 长期摘要 > 早期对话、添加最近3轮对话（最高优先级）、动态截断检查

### 存储层（测试 / 生产）

- src/frontend/context/storage/base_storage.py
  - 会话存储抽象接口，定义 Redis + DB 所需行为

- src/frontend/context/storage/prod_storage.py
  - 生产实现：Redis + MySQL
  - 存储短期上下文、核心实体、摘要与行程数据

- src/frontend/context/storage/test_storage.py
  - 测试实现：内存字典 + SQLite
  - 提供与生产一致的接口行为

- src/frontend/context/storage/date_time_encoder.py
  - JSON 序列化支持 datetime/date/timedelta

### 前端 UI（Streamlit）

- src/frontend/ui_manager.py
  - Streamlit 页面与交互入口
  - render_input_form：表单生成行程
  - render_chat_interface：多轮对话与消息流
  - render_trip_result / _format_trip_as_markdown：展示结构化行程
  - render_map_panel / 右侧悬浮地图布局
  - render_session_list：会话列表、切换与删除
  - render_llm_settings：模型配置与动态切换

### 地图渲染

- src/map/map_renderer.py
  - TripMap 负责 POI 坐标解析与地图渲染
  - 支持高德街道/卫星 + CartoDB 图层
  - POI 按天配色，支持线路折线与 fit_bounds
  - 地理编码失败时回退到城市中心坐标

### RAG 检索链路

- src/rag/rag_main.py
  - AIRetrievalPipeline 端到端流程：
    - 用户输入进行意图识别 -> 多源搜索（网络检索数据） -> 质量重排 -> 内容抓取 -> 向量检索 -> LLM 回答
  - 无需搜索时走直接回答

> 下面的几个文件都是上面rag_main.py的具体实现

- src/rag/module/intent_recognition.py
  - Sentence-BERT 进行意图分类
  - 低置信度时使用 LLM 二次识别

- src/rag/module/quality_filter.py
  - CrossEncoder 进行结果重排
  - 支持去重与 Top K 截断

- src/rag/network/multi_source_search.py
  - SearXNG 聚合搜索
  - 多实例 fallback + DuckDuckGo HTML 备选

- src/rag/network/crawler.py
  - ThreadPool 并发抓取网页正文
  - 解析 HTML 清洗内容并返回结构化文本

- src/rag/store/vector_store.py
  - Chroma + HuggingFaceEmbeddings
  - 文本分块、向量入库、相似度检索

## 关键数据结构与交互

- 行程数据：destination/days/daily_plan（按天分组的行程项）
- 消息上下文：short_term_messages + long_term_summary + core_entities
- 会话存储：Redis 2 小时短期缓存 + DB 长期持久化
- RAG 证据：搜索摘要 -> 抓取正文 -> 分块检索
