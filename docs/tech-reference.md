# Nyx Agent 实现索引

> 本文件只用于快速定位源码，不定义契约，不复制完整签名、DDL、事件语义或测试断言。
> 业务契约以 `docs/specs/` 中对应的唯一完整 spec 为准；当前实现以 `nyx/` 源码为准。
> 无关代码需要快速了解模块边界时，优先查对应的 `docs/facts/*-system-facts.md`。
> 合并多个旧 spec 后保留该领域原主编号；旧子 spec 编号不再作为引用使用。

## 契约入口

### 底层建设阅读顺序

底层契约按依赖方向阅读：`01-types` 定义共享类型，`02-config` 定义配置，`03-llm` 定义 LLM 客户端，`04-module-bus-system` 定义数据库、事务、事件总线、订阅、组合根和生命周期。后续业务 spec 依赖这些契约；这些底层 spec 不反向依赖业务 spec。

| 领域 | 完整契约 | 快速事实摘要 | 主要源码 |
|---|---|---|---|
| 类型 | [`01-types.md`](specs/01-types.md) | — | `nyx/types.py`, `nyx/enums.py` |
| 配置 | [`02-config.md`](specs/02-config.md) | — | `nyx/config.py` |
| LLM | [`03-llm.md`](specs/03-llm.md) | — | `nyx/llm/` |
| 模块与事件总线 | [`04-module-bus-system.md`](specs/04-module-bus-system.md) | [`module-bus-system-facts.md`](facts/module-bus-system-facts.md) | `nyx/db.py`, `nyx/events/`, `nyx/runtime.py` |
| 工具 | [`05-tools.md`](specs/05-tools.md) | — | `nyx/tools/` |
| 记忆 | [`06-memory-system.md`](specs/06-memory-system.md) | [`memory-system-facts.md`](facts/memory-system-facts.md) | `nyx/memory/` |
| 欲望 | [`07-desire.md`](specs/07-desire.md) | [`desire-system-facts.md`](facts/desire-system-facts.md) | `nyx/desire/` |
| 内在生命（含审美维度） | [`08-inner-life.md`](specs/08-inner-life.md) | [`inner-life-system-facts.md`](facts/inner-life-system-facts.md) | `nyx/inner_life/`、`nyx/types.py`、`nyx/db.py`、`nyx/memory/`、`nyx/expression/prompt.py`、`nyx/main.py` |
| 活动（含委派任务与统一选材） | [`09-activity.md`](specs/09-activity.md) | [`activity-system-facts.md`](facts/activity-system-facts.md) | `nyx/activity/`, `frontend/src/components/panels/ActivityPanel.tsx` |
| 评估 | [`10-eval.md`](specs/10-eval.md) | — | `nyx/eval/` |
| 表达 | [`11-expression.md`](specs/11-expression.md) | [`expression-system-facts.md`](facts/expression-system-facts.md) | `nyx/expression/`、`nyx/reading/companions.py`、`nyx/prompts/knowledge-boundary.md` |
| 阅读 | [`12-reading-system.md`](specs/12-reading-system.md)；表达侧读书交互归 11 | [`reading-system-facts.md`](facts/reading-system-facts.md) | `nyx/reading/` |
| 桌宠窗口与轻量入口 | [`13-desktop-pet.md`](specs/13-desktop-pet.md) | — | `frontend/src/components/desktop/`, `frontend/src/lib/desktopWindow.ts`, `frontend/src-tauri/tauri.conf.json` |
| 角色卡与多角色运行 | [`14-character-cards.md`](specs/14-character-cards.md) | [`character-cards-facts.md`](facts/character-cards-facts.md) | `nyx/character_cards.py`, `nyx/cards/nyx/`, `nyx/app_context.py`, `nyx/runtime.py` |
| 讨论伙伴 | [`15-discussion-partner.md`](specs/15-discussion-partner.md) | [`expression-system-facts.md`](facts/expression-system-facts.md)、[`memory-system-facts.md`](facts/memory-system-facts.md) | `nyx/prompts/skills/`, `nyx/bootstrap.py`, `nyx/app_context.py`, `nyx/research/`, `nyx/expression/`, `nyx/memory/`, `nyx/api/routes.py`, `frontend/src/components/chat/{ChatInput,KnowledgeFeed}.tsx` |
| 工具调用观测 | [`17-tool-call-observation.md`](specs/17-tool-call-observation.md) | — | `nyx/tools/{registry,tool_call_store}.py`, `nyx/db.py` |
| 规划与执行 | [`18-plan-and-execute.md`](specs/18-plan-and-execute.md) | [`expression-system-facts.md`](facts/expression-system-facts.md) | `nyx/types.py`（Plan/PlanStep）, `nyx/expression/{pipeline,tool_pipeline,prompt}.py` |
| 失败经验 | [`19-failure-reflection.md`](specs/19-failure-reflection.md) | [`memory-system-facts.md`](facts/memory-system-facts.md) | `nyx/enums.py`（LESSON）, `nyx/memory/{facade,store}.py`, `nyx/eval/evaluator.py`, `nyx/app_context.py` |

讨论伙伴 P1/P2 已完成；P2 性能修复第 3 步已按授权提交为 `34d201f`，P3 深度研究功能验收完成，未提交、待用户阶段审批。
验收证据及质量门限制见 [阶段记录](specs/15-discussion-partner.md) 与 [测试快照](test-inventory.md)。
P3 研究编排与工具构造见 `nyx/research/__init__.py`，装配见 `nyx/app_context.py`。
P4 批注输入/锚点校验见 `nyx/api/routes.py`；prompt 传递见 `nyx/expression/{facade,pipeline,prompt}.py`；
schema46 表与双 durable 路由见 `nyx/db.py`、`nyx/events/routing.py`、`nyx/subscriptions.py`。
反馈录入/中介见 `nyx/memory/{store,facade}.py`，纠错反思见 `nyx/inner_life/`；
批注选择、编辑、回执与角色代数保护见 `frontend/src/components/chat/{MessageBubble,ChatInput}.tsx`
和 `frontend/src/stores/chatStore.ts`，完整契约见 [讨论伙伴](specs/15-discussion-partner.md)。

P2 性能修复的增量证据索引与同块事实批量抽取见 `nyx/memory/{ann,store,facade}.py` 和
[记忆契约](specs/06-memory-system.md)；网页任务正文快照（schema 45）见
`nyx/db.py`、`nyx/activity/{store,facade}.py` 与 [活动契约](specs/09-activity.md)。

### 时间感知与离开归来导航

- 运行时快照、归来领取和 SSE 公共头见 [04](specs/04-module-bus-system.md)；presence 与采样见 [09](specs/09-activity.md)；时间 prompt、对话锚点及隔夜验收见 [11](specs/11-expression.md)。
- 前端时间函数与消息标签见 [聊天面板](frontend/03-chat-panel.md)；共享时钟、昼夜视觉及头像输入见 [App 布局](frontend/08-reading-chat-layout.md)；线协议与状态保留分别见 [SSE](frontend/01-sse.md)、[stores](frontend/02-stores.md)。

## 运行时地图

| 职责 | 入口 |
|---|---|
| 组合根与 Facade 装配 | `nyx/app_context.py` |
| 角色卡校验与一次初始化 | `nyx/character_cards.py`, `nyx/bootstrap.py` |
| 应用启动、seed、REST/SSE 绑定 | `nyx/main.py`, `nyx/api/routes.py` |
| tick、总线监督、视觉循环 | `nyx/runtime.py` |
| 事件订阅注册 | `nyx/subscriptions.py` |
| SQLite 连接、迁移、事务 | `nyx/db.py` |
| 事件持久化、投递、重试、SSE sink | `nyx/events/bus.py` |
| 路由声明与派生视图 | `nyx/events/routing.py` |
| 内部事件构造 | `nyx/events/event.py` |

## Facade 入口

| Facade | 源码 | 负责范围 |
|---|---|---|
| `MemoryFacade` | `nyx/memory/facade.py` | 记忆写入、召回、联想和回忆升级 |
| `DesireFacade` | `nyx/desire/facade.py` | 欲望入口、全量/待消费查询和生命周期接线 |
| `InnerLifeFacade` | `nyx/inner_life/facade.py` | 情感、精力、慢变量和反思接线 |
| `ActivityFacade` | `nyx/activity/facade.py` | 排期、活动启动/执行/完成/打断 |
| `ExpressionFacade` | `nyx/expression/facade.py`、`nyx/expression/store.py` | 快慢通道回复、durable 提问/搭话 attempt、主动搭话和碎碎念 |
| `ReadingFacade` | `nyx/reading/facade.py` | 书籍、进度、陪读冲动和笔记 |

## 前端地图

| 职责 | 入口 |
|---|---|
| REST 请求 | `frontend/src/api/client.ts` |
| 角色切换、请求 scope 与管理入口 | `frontend/src/api/characterScope.ts`, `frontend/src/stores/characterStore.ts`, `frontend/src/components/layout/CharacterSelect.tsx`, `CharacterManager.tsx` |
| SSE 连接与事件分发 | `frontend/src/hooks/useSSE.ts` |
| 桌面 presence 采集 | `frontend/src/hooks/usePresence.ts`, `frontend/src-tauri/src/lib.rs` |
| API 类型 | `frontend/src/types/api.ts` |
| Zustand 状态 | `frontend/src/stores/` |
| 页面与面板 | `frontend/src/components/` |
| 枚举展示文本 | `frontend/src/lib/labels.ts` |
| 时间标签、分钟时钟与昼夜视觉 | `frontend/src/lib/time.ts`, `frontend/src/App.tsx`, `frontend/src/index.css` |
| 桌宠/完整端窗口与入口 | `frontend/src/components/desktop/PetShell.tsx`, `frontend/src/lib/desktopWindow.ts`, `frontend/src-tauri/tauri.conf.json` |

## 数据与测试

- 数据库文件默认由 `nyx/db.py` 决定；表结构、迁移和关闭语义见 `04-module-bus-system.md`。
- schema47/48 增加 `tool_call_log`/`lesson_signal` 两表，schema49 增加 `checkpoints`/`writes`，schema50 增加临时 `tool_execution`，当前共43张业务及基础设施表（SQLite表总数46）。
  工具观测和经验阈值分别见17/19；跨失败主题去重边界及提交后学习语义也由19定义。
- schema 35 的 `memory_recall_use` 只为慢通道真实召回提供事件级幂等；对象生命周期与删除语义
  见 `06-memory-system.md`，不作为公开 API 对象。
- schema 42 的 `conversation_segment_recall_use` 独立记录新版段真实入选；段的新鲜度衰减、活跃索引淘汰和事实来源由 `nyx/memory/` 管理，运行时使用独立记忆计时器。
- 后端测试入口为 `tests/`；按系统分目录，覆盖索引见 [`test-inventory.md`](test-inventory.md)。
- 前端测试入口为 `frontend/tests/`，运行 `npm test`；构建检查运行 `npm run build`。
- 发现索引与源码不一致时，以源码和对应完整 spec 为准，并在同一变更中修正索引。
