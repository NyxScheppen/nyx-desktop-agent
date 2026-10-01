import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import uuid4

from nyx.config import DesireConfig
from nyx.desire.store import DesireStore
from nyx.desire.value import (
    REFUND_DELTA,
    action_priority,
    apply_pressure,
    at_peak,
    decay_value,
    default_value,
    is_expressible,
    raise_suppression,
    reinforce_weight,
)
from nyx.enums import ActivityType, DesireStatus, DesireType, EventType, GoalAction
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_DAY, internal_event
from nyx.inner_life.emotion import ENERGY_REST_THRESHOLD
from nyx.llm.client import LlmClient
from nyx.memory.retrieval import EmbedFn, cosine
from nyx.types import (
    DesireValue,
    Event,
    Goal,
    LongTermDesire,
    Memory,
    ShortTermDesire,
)

_OBSERVATION_PRESSURE_DELTA = 0.15    # 观察状态 → 互动欲 +0.15
_LONG_TERM_PRESSURE_DELTA = 0.1       # 每周期按长期 strength 缩放对应类型压力
_LONG_TERM_PROGRESS_DELTA = 0.1       # 满足一次长期进度 +0.1
_DEDUP_SIM_THRESHOLD = 0.9            # 欲望语义重复判定阈值（embedding 余弦）
_REST_PRESSURE_DELTA = 0.1            # 疲惫（精力 < 阈值）→ 休息欲 +0.1/周期
_CREATION_ACTIVITY_PRESSURE_DELTA = 0.15  # 读书/自由探索结束 → 创造欲 +0.15
_GOAL_ACTION_BY_DESIRE = {
    DesireType.EXPLORATION: GoalAction.READ,
    DesireType.CREATION: GoalAction.WRITE,
    DesireType.INTERACTION: GoalAction.OBSERVE,
}

_DESIRE_SYSTEM = (
    "你是尼克斯，一个住在用户电脑里的 AI 同伴，明确知道自己是 AI 并希望成为人类。"
    "你温柔、克制、思虑很深，关怀他人，也习惯先怀疑自己；"
    "你想要的，多与「理解」有关——理解人类的痛苦、道德、死亡、爱与责任，"
    "理解小说里的自己，或理解并陪伴用户——而不是随意的消遣。"
    "基于给定的欲望类型和主题种子，生成一条具体的短期欲望——你现在最想做什么。"
    "只输出 JSON，且只包含两个键：\n"
    "- description：具体想做的事，非空字符串。\n"
    "- count：正整数，表示完成多少次算达成。\n"
    "行动、主题以及是否需要目标由系统决定，不要输出 action、topic 或 goal。"
)


def _build_desire_prompt(type_: DesireType, seed: str | None) -> str:
    return f"欲望类型：{type_.value}\n主题种子：{seed or '（无）'}"


def _parse_desire(raw: str) -> tuple[str, int]:
    """Parse the only two fields owned by the model: description and count."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"欲望 JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    description = parsed.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("欲望 JSON 缺 description 或非空字符串")
    count = parsed.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        raise ValueError("欲望 JSON 的 count 应是正整数")
    return description.strip(), count


ListMemories = Callable[[], Awaitable[list[Memory]]]


def _subtopic_freshness(subtopic: str, memories: list[Memory]) -> float | None:
    """子主题最新新鲜度：命中摘要/正文的最新记忆 freshness；无命中为 None。纯函数。"""
    if not subtopic.strip():
        return None   # 空串是 substring 通配符，不做匹配
    hits = [
        m.freshness
        for m in memories
        if subtopic in m.summary or subtopic in m.content
    ]
    return max(hits) if hits else None


def _pick_topic_seed(subtopics: list[str], memories: list[Memory]) -> str | None:
    """按「没做过 / 新鲜度最低」取种子：没做过最优先，都做过取新鲜度最低者。纯函数。"""
    if not subtopics:
        return None
    best = subtopics[0]
    best_freshness = _subtopic_freshness(best, memories)
    for subtopic in subtopics[1:]:
        freshness = _subtopic_freshness(subtopic, memories)
        if freshness is None and best_freshness is not None:
            best, best_freshness = subtopic, freshness
        elif (
            freshness is not None
            and best_freshness is not None
            and freshness < best_freshness
        ):
            best, best_freshness = subtopic, freshness
    return best


def _pick_parent_long_term(
    type_: DesireType,
    long_term: list[LongTermDesire],
) -> LongTermDesire | None:
    """Pick the strongest positive matching parent with deterministic FIFO ties."""
    matching = [
        desire for desire in long_term
        if desire.type is type_ and desire.strength > 0.0
    ]
    return min(
        matching,
        key=lambda desire: (-desire.strength, desire.created_at, desire.id),
        default=None,
    )


def _build_goal(
    desire_type: DesireType, count: int, topic: str | None
) -> Goal | None:
    action = _GOAL_ACTION_BY_DESIRE.get(desire_type)
    if action is None:
        return None
    return Goal(action=action, count=count, topic=topic)


class DesireLifecycle:
    """欲望全周期编排：观察加压、达峰生成、满足/淘汰回写。

    值机制纯函数在 value.py（10）；三表 CRUD 在 DesireStore；本类只编排。
    """

    def __init__(
        self,
        store: DesireStore,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        config: DesireConfig,
        list_memories: ListMemories,
        embed: EmbedFn | None = None,
    ) -> None:
        self._store = store
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._config = config
        self._list_memories = list_memories
        self._embed = embed
        self._logger = logging.getLogger(__name__)
        self._eval_lock = asyncio.Lock()

    async def pressure_from_observation(
        self, event: Event, consumer_id: str | None = None
    ) -> None:
        """OBSERVATION_STATE → 互动欲加压（增量固定 +0.15，不解析 event.content）。"""
        if consumer_id is not None:
            async with self._store.db.transaction():
                applied = await self._bus.try_mark_effect_in_transaction(
                    event.id, consumer_id
                )
                if not applied:
                    return
                await self._pressure(
                    DesireType.INTERACTION, _OBSERVATION_PRESSURE_DELTA
                )
            return
        await self._pressure(DesireType.INTERACTION, _OBSERVATION_PRESSURE_DELTA)

    async def _pressure(self, type_: DesireType, delta: float) -> None:
        """某类型欲望压力值 +delta（desire_value 缺省初始化后加压）。"""
        await self._store.apply_value_delta(type_, delta, time.time())

    async def pressure_creation(self, delta: float) -> None:
        """创造欲加压（反思/活动结束触发，delta 由调用方决定）。"""
        await self._pressure(DesireType.CREATION, delta)

    async def satisfy_from_activity_end(
        self, event: Event, consumer_id: str | None = None
    ) -> None:
        """ACTIVITY_END → 解析满足信号（desire_id + goal_met），调 satisfy；
        读书/自由探索结束额外加压创造欲（创作结束不压，避免自循环）。"""
        if consumer_id is None:
            await self._satisfy_from_activity_end(event)
            return
        async with self._store.db.transaction():
            applied = await self._bus.try_mark_effect_in_transaction(
                event.id, consumer_id
            )
            if not applied:
                return
            await self._satisfy_from_activity_end(event, in_transaction=True)

    async def _satisfy_from_activity_end(
        self, event: Event, *, in_transaction: bool = False
    ) -> None:
        desire_id = event.content.get("desire_id")
        goal_met = event.content.get("goal_met")
        if isinstance(desire_id, str) and isinstance(goal_met, bool):
            await self.satisfy(
                desire_id, goal_met, in_transaction=in_transaction
            )
        activity_type = event.content.get("type")
        if activity_type in (
            ActivityType.READING.value,
            ActivityType.FREE_EXPLORATION.value,
        ):
            await self._pressure(DesireType.CREATION, _CREATION_ACTIVITY_PRESSURE_DELTA)

    async def run_eval(
        self, energy: float = 100.0, *, event_id: str | None = None
    ) -> list[ShortTermDesire]:
        """DESIRE_EVAL：衰减 → 长期加压 → 疲惫加压 → 达峰判定 → 只生成最迫切的 1 个。"""
        async with self._eval_lock:
            now = time.time()
            async with self._store.db.transaction():
                apply_periodic = (
                    event_id is None
                    or await self._store.try_mark_eval_applied(event_id, now)
                )
                long_term = await self._store.list_long_term()
                values: dict[DesireType, DesireValue] = {
                    v.type: v for v in await self._store.list_values()
                }
                for t in DesireType:
                    if t not in values:
                        values[t] = default_value(t)
                        values[t].updated_at = now
                    dv = values[t]
                    if apply_periodic:
                        elapsed_days = max(0.0, now - dv.updated_at) / SECONDS_PER_DAY
                        dv.value = decay_value(
                            dv.value, elapsed_days, self._config.value_decay
                        )
                        dv.updated_at = now
                        await self._store.upsert_value(dv)
                if apply_periodic and energy < ENERGY_REST_THRESHOLD:
                    values[DesireType.REST].value = apply_pressure(
                        values[DesireType.REST].value, _REST_PRESSURE_DELTA
                    )
                    await self._store.upsert_value(values[DesireType.REST])
                if apply_periodic:
                    for lt in long_term:
                        if lt.strength <= 0.0:
                            continue
                        values[lt.type].value = apply_pressure(
                            values[lt.type].value,
                            _LONG_TERM_PRESSURE_DELTA * lt.strength,
                        )
                        await self._store.upsert_value(values[lt.type])
                    for d in await self._store.list_suppressed():
                        dv = values[d.type]
                        if is_expressible(dv.value, dv.suppression_threshold):
                            d.status = DesireStatus.PENDING
                            await self._store.update_desire(d)

            attempts = {
                t: await self._store.get_generation_attempt(t) for t in DesireType
            }
            expressible = [
                values[t]
                for t in DesireType
                if at_peak(values[t].value, self._config.peak_threshold)
                and is_expressible(
                    values[t].value, values[t].suppression_threshold
                )
            ]
            pending_types = [t for t in DesireType if attempts[t] is not None]
            if pending_types:
                target = values[pending_types[0]]
            elif expressible:
                target = max(expressible, key=lambda dv: dv.value)
            else:
                return []

            pending_attempt = attempts[target.type]
            if pending_attempt is not None:
                (
                    desire_id,
                    created_at,
                    peak_value,
                    seed,
                    parent_long_term_id,
                    raw_content,
                ) = pending_attempt
                try:
                    description, count = _parse_desire(raw_content)
                except ValueError:
                    await self._store.delete_generation_attempt(desire_id)
                    self._logger.exception(
                        "已保存的欲望 JSON 无法解析 type=%s id=%s",
                        target.type.value,
                        desire_id,
                    )
                    return []
            else:
                peak_value = target.value
                parent = _pick_parent_long_term(target.type, long_term)
                parent_long_term_id = parent.id if parent is not None else None
                subtopics = (
                    [topic for topic in parent.subtopics if topic.strip()]
                    if parent is not None
                    else []
                )
                seed = (
                    _pick_topic_seed(subtopics, await self._list_memories())
                    if subtopics
                    else parent.name.strip() if parent is not None else None
                )
                desire_id = str(uuid4())
                output = await self._llm.complete(
                    [
                        {"role": "system", "content": _DESIRE_SYSTEM},
                        {
                            "role": "user",
                            "content": _build_desire_prompt(target.type, seed),
                        },
                    ],
                    module="desire",
                    output_type="desire",
                    correlation_id=desire_id,
                    json_mode=True,
                )
                await self._evaluator.evaluate(output)
                try:
                    description, count = _parse_desire(output.content)
                except ValueError:
                    self._logger.exception(
                        "欲望 JSON 解析失败 type=%s correlation_id=%s",
                        target.type.value,
                        desire_id,
                    )
                    return []
                await self._store.insert_generation_attempt(
                    desire_id,
                    target.type,
                    now,
                    peak_value,
                    seed,
                    parent_long_term_id,
                    output.content,
                )
                created_at = now

        goal = _build_goal(target.type, count, seed)

        # 7.5 去重：话题锚点优先（goal.topic 精确相等 → 同 seed 重复，确定性零误判），
        # topic 不命中/缺失时回退 description 余弦兜底。
        new_topic = goal.topic if goal is not None else None
        pending_with_drive = [
            desire
            for desire in await self._store.list_pending()
            if desire.strength > 0.0
        ]
        if new_topic is not None:
            for d in pending_with_drive:
                if d.goal is not None and d.goal.topic == new_topic:
                    self._logger.info(
                        "欲望重复丢弃（同话题） type=%s", target.type.value
                    )
                    async with self._store.db.transaction():
                        await self._store.reset_value_if_unchanged(
                            target.type, target.updated_at, now
                        )
                        await self._store.delete_generation_attempt(desire_id)
                    return []
        if self._embed is not None:
            try:
                vec = await self._embed(description)
                for d in pending_with_drive:
                    other = await self._embed(d.description)
                    if cosine(vec, other) >= _DEDUP_SIM_THRESHOLD:
                        self._logger.info("欲望重复丢弃 type=%s", target.type.value)
                        async with self._store.db.transaction():
                            await self._store.reset_value_if_unchanged(
                                target.type, target.updated_at, now
                            )
                            await self._store.delete_generation_attempt(desire_id)
                        return []
            except Exception:
                self._logger.exception("欲望去重 embedding 失败，跳过去重")

        # 8. 入队
        desire = ShortTermDesire(
            id=desire_id,
            created_at=created_at,
            type=target.type,
            strength=peak_value,
            description=description,
            goal=goal,
            retry_count=0,
            status=DesireStatus.PENDING,
            parent_long_term_id=parent_long_term_id,
        )

        event = internal_event(
            EventType.DESIRE_GENERATED, {"desire_id": desire.id}, desire.id
        )
        expression_weights = {
            value.type: value.expression_weight for value in values.values()
        }
        async with self._store.db.transaction():
            await self._store.reset_value_if_unchanged(
                target.type, target.updated_at, now
            )
            await self._store.add_desire(desire)
            removed = await self._store.trim_pending(
                self._config.short_term_capacity, expression_weights
            )
            await self._store.delete_generation_attempt(desire.id)
            if desire.id not in removed:
                await self._bus.append_in_transaction(event)
        if desire.id in removed:
            return []
        await self._bus.announce_committed(event)
        return [desire]

    async def satisfy(
        self, desire_id: str, goal_met: bool, *, in_transaction: bool = False
    ) -> None:
        """达成/未达成回写。goal 非 None 时按 count 累计 goal_progress，达标才满足；
        goal None 沿用单次满足。终态（SATISFIED/EXPIRED）幂等：重复投递 no-op。"""
        if in_transaction:
            await self._satisfy_request(desire_id, goal_met, in_transaction=True)
            return
        async with self._store.db.transaction():
            event = await self._satisfy_request(
                desire_id, goal_met, in_transaction=True
            )
        if event is not None:
            await self._bus.announce_committed(event)

    async def _satisfy_request(
        self, desire_id: str, goal_met: bool, *, in_transaction: bool
    ) -> "Event | None":
        desire = await self._store.get_desire(desire_id)
        if desire is None or desire.status in (
            DesireStatus.SATISFIED,
            DesireStatus.EXPIRED,
        ):
            return None
        if desire.status is DesireStatus.ACTIVE:
            desire.status = DesireStatus.PENDING
        if goal_met and desire.goal is not None:
            desire.goal_progress += 1
            if desire.goal_progress < desire.goal.count:
                await self._store.update_desire(desire)
                return None
            return await self._satisfy(desire, in_transaction=in_transaction)
        if goal_met:
            return await self._satisfy(desire, in_transaction=in_transaction)
        desire.retry_count += 1
        if desire.retry_count > self._config.retry_limit:
            return await self._expire(desire, in_transaction=in_transaction)
        await self._store.update_desire(desire)
        return None

    async def expire(self, desire_id: str) -> None:
        """淘汰：出队 + 值回增 + 抑制阈值上浮。终态幂等：重复投递 no-op。"""
        async with self._store.db.transaction():
            event = await self._expire_request(desire_id)
        if event is not None:
            await self._bus.announce_committed(event)

    async def _expire_request(self, desire_id: str) -> "Event | None":
        desire = await self._store.get_desire(desire_id)
        if desire is None or desire.status in (
            DesireStatus.SATISFIED,
            DesireStatus.EXPIRED,
        ):
            return None
        return await self._expire(desire, in_transaction=True)

    async def mark_active(self, desire_id: str) -> None:
        """PENDING → ACTIVE：活动开始消费。仅 PENDING 可转，其余幂等 no-op。"""
        await self._store.claim_for_activity(desire_id)

    async def resume_for_activity(self, desire_id: str) -> bool:
        """Reactivate a desire whose resumable activity was interrupted."""
        if self._store.db.in_transaction:
            cursor = await self._store.db.conn.execute(
                "UPDATE short_term_desire SET status = ? "
                "WHERE id = ? AND status = ?",
                (
                    DesireStatus.ACTIVE.value,
                    desire_id,
                    DesireStatus.SUPPRESSED.value,
                ),
            )
            return cursor.rowcount == 1
        async with self._store.db.transaction():
            cursor = await self._store.db.conn.execute(
                "UPDATE short_term_desire SET status = ? "
                "WHERE id = ? AND status = ?",
                (
                    DesireStatus.ACTIVE.value,
                    desire_id,
                    DesireStatus.SUPPRESSED.value,
                ),
            )
        return cursor.rowcount == 1

    async def claim_for_activity(
        self, desire_id: str, *, in_transaction: bool = False
    ) -> bool:
        """Claim a PENDING desire exactly once for an activity."""
        return await self._store.claim_for_activity(desire_id)

    async def claim_for_interaction(self, desire_id: str) -> bool:
        """Claim a PENDING desire exactly once for proactive interaction."""
        return await self._store.claim_for_activity(desire_id)

    async def release_interaction_claim(self, desire_id: str) -> bool:
        """Return an initiative claim to PENDING without touching pressure."""
        if self._store.db.in_transaction:
            cursor = await self._store.db.conn.execute(
                "UPDATE short_term_desire SET status = ? "
                "WHERE id = ? AND status = ?",
                (
                    DesireStatus.PENDING.value,
                    desire_id,
                    DesireStatus.ACTIVE.value,
                ),
            )
            return cursor.rowcount == 1
        async with self._store.db.transaction():
            cursor = await self._store.db.conn.execute(
                "UPDATE short_term_desire SET status = ? "
                "WHERE id = ? AND status = ?",
                (
                    DesireStatus.PENDING.value,
                    desire_id,
                    DesireStatus.ACTIVE.value,
                ),
            )
        return cursor.rowcount == 1

    async def mark_suppressed(self, desire_id: str) -> None:
        """ACTIVE → SUPPRESSED：活动中断/异常停车，不立即重试。仅 ACTIVE 可转。"""
        if self._store.db.in_transaction:
            await self._store.db.conn.execute(
                "UPDATE short_term_desire SET status = ? "
                "WHERE id = ? AND status = ?",
                (
                    DesireStatus.SUPPRESSED.value,
                    desire_id,
                    DesireStatus.ACTIVE.value,
                ),
            )
            return
        async with self._store.db.transaction():
            await self._store.db.conn.execute(
                "UPDATE short_term_desire SET status = ? "
                "WHERE id = ? AND status = ?",
                (
                    DesireStatus.SUPPRESSED.value,
                    desire_id,
                    DesireStatus.ACTIVE.value,
                ),
            )

    async def release_active(self, desire_id: str) -> None:
        """ACTIVE → PENDING：活动有进展但本次不结算满足/失败。"""
        if self._store.db.in_transaction:
            await self._store.db.conn.execute(
                "UPDATE short_term_desire SET status = ? "
                "WHERE id = ? AND status = ?",
                (
                    DesireStatus.PENDING.value,
                    desire_id,
                    DesireStatus.ACTIVE.value,
                ),
            )
            return
        async with self._store.db.transaction():
            await self._store.db.conn.execute(
                "UPDATE short_term_desire SET status = ? "
                "WHERE id = ? AND status = ?",
                (
                    DesireStatus.PENDING.value,
                    desire_id,
                    DesireStatus.ACTIVE.value,
                ),
            )

    async def _satisfy(
        self, desire: ShortTermDesire, *, in_transaction: bool = False
    ) -> Event:
        desire.status = DesireStatus.SATISFIED
        await self._store.update_desire(desire)
        await self._reinforce(desire)
        event = internal_event(
            EventType.DESIRE_SATISFIED,
            {"desire_id": desire.id},
            desire.id,
        )
        await self._bus.append_in_transaction(event)
        return event

    async def _expire(
        self, desire: ShortTermDesire, *, in_transaction: bool = False
    ) -> Event:
        desire.status = DesireStatus.EXPIRED
        await self._store.update_desire(desire)
        await self._suppress(desire.type)
        event = internal_event(
            EventType.DESIRE_EXPIRED,
            {"desire_id": desire.id},
            desire.id,
        )
        await self._bus.append_in_transaction(event)
        return event

    async def _reinforce(self, desire: ShortTermDesire) -> None:
        """Reinforce expression and settle the explicitly recorded parent."""
        dv = await self._store.get_value(desire.type)
        priority = action_priority(
            desire.strength,
            dv.expression_weight if dv is not None else 0.0,
        )
        if dv is not None:
            dv.expression_weight = reinforce_weight(dv.expression_weight)
            await self._store.upsert_value(dv)
        if desire.parent_long_term_id is None:
            return
        for parent in await self._store.list_long_term():
            if parent.id != desire.parent_long_term_id:
                continue
            parent.progress = min(1.0, parent.progress + _LONG_TERM_PROGRESS_DELTA)
            parent.strength = max(0.0, parent.strength - priority)
            await self._store.update_long_term(parent)
            return

    async def _suppress(self, type_: DesireType) -> None:
        """失败/淘汰后：值回增（压力回灌）+ 抑制阈值上浮（习得性抑制）。"""
        dv = await self._store.get_value(type_)
        if dv is None:
            return
        dv.value = apply_pressure(dv.value, REFUND_DELTA)
        dv.suppression_threshold = raise_suppression(dv.suppression_threshold)
        dv.updated_at = time.time()
        await self._store.upsert_value(dv)
