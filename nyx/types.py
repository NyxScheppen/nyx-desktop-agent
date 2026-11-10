from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, TypedDict

from nyx.enums import (
    ActivityStatus,
    ActivityType,
    AssignedTaskStatus,
    AssignedTaskType,
    DesireStatus,
    DesireType,
    EmotionCategory,
    EnergyState,
    EventType,
    GoalAction,
    InteractionKind,
    InteractionStatus,
    MemoryEdgeKind,
    MemoryKind,
    MemoryType,
    SearchMode,
    Source,
)


class LlmMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class ReplyAnnotation(TypedDict):
    reply_event_id: str
    quote: str
    comment: str


# ---- 事件 ----
@dataclass
class Event:
    id: str  # uuid4
    timestamp: float  # epoch 秒
    source: Source
    type: EventType
    # 按 type 变化的载荷；键结构由各生产方 spec 定义，本 spec 只声明 dict[str, Any]
    content: dict[str, Any]
    correlation_id: str  # 上游 Event.correlation_id（根事件 = 自身 id）


@dataclass
class ExpressionHistory:
    """持久化表达事件的前端查询投影。"""

    all: list[Event]
    mutter: list[Event]
    initiate_chat: list[Event]


# ---- 记忆 ----
@dataclass
class Memory:
    id: str
    created_at: float
    content: str
    kind: MemoryKind
    summary: str
    freshness: float  # 0-1，随时间衰减
    type: MemoryType
    topics: list[str] = field(default_factory=list[str])
    recall_count: int = 0  # "想起"次数（实际用进回复）
    aspect: list[str] = field(default_factory=list[str])  # 仅 user_profile 画像，可多值
    embedding: list[float] | None = None  # 向量检索用，未嵌入为 None
    sources: list[SearchMode] = field(default_factory=list[SearchMode])  # 检索来源层


@dataclass
class ConversationSegment:
    """连续对话段：原始轮次是主体，摘要与向量只作索引。"""

    id: str
    previous_id: str | None
    started_at: float
    ended_at: float
    raw_event_ids: list[str]
    raw_content: str
    summary: str = ""
    topics: list[str] = field(default_factory=list[str])
    embedding: list[float] | None = None
    importance: str = "normal"
    long_term: bool = False
    evaluated_at: float | None = None
    evaluation_reason: str = ""
    evidence_event_ids: list[str] = field(default_factory=list[str])
    freshness: float = 1.0
    freshness_updated_at: float = 0.0
    recall_count: int = 0
    active: bool = True
    facts_extracted_at: float | None = None
    fact_scope: str = "conversation"
    # Retrieval views only; never overwrite the archived raw_content with excerpts.
    retrieval_excerpt: str | None = None
    retrieval_chunk_ids: list[str] = field(default_factory=list[str])
    retrieval_ranges: list[tuple[int, int]] = field(
        default_factory=list[tuple[int, int]],
    )
    retrieval_sources: list[str] = field(default_factory=list[str])
    retrieval_trace_id: str | None = None


@dataclass(frozen=True)
class ReadingEvidence:
    id: str
    source_topic: str
    source_name: str
    content: str
    created_at: float
    location: str | None = None
    embedding: list[float] | None = None


@dataclass
class MemoryEdge:
    from_id: str
    to_id: str
    kind: MemoryEdgeKind = MemoryEdgeKind.SEMANTIC
    weight: float = 1.0
    created_at: float = 0.0


@dataclass
class MemoryFact:
    """时间有效的实体关系事实；来源记忆仅用于溯源，不决定事实生命周期。"""

    id: str
    subject: str
    predicate: str
    object_value: str
    valid_from: float
    valid_until: float | None
    source_memory_id: str | None
    created_at: float
    polarity: int = 1
    subject_type: str = "person"
    object_type: str = "concept"
    scope: str = "conversation"
    source_segment_id: str | None = None
    source_chunk_id: str | None = None


# ---- 欲望 ----
@dataclass
class Goal:  # 可量化目标，规则可判
    action: GoalAction  # read / write / observe
    count: int  # 达成所需次数
    topic: str | None = None


@dataclass
class ShortTermDesire:
    id: str
    created_at: float
    type: DesireType
    strength: float  # 强度（具体范围见欲望系统）
    description: str  # LLM 生成的具体描述
    goal: Goal | None
    retry_count: int = 0
    status: DesireStatus = DesireStatus.PENDING
    goal_progress: int = 0  # goal 已完成单位数（读完整本/写出整篇=1 单位）
    parent_long_term_id: str | None = None


@dataclass
class LongTermDesire:
    id: str
    created_at: float
    type: DesireType  # 对应 DesireType，长期欲望给对应类型加压 + 主题种子
    name: str
    description: str
    strength: float  # 迫切度，消退不归零（具体范围见欲望系统）
    progress: float  # 0-1
    subtopics: list[str]  # 子主题池


@dataclass
class DesireValue:  # 每类型一份（压力值）
    type: DesireType
    value: float  # 当前值，缓慢衰减
    expression_weight: float
    suppression_threshold: float
    updated_at: float  # 最后一次 value 变化的时间戳（衰减 elapsed 来源）


@dataclass
class DesireState:  # /api/desires 全量快照
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
class ActivityContext:
    """活动事实快照，供一次表达回合使用。

    ``interrupted`` 是用户消息处理前捕获的活动；``current`` 是打断处理后
    重新采样的当前活动。两者都只作为证据，不代表 LLM 可以推断未记录的进度。
    """

    current: Activity | None
    interrupted: Activity | None
    observed_at: float


@dataclass
class AssignedTask:
    id: str
    type: AssignedTaskType
    status: AssignedTaskStatus
    url: str | None
    book_id: str | None
    target_paragraph: int | None
    checkpoint: dict[str, Any]
    error: str | None
    created_at: float
    updated_at: float


# ---- 陪读 ----
@dataclass
class Book:  # 用户陪读的 EPUB 书
    id: str
    title: str
    author: str
    filename: str  # 消毒后的上传文件名（展示用）
    content_hash: str  # 分段后正文 SHA-256（去重键）
    total_paragraphs: int
    created_at: float
    updated_at: float


ParagraphBlockKind = Literal["paragraph", "heading", "blockquote", "list_item", "pre"]


@dataclass(frozen=True)
class ParagraphBlock:  # 受控块语义；offset 为 UTF-16 code unit
    kind: ParagraphBlockKind
    start: int
    end: int
    level: int | None


@dataclass(frozen=True)
class TextMark:  # 受控行内样式；offset 为 UTF-16 code unit
    start: int
    end: int
    bold: bool
    italic: bool


@dataclass
class Paragraph:  # 书的正文段落（index 从 1 起、per book 连续）
    id: str
    book_id: str
    index: int
    text: str
    is_chapter_start: bool  # 以 h1/h2 开头（阅读章末检测用）
    blocks: list[ParagraphBlock] = field(default_factory=list[ParagraphBlock])
    marks: list[TextMark] = field(default_factory=list[TextMark])


@dataclass
class ReadingProgress:  # 阅读进度与书籍 1:1
    book_id: str
    user_position: int  # ≥1，与 Paragraph.index 从 1 起对齐
    nyx_position: int  # ≥1
    reading_speed: int  # 字符/秒，10-200
    read_count: int  # 读完几遍（只由阅读系统整本读完 ++）
    updated_at: float
    revision: int = 0  # 条件写版本；0 = 尚未成功写入进度


@dataclass
class BookListItem:  # 书架列表项（books LEFT JOIN reading_progress）
    id: str
    title: str
    author: str
    filename: str
    total_paragraphs: int
    user_position: int  # 0 = 未读哨兵（COALESCE 派生，非落库值）
    last_read_at: float | None  # 未读 None


@dataclass
class Annotation:  # 用户笔记的 Nyx 批注
    id: str
    user_note_id: str
    content: str
    created_at: float


@dataclass
class Bookmark:  # 阅读书签；段号与预览由 paragraphs JOIN 派生
    id: str
    book_id: str
    paragraph_id: str
    paragraph_index: int
    preview: str
    created_at: float


@dataclass
class UserNote:  # 用户手写笔记
    id: str
    book_id: str | None  # 书删除后 ON DELETE SET NULL
    paragraph_id: str | None  # 段删除后 SET NULL；自由记无段也为 None
    content: str
    selected_text: str | None
    created_at: float
    updated_at: float
    paragraph_index: int | None = None  # JOIN 派生，不落库
    selection_start: int | None = None  # UTF-16 code unit
    selection_end: int | None = None
    # 派生，非落库列：list_user_notes 拼装每条笔记的批注列表
    annotations: list[Annotation] = field(default_factory=list[Annotation])


# ---- 内在生命 ----
@dataclass
class CurrentState:  # 只读快照
    valence: float  # [-1, 1]
    arousal: float  # [0, 1]
    emotion: EmotionCategory  # 最终表情（8 档之一，含 sleepy/thinking 覆盖）
    inner_document: "InnerDocument"
    energy: float  # 0-100
    energy_state: EnergyState
    # 当前活动类型（取 ActivityFacade.get_current() 返回对象的 .type，None=空闲）
    current_activity: ActivityType | None
    active_desires: list[ShortTermDesire]  # 装配自 DesireFacade.get_pending()


@dataclass
class InnerTopic:
    section: str
    id: str
    title: str
    content: str


@dataclass
class InnerDocument:
    """One current inner document; past revisions belong to reflection records."""

    topics: list[InnerTopic]
    revision: int
    updated_at: float


@dataclass
class ReflectionOutcome:
    """本轮反思摘要及文档是否变化；摘要不作为对话输出。"""
    summary: str
    document_changed: bool


# ---- 表达 ----
@dataclass
class Message:
    role: Literal["user", "nyx"]
    content: str
    timestamp: float
    # True = 快通道 Nyx 回复（回溯截断时跳过）；用户消息恒 False
    fast: bool = False
    # 生成该历史条目时的活动归属；None 表示旧数据或非活动回合。
    activity_id: str | None = None
    activity_type: ActivityType | None = None
    activity_summary: str | None = None
    event_id: str | None = None


@dataclass
class InteractionAttempt:
    id: str
    kind: InteractionKind
    source_id: str
    correlation_id: str
    text: str
    created_at: float
    expires_at: float
    status: InteractionStatus = InteractionStatus.WAITING
    answered_at: float | None = None
    answer_event_id: str | None = None
    failure_reason: str | None = None
    paused_at: float | None = None


# ---- 工具 / eval ----
@dataclass
class PlanStep:
    text: str
    expected_tool: str | None
    done_condition: str


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep]


@dataclass(frozen=True)
class Skill:
    name: str
    system_fragment: str
    tool_names: tuple[str, ...]
    version: int = 1


@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, Any]  # 给 LLM 的 JSON schema
    # 唯一允许裸 Any 处（其余为 dict[str, Any]）：工具签名天然异构
    handler: Callable[..., Awaitable[Any]]


@dataclass
class LLMOutput:
    module: str  # 产出模块
    type: str  # 产出类型
    model: str  # 本次调用所用模型
    content: str  # 原始文本
    correlation_id: str
    # bind_tools 时 LLM 请求的工具调用（无则空）
    tool_calls: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    # 本次调用 token 消耗（抽不到记 0）；call_id 一次 complete() 唯一，
    # think/speak 拆分后共享同一次调用（eval 总 token 去重锚点）
    prompt_tokens: int = 0
    completion_tokens: int = 0
    call_id: str = ""
    # 应用层传给 ainvoke 的最终有序消息；None = 调用方未捕获（旧 mock）。
    prompt_messages: list[LlmMessage] | None = field(default=None, repr=False)


@dataclass
class EvalRecord:
    """一次 evaluate() 的记账行（LLM 调用 + token 可查询）。

    不存 content 原文（数据最小化）。
    """
    id: str
    created_at: float
    call_id: str  # 一次 complete() 唯一 id；think/speak 共享
    module: str  # 产出模块
    output_type: str  # 产出类型（think/speak/tool/desire/…）
    model: str  # 本次调用模型名
    correlation_id: str
    ooc_keyword: float  # 关键词信号分 [0,1]，1=未命中；不直接判定角色偏离
    ooc_embed: float | None  # 历史 embedding OOC 分；新记录恒为 None
    prompt_tokens: int
    completion_tokens: int


@dataclass
class EvalStats:
    """累计 token（按 call_id 去重求和，think/speak 只计一次）。"""
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
