# 前端与桌面端基本事实

> 记录职责与关键边界；组件结构、store 字段、事件映射和窗口参数查源码。

## 职责与行为

- React/TypeScript 界面通过 REST 获取快照与执行操作，通过 SSE 接收实时事件；Zustand stores 保存当前展示状态。
- 角色管理读取已安装卡包的初始设定，当前成长状态在内在状态页展示；新建角色使用独立页面，按主题填写 v2 内在文档。创作、内在状态与历史按页面可用高度排版翻页。
- SSE 先回放连接时已提交的有限历史，再发送回放完成控制帧并进入实时流。历史聊天正常恢复，历史通知不冒气泡；回放完成后统一同步快照，同模块刷新合并在途请求并补刷最后状态。记忆展示接口不返回向量，列表查询也不读取向量。
- 浏览器生产端访问同源 API；Tauri 桌面端使用本机后端。认证下 REST、SSE 和角色图片均携带凭证，图片通过认证读取后展示。
- 角色切换会隔离旧请求、事件、计时器和回包；A→B→A 也不能复用旧代数响应。各角色的草稿和最近书籍分开保存。
- 聊天发送的 HTTP 锁与等待回复分开；已受理消息等待期间可继续补充，软超时不会自动重发或禁止继续输入。
- 终局按覆盖的输入结束等待，不能让迟到 POST 回包复活已完成项；历史、pending 快照和 SSE 共同恢复页面状态。
- 阅读器提供书架、正文、进度、划线、书签与笔记；这些状态归同一阅读 store 管理，读书提问仍接入统一聊天等待。
- 完整界面与透明桌宠是不同窗口模式，入口、布局和原生窗口行为需一起验证。
- 启动器与打包桌面端只管理自己的后端，启动时验证实例身份；退出时清理其持有资源。

## 源码入口

- `frontend/src/App.tsx`、`components/`、`stores/`：页面与状态。
- `frontend/src/api/client.ts`、`api/characterScope.ts`、`hooks/useSSE.ts`：请求、角色归属与事件。
- `frontend/src/stores/chatStore.ts`、`readerStore.ts`：聊天和阅读。
- `frontend/src/components/desktop/`、`lib/desktopWindow.ts`、`frontend/src-tauri/src/lib.rs`：桌宠及原生入口。
- `frontend/src/lib/time.ts`、`frontend/src/index.css`：时间显示与公共样式；`dev.py`：开发启动。
