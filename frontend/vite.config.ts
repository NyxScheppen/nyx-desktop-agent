import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 端口固定 5173（src-tauri/tauri.conf.json 的 devUrl 依赖）；后端跑 localhost:8000
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./tests/setup.ts",
  },
});
