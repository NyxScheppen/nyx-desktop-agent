import asyncio
import logging

from nyx.config import DesireConfig
from nyx.db import Database
from nyx.desire.lifecycle import DesireLifecycle, ListExplorations, ListMemories
from nyx.desire.store import DesireStore, normalize_name
from nyx.desire.value import action_priority
from nyx.enums import EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient
from nyx.memory.retrieval import EmbedFn, cosine
from nyx.policy import policy
from nyx.types import DesireState, Event, LongTermDesire, ShortTermDesire

_LT_DEDUP_SIM_THRESHOLD = policy(
    "desire", "facade._LT_DEDUP_SIM_THRESHOLD", float
)  # 长期欲望语义重复判定阈值（embedding 余弦）
LongTermSnapshot = tuple[tuple[str, str, str], ...]


def _long_term_snapshot(desires: list[LongTermDesire]) -> LongTermSnapshot:
    return tuple(sorted(
        (desire.id, normalize_name(desire.name), desire.description)
        for desire in desires
    ))


class DesireFacade:
    """欲望模块门面：事件入口 + 达峰生成 + 队列/快照读 + 满足/淘汰回写。

    全周期编排在 DesireLifecycle（内部构造，共享 store）；纯 CRUD 在 DesireStore。
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
        list_explorations: ListExplorations | None = None,
        *,
        canon: str = "",
    ) -> None:
        self._store = store
        self._config = config
        self._embed = embed
        self._logger = logging.getLogger(__name__)
        self._long_term_lock = asyncio.Lock()
        self._lifecycle = DesireLifecycle(
            store, bus, llm, evaluator, config, list_memories, embed,
            list_explorations, canon=canon,
        )

    @property
    def db(self) -> Database:
        """Return the shared database for cross-module local transactions."""
        return self._store.db

    async def add_value(
        self, source: Event, consumer_id: str | None = None
    ) -> None:
        """消费旧观察投递但不改变欲望；活动结束仍结算满足。"""
        if source.type is EventType.OBSERVATION_STATE:
            await self._lifecycle.acknowledge_observation(source, consumer_id)
        elif source.type is EventType.ACTIVITY_END:
            await self._lifecycle.satisfy_from_activity_end(source, consumer_id)

    async def evaluate(
        self,
        energy: float = policy("desire", "facade.parameters.evaluate_energy", float),
        event_id: str | None = None,
        *,
        initiative_ref: str | None = None,
    ) -> list[ShortTermDesire]:
        return await self._lifecycle.run_eval(
            energy, event_id=event_id, initiative_ref=initiative_ref
        )

    async def pressure_creation(self, delta: float) -> None:
        """创造欲加压入口（反思/活动结束触发，delta 由调用方决定）。"""
        await self._lifecycle.pressure_creation(delta)

    async def get_pending(self) -> list[ShortTermDesire]:
        pending = [
            desire
            for desire in await self._store.list_pending()
            if desire.strength > 0.0
        ]
        weights = {
            value.type: value.expression_weight
            for value in await self._store.list_values()
        }
        return sorted(
            pending,
            key=lambda desire: (
                -action_priority(
                    desire.strength, weights.get(desire.type, 0.0)
                ),
                desire.created_at,
                desire.id,
            ),
        )

    async def add_long_term_subtopics(
        self, desire_id: str, subtopics: list[str]
    ) -> bool:
        """Append exploration topics only to its explicit long-term parent."""
        return await self._store.add_long_term_subtopics(desire_id, subtopics)

    async def get_all(self) -> DesireState:
        return DesireState(
            values=await self._store.list_values(),
            short_term=await self._store.list_short_term(),
            long_term=await self._store.list_long_term(),
        )

    async def satisfy(self, desire_id: str, goal_met: bool) -> None:
        await self._lifecycle.satisfy(desire_id, goal_met)

    async def expire(self, desire_id: str) -> None:
        await self._lifecycle.expire(desire_id)

    async def finish_attempt_in_transaction(self, desire_id: str) -> None:
        await self._lifecycle.finish_attempt(desire_id, in_transaction=True)

    async def mark_active(self, desire_id: str) -> None:
        await self._lifecycle.mark_active(desire_id)

    async def claim_for_activity(self, desire_id: str) -> bool:
        return await self._lifecycle.claim_for_activity(desire_id)

    async def claim_for_activity_in_transaction(self, desire_id: str) -> bool:
        return await self._lifecycle.claim_for_activity(
            desire_id, in_transaction=True
        )

    async def resume_for_activity_in_transaction(
        self, desire_id: str, *, already_claimed: bool = False
    ) -> bool:
        return await self._lifecycle.resume_for_activity(
            desire_id, already_claimed=already_claimed
        )

    async def claim_for_interaction(self, desire_id: str) -> bool:
        """Atomically claim a pending interaction desire for initiative."""
        return await self._lifecycle.claim_for_interaction(desire_id)

    async def release_interaction_claim(self, desire_id: str) -> bool:
        """Release an initiative claim when generation did not commit."""
        return await self._lifecycle.release_interaction_claim(desire_id)

    async def mark_suppressed(self, desire_id: str) -> None:
        await self._lifecycle.mark_suppressed(desire_id)

    async def release_active(self, desire_id: str) -> None:
        await self._lifecycle.release_active(desire_id)

    async def prepare_long_term_candidates(
        self, desires: tuple[LongTermDesire, ...]
    ) -> tuple[tuple[LongTermDesire, ...], LongTermSnapshot]:
        """Preflight a batch outside the database transaction."""
        if self._store.db.in_transaction:
            raise RuntimeError(
                policy("desire", "facade.copy.prepare_long_term_candidates_text", str)
            )
        async with self._long_term_lock:
            existing = await self._store.list_long_term()
            return await self._prepare_long_term_candidates(desires, existing)

    async def _prepare_long_term_candidates(
        self,
        desires: tuple[LongTermDesire, ...],
        existing: list[LongTermDesire],
    ) -> tuple[tuple[LongTermDesire, ...], LongTermSnapshot]:
        snapshot = _long_term_snapshot(existing)
        remaining = max(0, self._config.long_term_capacity - len(existing))
        if remaining == 0:
            return (), snapshot

        known_names = {normalize_name(desire.name) for desire in existing}
        existing_vectors: list[list[float]] | None = None
        accepted_vectors: list[list[float]] = []
        accepted: list[LongTermDesire] = []
        for desire in desires:
            name = normalize_name(desire.name)
            if not name:
                raise ValueError(
                    policy(
                        "desire", "facade.copy.prepare_long_term_candidates_text_2", str
                    )
                )
            if name in known_names:
                self._logger.info(
                    policy(
                        "desire", "facade.copy.prepare_long_term_candidates_text_3", str
                    ),
                    name,
                )
                continue
            if len(accepted) >= remaining:
                break

            vector: list[float] | None = None
            if self._embed is not None:
                if existing_vectors is None:
                    existing_vectors = [
                        await self._embed(f"{item.name} {item.description}")
                        for item in existing
                    ]
                vector = await self._embed(f"{desire.name} {desire.description}")
                if any(
                    cosine(vector, other) >= _LT_DEDUP_SIM_THRESHOLD
                    for other in [*existing_vectors, *accepted_vectors]
                ):
                    self._logger.info(
                        policy(
                            "desire",
                            "facade.copy.prepare_long_term_candidates_text_4",
                            str,
                        ),
                        name,
                    )
                    continue

            accepted.append(desire)
            known_names.add(name)
            if vector is not None:
                accepted_vectors.append(vector)
        return tuple(accepted), snapshot

    async def add_prepared_long_terms_in_transaction(
        self,
        desires: tuple[LongTermDesire, ...],
        snapshot: LongTermSnapshot,
    ) -> None:
        """Commit preflighted candidates inside the caller's transaction."""
        if not self._store.db.in_transaction:
            raise RuntimeError(
                policy(
                    "desire",
                    "facade.copy.add_prepared_long_terms_in_transaction_text",
                    str,
                )
            )
        if not desires:
            return
        current = await self._store.list_long_term()
        if _long_term_snapshot(current) != snapshot:
            raise RuntimeError(
                policy(
                    "desire",
                    "facade.copy.add_prepared_long_terms_in_transaction_text_2",
                    str,
                )
            )
        for desire in desires:
            await self._store.insert_long_term_if_available(
                desire, self._config.long_term_capacity
            )
