import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 端口固定 5173（src-tauri/tauri.conf.json 的 devUrl 依赖）；后端跑 localhost:8000
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    // /api 同源转发到后端：前端请求相对路径 /api/*，Vite 转发到 8000，
    // 浏览器视角同源（5173），后端无需 CORS（18-api「不做 CORS（localhost 同源）」）
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./tests/setup.ts",
  },
});
