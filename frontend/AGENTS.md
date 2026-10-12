# 前端指令

适用于 `frontend/`。与根通用规则共同使用。路径均相对仓库根目录。

技术栈：React 18 / TypeScript（strict）/ Zustand / Vite / Tauri v2。通过 REST 获取状态与执行操作，通过 SSE 接收后端事件。

涉及领域契约或文档同步时读取 `docs/AGENTS.md`；跨后端任务同时读取 `nyx/AGENTS.md`。

## 编码规范

#### TypeScript / React

- TypeScript 严格模式（`strict: true`）
- 组件命名 `PascalCase`，文件命名 `camelCase.tsx`
- 所有 API 端点必须有测试
- 全局状态用 Zustand stores（每个系统一个 store）
- SSE 事件流用自定义 hook（`hooks/useSSE.ts`）

## 输入与渲染

用户输入不做 HTML/JS 注入过滤（桌面端无浏览器渲染风险）；LLM 返回内容在 UI 渲染前做基本清理。

## 验证

遵守根通用质量门；检查 TypeScript 严格模式、API 测试、Zustand 状态与 SSE 接线。改测试后同步 `docs/test-inventory.md`。
