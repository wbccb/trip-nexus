# TripNexus Web

## 代码结构
- [App.jsx](src/App.jsx)：组合布局与 Tab 容器
- [SessionSider.jsx](src/components/SessionSider.jsx)：会话列表与切换
- [TripTab.jsx](src/components/TripTab.jsx)：行程输入与结果展示
- [KnowledgeTab.jsx](src/components/KnowledgeTab.jsx)：检索输入与结果展示
- [useSessions.js](src/hooks/useSessions.js)：会话加载、创建、切换
- [useTrip.js](src/hooks/useTrip.js)：行程生成与结果整理
- [useKnowledge.js](src/hooks/useKnowledge.js)：知识库检索
- [tripUtils.js](src/utils/tripUtils.js)：行程数据归一化
- [appConfig.js](src/constants/appConfig.js)：用户与本地缓存常量

## 逻辑说明
- 会话流：启动时加载会话列表与本地缓存会话，创建会话后刷新列表，切换会话写入本地缓存
- 行程流：提交表单后拼装 payload 调用行程生成接口，更新会话与行程结果并刷新会话列表
- 检索流：输入问题后触发检索接口，更新结果并展示证据摘要

## 核心流程
```text
useSessions()
  -> loadSessions()
  -> ensureSession()
  -> startNewSession() ...
```

```text
useTrip()
  -> handleTripSubmit(values)
  -> generateTrip(payload) ...
```

```text
useKnowledge()
  -> handleKnowledgeSearch()
  -> searchKnowledge(query, false) ...
```
