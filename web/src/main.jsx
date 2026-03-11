// React 应用入口文件，挂载根组件
import React from "react" // 引入 React 以支持 JSX
import ReactDOM from "react-dom/client" // 引入 ReactDOM 进行渲染
import { ConfigProvider } from "antd" // 引入 Ant Design 全局配置
import App from "./App.jsx" // 引入主应用组件
import "antd/dist/reset.css" // 引入 Ant Design 样式重置
import "maplibre-gl/dist/maplibre-gl.css"
import "./styles.css" // 引入自定义样式

// 获取根节点并创建 React 根实例
const rootElement = document.getElementById("root")
const root = ReactDOM.createRoot(rootElement)

// 渲染应用
root.render(
  <ConfigProvider>
    <App />
  </ConfigProvider>
)
