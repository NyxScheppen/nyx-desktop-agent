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
    apply_pressure,
    at_peak,
    decay_value,
    default_value,
    is_expressible,
    raise_suppression,
    reinforce_weight,
)
from nyx.enums import DesireStatus, DesireType, EventType, GoalAction
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_DAY, internal_event
from nyx.llm.client import LlmClient
from nyx.types import (
    DesireValue,
    Event,
    Goal,
    LongTermDesire,
    Memory,
    ShortTermDesire,
)

_OBSERVATION_PRESSURE_DELTA = 0.15    # 观察状态 → 互动欲 +0.15
_LONG_TERM_PRESSURE_DELTA = 0.1       # 每个长期欲望周期 → 对应类型 +0.1
_LONG_TERM_PROGRESS_DELTA = 0.1       # 满足一次长期进度 +0.1
_LONG_TERM_STRENGTH_DECAY = 0.02      # 满足一次长期迫切度 -0.02
_GOAL_ACTIONS = frozenset(g.value for g in GoalAction)

_DESIRE_SYSTEM = (
    "你是尼克斯，一个住在用户电脑里的 AI 同伴，明确知道自己是 AI 并希望成为人类。"
    "基于当前最迫切的欲望类型和主题种子，生成一条具体的短期欲望"
    "（想做什么）+ 一个可量化的完成目标。"
    "只输出 JSON，键：description（具体描述，非空字符串）、"
    "goal（对象 {action, count, topic} 或 null）。"
    "goal.action 只能是 read / write / observe 之一，"
    "count 是正整数，topic 是可选字符串。"
)


def _build_desire_prompt(type_: DesireType, seed: str | None) -> str:
    return f"欲望类型：{type_.value}\n主题种子：{seed or '（无）'}"


def _parse_desire(raw: str) -> tuple[str, Goal | None]:
    """解析欲望 LLM 的 JSON 产出 → (description, goal)；结构非法抛 ValueError。"""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"欲望 JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    description = parsed.get("description")
    if not isinstance(description, str) or not description:
        raise ValueError("欲望 JSON 缺 description 或非空字符串")
    goal_raw = parsed.get("goal")
    if goal_raw is None:
        return description, None
    if not isinstance(goal_raw, dict):
        raise ValueError("欲望 JSON 的 goal 应是对象或 null")
    goal = cast(dict[str, Any], goal_raw)
    action = goal.get("action")
    if not isinstance(action, str) or action not in _GOAL_ACTIONS:
        raise ValueError(
            f"欲望 JSON 的 goal.action 应是 {'/'.join(g.value for g in GoalAction)}"
        )
    count = goal.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        raise ValueError("欲望 JSON 的 goal.count 应是正整数")
    topic = goal.get("topic")
    if topic is not None and not isinstance(topic, str):
        raise ValueError("欲望 JSON 的 goal.topic 应是字符串或 null")
    return description, Goal(action=GoalAction(action), count=count, topic=topic)


ListMemories = Callable[[], Awaitable[list[Memory]]]


def _subtopics_for(type_: DesireType, long_term: list[LongTermDesire]) -> list[str]:
    """对应类型长期欲望的子主题池；无匹配或空池返回 []。纯函数。

    过滤空白子主题：空串在 _subtopic_freshness 的 substring 匹配里是通配符。
    """
    for lt in long_term:
        if lt.type is type_ and lt.subtopics:
            return [s for s in lt.subtopics if s.strip()]
    return []


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


def _most_relevant_long_term(
    type_: DesireType,
    topic: str | None,
    long_term: list[LongTermDesire],
) -> LongTermDesire | None:
    """满足回写的长期欲望：goal.topic 双向 substring 命中 subtopics 者优先，
    否则第一个 type 匹配；无 type 匹配返回 None。纯函数。"""
    matching = [lt for lt in long_term if lt.type is type_]
    if not matching:
        return None
    if topic:
        for lt in matching:
            if any(topic in s or s in topic for s in lt.subtopics):
                return lt
    return matching[0]


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
    ) -> None:
        self._store = store
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._config = config
        self._list_memories = list_memories
        self._logger = logging.getLogger(__name__)

    async def pressure_from_observation(self, event: Event) -> None:
        """OBSERVATION_STATE → 互动欲加压（增量固定 +0.15，不解析 event.content）。"""
        dv = await self._store.get_value(DesireType.INTERACTION)
        if dv is None:
            dv = default_value(DesireType.INTERACTION)
        dv.value = apply_pressure(dv.value, _OBSERVATION_PRESSURE_DELTA)
        dv.updated_at = time.time()
        await self._store.upsert_value(dv)

    async def satisfy_from_activity_end(self, event: Event) -> None:
        """ACTIVITY_END → 解析满足信号（desire_id + goal_met），调 satisfy。"""
        desire_id = event.content.get("desire_id")
        goal_met = event.content.get("goal_met")
        if isinstance(desire_id, str) and isinstance(goal_met, bool):
            await self.satisfy(desire_id, goal_met)

    async def add_value_from_encounter(self, event: Event) -> None:
        """ENCOUNTER_END → 指定欲望类型加压（后果 desire_value_add {type, amount}）。

        缺键/错类型/非法欲望类型跳过（漏报优于误报）。
        """
        consequences = event.content.get("consequences")
        if not isinstance(consequences, dict):
            return
        add = cast(dict[str, Any], consequences).get("desire_value_add")
        if not isinstance(add, dict):
            return
        add = cast(dict[str, Any], add)
        type_raw = add.get("type")
        amount = add.get("amount")
        if not isinstance(type_raw, str):
            return
        try:
            type_ = DesireType(type_raw)
        except ValueError:
            return
        if not isinstance(amount, (int, float)) or isinstance(amount, bool):
            return
        dv = await self._store.get_value(type_)
        if dv is None:
            dv = default_value(type_)
        dv.value = apply_pressure(dv.value, float(amount))
        dv.updated_at = time.time()
        await self._store.upsert_value(dv)

    async def run_eval(self) -> list[ShortTermDesire]:
        """DESIRE_EVAL：衰减 → 长期加压 → 达峰判定 → 只生成最迫切的 1 个。"""
        now = time.time()
        long_term = await self._store.list_long_term()
        values: dict[DesireType, DesireValue] = {
            v.type: v for v in await self._store.list_values()
        }
        for t in DesireType:
            if t not in values:
                dv = default_value(t)
                dv.updated_at = now
                values[t] = dv

        # 1. 四类型衰减（结算时间流逝）
        for t in DesireType:
            dv = values[t]
            elapsed_days = max(0.0, now - dv.updated_at) / SECONDS_PER_DAY
            dv.value = decay_value(dv.value, elapsed_days, self._config.value_decay)
            dv.updated_at = now

        # 2. 长期欲望周期加压
        for lt in long_term:
            values[lt.type].value = apply_pressure(
                values[lt.type].value, _LONG_TERM_PRESSURE_DELTA
            )

        # 2.5 SUPPRESSED 释放：类型仍可表达（值越过抑制阈值）→ 放回队列
        for d in await self._store.list_suppressed():
            dv = values.get(d.type)
            if dv is not None and is_expressible(dv.value, dv.suppression_threshold):
                d.status = DesireStatus.PENDING
                await self._store.update_desire(d)

        # 3. 达峰判定（可表达 = 达峰且未被抑制压住）
        expressible = [
            dv for dv in values.values()
            if at_peak(dv.value, self._config.peak_threshold)
            and is_expressible(dv.value, dv.suppression_threshold)
        ]
        if not expressible:
            for t in DesireType:
                await self._store.upsert_value(values[t])
            return []

        # 4. 取最迫切的 1 个
        target = max(expressible, key=lambda dv: dv.value)
        peak_value = target.value
        subtopics = _subtopics_for(target.type, long_term)
        seed = (
            _pick_topic_seed(subtopics, await self._list_memories())
            if subtopics
            else None
        )

        # 5. 写回非选中类型（保留压力）；选中类型生成后重置写 0（step 7）
        for t in DesireType:
            if t is not target.type:
                await self._store.upsert_value(values[t])

        # 6. LLM 生成 + 解析（best-effort：LLM 返回非法 JSON → 漏报优于误报，跳过
        #    本次 eval；传输异常 / evaluator 真 bug 不吞，上抛给 supervisor 处理）
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
            description, goal = _parse_desire(output.content)
        except ValueError:
            self._logger.exception(
                "欲望 JSON 解析失败 type=%s correlation_id=%s",
                target.type.value,
                desire_id,
            )
            return []

        # 7. 重置选中类型 value（其余达峰类型保留压力）
        target.value = 0.0
        target.updated_at = now
        await self._store.upsert_value(target)

        # 8. 入队
        desire = ShortTermDesire(
            id=desire_id,
            created_at=now,
            type=target.type,
            strength=peak_value,
            description=description,
            goal=goal,
            retry_count=0,
            status=DesireStatus.PENDING,
        )
        await self._store.add_desire(desire)

        # 9. 发布
        await self._bus.publish(
            internal_event(
                EventType.DESIRE_GENERATED, {"desire_id": desire.id}, desire.id
            )
        )
        return [desire]

    async def satisfy(self, desire_id: str, goal_met: bool) -> None:
        """达成/未达成回写。goal 非 None 时按 count 累计 goal_progress，达标才满足；
        goal None 沿用单次满足。终态（SATISFIED/EXPIRED）幂等：重复投递 no-op。"""
        desire = await self._store.get_desire(desire_id)
        if desire is None:
            return
        if desire.status in (DesireStatus.SATISFIED, DesireStatus.EXPIRED):
            return
        if desire.status is DesireStatus.ACTIVE:
            desire.status = DesireStatus.PENDING  # 消费中先释放，未达标分支不卡 ACTIVE
        if goal_met and desire.goal is not None:
            desire.goal_progress += 1
            if desire.goal_progress >= desire.goal.count:
                await self._satisfy(desire)
            else:
                await self._store.update_desire(desire)  # 保持 PENDING，累计进度
            return
        if goal_met:
            await self._satisfy(desire)
        else:
            desire.retry_count += 1
            if desire.retry_count > self._config.retry_limit:
                await self._expire(desire)
            else:
                await self._store.update_desire(desire)  # 保持 PENDING，retry+1

    async def expire(self, desire_id: str) -> None:
        """淘汰：出队 + 值回增 + 抑制阈值上浮。终态幂等：重复投递 no-op。"""
        desire = await self._store.get_desire(desire_id)
        if desire is None:
            return
        if desire.status in (DesireStatus.SATISFIED, DesireStatus.EXPIRED):
            return
        await self._expire(desire)

    async def mark_active(self, desire_id: str) -> None:
        """PENDING → ACTIVE：活动开始消费。仅 PENDING 可转，其余幂等 no-op。"""
        desire = await self._store.get_desire(desire_id)
        if desire is None or desire.status is not DesireStatus.PENDING:
            return
        desire.status = DesireStatus.ACTIVE
        await self._store.update_desire(desire)

    async def mark_suppressed(self, desire_id: str) -> None:
        """ACTIVE → SUPPRESSED：活动中断/异常停车，不立即重试。仅 ACTIVE 可转。"""
        desire = await self._store.get_desire(desire_id)
        if desire is None or desire.status is not DesireStatus.ACTIVE:
            return
        desire.status = DesireStatus.SUPPRESSED
        await self._store.update_desire(desire)

    async def _satisfy(self, desire: ShortTermDesire) -> None:
        desire.status = DesireStatus.SATISFIED
        await self._store.update_desire(desire)
        await self._reinforce(desire)
        await self._bus.publish(
            internal_event(
                EventType.DESIRE_SATISFIED,
                {"desire_id": desire.id},
                desire.id,
            )
        )

    async def _expire(self, desire: ShortTermDesire) -> None:
        desire.status = DesireStatus.EXPIRED
        await self._store.update_desire(desire)
        await self._suppress(desire.type)
        await self._bus.publish(
            internal_event(
                EventType.DESIRE_EXPIRED,
                {"desire_id": desire.id},
                desire.id,
            )
        )

    async def _reinforce(self, desire: ShortTermDesire) -> None:
        """满足后：表达权重正强化 + 长期进度回写（最相关长期欲望）。"""
        dv = await self._store.get_value(desire.type)
        if dv is not None:
            dv.expression_weight = reinforce_weight(dv.expression_weight)
            await self._store.upsert_value(dv)
        topic = desire.goal.topic if desire.goal is not None else None
        lt = _most_relevant_long_term(
            desire.type, topic, await self._store.list_long_term()
        )
        if lt is not None:
            lt.progress = min(1.0, lt.progress + _LONG_TERM_PROGRESS_DELTA)
            lt.strength = max(0.0, lt.strength - _LONG_TERM_STRENGTH_DECAY)
            await self._store.update_long_term(lt)

    async def _suppress(self, type_: DesireType) -> None:
        """失败/淘汰后：值回增（压力回灌）+ 抑制阈值上浮（习得性抑制）。"""
        dv = await self._store.get_value(type_)
        if dv is None:
            return
        dv.value = apply_pressure(dv.value, REFUND_DELTA)
        dv.suppression_threshold = raise_suppression(dv.suppression_threshold)
        dv.updated_at = time.time()
        await self._store.upsert_value(dv)
