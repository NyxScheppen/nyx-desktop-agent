import time
from uuid import uuid4

from nyx.activity.facade import ActivityFacade
from nyx.config import Config
from nyx.desire.facade import DesireFacade
from nyx.enums import ActivityType, EmotionCategory, EnergyState, EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_DAY, SECONDS_PER_HOUR, internal_event
from nyx.inner_life.emotion import (
    BASELINE_AROUSAL,
    BASELINE_VALENCE,
    EMOTION_DECAY_RATE,
    ENERGY_REST_THRESHOLD,
    apply_offset,
    decay_emotion,
    event_offset,
    resolve_emotion,
    vad_to_category,
)
from nyx.inner_life.reflection import Reflection, ReflectionPlan
from nyx.inner_life.store import InnerLifeStore
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.types import CurrentState, Event, ReflectionOutcome, SelfNarrative

_ENERGY_RECOVERY_PER_HOUR = 5.0   # 闲置每小时恢复（"夜间自动恢复"简化为恒定闲置恢复）

_ENERGY_TIERS = (
    (80.0, EnergyState.ENERGETIC),
    (60.0, EnergyState.OKAY),
    (ENERGY_REST_THRESHOLD, EnergyState.TIRED),
    (20.0, EnergyState.EXHAUSTED),
)


def energy_to_state(value: float) -> EnergyState:
    """精力值 → 五档状态（80/60/40/20 分界）。纯函数。"""
    for threshold, state in _ENERGY_TIERS:
        if value >= threshold:
            return state
    return EnergyState.DRAINED


class InnerLifeFacade:
    """内在生命门面：apply_event（情感/精力更新）+ reflect（反思协调器）
    + get_state / get_narrative。

    情感在内存（不持久化，design §4.5）；性格/三观/精力/自我叙事走 InnerLifeStore；
    反思在 Reflection（内部构造，共享 store，不反 import facade）。
    """

    def __init__(
        self,
        store: InnerLifeStore,
        activity_facade: ActivityFacade,
        desire_facade: DesireFacade,
        memory_facade: MemoryFacade,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        config: Config,
    ) -> None:
        self._store = store
        self._activity_facade = activity_facade
        self._desire_facade = desire_facade
        self._bus = bus
        self._reflection = Reflection(
            store, memory_facade, desire_facade, llm, evaluator, config.desire
        )
        self._valence: float = BASELINE_VALENCE
        self._arousal: float = BASELINE_AROUSAL
        self._emotion_updated_at: float = time.time()
        self._energy_updated_at: float = time.time()

    async def apply_event(
        self, event: Event, consumer_id: str | None = None
    ) -> None:
        """情感/精力更新入口：衰减 + 偏移；ACTIVITY_END 额外更新精力；
        REFLECTION 额外触发反思。"""
        if consumer_id is not None:
            await self._apply_durable_event(event, consumer_id)
            return
        snapshot: tuple[float, float, float, float] = (
            self._valence,
            self._arousal,
            self._emotion_updated_at,
            self._energy_updated_at,
        )
        async with self._store.db.transaction():
            try:
                derived_events = await self._apply_event(event, in_transaction=True)
            except BaseException:
                (
                    self._valence,
                    self._arousal,
                    self._emotion_updated_at,
                    self._energy_updated_at,
                ) = snapshot
                raise
        for derived_event in derived_events:
            await self._bus.announce_committed(derived_event)

    async def _apply_durable_event(self, event: Event, consumer_id: str) -> None:
        snapshot: tuple[float, float, float, float] = (
            self._valence,
            self._arousal,
            self._emotion_updated_at,
            self._energy_updated_at,
        )
        if await self._bus.has_effect(event.id, consumer_id):
            return
        reflection_plan: ReflectionPlan | None = None
        if event.type is EventType.REFLECTION:
            reflection_plan = await self._reflection.prepare(event.correlation_id)
        async with self._store.db.transaction():
            applied = await self._bus.try_mark_effect_in_transaction(
                event.id, consumer_id
            )
            if not applied:
                return
            try:
                derived_events = await self._apply_event(
                    event,
                    in_transaction=True,
                    reflection_plan=reflection_plan,
                )
            except BaseException:
                (
                    self._valence,
                    self._arousal,
                    self._emotion_updated_at,
                    self._energy_updated_at,
                ) = snapshot
                raise
        for derived_event in derived_events:
            await self._bus.announce_committed(derived_event)

    async def _apply_event(
        self,
        event: Event,
        *,
        in_transaction: bool = False,
        reflection_plan: ReflectionPlan | None = None,
    ) -> list[Event]:
        now = time.time()
        self._settle_emotion(now)
        d_valence, d_arousal = event_offset(event.type)
        self._valence, self._arousal = apply_offset(
            self._valence, self._arousal, d_valence, d_arousal
        )
        self._emotion_updated_at = now

        if event.type is EventType.ACTIVITY_END:
            await self._apply_energy(event, now)
        derived_events: list[Event] = []
        if event.type is EventType.REFLECTION:
            _, reflection_event = await self._run_reflection(
                event.correlation_id,
                in_transaction=in_transaction,
                plan=reflection_plan,
            )
            if reflection_event is not None:
                derived_events.append(reflection_event)

        derived_events.append(
            await self._publish_emotion(
                event.correlation_id, in_transaction=in_transaction
            )
        )
        return derived_events

    async def reflect(
        self, correlation_id: str | None = None, *, in_transaction: bool = False
    ) -> ReflectionOutcome:
        """反思协调器（慢变量唯一入口）：内部调 MemoryFacade/DesireFacade。

        correlation_id 来自触发 REFLECTION 事件（缺省自生成），串起反思 LLM 的溯源链。
        成功后 publish REFLECTION_DONE（仅广播前端：叙事/欲望刷新 + 高亮气泡），
        返回反思产物摘要；解析失败或提交失败直接抛出，交由 durable delivery 重试。
        """
        cid = correlation_id or str(uuid4())
        outcome, _ = await self._run_reflection(cid, in_transaction=in_transaction)
        return outcome

    async def _run_reflection(
        self,
        correlation_id: str,
        *,
        in_transaction: bool = False,
        plan: ReflectionPlan | None = None,
    ) -> tuple[ReflectionOutcome, Event | None]:
        prepared = plan if plan is not None else await self._reflection.prepare(
            correlation_id
        )
        if in_transaction:
            outcome = await self._reflection.apply(prepared)
            event = internal_event(
                EventType.REFLECTION_DONE,
                {"story": outcome.story, "story_is_new": outcome.story_is_new},
                correlation_id,
            )
            await self._bus.append_in_transaction(event)
            return outcome, event

        async with self._store.db.transaction():
            outcome = await self._reflection.apply(prepared)
            event = internal_event(
                EventType.REFLECTION_DONE,
                {"story": outcome.story, "story_is_new": outcome.story_is_new},
                correlation_id,
            )
            await self._bus.append_in_transaction(event)
        await self._bus.announce_committed(event)
        return outcome, event

    async def get_state(self) -> CurrentState:
        personality = await self._store.get_personality()
        values = await self._store.get_values()
        aesthetic = await self._store.get_aesthetic()
        if personality is None or values is None or aesthetic is None:
            raise RuntimeError("inner_life 单行表未初始化（组合根必须先 seed）")
        now = time.time()
        self._settle_emotion(now)
        energy_value, energy_state = await self._settle_energy(now)
        current_activity = await self._current_activity_type()
        emotion = self._resolve_emotion(energy_state, current_activity)
        return CurrentState(
            valence=self._valence,
            arousal=self._arousal,
            emotion=emotion,
            personality=personality,
            values=values,
            aesthetic=aesthetic,
            energy=energy_value,
            energy_state=energy_state,
            current_activity=current_activity,
            active_desires=await self._desire_facade.get_pending(),
        )

    async def get_narrative(self) -> SelfNarrative:
        narrative = await self._store.get_narrative()
        if narrative is None:
            raise RuntimeError("self_narrative 未初始化（组合根必须先 seed）")
        return narrative

    async def _apply_energy(self, event: Event, now: float) -> None:
        energy = await self._store.get_energy()
        if energy is None:
            raise RuntimeError("energy 未初始化（组合根必须先 seed）")
        value, _ = energy
        elapsed_hours = max(0.0, now - self._energy_updated_at) / SECONDS_PER_HOUR
        value += _ENERGY_RECOVERY_PER_HOUR * elapsed_hours
        delta = event.content.get("energy_delta")
        if isinstance(delta, (int, float)) and not isinstance(delta, bool):
            value += float(delta)
        value = max(0.0, min(100.0, value))
        await self._store.upsert_energy(value, energy_to_state(value))
        self._energy_updated_at = now

    def _settle_emotion(self, now: float) -> None:
        """结算截至 now 的情感衰减，不发布事件。"""
        elapsed_days = max(0.0, now - self._emotion_updated_at) / SECONDS_PER_DAY
        self._valence, self._arousal = decay_emotion(
            self._valence, self._arousal, elapsed_days, EMOTION_DECAY_RATE
        )
        self._emotion_updated_at = now

    async def _settle_energy(self, now: float) -> tuple[float, EnergyState]:
        """结算截至 now 的闲置恢复，并把最新精力快照落库。"""
        energy = await self._store.get_energy()
        if energy is None:
            raise RuntimeError("energy 未初始化（组合根必须先 seed）")
        value, _ = energy
        elapsed_hours = max(0.0, now - self._energy_updated_at) / SECONDS_PER_HOUR
        if elapsed_hours > 0.0:
            value = max(
                0.0,
                min(100.0, value + _ENERGY_RECOVERY_PER_HOUR * elapsed_hours),
            )
            state = energy_to_state(value)
            await self._store.upsert_energy(value, state)
        else:
            state = energy_to_state(value)
        self._energy_updated_at = now
        return value, state

    async def _current_activity_type(self) -> ActivityType | None:
        activity = await self._activity_facade.get_current()
        return activity.type if activity is not None else None

    def _resolve_emotion(
        self, energy_state: EnergyState, current_activity: ActivityType | None
    ) -> EmotionCategory:
        """valence/arousal + 精力档 + 当前活动 → 情绪类别（组合 resolve_emotion）。"""
        return resolve_emotion(
            vad_to_category(self._valence, self._arousal),
            energy_state,
            current_activity,
        )

    async def _publish_emotion(
        self, correlation_id: str, *, in_transaction: bool = False
    ) -> Event:
        energy = await self._store.get_energy()
        if energy is None:
            raise RuntimeError("energy 未初始化（组合根必须先 seed）")
        emotion = self._resolve_emotion(energy[1], await self._current_activity_type())
        event = internal_event(
            EventType.EMOTION_UPDATE,
            {
                "valence": self._valence,
                "arousal": self._arousal,
                "emotion": emotion.value,
            },
            correlation_id,
        )
        if in_transaction:
            await self._bus.append_in_transaction(event)
        else:
            await self._bus.publish(event)
        return event
