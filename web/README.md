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
- 右栏：地图概览（由后端生成地图 HTML，前端 iframe 渲染）

## 交互逻辑
- 会话抽屉：点击“会话列表”按钮打开抽屉，选择会话后自动关闭并切换当前会话
- 行程输入弹窗：点击“行程输入”打开表单，提交后生成结构化 prompt 并追加到聊天记录
- 行程生成：提交表单会调用行程生成接口，生成结果展示在中栏“行程详情”
- 聊天输入：左栏输入框发送文本后追加用户消息到聊天记录
- 旅行灵感：在“旅行灵感”Tab 中输入问题并检索，结果在中栏列表展示
- 地图渲染：行程生成/切换后调用后端地图渲染接口，返回 HTML 并在右栏展示

## 地图渲染实现

> 在folium中使用OpenStreetMap作为基础（默认）底图，然后加载高德地图（Gaode/Amap）替换 OpenStreetMap 
> 
> 然后python这个库folium会根据你传入的数据（Marker、每个Marker之间的连线等等数据）自动构建出能够显示正确地图样式的html数据，然后在iframe（本质就是一个h5页面）中显示出来这个html数据，这就是python这个库的厉害之处

- 后端渲染模式（两种）
  - 旧模式：`TripMap.render_map`
    - 一次性渲染完整地图（POI 标注 + 每日路线折线 + 多底图切换）
    - 适用于一次性生成静态地图并直接输出 HTML
  - 新模式：`TripMap.render_map_batches`
    - 分批渲染 POI，逐步输出 HTML 片段（事件包含 `sequence/day/is_final`）
    - 适用于前端逐步刷新地图，避免大量 POI 造成卡顿
  - 关键文件：[map_renderer.py](../src/map/map_renderer.py)
  - 默认底图：高德街道/高德卫星 + CartoDB Positron，支持 LayerControl 切换
- 接口：`POST /api/map/render`，请求体包含 `trip_data`，返回 `map_html`
  - 关键文件：[app.py](../src/api/app.py)
- 前端：`App.jsx` 在获取到行程后调用接口，将 `map_html` 写入 iframe 的 `srcDoc` 渲染
  - 关键文件：[App.jsx](src/App.jsx)

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
