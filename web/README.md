# TripNexus Web

## 代码结构
- [App.jsx](src/App.jsx)：三栏布局、聊天逻辑、行程输入弹窗、会话抽屉
- [SessionSider.jsx](src/components/SessionSider.jsx)：会话列表内容，用于抽屉展示
- [TripTab.jsx](src/components/TripTab.jsx)：行程详情展示（已移除行程输入表单）
- [KnowledgeTab.jsx](src/components/KnowledgeTab.jsx)：旅行灵感检索展示
- [useSessions.js](src/hooks/useSessions.js)：会话加载、创建、切换
- [useTrip.js](src/hooks/useTrip.js)：行程生成与结果整理
- [useKnowledge.js](src/hooks/useKnowledge.js)：知识库检索
- [tripUtils.js](src/utils/tripUtils.js)：行程数据归一化
- [appConfig.js](src/constants/appConfig.js)：用户与本地缓存常量

## 布局说明
- 左栏：AI 助手聊天区（历史对话 + 底部悬浮输入框），顶部按钮打开行程输入弹窗与会话列表抽屉
- 中栏：行程详情展示 + 旅行灵感 Tab（行程输入已移除）
- 右栏：地图概览占位

## 交互逻辑
- 会话抽屉：点击“会话列表”按钮打开抽屉，选择会话后自动关闭并切换当前会话
- 行程输入弹窗：点击“行程输入”打开表单，提交后生成结构化 prompt 并追加到聊天记录
- 行程生成：提交表单会调用行程生成接口，生成结果展示在中栏“行程详情”
- 聊天输入：左栏输入框发送文本后追加用户消息到聊天记录
- 旅行灵感：在“旅行灵感”Tab 中输入问题并检索，结果在中栏列表展示

## 核心流程
```text
行程输入弹窗
  -> 生成结构化 prompt
  -> 追加到聊天记录
  -> 调用行程生成接口
  -> 更新中栏行程详情
```

```text
会话抽屉
  -> 打开抽屉
  -> 选择会话
  -> 关闭抽屉并切换会话
```

```text
旅行灵感
  -> handleKnowledgeSearch()
  -> searchKnowledge(query, false) ...
```
