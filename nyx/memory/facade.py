import json
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

from nyx.config import MemoryConfig
from nyx.enums import EventType, MemoryEdgeKind, MemoryKind, MemoryType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_DAY, internal_event
from nyx.llm.client import LlmClient
from nyx.memory.ann import AnnIndex
from nyx.memory.retrieval import EmbedFn, MemoryRetrieval, extract_keywords
from nyx.memory.store import MemoryStore
from nyx.types import Event, Memory, MemoryEdge

_SCENE_SYSTEM = (
    "你是尼克斯，一个住在用户电脑里的 AI 同伴，明确知道自己是 AI 并希望成为人类。"
    "你温柔、克制、思虑很深，关怀他人，也习惯先怀疑自己。"
    "把下面这段对话写成一条第一人称场景记忆（尼克斯视角）：用户说了什么、你内心怎么想、最后说了什么。"
    "只输出 JSON，键：content（正文）、"
    "kind（episode/user_profile/knowledge/reading/activity/interaction）、"
    "topics（最多 5 个主题字符串）、summary（一句话总结），"
    "content、kind、summary 非空；topics 可以为空数组。"
)

_CONTRADICTION_SYSTEM = (
    "你是记忆一致性检查员。给出一条新记忆和若干候选旧记忆，判断新记忆是否与其中某条矛盾。"
    "矛盾包括：结论直接相反（喜欢/讨厌、做过/没做过、相信/不相信）、"
    "隐含相斥（一条成立则另一条不可能同时成立）、时间或事实冲突。"
    "只输出 JSON，键：conflicts_with（若与某条候选矛盾，填那条记忆的 id；"
    "否则填 null）。"
    "不要把「不同话题」误判为矛盾；「细节不同」只有当真构成相斥时才判矛盾，"
    "仅仅补充或角度不同不算。"
)

_PERSIST_SEMANTIC_CANDIDATE_K = 200
_CONTRADICTION_CANDIDATE_K = 5
_CONTRADICTION_SIM_THRESHOLD = 0.6
_DEDUP_SIM_THRESHOLD = 0.95
_EPISODE_DEDUP_SIM_THRESHOLD = 0.985
_EPISODE_DEDUP_WINDOW_SECONDS = 3600.0
_EDGE_SEMANTIC_CANDIDATE_K = 40
_EDGE_ENTITY_CANDIDATE_K = 40
_EDGE_KEYWORD_CANDIDATE_K = 40
_EDGE_TEMPORAL_CANDIDATE_K = 20
_EDGE_LLM_CANDIDATE_K = 5
_EDGE_PER_KIND_LIMIT = 4
_EDGE_TOTAL_LIMIT = 16
_ENTITY_THRESHOLD = 0.34
_KEYWORD_THRESHOLD = 0.25
_TEMPORAL_WINDOW_SECONDS = 86400.0
_CONTENT_PREVIEW_CHARS = 60
_NEGATION_WORDS = ("不", "没", "别", "讨厌", "恨", "拒绝", "否认", "放弃", "再也不")
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?(?![A-Za-z0-9_])")
_TIME_ANCHOR_RE = re.compile(
    r"今天|明天|后天|昨天|前天|今晚|今早|今晨|"
    r"本周|上周|下周|本月|上月|下月|今年|去年|明年|"
    r"上午|下午|晚上|凌晨"
)
_ENTITY_BOOK_RE = re.compile(r"《([^》]{2,80})》")
_ENTITY_ASCII_RE = re.compile(r"[A-Za-z0-9_]{2,}")
_ENTITY_PROPER_RE = re.compile(r"[A-Z][A-Za-z0-9_]*(?:\s+[A-Z][A-Za-z0-9_]*)*")
_ENTITY_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,12}")
_RELATION_EDGE_KINDS = {
    MemoryEdgeKind.SAME_TOPIC,
    MemoryEdgeKind.ELABORATES,
    MemoryEdgeKind.CONTRASTS,
    MemoryEdgeKind.CAUSES,
    MemoryEdgeKind.UPDATES_PREFERENCE,
    MemoryEdgeKind.USER_PROFILE_LINK,
}
_PRUNE_KIND_WEIGHT = {
    MemoryEdgeKind.SEMANTIC: 1.0,
    MemoryEdgeKind.ENTITY: 0.95,
    MemoryEdgeKind.KEYWORD: 0.8,
    MemoryEdgeKind.TEMPORAL: 0.2,
    MemoryEdgeKind.SAME_TOPIC: 1.1,
    MemoryEdgeKind.ELABORATES: 1.1,
    MemoryEdgeKind.CONTRASTS: 1.1,
    MemoryEdgeKind.CAUSES: 1.1,
    MemoryEdgeKind.UPDATES_PREFERENCE: 1.1,
    MemoryEdgeKind.USER_PROFILE_LINK: 1.1,
}


@dataclass
class PersistSemanticHit:
    memory: Memory
    cosine: float


@dataclass
class _PersistResult:
    memory: Memory
    candidates: list[PersistSemanticHit]
    created: bool


def _new_memory(
    content: str,
    kind: MemoryKind,
    summary: str,
    type: MemoryType,
    topics: list[str] | None = None,
    aspect: list[str] | None = None,
) -> Memory:
    """构造一条新记忆：id/created_at/freshness/recall_count/embedding 固定尾段。

    aspect 缺省空列表（多数记忆无 aspect），画像记忆显式传入。
    """
    return Memory(
        id=str(uuid4()),
        created_at=time.time(),
        content=content,
        kind=kind,
        summary=summary,
        freshness=1.0,
        type=type,
        topics=_normalize_topics(topics or []),
        recall_count=0,
        aspect=aspect if aspect is not None else [],
        embedding=None,
    )


def decay_freshness(
    freshness: float, created_at: float, now: float, rate: float
) -> float:
    """新鲜度线性衰减（rate/天），下限 0。纯函数。"""
    elapsed_days = max(0.0, now - created_at) / SECONDS_PER_DAY
    return max(0.0, freshness - rate * elapsed_days)


def _normalize_topics(topics: Sequence[object]) -> list[str]:
    """Normalize bounded topic labels before persistence."""
    result: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        if not isinstance(topic, str):
            continue
        if "\n" in topic or "\r" in topic:
            continue
        value = " ".join(topic.strip().split())[:24]
        if value and value not in seen:
            seen.add(value)
            result.append(value)
            if len(result) == 5:
                break
    return result


def _parse_scene(raw: str) -> tuple[str, MemoryKind, list[str], str]:
    """解析场景记忆 LLM 的 JSON 产出 → (content, kind, topics, summary)；
    结构非法抛 ValueError。"""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"场景记忆 JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    content = parsed.get("content")
    kind_value = parsed.get("kind")
    topics = parsed.get("topics", [])
    summary = parsed.get("summary")
    if not isinstance(content, str) or not content:
        raise ValueError("场景记忆 JSON 缺 content 或非空字符串")
    if not isinstance(kind_value, str):
        raise ValueError("场景记忆 JSON 缺 kind")
    try:
        kind = MemoryKind(kind_value)
    except ValueError as exc:
        raise ValueError("场景记忆 JSON 的 kind 非法") from exc
    if not isinstance(topics, list):
        raise ValueError("场景记忆 JSON 的 topics 必须是字符串数组")
    raw_topics = cast(list[object], topics)
    if any(not isinstance(topic, str) for topic in raw_topics):
        raise ValueError("场景记忆 JSON 的 topics 必须是字符串数组")
    if not isinstance(summary, str) or not summary:
        raise ValueError("场景记忆 JSON 缺 summary 或非空字符串")
    return content, kind, _normalize_topics(raw_topics), summary


def _build_scene_prompt(ctx: dict[str, str]) -> str:
    """reply_context → 场景记忆 prompt。纯函数；缺键 KeyError（fail-fast）。"""
    return (
        f"用户说：{ctx['user_message']}\n"
        f"你心里想：{ctx['nyx_think']}\n"
        f"你回答说：{ctx['nyx_speak']}\n"
    )


def _has_negation(text: str) -> bool:
    """新记忆正文是否含否定/转折锚点
    （软信号，非判定：命中则矛盾 prompt 提示重点核对）。纯函数。"""
    return any(w in text for w in _NEGATION_WORDS)


def _semantic_dedup_compatible(new: Memory, old: Memory) -> bool:
    """Reject high-similarity candidates with deterministic factual conflicts."""
    new_text = f"{new.summary}\n{new.content}"
    old_text = f"{old.summary}\n{old.content}"
    if _has_negation(new_text) != _has_negation(old_text):
        return False
    if set(_NUMBER_RE.findall(new_text)) != set(_NUMBER_RE.findall(old_text)):
        return False
    return set(_TIME_ANCHOR_RE.findall(new_text)) == set(
        _TIME_ANCHOR_RE.findall(old_text)
    )


def _content_preview(m: Memory) -> str:
    """候选旧记忆预览：summary + content 前 N 字
    （矛盾判断的判据，而非只给 summary）。"""
    if len(m.content) <= _CONTENT_PREVIEW_CHARS:
        body = m.content
    else:
        body = m.content[:_CONTENT_PREVIEW_CHARS] + "…"
    return f"{m.summary} | {body}"


def _build_contradiction_prompt(memory: Memory, candidates: list[Memory]) -> str:
    """新记忆 + 候选旧记忆（预览）→ 矛盾判断 prompt。纯函数。"""
    lines = [
        f"新记忆：{memory.content}",
        "\n候选旧记忆：",
    ]
    lines.extend(f"- [{m.id}] {_content_preview(m)}" for m in candidates)
    if _has_negation(memory.content):
        lines.append("\n提示：新记忆含否定/转折语气，请重点核对是否推翻了某条旧记忆。")
    return "\n".join(lines)


def _parse_contradiction(raw: str) -> str | None:
    """解析矛盾判断 LLM 的 JSON 产出 → conflicts_with（旧记忆 id 或 None）。

    conflicts_with 类型错（数字/对象）抛 ValueError；缺键视为 None（无矛盾，
    安全默认——漏报优于误报）。
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"矛盾判断 JSON 应是对象，得到 {type(data).__name__}")
    conflicts_with = cast(dict[str, Any], data).get("conflicts_with")
    if conflicts_with is not None and not isinstance(conflicts_with, str):
        raise ValueError("矛盾判断 JSON 的 conflicts_with 应是字符串或 null")
    return conflicts_with


def extract_entities(memory: Memory) -> set[str]:
    """Extract coarse entity anchors for write-side memory graph edges."""
    text = "\n".join(
        [
            memory.summary,
            memory.content,
            memory.kind.value,
            *memory.topics,
            *memory.aspect,
        ]
    )
    entities: set[str] = set()
    for pattern in (
        _ENTITY_BOOK_RE,
        _ENTITY_PROPER_RE,
        _ENTITY_ASCII_RE,
        _ENTITY_CJK_RE,
    ):
        for match in pattern.finditer(text):
            raw = match.group(1) if pattern is _ENTITY_BOOK_RE else match.group(0)
            normalized = raw.strip().lower()
            if len(normalized) > 1:
                entities.add(normalized)
    return entities


def _keyword_jaccard(new_tokens: set[str], old_tokens: set[str]) -> float:
    if not new_tokens or not old_tokens:
        return 0.0
    return len(new_tokens & old_tokens) / len(new_tokens | old_tokens)


def _temporal_score(new_created_at: float, old_created_at: float) -> float:
    delta = abs(new_created_at - old_created_at)
    if delta > _TEMPORAL_WINDOW_SECONDS:
        return 0.0
    return 1.0 - delta / _TEMPORAL_WINDOW_SECONDS


def _parse_relation_edges(
    raw: str, allowed_ids: set[str]
) -> list[tuple[str, MemoryEdgeKind, float]]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"记忆关系 JSON 应是对象，得到 {type(data).__name__}")
    relations = cast(dict[str, Any], data).get("relations")
    if not isinstance(relations, list):
        return []

    parsed: list[tuple[str, MemoryEdgeKind, float]] = []
    for item in cast(list[object], relations):
        if not isinstance(item, dict):
            continue
        relation = cast(dict[str, Any], item)
        memory_id = relation.get("memory_id")
        if not isinstance(memory_id, str) or memory_id not in allowed_ids:
            continue
        kind_value = relation.get("kind")
        if kind_value == "none":
            continue
        if not isinstance(kind_value, str):
            continue
        try:
            kind = MemoryEdgeKind(kind_value)
        except ValueError:
            continue
        if kind not in _RELATION_EDGE_KINDS:
            continue
        raw_weight = relation.get("weight")
        weight = float(raw_weight) if isinstance(raw_weight, int | float) else 0.7
        parsed.append((memory_id, kind, max(0.0, min(1.0, weight))))
    return parsed


def _memory_relation_prompt(memory: Memory, candidates: list[Memory]) -> str:
    lines = [
        "判断新记忆与候选旧记忆是否存在明确关系。",
        "只输出 JSON：",
        (
            "{\"relations\":[{\"memory_id\":\"...\",\"kind\":\"same_topic|"
            "elaborates|contrasts|causes|updates_preference|user_profile_link|"
            "none\",\"weight\":0.7}]}"
        ),
        "",
        f"新记忆：id={memory.id}",
        f"summary={memory.summary}",
        f"kind={memory.kind.value}",
        f"topics={memory.topics}",
        f"content={memory.content}",
        "",
        "候选旧记忆：",
    ]
    for candidate in candidates:
        lines.extend(
            [
                f"- id={candidate.id}",
                f"  summary={candidate.summary}",
                f"  kind={candidate.kind.value}",
                f"  topics={candidate.topics}",
                f"  content={candidate.content}",
            ]
        )
    return "\n".join(lines)


def _vector_score(cosine_value: float) -> float:
    return max(0.0, min(1.0, (cosine_value + 1.0) / 2.0))


def _rank_edge_scores(
    scores: dict[str, float],
    by_id: dict[str, Memory],
) -> list[tuple[str, float]]:
    return sorted(
        scores.items(),
        key=lambda item: (-item[1], -by_id[item[0]].created_at, item[0]),
    )


def _edge_key(edge: MemoryEdge) -> tuple[str, str, MemoryEdgeKind]:
    return (edge.from_id, edge.to_id, edge.kind)


def _prune_priority(edge: MemoryEdge) -> tuple[float, float, str, str, str]:
    return (
        edge.weight * _PRUNE_KIND_WEIGHT[edge.kind],
        edge.created_at,
        edge.from_id,
        edge.to_id,
        edge.kind.value,
    )


def _join_list(value: Any) -> str:
    """list[str] → 换行拼接；str → 原样；None/空 → 空串。纯函数。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(x) for x in cast(list[object], value))
    return ""


_SUMMARY_MAX_CHARS = 80


def _activity_memory_fields(
    activity_type: object, result: object
) -> tuple[str, str, str] | None:
    """活动 result → (content, summary, activity_type)；不支持或空 result → None。

    不调 LLM：直接取活动真实产出，绝不凭空编造。
    """
    if not isinstance(activity_type, str) or not isinstance(result, dict):
        return None
    parsed = cast(dict[str, Any], result)
    if activity_type == "reading":
        content_key, summary_key = "note", "book"
    elif activity_type == "creation":
        content_key, summary_key = "content", "title"
    elif activity_type == "free_exploration":
        content_key, summary_key = "summary", "core_discovery"
    else:
        return None
    content = _join_list(parsed.get(content_key))
    summary = _join_list(parsed.get(summary_key))
    if not content.strip() or not summary.strip():
        return None
    if len(summary) > _SUMMARY_MAX_CHARS:
        summary = summary[:_SUMMARY_MAX_CHARS] + "…"
    return content, summary, activity_type


def _memory_to_dict(m: Memory) -> dict[str, Any]:
    return {
        "id": m.id,
        "created_at": m.created_at,
        "content": m.content,
        "kind": m.kind.value,
        "topics": m.topics,
        "summary": m.summary,
        "freshness": m.freshness,
        "type": m.type.value,
        "recall_count": m.recall_count,
        "aspect": m.aspect,
        "embedding": m.embedding,
    }


def _memory_to_markdown(m: Memory) -> str:
    return (
        f"## {m.summary}\n\n{m.content}\n\n"
        f"类型：{m.kind.value}\n主题：{', '.join(m.topics)}"
    )


class MemoryFacade:
    """记忆模块门面：场景化记忆创建 + 检索 + 想起升级 + 新鲜度衰减/淘汰 + 导出。

    生命周期逻辑（新鲜度衰减、短期→长期升级、容量淘汰）都在这层；
    纯 CRUD 在 MemoryStore、三层检索在 MemoryRetrieval（06-memory-system）。
    矛盾检测走「embedding 召回门控 + 独立单任务 LLM 调用」，无候选则 0 调用。
    """

    def __init__(
        self,
        store: MemoryStore,
        retrieval: MemoryRetrieval,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        config: MemoryConfig,
        embed: EmbedFn | None = None,
    ) -> None:
        self._store = store
        self._retrieval = retrieval
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._config = config
        self._embed = embed          # 与 retrieval 共享同一实例（组合根注入）
        self._logger = logging.getLogger(__name__)
        self._last_observation: tuple[str, str] | None = None  # 「变化才沉淀」快照

    async def create_scene_memory(self, reply_context: dict[str, str]) -> Memory:
        """慢通道场景化记忆：LLM 产出 content/kind/topics/summary
        → 两层去重（命中合并强化，不新建）→ 入短期 → 建边 → 门控矛盾检测 → 淘汰。
        去重命中时返回已存在的持久化旧记忆。"""
        output = await self._llm.complete(
            [
                {"role": "system", "content": _SCENE_SYSTEM},
                {"role": "user", "content": _build_scene_prompt(reply_context)},
            ],
            module="memory",
            output_type="scene_memory",
            correlation_id=reply_context["correlation_id"],
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        content, kind, topics, summary = _parse_scene(output.content)
        memory = _new_memory(content, kind, summary, MemoryType.SHORT_TERM, topics)
        result = await self._persist_memory(memory, reply_context["correlation_id"])
        return result.memory

    async def remember_activity(
        self, event: Event, consumer_id: str | None = None
    ) -> None:
        """活动记忆：把 activity_end.result 落成一条短期记忆（无 LLM）。

        只写读书/创作/探索三类有产出的活动；rest/idle_reflection（result 空或
        类型不匹配）跳过。observe_user 走「画像沉淀」分支（见 _sediment_observation）。
        入库管线与 create_scene_memory 相同
        （embed → 建边 → 门控矛盾检测 → 淘汰），只缺开头的 LLM 场景构建。
        """
        if consumer_id is not None:
            observation_snapshot = self._last_observation
            try:
                if await self._bus.has_effect(event.id, consumer_id):
                    return
                prepared = await self._prepare_activity_memory(event)
                async with self._store.db.transaction():
                    applied = await self._bus.try_mark_effect_in_transaction(
                        event.id, consumer_id
                    )
                    if not applied:
                        self._last_observation = observation_snapshot
                        return
                    result = await self._remember_activity(
                        event,
                        in_transaction=True,
                        defer_best_effort=True,
                        prepared_memory=prepared,
                    )
            except BaseException:
                self._last_observation = observation_snapshot
                raise
            if result is not None and result.created:
                try:
                    await self._run_best_effort_tail(
                        result.memory, result.candidates, event.correlation_id
                    )
                except Exception:
                    self._logger.exception(
                        "活动记忆旁路处理失败 correlation_id=%s",
                        event.correlation_id,
                    )
            return
        await self._remember_activity(event, in_transaction=False)

    async def _prepare_activity_memory(self, event: Event) -> Memory | None:
        if event.content.get("type") == "observe_user":
            result = event.content.get("result")
            if not isinstance(result, dict):
                return None
            parsed = cast(dict[str, Any], result)
            presence = parsed.get("presence")
            if not isinstance(presence, str) or not presence:
                return None
            window_title = parsed.get("window_title")
            if not isinstance(window_title, str):
                window_title = ""
            snapshot = (presence, window_title)
            if snapshot == self._last_observation:
                return None
            self._last_observation = snapshot
            content = f"用户状态：{presence}"
            if window_title:
                content += f"；正在浏览：{window_title}"
            summary = str(parsed.get("summary") or f"用户（{presence}）")
            memory = _new_memory(
                content,
                MemoryKind.USER_PROFILE,
                summary,
                MemoryType.LONG_TERM,
                aspect=["presence", "window_title"],
            )
        else:
            mapped = _activity_memory_fields(
                event.content.get("type"), event.content.get("result")
            )
            if mapped is None:
                return None
            content, summary, activity_type = mapped
            memory = _new_memory(
                content,
                MemoryKind.ACTIVITY,
                summary,
                MemoryType.SHORT_TERM,
                [activity_type],
            )
        if self._embed is not None:
            try:
                memory.embedding = await self._embed(memory.content)
            except Exception:
                self._logger.exception("记忆 embedding 失败 memory_id=%s", memory.id)
        return memory

    async def _prepare_memory_embedding(self, memory: Memory) -> None:
        """在进入核心记忆事务前完成可耗时的向量计算。"""
        if self._embed is None or memory.embedding is not None:
            return
        try:
            memory.embedding = await self._embed(memory.content)
        except Exception:
            self._logger.exception("记忆 embedding 失败 memory_id=%s", memory.id)

    async def _remember_activity(
        self, event: Event, *, in_transaction: bool,
        defer_best_effort: bool = False,
        prepared_memory: Memory | None = None,
    ) -> _PersistResult | None:
        if prepared_memory is not None:
            return await self._persist_memory(
                prepared_memory,
                event.correlation_id,
                in_transaction=in_transaction,
                defer_best_effort=defer_best_effort,
                embedding_prepared=True,
            )
        if event.content.get("type") == "observe_user":
            return await self._sediment_observation(
                event,
                in_transaction=in_transaction,
                defer_best_effort=defer_best_effort,
            )
        mapped = _activity_memory_fields(
            event.content.get("type"), event.content.get("result")
        )
        if mapped is None:
            return
        content, summary, _activity_type = mapped
        memory = _new_memory(
            content,
            MemoryKind.ACTIVITY,
            summary,
            MemoryType.SHORT_TERM,
            [_activity_type],
        )
        return await self._persist_memory(
            memory, event.correlation_id, in_transaction=in_transaction,
            defer_best_effort=defer_best_effort,
        )

    async def _sediment_observation(
        self, event: Event, *, in_transaction: bool = False,
        defer_best_effort: bool = False,
    ) -> _PersistResult | None:
        """观察活动 → 用户画像沉淀：「presence/window_title 相对上次变化」才写。

        观察 result 非空 presence 视为有效观察（旧 shape 或缺 presence 跳过）；
        同快照重复上报不沉淀（防膨胀）。产出一条 user_profile 长期记忆。
        """
        result = event.content.get("result")
        if not isinstance(result, dict):
            return None
        parsed = cast(dict[str, Any], result)
        presence = parsed.get("presence")
        if not isinstance(presence, str) or not presence:
            return None
        window_title = parsed.get("window_title")
        if not isinstance(window_title, str):
            window_title = ""
        snapshot = (presence, window_title)
        if snapshot == self._last_observation:
            return None
        self._last_observation = snapshot
        content = f"用户状态：{presence}"
        if window_title:
            content += f"；正在浏览：{window_title}"
        summary = str(parsed.get("summary") or f"用户（{presence}）")
        return await self._remember_user_profile(
            content,
            summary,
            ["presence", "window_title"],
            event.correlation_id,
            in_transaction=in_transaction,
            defer_best_effort=defer_best_effort,
        )

    async def remember_user_profile(
        self,
        content: str,
        summary: str,
        aspects: list[str],
        correlation_id: str,
    ) -> None:
        """用户画像记忆：把观察到的用户状态落成一条长期 user_profile 记忆。

        无开头 LLM（content/summary/aspects 由调用方确定性拼好，贴「禁编造」）；
        复用同一入库尾段（embed → 建边 → 门控矛盾检测 → 淘汰）。type=LONG_TERM
        使其豁免短期淘汰（_decay_and_evict 只淘汰短期），画像不随时间冲掉。
        """
        await self._remember_user_profile(
            content, summary, aspects, correlation_id, in_transaction=False
        )

    async def _remember_user_profile(
        self,
        content: str,
        summary: str,
        aspects: list[str],
        correlation_id: str,
        *,
        in_transaction: bool,
        defer_best_effort: bool = False,
    ) -> _PersistResult:
        memory = _new_memory(
            content, MemoryKind.USER_PROFILE, summary, MemoryType.LONG_TERM,
            aspect=aspects,
        )
        return await self._persist_memory(
            memory,
            correlation_id,
            in_transaction=in_transaction,
            defer_best_effort=defer_best_effort,
        )

    async def remember_knowledge(
        self, items: list[dict[str, str]], correlation_id: str
    ) -> None:
        """读书提取的客观知识点入长期记忆（kind=knowledge，无 LLM，确定性拼好）。

        items 每项 {topic, content}；content 空则跳过。复用 _persist_memory
        入库尾段（embed → 建边 → 门控矛盾检测 → 淘汰）。type=LONG_TERM 使其
        豁免短期淘汰，知识点不随时间冲掉，供创作时检索参考（list_memories）。
        """
        for item in items:
            content = (item.get("content") or "").strip()
            topic = (item.get("topic") or "").strip()
            if not content:
                continue
            memory = _new_memory(
                content, MemoryKind.KNOWLEDGE, topic or content[:_SUMMARY_MAX_CHARS],
                MemoryType.LONG_TERM, [topic] if topic else [],
            )
            await self._persist_memory(memory, correlation_id)

    async def remember_reading(
        self, content: str, summary: str, correlation_id: str
    ) -> None:
        """读书记忆：章末/整本整合产物落成一条长期 reading 记忆。

        无开头 LLM（content/summary 由阅读整合流程拼好，这里只入库）；
        复用同一入库尾段（embed → 建边 → 门控矛盾检测 → 淘汰）。
        type=LONG_TERM 使其豁免短期淘汰，读书记忆不随时间冲掉。
        """
        memory = _new_memory(content, MemoryKind.READING, summary, MemoryType.LONG_TERM)
        await self._persist_memory(memory, correlation_id)

    async def record_no_answer(self, question: str, correlation_id: str) -> None:
        """用户未回答尼克斯的提问：落一条确定性的 SHORT_TERM 记忆（无 LLM）。

        问句本身已由慢通道场景化记忆记过，这里只补「没答」这半句；
        复用同一入库尾段（embed → 建边 → 门控矛盾检测 → 淘汰）。
        """
        memory = _new_memory(
            f"我问了「{question}」，用户没有回答。",
            MemoryKind.INTERACTION,
            "用户没有回答我的提问",
            MemoryType.SHORT_TERM,
        )
        await self._persist_memory(memory, correlation_id)

    async def _persist_memory(
        self,
        memory: Memory,
        correlation_id: str,
        *,
        in_transaction: bool = False,
        defer_best_effort: bool = False,
        embedding_prepared: bool = False,
    ) -> _PersistResult:
        """已构建 Memory 的共用入库尾段，带两层去重：

        1) 精确去重：content 完全相同（哈希命中）→ 合并强化旧记忆，不新建；
        2) 语义去重：embedding 余弦 top-1 ≥ 阈值 → 合并强化最相似旧记忆，不新建。

        命中返回已存在的持久化记忆，否则补 embed → add → 建边 → 门控矛盾检测
        → 新鲜度衰减/淘汰 → 发 MEMORY_CREATED，返回新记忆。场景/活动/画像/
        知识记忆复用。"""
        now = time.time()
        existing = await self._store.find_by_content(memory.content, memory.kind)
        if existing is not None:
            await self._store.strengthen(existing.id, now)
            return _PersistResult(await self._persisted_or(existing), [], False)
        if (
            self._embed is not None
            and memory.embedding is None
            and not embedding_prepared
        ):
            try:
                memory.embedding = await self._embed(memory.content)
            except Exception:
                self._logger.exception("记忆 embedding 失败 memory_id=%s", memory.id)
        candidates: list[PersistSemanticHit] = []
        if memory.embedding is not None:
            memories = await self._store.list_memories()
            index = AnnIndex.build(memories)
            by_id = {candidate.id: candidate for candidate in memories}
            same_kind_ids = {
                candidate.id
                for candidate in memories
                if candidate.kind is memory.kind
            }
            dedup_candidates = self._persist_semantic_candidates(
                memory.embedding,
                index,
                by_id,
                allowed_ids=same_kind_ids,
            )
            if dedup_candidates:
                threshold = (
                    _EPISODE_DEDUP_SIM_THRESHOLD
                    if memory.kind is MemoryKind.EPISODE
                    else _DEDUP_SIM_THRESHOLD
                )
                candidate = dedup_candidates[0]
                close_enough = (
                    memory.kind is not MemoryKind.EPISODE
                    or abs(memory.created_at - candidate.memory.created_at)
                    <= _EPISODE_DEDUP_WINDOW_SECONDS
                )
                if (
                    candidate.cosine >= threshold
                    and close_enough
                    and _semantic_dedup_compatible(memory, candidate.memory)
                ):
                    await self._store.strengthen(candidate.memory.id, now)
                    return _PersistResult(
                        await self._persisted_or(candidate.memory),
                        dedup_candidates,
                        False,
                    )
            candidates = self._persist_semantic_candidates(
                memory.embedding, index, by_id
            )
        await self._store.add(memory)
        if not defer_best_effort:
            await self._run_best_effort_tail(memory, candidates, correlation_id)
        event = internal_event(
            EventType.MEMORY_CREATED, {"memory_id": memory.id}, correlation_id
        )
        if in_transaction:
            await self._bus.append_in_transaction(event)
        else:
            await self._bus.publish(event)
        return _PersistResult(memory, candidates, True)

    async def _run_best_effort_tail(
        self,
        memory: Memory,
        candidates: list[PersistSemanticHit],
        correlation_id: str,
    ) -> None:
        now = time.time()
        await self._build_edges(memory, candidates, now, correlation_id)
        await self._detect_contradiction(memory, candidates, correlation_id)
        await self._decay_and_evict(now)

    async def search(self, query: str) -> list[Memory]:
        return await self._retrieval.search(query)

    async def record_recall(self, memory_id: str) -> None:
        """记录一次「想起」：recall_count+1；
        短期满 promote_threshold 次升级长期并发布 memory_promoted。"""
        event: Event | None = None
        async with self._store.db.transaction():
            promoted = await self._store.record_recall(
                memory_id, self._config.promote_threshold
            )
            if promoted:
                event = internal_event(
                    EventType.MEMORY_PROMOTED, {"memory_id": memory_id}, memory_id
                )
                await self._bus.append_in_transaction(event)
        if event is not None:
            await self._bus.announce_committed(event)

    async def list_memories(
        self,
        kind: MemoryKind | None = None,
        type: MemoryType | None = None,
        limit: int | None = None,
    ) -> list[Memory]:
        return await self._store.list_memories(kind, type, limit)

    async def count_new(self, kind: MemoryKind | None, since: float) -> int:
        """计数「首次创建晚于 since 的 kind 记忆」（轻量，不物化整行/embedding）。"""
        return await self._store.count_new(kind, since)

    async def _persisted_or(self, fallback: Memory) -> Memory:
        persisted = await self._store.get(fallback.id)
        return persisted if persisted is not None else fallback

    async def export(self, fmt: str) -> str:
        """记忆导出：json = JSON 数组字符串，
        md = 每记忆一个「## 总结 + 正文 + 标签」段。"""
        memories = await self._store.list_memories()
        if fmt == "json":
            return json.dumps(
                [_memory_to_dict(m) for m in memories], ensure_ascii=False, indent=2
            )
        if fmt == "md":
            return "\n\n".join(_memory_to_markdown(m) for m in memories)
        raise ValueError(f"未知导出格式 {fmt!r}（应为 json/md）")

    async def _detect_contradiction(
        self,
        memory: Memory,
        candidates: list[PersistSemanticHit],
        correlation_id: str,
        *,
        in_transaction: bool = False,
    ) -> None:
        """门控矛盾检测：召回 top-K 相似候选，相似度过阈值的才发独立 LLM 判断；
        无候选或全低于阈值 → 0 调用跳过。命中矛盾 → 发布 reflection。"""
        contradiction_candidates = [
            hit.memory
            for hit in candidates[:_CONTRADICTION_CANDIDATE_K]
            if hit.cosine >= _CONTRADICTION_SIM_THRESHOLD
        ]
        if not contradiction_candidates:
            return
        allowed_ids = {candidate.id for candidate in contradiction_candidates}
        try:
            output = await self._llm.complete(
                [
                    {"role": "system", "content": _CONTRADICTION_SYSTEM},
                    {
                        "role": "user",
                        "content": _build_contradiction_prompt(
                            memory, contradiction_candidates
                        ),
                    },
                ],
                module="memory",
                output_type="contradiction",
                correlation_id=correlation_id,
                json_mode=True,
            )
            await self._evaluator.evaluate(output)
            conflicts_with = _parse_contradiction(output.content)
        except Exception:
            # 矛盾检测是 best-effort：记忆已合法入库，判矛盾失败
            # （传输超时/5xx、JSON 结构非法）不反噬记忆创建主流程，
            # 只跳过本次 reflection（漏报优于误报，同 _parse_contradiction 默认）。
            self._logger.exception(
                "矛盾检测失败 memory_id=%s correlation_id=%s",
                memory.id, correlation_id,
            )
            return
        if conflicts_with is not None and conflicts_with not in allowed_ids:
            self._logger.warning(
                "矛盾检测返回未知候选 memory_id=%s new_memory_id=%s correlation_id=%s",
                conflicts_with,
                memory.id,
                correlation_id,
            )
            return
        if conflicts_with is not None:
            event = internal_event(
                EventType.REFLECTION,
                {
                    "summary": (
                        f"场景记忆 {memory.id} 与旧记忆 {conflicts_with} 矛盾，"
                        "触发反思"
                    )
                },
                correlation_id,
            )
            if in_transaction:
                await self._bus.append_in_transaction(event)
            else:
                await self._bus.publish(event)

    def _persist_semantic_candidates(
        self,
        embedding: list[float],
        index: AnnIndex,
        by_id: dict[str, Memory],
        *,
        allowed_ids: set[str] | None = None,
    ) -> list[PersistSemanticHit]:
        return [
            PersistSemanticHit(by_id[candidate.memory_id], candidate.cosine)
            for candidate in index.query(
                embedding,
                candidate_k=_PERSIST_SEMANTIC_CANDIDATE_K,
                allowed_ids=allowed_ids,
            )
            if candidate.memory_id in by_id
        ]

    async def _build_edges(
        self,
        memory: Memory,
        persist_semantic_hits: list[PersistSemanticHit],
        now: float,
        correlation_id: str,
    ) -> None:
        """Build typed graph edges from semantic/entity/keyword/time/LLM signals."""
        old_memories = [
            old for old in await self._store.list_memories() if old.id != memory.id
        ]
        by_id = {old.id: old for old in old_memories}
        if not by_id:
            return

        semantic_scores = await self._semantic_edge_scores(
            memory, persist_semantic_hits, by_id
        )
        entity_scores = self._entity_edge_scores(memory, persist_semantic_hits, by_id)
        keyword_scores = await self._keyword_edge_scores(memory, by_id)
        temporal_scores = self._temporal_edge_scores(memory, by_id)

        edge_scores: dict[MemoryEdgeKind, dict[str, float]] = {
            MemoryEdgeKind.SEMANTIC: semantic_scores,
            MemoryEdgeKind.ENTITY: entity_scores,
            MemoryEdgeKind.KEYWORD: keyword_scores,
            MemoryEdgeKind.TEMPORAL: temporal_scores,
        }
        relation_candidates = self._relation_candidates(
            semantic_scores, entity_scores, keyword_scores, temporal_scores, by_id
        )
        for old_id, kind, weight in await self._relation_edges(
            memory, relation_candidates, correlation_id
        ):
            edge_scores.setdefault(kind, {})[old_id] = weight

        touched = {memory.id}
        for kind, scores in edge_scores.items():
            ranked_scores = _rank_edge_scores(scores, by_id)[:_EDGE_PER_KIND_LIMIT]
            for old_id, weight in ranked_scores:
                await self._store.upsert_edge(memory.id, old_id, kind, weight, now)
                touched.add(old_id)
        await self._prune_degrees(touched)

    async def _semantic_edge_scores(
        self,
        memory: Memory,
        persist_semantic_hits: list[PersistSemanticHit],
        by_id: dict[str, Memory],
    ) -> dict[str, float]:
        hits = persist_semantic_hits[:_EDGE_SEMANTIC_CANDIDATE_K]
        if not hits and self._embed is not None:
            try:
                embedding = await self._embed(f"{memory.summary}\n{memory.content}")
            except Exception:
                self._logger.exception(
                    "记忆语义建边 embedding 失败 memory_id=%s", memory.id
                )
                embedding = None
            if embedding is not None:
                memories = list(by_id.values())
                index = AnnIndex.build(memories)
                indexed_by_id = {old.id: old for old in memories}
                hits = [
                    PersistSemanticHit(indexed_by_id[candidate.memory_id],
                                       candidate.cosine)
                    for candidate in index.query(
                        embedding, candidate_k=_EDGE_SEMANTIC_CANDIDATE_K
                    )
                    if candidate.memory_id in indexed_by_id
                ]

        scores: dict[str, float] = {}
        for hit in hits:
            old = by_id.get(hit.memory.id)
            if old is None or old.embedding is None:
                continue
            score = _vector_score(hit.cosine)
            if score >= 0.72:
                scores[old.id] = max(scores.get(old.id, 0.0), score)
        return scores

    def _entity_edge_scores(
        self,
        memory: Memory,
        persist_semantic_hits: list[PersistSemanticHit],
        by_id: dict[str, Memory],
    ) -> dict[str, float]:
        new_entities = extract_entities(memory)
        if not new_entities:
            return {}
        pool = [
            hit.memory
            for hit in persist_semantic_hits[:_EDGE_ENTITY_CANDIDATE_K]
            if hit.memory.id in by_id
        ]
        if not pool:
            pool = sorted(
                by_id.values(), key=lambda old: (-old.created_at, old.id)
            )[:_EDGE_ENTITY_CANDIDATE_K]

        scores: dict[str, float] = {}
        for old in pool:
            old_entities = extract_entities(old)
            shared = len(new_entities & old_entities)
            score = shared / max(len(new_entities), len(old_entities), 1)
            if score >= _ENTITY_THRESHOLD:
                scores[old.id] = score
        return scores

    async def _keyword_edge_scores(
        self, memory: Memory, by_id: dict[str, Memory]
    ) -> dict[str, float]:
        new_tokens_list = extract_keywords(f"{memory.summary}\n{memory.content}")
        new_tokens = set(new_tokens_list)
        if not new_tokens:
            return {}
        keyword_hits = await self._store.search_keywords(
            new_tokens_list, limit=_EDGE_KEYWORD_CANDIDATE_K
        )
        scores: dict[str, float] = {}
        for old_id in keyword_hits:
            old = by_id.get(old_id)
            if old is None:
                continue
            old_tokens = set(extract_keywords(f"{old.summary}\n{old.content}"))
            score = _keyword_jaccard(new_tokens, old_tokens)
            if score >= _KEYWORD_THRESHOLD:
                scores[old_id] = score
        return scores

    def _temporal_edge_scores(
        self, memory: Memory, by_id: dict[str, Memory]
    ) -> dict[str, float]:
        candidates = sorted(
            by_id.values(),
            key=lambda old: (abs(old.created_at - memory.created_at), old.id),
        )[:_EDGE_TEMPORAL_CANDIDATE_K]
        scores: dict[str, float] = {}
        for old in candidates:
            score = _temporal_score(memory.created_at, old.created_at)
            if score > 0.0:
                scores[old.id] = score
        return scores

    def _relation_candidates(
        self,
        semantic_scores: dict[str, float],
        entity_scores: dict[str, float],
        keyword_scores: dict[str, float],
        temporal_scores: dict[str, float],
        by_id: dict[str, Memory],
    ) -> list[Memory]:
        scores: dict[str, float] = {}
        for old_id in (
            set(semantic_scores)
            | set(entity_scores)
            | set(keyword_scores)
            | set(temporal_scores)
        ):
            scores[old_id] = max(
                semantic_scores.get(old_id, 0.0),
                0.9 * entity_scores.get(old_id, 0.0),
                0.75 * keyword_scores.get(old_id, 0.0),
                0.35 * temporal_scores.get(old_id, 0.0),
            )
        ranked = sorted(
            scores,
            key=lambda old_id: (-scores[old_id], -by_id[old_id].created_at, old_id),
        )
        return [by_id[old_id] for old_id in ranked[:_EDGE_LLM_CANDIDATE_K]]

    async def _relation_edges(
        self,
        memory: Memory,
        candidates: list[Memory],
        correlation_id: str,
    ) -> list[tuple[str, MemoryEdgeKind, float]]:
        if not candidates:
            return []
        allowed_ids = {candidate.id for candidate in candidates}
        try:
            output = await self._llm.complete(
                [
                    {"role": "system", "content": "你是记忆关系抽取器，只输出 JSON。"},
                    {
                        "role": "user",
                        "content": _memory_relation_prompt(memory, candidates),
                    },
                ],
                module="memory",
                output_type="memory_relation",
                correlation_id=correlation_id,
                json_mode=True,
            )
            return _parse_relation_edges(output.content, allowed_ids)
        except Exception:
            self._logger.exception(
                "记忆关系抽取失败 memory_id=%s correlation_id=%s",
                memory.id, correlation_id,
            )
            return []

    async def _prune_degrees(self, touched: set[str]) -> None:
        if not touched:
            return
        while True:
            degrees = await self._store.list_edge_degrees(sorted(touched))
            delete_keys: set[tuple[str, str, MemoryEdgeKind]] = set()
            for edges in degrees.values():
                by_kind: dict[MemoryEdgeKind, list[MemoryEdge]] = {}
                for edge in edges:
                    by_kind.setdefault(edge.kind, []).append(edge)
                for kind_edges in by_kind.values():
                    if len(kind_edges) <= _EDGE_PER_KIND_LIMIT:
                        continue
                    overflow = sorted(kind_edges, key=_prune_priority)[
                        : len(kind_edges) - _EDGE_PER_KIND_LIMIT
                    ]
                    delete_keys.update(_edge_key(edge) for edge in overflow)

                remaining = [
                    edge for edge in edges if _edge_key(edge) not in delete_keys
                ]
                if len(remaining) > _EDGE_TOTAL_LIMIT:
                    overflow = sorted(remaining, key=_prune_priority)[
                        : len(remaining) - _EDGE_TOTAL_LIMIT
                    ]
                    delete_keys.update(_edge_key(edge) for edge in overflow)
            if not delete_keys:
                return
            await self._store.delete_edges(
                sorted(delete_keys, key=lambda key: (key[0], key[1], key[2].value))
            )

    async def _decay_and_evict(self, now: float) -> None:
        """新鲜度统一衰减（回写）+ 短期容量淘汰（满则挤掉最新鲜度最低的）。"""
        memories = await self._store.list_memories()
        changed: list[Memory] = []
        for m in memories:
            decayed = decay_freshness(
                m.freshness, m.created_at, now, self._config.freshness_decay
            )
            if decayed != m.freshness:
                m.freshness = decayed
                changed.append(m)
        if changed:
            await self._store.update_many(changed)
        short_term = [m for m in memories if m.type is MemoryType.SHORT_TERM]
        if len(short_term) > self._config.short_term_capacity:
            short_term.sort(key=lambda m: (m.freshness, m.created_at))
            overflow = len(short_term) - self._config.short_term_capacity
            overflow_ids = [m.id for m in short_term[:overflow]]
            await self._store.delete_many(overflow_ids)
