# Nyx Agent 实现索引

> 本文件只用于快速定位源码，不定义契约，不复制完整签名、DDL、事件语义或测试断言。
> 业务契约以 `docs/specs/` 中对应的唯一完整 spec 为准；当前实现以 `nyx/` 源码为准。
> 无关代码需要快速了解模块边界时，优先查对应的 `docs/*-system-facts.md`。

## 契约入口

| 领域 | 完整契约 | 快速事实摘要 | 主要源码 |
|---|---|---|---|
| 类型 | [`01-types.md`](specs/01-types.md) | — | `nyx/types.py`, `nyx/enums.py` |
| 配置 | [`02-config.md`](specs/02-config.md) | — | `nyx/config.py` |
| LLM | [`03-llm.md`](specs/03-llm.md) | — | `nyx/llm/` |
| 模块与事件总线 | [`05-module-bus-system.md`](specs/05-module-bus-system.md) | [`module-bus-system-facts.md`](module-bus-system-facts.md) | `nyx/db.py`, `nyx/events/`, `nyx/runtime.py` |
| 记忆 | [`07-memory-system.md`](specs/07-memory-system.md) | [`memory-system-facts.md`](memory-system-facts.md) | `nyx/memory/` |
| 欲望 | [`11-desire.md`](specs/11-desire.md) | — | `nyx/desire/` |
| 内在生命（含审美维度） | [`12-inner-life.md`](specs/12-inner-life.md) | — | `nyx/inner_life/`、`nyx/types.py`、`nyx/db.py`、`nyx/memory/`、`nyx/expression/prompt.py`、`nyx/main.py` |
| 活动 | [`14-activity.md`](specs/14-activity.md) | [`activity-system-facts.md`](activity-system-facts.md) | `nyx/activity/` |
| 表达 | [`16-expression-prompt.md`](specs/16-expression-prompt.md)、[`17-expression.md`](specs/17-expression.md) | — | `nyx/expression/` |
| 阅读 | [`19-reading-content.md`](specs/19-reading-content.md) 至 [`24-reading-chat-turn.md`](specs/24-reading-chat-turn.md) | — | `nyx/reading/` |
| 工具 | [`06-tools.md`](specs/06-tools.md) | — | `nyx/tools/` |
| 评估 | [`15-eval.md`](specs/15-eval.md) | — | `nyx/eval/` |

## 运行时地图

| 职责 | 入口 |
|---|---|
| 组合根与 Facade 装配 | `nyx/app_context.py` |
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
| `ExpressionFacade` | `nyx/expression/facade.py` | 快慢通道回复、搭话和碎碎念 |
| `ReadingFacade` | `nyx/reading/facade.py` | 书籍、进度、陪读冲动和笔记 |

## 前端地图

| 职责 | 入口 |
|---|---|
| REST 请求 | `frontend/src/api/client.ts` |
| SSE 连接与事件分发 | `frontend/src/hooks/useSSE.ts` |
| API 类型 | `frontend/src/types/api.ts` |
| Zustand 状态 | `frontend/src/stores/` |
| 页面与面板 | `frontend/src/components/` |
| 枚举展示文本 | `frontend/src/lib/labels.ts` |

## 数据与测试

- 数据库文件默认由 `nyx/db.py` 决定；表结构、迁移和关闭语义见 `05-module-bus-system.md`。
- 后端测试入口为 `tests/`；按系统分目录，覆盖索引见 [`test-inventory.md`](test-inventory.md)。
- 前端测试入口为 `frontend/tests/`，运行 `npm test`；构建检查运行 `npm run build`。
- 发现索引与源码不一致时，以源码和对应完整 spec 为准，并在同一变更中修正索引。
