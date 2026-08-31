# 枚举 + 实体类型

> 范围：`nyx/enums.py`（13 个 `StrEnum`）、`nyx/types.py`（2 个 TypedDict + 18 个 dataclass）。
> 纯声明 spec：只定义类型，不含函数、不含序列化 helper、不含 DDL（DDL 在 04-db）。
> spec 只定义契约（签名 + 语义 + 决策）；枚举成员与 dataclass 字段以 `nyx/enums.py` / `nyx/types.py` 源文件为准。

## 元信息

- **前置依赖**：无（类型定义在 `nyx/enums.py` / `nyx/types.py`，此处只给契约）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一份全系统共享、pyright strict 下零告警的类型与枚举定义，以便后续每个 spec 引用同一套实体、各处不再重复定义。

## 验收标准

- [ ] `enums.py` 含 13 个 `StrEnum`，成员与源文件一致（实现见 `nyx/enums.py`）
- [ ] `types.py` 含 2 个 TypedDict + 18 个 dataclass，字段与源文件一致（实现见 `nyx/types.py`）
- [ ] 所有枚举 `.value` 为小写 snake_case 字符串，可直接 `json.dumps` / 存 SQLite
- [ ] 固定键字段用 TypedDict、异构载荷用 `dict[str, Any]`（边界见「嵌套 dict 字段的边界」表）、不加 `frozen`
- [ ] `pyright` strict 下零报错：无 implicit Any、无 `str` 赋给枚举成员的默认值告警

## 技术方案

- **新文件**：`nyx/enums.py`、`nyx/types.py`（无 Facade、无 API、无数据变更）
- **约定**：枚举统一 `class X(StrEnum)`，成员 `UPPER_SNAKE`、值 = `成员名.lower()` 的 snake_case；dataclass 默认值用枚举成员而非裸字符串。
- **公开面**：`nyx/__init__.py` 保持空（不 re-export）；引用一律 `from nyx.enums import X` / `from nyx.types import Y`，不从 `nyx` 根导入；两模块不加 `__all__`（CLAUDE.md 禁 `*` 导入，`__all__` 是死代码）。
- **枚举清单（13 个）**：`EventType`（事件类型，19 键 ROUTING 的域，见 05-event）、`Source`、`TickType`（5 成员）、`ContextMode`、`EmotionCategory`（8 档，1:1 对应前端 sprites/）、`DesireType`、`ActivityType`（6 类活动）、`MemoryType`、`DesireStatus`、`ActivityStatus`、`EnergyState`、`SearchMode`（记忆检索三层，内部层标签）、`GoalAction`（read/write/observe，完成判定纯函数 switch 值）。
- **实体清单（18 个 dataclass）**：事件 `Event`；记忆 `Memory` / `MemoryEdge`；欲望 `Goal` / `ShortTermDesire` / `LongTermDesire` / `DesireValue` / `DesireState`；活动 `Activity` / `Material`；内在生命 `CurrentState` / `SelfNarrative` / `ReflectionOutcome`；表达 `Message`；工具/eval `Tool` / `LLMOutput`；陪读 `Book` / `Paragraph`。字段形状以 `nyx/types.py` 为准。

### 嵌套 dict 字段的边界（哪些收 TypedDict / 哪些留 `dict[str, Any]`）

| 字段 | 归属 | 理由 |
|---|---|---|
| `CurrentState.personality` | `Personality` | 固定 5 键（Big Five） |
| `CurrentState.values` | `Values` | 固定 4 键（三观） |
| `LLMOutput.tool_calls` | `list[dict[str, Any]]` | bind_tools 的工具调用，异构载荷（name/args 等） |
| `Event.content` | `dict[str, Any]` | 形状随 `EventType` 变 |
| `Activity.progress` | `dict[str, Any]` | 形状随 `ActivityType` 变 |
| `Tool.schema` | `dict[str, Any]` | 任意 JSON schema |
| `SelfNarrative.self_view` | `dict[str, str]`（普通 dict） | 键是开放的自画像维度，但值类型统一 str |

- **明确不做**：不加 `frozen`；`vad_to_category`、Goal 完成判定等纯函数留在各自 spec；`ReplyState`（LangGraph 内部 state）留在 17 spec。
- **default_factory 约定**：`field(default_factory=list)` 在 pyright strict 下报 `list[Unknown]`（裸 `list` 被推断为 `type[list[Unknown]]`，与字段注解 `list[str]` 不匹配）。故用 `field(default_factory=list[str])`——`list[str]` 作为类型对象可调用、返回空 `list[str]`，运行时等价 `list`，但类型精确、pyright 零报错、无需 ignore 压制。

## 测试要点

- [ ] 单元测试 `tests/test_types/`：
  - [ ] 13 个枚举**穷尽断言**（防漏成员/多成员/改值）：`EXPECTED` 硬编码每个枚举的完整值集合，`{m.value for m in X} == expected` 逐枚举比对（EXPECTED 字典与枚举成员同源，随 `nyx/enums.py` 维护）
  - [ ] 命名约定断言 `all(m.value == m.name.lower() for m in X)`（值 = 成员名小写，防手滑改值）
  - [ ] `json.dumps(EventType.USER_MESSAGE) == '"user_message"'`（StrEnum 可直接序列化）
  - [ ] `ShortTermDesire("", 0.0, DesireType.INTERACTION, 1.0, "", None).status is DesireStatus.PENDING`
  - [ ] `Memory("", 0.0, "", "", "", 1.0, MemoryType.SHORT_TERM).aspect` 两次实例化互不共享（`default_factory` 隔离）
  - [ ] 2 个 TypedDict 用 `get_type_hints` 断言键集合完整：`set(get_type_hints(Personality)) == {"openness","conscientiousness","extraversion","agreeableness","neuroticism"}` 等
- [ ] 集成测试：无（纯声明，无管道）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 后续 spec（04-db 起）引用本 spec 的枚举/实体，形成单一事实来源
