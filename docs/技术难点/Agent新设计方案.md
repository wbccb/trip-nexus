# Agent 新设计方案（Plan-and-Execute 动态编排）

## 背景与问题
- 节点驱动的固定流程很难覆盖多样的用户诉求
- 工具调用与行程生成被强耦合，任务编排不够灵活
- 用户希望“先意图识别、再由大模型决定任务与顺序”，而不是固定节点链路

## 新设计目标
- **任务驱动**：节点流转围绕任务列表，而不是围绕固定节点
- **动态规划**：支持 Plan-and-Execute 模式，并具备运行时 Re-planning 能力
- **工具内聚**：任务可以是工具调用、行程生成、行程检验、总结输出、地图渲染等
- **可解释与可干预**：任务计划可被 UI 展示、回放，甚至在执行前被用户修正
- **数据流清晰**：明确任务间的数据传递协议，避免 Context 污染

## 核心概念

### 任务（Task）
- 每个任务是一个原子动作
- 任务类型示例
  - `tool_call`：调用天气、POI、地理编码等工具
  - `trip_generate`：生成行程草案
  - `trip_validate`：校验预算/密度/天气冲突
  - `trip_summarize`：生成行程总结
  - `map_render`：渲染地图

### 任务计划（Plan）
- 由 LLM 输出的结构化任务序列
- 包含任务类型、输入参数、依赖关系、**数据提取规则**
- 支持 SOP（标准作业程序）模板与 LLM 动态生成的混合模式
 - 计划合法性校验：任务类型/工具存在性/依赖闭环/输入映射引用都必须可解析
 - 反幻觉校验：计划中出现未知工具或非法参数时直接拒绝或回退到 SOP

### 共享上下文（Shared Context）
- 解决任务间数据传递的核心机制
- **结构设计**：强制采用 `Task ID` 作为一级命名空间，避免并发写入冲突
  - 格式：`ctx[task_id][output_key] = value`
- **数据清洗**：存储工具执行后的关键事实（Facts），而非原始冗余 JSON
- **引用机制**：
  - **显式引用**：通过 `input_mapping` 指定源任务 ID 与 Key（推荐）
  - **隐式注入**：特定任务（如 Generate）可配置自动读取所有前序 `weather` 类型的数据
- **版本与审计**：每次写入生成 revision，支持回滚

### 依赖图（DAG）
- 任务间通过依赖关系形成有向无环图
- 支持无依赖任务的**并发执行**（如同时查天气和 POI）
 - 调度控制：并发上限、速率限制、全局预算（token/时间/工具调用次数）

## 新流程总览（Plan-and-Execute Loop）

1. **意图识别 (Intent Parsing)**：用户输入规范化
2. **任务规划 (Planning)**：
   - 基于意图匹配 SOP 模板或由 LLM 生成新计划
   - 生成初步 DAG
3. **[可选] 计划审查 (Plan Review)**：
   - 用户可查看并修改计划（增删改任务、调整参数）
4. **执行循环 (Execution Loop)**：
   - **调度 (Scheduler)**：分析 DAG，提取就绪任务（Ready Tasks）
   - **并发执行 (Execution)**：并行执行无依赖任务，工具调用/模型生成
   - **结果提取 (Extraction)**：从 Raw Result 提取关键信息写入 Shared Context
   - **反思与重规划 (Reflect & Re-plan)**：
     - 检查任务结果是否符合预期（如：POI 搜索结果为空？）
     - 若异常，触发 Re-planning（如：插入新搜索任务，或调整后续参数）
5. **结果聚合 (Aggregation)**：生成最终行程、地图与总结

## 任务规划逻辑（LLM Prompt）

### 输入
- `user_intent`：用户意图
- `user_input`：目的地/天数/预算/偏好等
- `tool_registry`：工具清单与参数说明
- `sop_templates`：可选的标准流程模板（针对标准行程生成场景）

### 输出（示意）
```json
{
  "tasks": [
    {
      "id": "t1",
      "type": "tool_call",
      "tool": "weather.get_daily",
      "params": {"city": "东京"},
      "output_key": "weather_info", 
      "description": "查询东京未来天气"
    },
    {
      "id": "t2",
      "type": "tool_call",
      "tool": "poi.search",
      "params": {"query": "美食", "city": "东京", "top_k": 5},
      "output_key": "poi_candidates",
      "description": "搜索东京热门美食"
    },
    {
      "id": "t3",
      "type": "trip_generate",
      "dependencies": ["t1", "t2"],
      "input_mapping": {
        "weather_context": "t1.weather_info", 
        "poi_context": "t2.poi_candidates"
        // 💡 优化注：LLM 仅需生成源 TaskID，具体字段提取逻辑可由代码自动处理，避免 LLM 幻觉生成错误的 JSON Path
      },
      "output_key": "draft_trip",
      "description": "基于天气和POI生成行程草案"
    },
    {
      "id": "t4",
      "type": "trip_validate",
      "dependencies": ["t3"],
      "input_mapping": {
        "trip": "t3.draft_trip"
      },
      "output_key": "validation_result"
    },
    {
      "id": "t5",
      "type": "trip_adjust",
      "dependencies": ["t4"],
      "condition": "t4.passed == false", 
      "input_mapping": {
        "trip": "t3.draft_trip",
        "issues": "t4.validation_result"
      },
      "output_key": "final_trip"
    }
  ]
}
```

## 节点与职责

### 1) intent_parse
- **目标**：意图识别与输入规范化
- **输出**：`normalized_user_input` + `user_intent`

### 2) task_plan (Planner)
- **目标**：生成初始 DAG
- **策略**：**混合规划 (Hybrid Planning)**
  - 优先匹配 SOP 模板（保证标准场景稳定性）
    - **参数化 SOP**：SOP 不仅是静态列表，支持 LLM 仅填空参数（如 `city`），结构保持不变
  - 无法匹配时 fallback 到 LLM 动态生成
- **输出**：`task_plan` (Task List + Dependencies)

### 3) task_scheduler (Executor)
- **目标**：管理 DAG 执行生命周期
- **逻辑**：
  - 维护 `ready_queue`
  - **并发执行**：使用 `asyncio` 并行运行无依赖的工具任务
  - **上下文注入**：根据 `input_mapping` 从 `shared_context` 组装任务输入
  - **结果回写**：执行后将结果清洗并更新至 `shared_context`
  - **预算与节流**：执行前检查全局预算与速率阈值，超限则降级或中止
  - **任务摘要**：每个任务完成后生成可读摘要，用于 UI 追踪与审计

### 4) task_reflect (Reflector) - *新增*
- **目标**：运行时质量控制与动态调整
- **逻辑**：
  - 监控关键任务结果（如：搜索结果是否为空？生成是否报错？）
  - 决策：继续 / 重试 / 修改后续计划 (Re-plan) / 终止报错
  - 停止条件：达到最大重试/重规划深度或预算耗尽时强制终止
  - 终止信号：支持用户或系统发出 cancel/abort 触发链路停止

### 5) task_aggregate
- **目标**：聚合最终产物
- **输出**：`final_payload`（trip + validation + summary + map）
 - 失败降级：当计划失败时输出可解释错误与已完成任务结果

## 推荐状态结构（TripState）

```python
class TripState(TypedDict):
    # 原始输入
    user_intent: str
    user_input: dict
    
    # 规划层
    plan: List[Task]           # 当前的任务计划列表
    plan_history: List[List[Task]] # 计划变更历史（用于 Re-planning 回溯）
    
    # 执行层
    execution_queue: List[str] # 待执行任务 ID 队列
    completed_tasks: Set[str]  # 已完成任务 ID
    failed_tasks: Set[str]     # 失败任务 ID
    task_summaries: Dict[str, str] # 任务摘要
    
    # 数据层
    shared_context: Dict[str, Any] # 核心：任务间共享的数据黑板 (Key-Value)
    task_results: Dict[str, Any]   # 原始执行结果（用于审计/调试）
    context_revisions: List[Dict]  # 共享上下文写入版本记录
    
    # 最终产物
    final_payload: Dict
    
    # 系统状态
    status: str
    error: Optional[str]
    stop_reason: Optional[str]
```

## 风险与应对 (Gap Analysis)

| 风险点 | 描述 | 应对策略 |
| :--- | :--- | :--- |
| **Context 爆炸** | 多个工具返回大量 JSON 撑爆 LLM 窗口 | 引入 **Data Mapper** 层，工具执行后只提取摘要写入 `shared_context` |
| **死循环** | Re-planning 反复触发，无法收敛 | 设置最大 `retry_limit` 和 `replan_depth`，超时强制降级或报错 |
| **幻觉规划** | LLM 生成不存在的工具或错误的依赖 | 使用 **SOP 模板** 覆盖 80% 场景，动态规划仅作为 fallback |
| **并发冲突** | 多个任务同时写 Shared Context | 强制使用 `Task ID` 命名空间隔离；只有 DAG 拓扑序后的任务才能读前序结果 |
| **计划幻觉/非法依赖** | LLM 输出不存在的工具或环形依赖 | 计划合法性校验 + 工具白名单 + 依赖闭环检测 |
| **UI 交互限制** | Streamlit 不支持复杂的交互式 DAG 编辑 | **MVP 阶段简化**：仅提供只读的计划展示或简单的“删除任务”功能，避免过度工程化 |
| **预算失控** | 任务过多或重试导致资源爆炸 | 全局预算与任务级预算，超过阈值直接降级或终止 |
| **不可观测** | 计划与执行不一致难追踪 | DAG 可视化 + 任务摘要 + trace/span 级追踪 |
| **安全风险** | 工具参数越权或敏感信息泄露 | 参数校验 + 敏感字段过滤 + 允许/禁止工具清单 |

## 与现有实现的对齐映射
- `intent_parse`：复用 `analyze_user_message`
- `task_plan`：**升级**为 Planner Agent，集成 SOP 库
- `task_scheduler`：**重构** `AgentOrchestrator`，从线性流转改为 DAG 调度
- `task_execute`：复用 `LlmManager.call_tool` 等底层能力，但由 Scheduler 统一调用
- `task_aggregate`：复用 `TripMap.render_map`



## 开发步骤与里程碑

### Phase 1: 基础设施 (Infrastructure)
1. **定义 Schema**
   - 定义 `Task`, `Plan`, `SharedContext`, `TripState` 的 Pydantic 模型
   - 确定任务间数据传递的协议（Key-Value 约定）
   - 增加计划合法性校验与工具白名单校验
2. **实现 DAG Scheduler**
   - 实现一个纯逻辑的 `Scheduler` 类
   - 输入：Task List + Dependencies
   - 输出：可并发执行的 `Ready Tasks` 迭代器
   - 单元测试：验证拓扑排序与并发分组逻辑
   - 预算与并发控制：速率限制、并发上限、全局预算
3. **改造 ToolRegistry**
   - 统一工具的输入输出接口，使其支持从 SharedContext 注入参数
   - 工具参数校验与敏感字段过滤

### Phase 2: 核心链路 (Core Pipeline)
4. **实现 Executor 循环**
   - 编写 `run_agent_loop` 主循环
   - 集成 `Scheduler` 与 `asyncio` 并发执行工具
   - 实现 `Result Extraction`：将工具原始结果清洗后写入 SharedContext
   - 任务级错误码与重试策略
5. **实现 Planner Agent**
   - 编写 `Prompt` 让 LLM 输出符合 Schema 的 JSON 计划
   - 实现 SOP 模板加载器（支持硬编码的标准行程模板）
   - 联调：Intent -> Planner -> Scheduler -> Execution -> Context
   - 计划反幻觉校验与回退策略

### Phase 3: 增强能力 (Advanced Features)
6. **实现 Reflector (反思器)**
   - 添加检查逻辑：当工具结果为空或校验失败时，中断执行
   - 实现简单的 Re-planning：将错误反馈给 Planner 并请求新计划
   - 明确停止条件与终止信号
7. **UI 适配**
   - 改造前端 State 面板，支持展示 DAG 结构与执行进度
   - 实现“计划确认”弹窗（Human-in-the-loop）
   - 展示任务摘要与 trace 追踪信息
8. **集成与迁移**
   - 将现有 `generate_trip`, `render_map` 封装为标准 Task
   - 替换旧的 Graph Pipeline 入口
   - 增加离线评估与线上 A/B 监控入口
