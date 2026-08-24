from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, TypedDict

from nyx.enums import (
    ActivityStatus,
    ActivityType,
    DesireStatus,
    DesireType,
    EmotionCategory,
    EnergyState,
    EventType,
    GoalAction,
    MemoryType,
    SearchMode,
    Source,
)


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


# ---- 事件 ----
@dataclass
class Event:
    id: str                 # uuid4
    timestamp: float        # epoch 秒
    source: Source
    type: EventType
    # 按 type 变化的载荷；键结构由各生产方 spec 定义，本 spec 只声明 dict[str, Any]
    content: dict[str, Any]
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
    sources: list[SearchMode] = field(default_factory=list[SearchMode])  # 检索来源层


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
    goal_progress: int = 0   # goal 已完成单位数（读完整本/写出整篇=1 单位）


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


@dataclass
class ReadingNote:                 # 读完整本书后的完整笔记（可删除、可批注）
    id: str
    book: str                      # 书名（filename，仅供展示）
    content: str                   # 完整笔记正文（Markdown）
    created_at: float
    path: str = ""                 # 读物绝对路径（去重键；book 仅展示）
    annotation_count: int = 0      # 非 DB 列，list() 里 LEFT JOIN 算出


@dataclass
class Annotation:                  # 读书笔记的批注
    id: str
    target_id: str                 # reading_note.id
    author: str                    # 'user' | 'nyx'
    content: str
    created_at: float


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
    # 当前活动类型（取 ActivityFacade.get_current() 返回对象的 .type，None=空闲）
    current_activity: ActivityType | None
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
    # 唯一允许裸 Any 处（其余为 dict[str, Any]）：工具签名天然异构
    handler: Callable[..., Awaitable[Any]]


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
