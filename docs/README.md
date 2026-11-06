# 文档入口

具体实现以当前源码为准。这里只维护操作方法、简要系统事实和项目基本设计思路。

## 按任务查阅

| 需要做什么 | 入口 |
|---|---|
| 修改代码、选择验证方式 | [开发约定](development.md) |
| 安装、启动、构建、配置、角色卡、QQ、数据库迁移 | [使用与本地操作](operations.md) |
| 同步 Railway、生产验收、回退与备份 | [线上操作](deployment.md) |
| 路径验证、真实页面与模型行为评测 | [评测方法与历史记录](eval/README.md) |
| 了解产品目标与架构取舍 | [基本设计思路](design/design.md)（不作为系统事实参考） |
| 了解 Nyx 的静态角色设定 | [角色设定](canon.md) |
| 查未完成事项或可复用教训 | [待办](TODO.md)、[经验教训](LessonsLearned.md) |

## 系统基本事实

| 系统 | 摘要 | 主要源码 |
|---|---|---|
| 角色与宿主 | [角色卡](facts/character-cards-facts.md) | `nyx/character_cards.py`、`nyx/app_context.py` |
| 事件与运行时 | [模块总线](facts/module-bus-system-facts.md) | `nyx/events/`、`nyx/runtime.py`、`nyx/subscriptions.py` |
| 记忆 | [记忆系统](facts/memory-system-facts.md) | `nyx/memory/` |
| 欲望 | [欲望系统](facts/desire-system-facts.md) | `nyx/desire/` |
| 内在生命 | [内在生命](facts/inner-life-system-facts.md) | `nyx/inner_life/` |
| 活动 | [活动系统](facts/activity-system-facts.md) | `nyx/activity/` |
| 表达 | [表达系统](facts/expression-system-facts.md) | `nyx/expression/` |
| 阅读 | [阅读系统](facts/reading-system-facts.md) | `nyx/reading/` |
| 配置、存储、模型、工具、评估、传输与装配 | [基础设施](facts/infrastructure-facts.md) | `nyx/config.py`、`nyx/db.py`、`nyx/llm/`、`nyx/tools/`、`nyx/eval/`、`nyx/transports/`、`nyx/harness.py` |
| 前端与桌面端 | [前端](facts/frontend-facts.md) | `frontend/src/`、`frontend/src-tauri/` |

## 维护边界

- facts 只保留职责、关键行为边界和源码入口；签名、字段、表结构、算法、阈值和完整状态机直接查代码。
- 只有基本事实或操作方法发生变化时更新相关文档；内部实现调整不要求同步其他文档。
- 设计文档只在设计方向变化时维护；不能据此推断某功能已经实现或已通过验收。
- 不维护规格书、逐测试文件索引、测试数量快照或重复的实现索引。已有历史记录仅表示当时结果。
