# 枚举 + 实体类型

> 范围：`nyx/enums.py`（23 个 `StrEnum`）、`nyx/types.py`（4 个 TypedDict + 36 个 dataclass）。
> 纯声明 spec：只定义类型，不含函数、不含序列化 helper、不含 DDL。
> spec 只定义契约（签名 + 语义 + 决策）；枚举成员与 dataclass 字段以 `nyx/enums.py` / `nyx/types.py` 源文件为准。

## 元信息

- **前置依赖**：无（类型定义在 `nyx/enums.py` / `nyx/types.py`，此处只给契约）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一份全系统共享、pyright strict 下零告警的类型与枚举定义，以便后续每个 spec 引用同一套实体、各处不再重复定义。

## 验收标准

- [ ] `enums.py` 含 23 个 `StrEnum`，成员与源文件一致（实现见 `nyx/enums.py`）
- [ ] `types.py` 含 4 个 TypedDict + 36 个 dataclass，字段与源文件一致（实现见 `nyx/types.py`）
- [ ] 所有枚举 `.value` 为小写 snake_case 字符串，可直接 `json.dumps` / 存 SQLite
- [ ] 固定键字段用 TypedDict、异构载荷用 `dict[str, Any]`（边界见「嵌套 dict 字段的边界」表）；仅不可变快照及技能声明使用 `frozen`
- [ ] `pyright` strict 下零报错：无 implicit Any、无 `str` 赋给枚举成员的默认值告警

## 技术方案

- **新文件**：`nyx/enums.py`、`nyx/types.py`（无 Facade、无 API、无数据变更）
- **约定**：枚举统一 `class X(StrEnum)`，成员 `UPPER_SNAKE`、值 = `成员名.lower()` 的 snake_case；dataclass 默认值用枚举成员而非裸字符串。
- **公开面**：`nyx/__init__.py` 保持空（不 re-export）；引用一律 `from nyx.enums import X` / `from nyx.types import Y`，不从 `nyx` 根导入；两模块不加 `__all__`（CLAUDE.md 禁 `*` 导入，`__all__` 是死代码）。
- **枚举清单**：包含 `MemoryKind`、`AssignedTaskType` 与 `AssignedTaskStatus`；成员与领域语义以 `nyx/enums.py` 及对应业务 spec 为准。
- **实体清单**：除既有事件、记忆、欲望、活动、内在生命、表达、工具/eval 与 EPUB 陪读实体外，委派任务包含 `AssignedTask`；完整字段形状以 `nyx/types.py` 为准。旧通用文件书库的 `Material` 已退役，不再是跨模块领域对象。
- **消息 TypedDict**：`LlmMessage` 固定为 `role: Literal["system", "user", "assistant"]` + `content: str`；`nyx.llm.client` 导入并继续公开该名字。
- **活动表达类型**：`ActivityContext` 是一次表达回合的运行时快照，包含打断前捕获的
  `interrupted`、打断后重新采样的 `current` 和 `observed_at`；`Message` 可选保存
  `activity_id`、`activity_type`、`activity_summary`，用于历史归属展示，不作为新的活动状态来源。
- **记忆类型字段**：`Memory.kind` 使用 `MemoryKind`，`Memory.topics` 为受限主题列表；`Memory.sources` 的类型与默认值由本文件定义，检索来源、持久化和 API 语义由 `06-memory-system` 定义。
- **阅读证据**：`ReadingEvidence` 是 frozen dataclass，字段为 `id: str`、
  `source_topic: str`、`source_name: str`、`content: str`、`created_at: float`，以及
  可空的 `location: str | None`、`embedding: list[float] | None`。它也是通用
  knowledge_chunk 原文快照；消费状态与 book_id 只在存储层保存，生命周期见 06/12/15。
- **知识事实溯源**：`MemoryFact.source_chunk_id: str | None = None` 只保存摄入方明确传入的
  原文块 ID。schema 43 不回填旧事实；来源块删除时外键置空，事实保留。

### 嵌套 dict 字段的边界（哪些收 TypedDict / 哪些留 `dict[str, Any]`）

| 字段 | 归属 | 理由 |
|---|---|---|
| `CurrentState.personality` | `Personality` | 固定 5 键（Big Five） |
| `CurrentState.values` | `Values` | 固定 4 键（三观） |
| `LLMOutput.tool_calls` | `list[dict[str, Any]]` | bind_tools 的工具调用，异构载荷（name/args 等） |
| `LLMOutput.prompt_messages` | `list[LlmMessage] | None` | `ainvoke` 的应用层最终有序消息；`None` 表示旧 mock 未捕获，且字段不进入 repr |
| `Event.content` | `dict[str, Any]` | 形状随 `EventType` 变 |
| `Activity.progress` | `dict[str, Any]` | 形状随 `ActivityType` 变 |
| `Tool.schema` | `dict[str, Any]` | 任意 JSON schema |
| `SelfNarrative.self_view` | `dict[str, str]`（普通 dict） | 键是开放的自画像维度，但值类型统一 str |

- **讨论技能**：`Skill` 是 frozen dataclass，字段为 `name: str`、`system_fragment: str`、`tool_names: tuple[str, ...]`；启动时从 JSON 加载并注入表达层，不持久化，完整契约见 [15](15-discussion-partner.md)。
- **工具计划**：`PlanStep(text: str, expected_tool: str | None, done_condition: str)` 与
  `Plan(goal: str, steps: list[PlanStep])` 是单次回复内的 dataclass，不持久化、不进入 ReplyState；
  生命周期和降级见 [18](18-plan-and-execute.md)。`MemoryKind.LESSON="lesson"` 的生产与消费见 [19](19-failure-reflection.md)。
- **明确不做**：不为其他可变领域对象增加 `frozen`；`vad_to_category`、Goal 完成判定等纯函数留在各自 spec；`ReplyState`（LangGraph 内部 state）留在 `11-expression`；不在本文件定义枚举或实体的业务流程。
- **default_factory 约定**：`field(default_factory=list)` 在 pyright strict 下报 `list[Unknown]`（裸 `list` 被推断为 `type[list[Unknown]]`，与字段注解 `list[str]` 不匹配）。故用 `field(default_factory=list[str])`——`list[str]` 作为类型对象可调用、返回空 `list[str]`，运行时等价 `list`，但类型精确、pyright 零报错、无需 ignore 压制。
- **短期欲望父关系**：`ShortTermDesire.parent_long_term_id: str | None = None` 保存生成它的长期欲望 ID；旧数据、纯类型压力生成和父记录删除后的短期欲望为 `None`，消费者不得按类型或“最新一条”猜测父对象。

## 测试要点

- [ ] 单元测试 `tests/test_types/`：
  - [ ] 后端 `EventType` 与前端 SSE 监听事件集合一致，防止新增事件无法被接收
  - [ ] `ShortTermDesire("", 0.0, DesireType.INTERACTION, 1.0, "", None).status is DesireStatus.PENDING`
  - [ ] `Memory("", 0.0, "", "", "", 1.0, MemoryType.SHORT_TERM).aspect` 两次实例化互不共享（`default_factory` 隔离）
  - [ ] `UserNote.annotations` 和 `LongTermDesire.linked_values` 的可变默认值在实例间隔离
- 类型字段和枚举声明由源码与 `pyright` 检查；不在测试中复写完整声明或验证标准库序列化。枚举的业务语义在对应消费者、持久化和 API 行为测试中验证。
- [ ] 集成测试：无（纯声明，无管道）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 后续 spec（04-module-bus-system 起）引用本 spec 的枚举/实体，形成单一事实来源


## P4 批注契约补充（已批准）

新增 `ReplyAnnotation(TypedDict)`：reply_event_id、quote、comment 均 str。EventType 增加 ANNOTATION_FEEDBACK="annotation_feedback"；content={reply_event_id,quote,comment,user_event_id}，correlation_id 沿用首次用户事件。完整入口/消费者/状态与六类 bad case 见 15 的“批注反馈”：API/历史重放生产快照，表达/记忆/前端消费，不新增删除或历史回填。
