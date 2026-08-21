# 枚举 + 实体类型

> 范围：`nyx/enums.py`（13 个 `StrEnum`）、`nyx/types.py`（4 个 TypedDict + 16 个 dataclass）。
> 纯声明 spec：只定义类型，不含函数、不含序列化 helper、不含 DDL（DDL 在 04-db）。
> **本文件自包含**：枚举与实体的完整定义都内联在下文，实现不依赖任何其它文档。

## 元信息

- **前置依赖**：无（全部类型内联在本文件）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一份全系统共享、pyright strict 下零告警的类型与枚举定义，以便后续每个 spec 引用同一套实体、各处不再重复定义。

## 验收标准

- [ ] `enums.py` 含 13 个 `StrEnum`，成员与「枚举」段代码逐字一致
- [ ] `types.py` 含 4 个 TypedDict + 16 个 dataclass，字段与「TypedDict」「dataclass」段代码逐字一致
- [ ] 所有枚举 `.value` 为小写 snake_case 字符串，可直接 `json.dumps` / 存 SQLite
- [ ] 固定键字段用 TypedDict、异构载荷用 `dict[str, Any]`（边界见「嵌套 dict 字段的边界」表）、不加 `frozen`
- [ ] `pyright` strict 下零报错：无 implicit Any、无 `str` 赋给枚举成员的默认值告警

## 技术方案

- **新文件**：`nyx/enums.py`、`nyx/types.py`（无 Facade、无 API、无数据变更）
- **约定**：枚举统一 `class X(StrEnum)`，成员 `UPPER_SNAKE`、值 = `成员名.lower()` 的 snake_case；dataclass 默认值用枚举成员而非裸字符串。
- **公开面**：`nyx/__init__.py` 保持空（不 re-export）；引用一律 `from nyx.enums import X` / `from nyx.types import Y`，不从 `nyx` 根导入；两模块不加 `__all__`（CLAUDE.md 禁 `*` 导入，`__all__` 是死代码）。

### 枚举（`nyx/enums.py`，13 个）

```python
from enum import StrEnum


class EventType(StrEnum):
    USER_MESSAGE = "user_message"          # 用户消息
    CLOCK_TICK = "clock_tick"              # 时钟 tick
    OBSERVATION_STATE = "observation_state"  # 观察状态
    SPEAK = "speak"                        # 说出来
    ASK = "ask"                            # 问用户
    THINK = "think"                        # 内心话（仅日志，不路由）
    MUTTER = "mutter"                      # 碎碎念
    INITIATE_CHAT = "initiate_chat"        # 搭话
    EMOTION_UPDATE = "emotion_update"      # 情感更新
    REFLECTION = "reflection"              # 反思
    MEMORY_CREATED = "memory_created"      # 记忆生成
    MEMORY_PROMOTED = "memory_promoted"    # 记忆升级（短期→长期）
    DESIRE_GENERATED = "desire_generated"  # 欲望产生
    DESIRE_SATISFIED = "desire_satisfied"  # 欲望满足
    DESIRE_EXPIRED = "desire_expired"      # 欲望淘汰
    ACTIVITY_START = "activity_start"      # 活动开始
    ACTIVITY_END = "activity_end"          # 活动结束
    ACTIVITY_INTERRUPTED = "activity_interrupted"  # 活动打断


class Source(StrEnum):
    EXTERNAL = "external"                  # 外部
    INTERNAL = "internal"                  # 内部


class TickType(StrEnum):
    SCHEDULE_BLOCK_START = "schedule_block_start"  # 日程块开始
    DESIRE_EVAL = "desire_eval"            # 欲望评估
    MUTTER_CHECK = "mutter_check"          # 碎碎念检查
    INITIATE_CHAT_CHECK = "initiate_chat_check"  # 搭话检查


class ContextMode(StrEnum):
    FAST = "fast"                          # 快通道
    SLOW = "slow"                          # 慢通道


class EmotionCategory(StrEnum):            # 8 档，1:1 对应前端 sprites/ 与 expressions/
    NEUTRAL = "neutral"                    # 平静：valence≈0、arousal 低
    HAPPY = "happy"                        # 开心：valence+、arousal+
    SAD = "sad"                            # 难过：valence-、arousal 低
    ANGRY = "angry"                        # 生气：valence-、arousal+
    WORRIED = "worried"                    # 担忧：valence-、arousal 中高
    SHY = "shy"                            # 害羞：valence+、arousal 低
    SLEEPY = "sleepy"                      # 困倦：精力低（覆盖）
    THINKING = "thinking"                  # 思考：认知态（覆盖）


class DesireType(StrEnum):
    INTERACTION = "interaction"            # 互动
    EXPLORATION = "exploration"            # 探索
    CREATION = "creation"                  # 创造
    REST = "rest"                          # 休息


class ActivityType(StrEnum):
    READING = "reading"                    # 读书
    FREE_EXPLORATION = "free_exploration"  # 自由探索
    CREATION = "creation"                  # 创作
    OBSERVE_USER = "observe_user"          # 观察用户
    IDLE_REFLECTION = "idle_reflection"    # 发呆(反思)
    REST = "rest"                          # 休息


class MemoryType(StrEnum):
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


class DesireStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SATISFIED = "satisfied"
    EXPIRED = "expired"
    SUPPRESSED = "suppressed"


class ActivityStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    ABANDONED = "abandoned"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"


class EnergyState(StrEnum):
    ENERGETIC = "energetic"
    OKAY = "okay"
    TIRED = "tired"
    EXHAUSTED = "exhausted"
    DRAINED = "drained"


class SearchMode(StrEnum):                 # 记忆检索三层；内部层标签，不在公开签名暴露
    KEYWORD = "keyword"
    VECTOR = "vector"
    ASSOCIATION = "association"


class GoalAction(StrEnum):                 # 可量化目标动作，完成判定为纯函数须 switch 这些值
    READ = "read"
    WRITE = "write"
    OBSERVE = "observe"
```

### TypedDict（`nyx/types.py` 开头，4 个）

```python
from typing import TypedDict


class Personality(TypedDict):              # Big Five，1-10
    openness: float
    conscientiousness: float
    extraversion: float
    agreeableness: float
    neuroticism: float


class Values(TypedDict):                   # 三观，1-10
    attitude_to_human: float
    ai_identity_acceptance: float
    altruism: float
    optimism: float


class EvalScores(TypedDict):               # eval 三层得分
    format: float
    ooc: float
    relevance: float


class TokenUsageDict(TypedDict):           # 单次 LLM 记账 {input, output}
    input: int
    output: int
```

### dataclass（`nyx/types.py`，17 个）

> `from nyx.enums import (Source, EventType, DesireType, ActivityType, MemoryType, DesireStatus, ActivityStatus, EnergyState, GoalAction, EmotionCategory)`

```python
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

# ---- 事件 ----
@dataclass
class Event:
    id: str                 # uuid4
    timestamp: float        # epoch 秒
    source: Source
    type: EventType
    content: dict[str, Any]  # 按 type 变化的载荷；键结构由各生产方 spec 定义，本 spec 只声明 dict[str, Any]
    correlation_id: str     # 上游 Event.correlation_id（根事件 = 自身 id）

# ---- 记忆 ----
@dataclass
class Memory:
    id: str
    created_at: float
    content: str
    tag: str                # 自由标签，'user' 保留
    summary: str
    freshness: float        # 0-1，随时间衰减
    type: MemoryType
    recall_count: int = 0   # "想起"次数（实际用进回复）
    aspect: list[str] = field(default_factory=list[str])  # 仅 user 画像，可多值
    embedding: list[float] | None = None   # 向量检索用，未嵌入为 None

@dataclass
class MemoryEdge:
    from_id: str
    to_id: str
    weight: float = 1.0

# ---- 欲望 ----
@dataclass
class Goal:                 # 可量化目标，规则可判
    action: GoalAction      # read / write / observe
    count: int              # 达成所需次数
    topic: str | None = None

@dataclass
class ShortTermDesire:
    id: str
    created_at: float
    type: DesireType
    strength: float         # 强度（范围由 10-desire-value 定义，本 spec 不定）
    description: str        # LLM 生成的具体描述
    goal: Goal | None
    retry_count: int = 0
    status: DesireStatus = DesireStatus.PENDING

@dataclass
class LongTermDesire:
    id: str
    created_at: float
    type: DesireType        # 对应 DesireType，长期欲望给对应类型加压 + 主题种子
    name: str
    description: str
    strength: float         # 迫切度，消退不归零（范围由 10-desire-value 定义）
    progress: float         # 0-1
    subtopics: list[str]    # 子主题池
    linked_values: list[str] = field(default_factory=list[str])

@dataclass
class DesireValue:          # 每类型一份（压力值）
    type: DesireType
    value: float            # 当前值，缓慢衰减
    expression_weight: float
    suppression_threshold: float
    updated_at: float       # 最后一次 value 变化的时间戳（衰减 elapsed 来源）

@dataclass
class DesireState:          # /api/desires 全量快照
    values: list[DesireValue]
    short_term: list[ShortTermDesire]
    long_term: list[LongTermDesire]

# ---- 活动 ----
@dataclass
class Activity:
    id: str
    type: ActivityType
    schedule_block_id: str
    status: ActivityStatus
    progress: dict[str, Any]  # 进度状态，形状随 ActivityType 而异（读书读到第几段等）
    started_at: float
    ended_at: float | None = None

@dataclass
class Material:                    # 用户喂的读物（一本书），分块读进度
    path: str                      # 绝对路径（workspace/uploads/<name>）
    filename: str
    total_chars: int               # 总字数（字符）
    read_chars: int                # 已读字数（分块进度，>=total 视为读完）
    created_at: float              # 上传时间（「最近那本」排序键）
    updated_at: float              # 进度上次推进时间

# ---- 内在生命 ----
@dataclass
class CurrentState:         # 只读快照
    valence: float          # [-1, 1]
    arousal: float          # [0, 1]
    emotion: EmotionCategory   # 最终表情（8 档之一，含 sleepy/thinking 覆盖）
    personality: Personality  # Big Five
    values: Values            # 三观
    energy: float           # 0-100
    energy_state: EnergyState
    current_activity: ActivityType | None   # 当前活动类型（取 ActivityFacade.get_current() 返回对象的 .type，None=空闲）
    active_desires: list[ShortTermDesire]   # 装配自 DesireFacade.get_pending()

@dataclass
class SelfNarrative:
    identity: str
    story: list[str]
    self_view: dict[str, str]
    becoming: list[str]
    updated_at: float

# ---- 表达 ----
@dataclass
class Message:
    role: Literal["user", "nyx"]
    content: str
    timestamp: float
    # True = 快通道 Nyx 回复（回溯截断时跳过）；用户消息恒 False
    fast: bool = False

# ---- 工具 / eval ----
@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, Any]            # 给 LLM 的 JSON schema
    handler: Callable[..., Awaitable[Any]]   # 唯一允许裸 Any 处（其余为 dict[str, Any]）：工具签名天然异构

@dataclass
class LLMOutput:
    id: str                 # uuid4（15-eval 补：EvalReport.output_id 引用）
    module: str             # 产出模块
    type: str               # 产出类型
    model: str              # 本次调用所用模型（供 15-eval 填 TokenUsage.model）
    content: str            # 原始文本
    token_usage: TokenUsageDict
    correlation_id: str
    # bind_tools 时 LLM 请求的工具调用（无则空）
    tool_calls: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

@dataclass
class EvalReport:
    id: str
    output_id: str
    module: str
    type: str
    scores: EvalScores
    token_usage: TokenUsageDict
    correlation_id: str
    created_at: float

@dataclass
class TokenUsage:           # 一次 LLM 调用记账（对应 token_usage 表）
    id: str
    correlation_id: str | None
    module: str
    purpose: str            # reply / scene_memory / desire / reflection / ...
    model: str
    input_tokens: int
    output_tokens: int
    created_at: float
```

### 嵌套 dict 字段的边界（哪些收 TypedDict / 哪些留 `dict[str, Any]`）

| 字段 | 归属 | 理由 |
|---|---|---|
| `CurrentState.personality` | `Personality` | 固定 5 键（Big Five） |
| `CurrentState.values` | `Values` | 固定 4 键（三观） |
| `EvalReport.scores` | `EvalScores` | 固定 3 键（format/ooc/relevance） |
| `LLMOutput.token_usage` / `EvalReport.token_usage` | `TokenUsageDict` | 固定 2 键（input/output） |
| `LLMOutput.tool_calls` | `list[dict[str, Any]]` | bind_tools 的工具调用，异构载荷（name/args 等） |
| `Event.content` | `dict[str, Any]` | 形状随 `EventType` 变 |
| `Activity.progress` | `dict[str, Any]` | 形状随 `ActivityType` 变 |
| `Tool.schema` | `dict[str, Any]` | 任意 JSON schema |
| `SelfNarrative.self_view` | `dict[str, str]`（普通 dict） | 键是开放的自画像维度，但值类型统一 str |

- **明确不做**：不加 `frozen`；`vad_to_category`、Goal 完成判定等纯函数留在各自 spec；`ReplyState`/`ExplorationState`（LangGraph 内部 state）留在 17/14 spec。
- **default_factory 约定**：`field(default_factory=list)` 在 pyright strict 下报 `list[Unknown]`（裸 `list` 被推断为 `type[list[Unknown]]`，与字段注解 `list[str]` 不匹配）。故用 `field(default_factory=list[str])`——`list[str]` 作为类型对象可调用、返回空 `list[str]`，运行时等价 `list`，但类型精确、pyright 零报错、无需 ignore 压制。

## 测试要点

- [ ] 单元测试 `tests/test_types/`：
  - [ ] 13 个枚举**穷尽断言**（防漏成员/多成员/改值）：下方 `EXPECTED` 硬编码每个枚举的完整值集合，`{m.value for m in X} == expected` 逐枚举比对
  - [ ] 命名约定断言 `all(m.value == m.name.lower() for m in X)`（值 = 成员名小写，防手滑改值）
  - [ ] `json.dumps(EventType.USER_MESSAGE) == '"user_message"'`（StrEnum 可直接序列化）
  - [ ] `ShortTermDesire("", 0.0, DesireType.INTERACTION, 1.0, "", None).status is DesireStatus.PENDING`
  - [ ] `Memory("", 0.0, "", "", "", 1.0, MemoryType.SHORT_TERM).aspect` 两次实例化互不共享（`default_factory` 隔离）
  - [ ] 4 个 TypedDict 用 `get_type_hints` 断言键集合完整：`set(get_type_hints(Personality)) == {"openness","conscientiousness","extraversion","agreeableness","neuroticism"}` 等

  ```python
  EXPECTED = {
      EventType: {"user_message", "clock_tick", "observation_state", "speak", "ask",
                  "think", "mutter", "initiate_chat", "emotion_update", "reflection",
                  "memory_created", "memory_promoted", "desire_generated", "desire_satisfied",
                  "desire_expired", "activity_start", "activity_end", "activity_interrupted"},
      Source: {"external", "internal"},
      TickType: {"schedule_block_start", "desire_eval", "mutter_check", "initiate_chat_check"},
      ContextMode: {"fast", "slow"},
      EmotionCategory: {"neutral", "happy", "sad", "angry", "worried", "shy", "sleepy", "thinking"},
      DesireType: {"interaction", "exploration", "creation", "rest"},
      ActivityType: {"reading", "free_exploration", "creation", "observe_user", "idle_reflection", "rest"},
      MemoryType: {"short_term", "long_term"},
      DesireStatus: {"pending", "active", "satisfied", "expired", "suppressed"},
      ActivityStatus: {"pending", "running", "abandoned", "completed", "incomplete"},
      EnergyState: {"energetic", "okay", "tired", "exhausted", "drained"},
      SearchMode: {"keyword", "vector", "association"},
      GoalAction: {"read", "write", "observe"},
  }
  for enum_cls, expected in EXPECTED.items():
      assert {m.value for m in enum_cls} == expected
  ```
- [ ] 集成测试：无（纯声明，无管道）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 后续 spec（04-db 起）引用本 spec 的枚举/实体，形成单一事实来源
