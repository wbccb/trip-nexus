// Vite 配置文件，启用 React 插件
import { defineConfig } from "vite" // 引入 Vite 配置定义
import react from "@vitejs/plugin-react" // 引入 React 插件

export default defineConfig({
  plugins: [react()], // 使用 React 插件以支持 JSX
  server: {
    port: 5173, // 前端开发端口
  },
})
