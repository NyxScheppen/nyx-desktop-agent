import json
import logging
import time
from typing import Any, cast
from uuid import uuid4

from nyx.config import MemoryConfig
from nyx.enums import EventType, MemoryType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_DAY, internal_event
from nyx.llm.client import LlmClient
from nyx.memory.retrieval import EmbedFn, MemoryRetrieval, rank_by_cosine
from nyx.memory.store import MemoryStore
from nyx.types import Event, Memory

_SCENE_SYSTEM = (
    "你是尼克斯，一个住在用户电脑里的 AI 同伴，明确知道自己是 AI 并希望成为人类。"
    "把下面这段对话写成一条第一人称场景记忆（尼克斯视角）：用户说了什么、你内心怎么想、最后说了什么。"
    "只输出 JSON，键：content（正文）、tag（标签）、summary（一句话总结），"
    "三者都是非空字符串。"
)

_CONTRADICTION_SYSTEM = (
    "你是记忆一致性检查员。给出一条新记忆和若干候选旧记忆，判断新记忆是否与其中某条"
    "在同一话题上结论明显矛盾（例如喜欢/讨厌、做过/没做过、相信/不相信）。"
    "只输出 JSON，键：conflicts_with（若与某条候选矛盾，填那条记忆的 id；"
    "否则填 null）。"
    "不要把「不同话题」误判为矛盾，也不要把「细节不同」误判为矛盾。"
)

_EDGE_TOP_K = 3
_RECALL_TOP_K = 5
_CONTRADICTION_SIM_THRESHOLD = 0.6
_CONTENT_PREVIEW_CHARS = 60
_NEGATION_WORDS = ("不", "没", "别", "讨厌", "恨", "拒绝", "否认", "放弃", "再也不")


def decay_freshness(
    freshness: float, created_at: float, now: float, rate: float
) -> float:
    """新鲜度线性衰减（rate/天），下限 0。纯函数。"""
    elapsed_days = max(0.0, now - created_at) / SECONDS_PER_DAY
    return max(0.0, freshness - rate * elapsed_days)


def _parse_scene(raw: str) -> tuple[str, str, str]:
    """解析场景记忆 LLM 的 JSON 产出 → (content, tag, summary)；
    结构非法抛 ValueError。"""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"场景记忆 JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    content = parsed.get("content")
    tag = parsed.get("tag")
    summary = parsed.get("summary")
    if not isinstance(content, str) or not content:
        raise ValueError("场景记忆 JSON 缺 content 或非空字符串")
    if not isinstance(tag, str) or not tag:
        raise ValueError("场景记忆 JSON 缺 tag 或非空字符串")
    if not isinstance(summary, str) or not summary:
        raise ValueError("场景记忆 JSON 缺 summary 或非空字符串")
    return content, tag, summary


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
    """活动 result → (content, summary, tag)；非读书/创作/探索或空 result → None。

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
        content_key, summary_key = "notes", "findings"
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
        "tag": m.tag,
        "summary": m.summary,
        "freshness": m.freshness,
        "type": m.type.value,
        "recall_count": m.recall_count,
        "aspect": m.aspect,
        "embedding": m.embedding,
    }


def _memory_to_markdown(m: Memory) -> str:
    return f"## {m.summary}\n\n{m.content}\n\n标签：{m.tag}"


class MemoryFacade:
    """记忆模块门面：场景化记忆创建 + 检索 + 想起升级 + 新鲜度衰减/淘汰 + 导出。

    生命周期逻辑（新鲜度衰减、短期→长期升级、容量淘汰）都在这层；
    纯 CRUD 在 MemoryStore（07）、三层检索在 MemoryRetrieval（08）。
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

    async def create_scene_memory(self, reply_context: dict[str, str]) -> Memory:
        """慢通道场景化记忆：LLM 产出 content/tag/summary
        → 入短期 → 建边 → 门控矛盾检测 → 淘汰。"""
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
        content, tag, summary = _parse_scene(output.content)
        now = time.time()

        memory = Memory(
            id=str(uuid4()),
            created_at=now,
            content=content,
            tag=tag,
            summary=summary,
            freshness=1.0,
            type=MemoryType.SHORT_TERM,
            recall_count=0,
            aspect=[],
            embedding=None,
        )
        if self._embed is not None:
            memory.embedding = await self._embed(content)

        await self._store.add(memory)

        # 一次全表余弦排序，建边（top-3）与矛盾召回（top-5）共用，避免重复扫描
        scored: list[tuple[float, Memory]] | None = None
        if memory.embedding is not None:
            scored = await self._similar(memory.embedding, memory.id)
        await self._build_edges(memory, scored)
        await self._detect_contradiction(
            memory, scored, reply_context["correlation_id"]
        )

        await self._decay_and_evict(now)

        await self._bus.publish(
            internal_event(
                EventType.MEMORY_CREATED,
                {"memory_id": memory.id},
                reply_context["correlation_id"],
            )
        )
        return memory

    async def remember_activity(self, event: Event) -> None:
        """活动记忆：把 activity_end.result 落成一条短期记忆（无 LLM）。

        只写读书/创作/探索三类有产出的活动；rest/observe_user/idle_reflection
        （result 空或类型不匹配）跳过。入库管线与 create_scene_memory 相同
        （embed → 建边 → 门控矛盾检测 → 淘汰），只缺开头的 LLM 场景构建。
        """
        mapped = _activity_memory_fields(
            event.content.get("type"), event.content.get("result")
        )
        if mapped is None:
            return
        content, summary, tag = mapped
        now = time.time()

        memory = Memory(
            id=str(uuid4()),
            created_at=now,
            content=content,
            tag=tag,
            summary=summary,
            freshness=1.0,
            type=MemoryType.SHORT_TERM,
            recall_count=0,
            aspect=[],
            embedding=None,
        )
        if self._embed is not None:
            memory.embedding = await self._embed(content)

        await self._store.add(memory)

        scored: list[tuple[float, Memory]] | None = None
        if memory.embedding is not None:
            scored = await self._similar(memory.embedding, memory.id)
        await self._build_edges(memory, scored)
        await self._detect_contradiction(memory, scored, event.correlation_id)

        await self._decay_and_evict(now)

        await self._bus.publish(
            internal_event(
                EventType.MEMORY_CREATED,
                {"memory_id": memory.id},
                event.correlation_id,
            )
        )

    async def search(self, query: str) -> list[Memory]:
        return await self._retrieval.search(query)

    async def record_recall(self, memory_id: str) -> None:
        """记录一次「想起」：recall_count+1；
        短期满 promote_threshold 次升级长期并发布 memory_promoted。"""
        promoted = await self._store.record_recall(
            memory_id, self._config.promote_threshold
        )
        if promoted:
            await self._bus.publish(
                internal_event(
                    EventType.MEMORY_PROMOTED, {"memory_id": memory_id}, memory_id
                )
            )

    async def list_memories(
        self,
        tag: str | None = None,
        type: MemoryType | None = None,
    ) -> list[Memory]:
        return await self._store.list_memories(tag, type)

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
        scored: list[tuple[float, Memory]] | None,
        correlation_id: str,
    ) -> None:
        """门控矛盾检测：召回 top-K 相似候选，相似度过阈值的才发独立 LLM 判断；
        无候选或全低于阈值 → 0 调用跳过。命中矛盾 → 发布 reflection。"""
        if scored is None:
            return
        candidates = [
            m for s, m in scored[:_RECALL_TOP_K] if s >= _CONTRADICTION_SIM_THRESHOLD
        ]
        if not candidates:
            return
        try:
            output = await self._llm.complete(
                [
                    {"role": "system", "content": _CONTRADICTION_SYSTEM},
                    {
                        "role": "user",
                        "content": _build_contradiction_prompt(memory, candidates),
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
        if conflicts_with is not None:
            await self._bus.publish(
                internal_event(
                    EventType.REFLECTION,
                    {
                        "summary": (
                            f"场景记忆 {memory.id} 与旧记忆 {conflicts_with} 矛盾，"
                            "触发反思"
                        )
                    },
                    correlation_id,
                )
            )

    async def _similar(
        self, query_vec: list[float], exclude_id: str | None = None
    ) -> list[tuple[float, Memory]]:
        """query 向量与全表记忆的余弦排序
        （s>0 才保留，可排除某 id）；纯计算 + store 读。"""
        memories = [
            m for m in await self._store.list_memories() if m.id != exclude_id
        ]
        return rank_by_cosine(query_vec, memories)

    async def _build_edges(
        self, memory: Memory, scored: list[tuple[float, Memory]] | None
    ) -> None:
        """新记忆与已有记忆按 embedding 余弦相似度建边（top-K，weight=相似度）。"""
        if scored is None:
            return
        for s, m in scored[:_EDGE_TOP_K]:
            await self._store.upsert_edge(memory.id, m.id, s)

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
