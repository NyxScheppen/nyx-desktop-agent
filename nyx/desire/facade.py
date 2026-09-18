import asyncio
import logging

from nyx.config import DesireConfig
from nyx.db import Database
from nyx.desire.lifecycle import DesireLifecycle, ListMemories
from nyx.desire.store import DesireStore, normalize_name
from nyx.enums import EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient
from nyx.memory.retrieval import EmbedFn, cosine
from nyx.types import DesireState, Event, LongTermDesire, ShortTermDesire

_LT_DEDUP_SIM_THRESHOLD = 0.9  # 长期欲望语义重复判定阈值（embedding 余弦）
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
    ) -> None:
        self._store = store
        self._config = config
        self._embed = embed
        self._logger = logging.getLogger(__name__)
        self._long_term_lock = asyncio.Lock()
        self._lifecycle = DesireLifecycle(
            store, bus, llm, evaluator, config, list_memories, embed
        )

    @property
    def db(self) -> Database:
        """Return the shared database for cross-module local transactions."""
        return self._store.db

    async def add_value(
        self, source: Event, consumer_id: str | None = None
    ) -> None:
        """事件入口：OBSERVATION_STATE 加压互动欲，ACTIVITY_END 满足回写。"""
        if source.type is EventType.OBSERVATION_STATE:
            await self._lifecycle.pressure_from_observation(source, consumer_id)
        elif source.type is EventType.ACTIVITY_END:
            await self._lifecycle.satisfy_from_activity_end(source, consumer_id)

    async def evaluate(
        self, energy: float = 100.0, event_id: str | None = None
    ) -> list[ShortTermDesire]:
        return await self._lifecycle.run_eval(energy, event_id=event_id)

    async def pressure_creation(self, delta: float) -> None:
        """创造欲加压入口（反思/活动结束触发，delta 由调用方决定）。"""
        await self._lifecycle.pressure_creation(delta)

    async def get_pending(self) -> list[ShortTermDesire]:
        return await self._store.list_pending()

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

    async def mark_active(self, desire_id: str) -> None:
        await self._lifecycle.mark_active(desire_id)

    async def claim_for_activity(self, desire_id: str) -> bool:
        return await self._lifecycle.claim_for_activity(desire_id)

    async def claim_for_activity_in_transaction(self, desire_id: str) -> bool:
        return await self._lifecycle.claim_for_activity(
            desire_id, in_transaction=True
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

    async def add_long_term(self, desire: LongTermDesire) -> None:
        """新增长期欲望入口：容量检查 + 精确/语义去重，命中/超容则跳过。

        去重与容量下沉到此处，探索（14）与反思（12）两个调用方统一走；
        满不新增（不淘汰）。
        """
        async with self._long_term_lock:
            while True:
                existing = await self._store.list_long_term()
                accepted, snapshot = await self._prepare_long_term_candidates(
                    (desire,), existing
                )
                if not accepted:
                    return
                conflict = False
                async with self._store.db.transaction():
                    current = await self._store.list_long_term()
                    if _long_term_snapshot(current) != snapshot:
                        conflict = True
                    else:
                        await self._store.insert_long_term_if_available(
                            accepted[0], self._config.long_term_capacity
                        )
                if not conflict:
                    return

    async def prepare_long_term_candidates(
        self, desires: tuple[LongTermDesire, ...]
    ) -> tuple[tuple[LongTermDesire, ...], LongTermSnapshot]:
        """Preflight a batch outside the database transaction."""
        if self._store.db.in_transaction:
            raise RuntimeError("长期欲望预检必须在数据库事务外执行")
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
                raise ValueError("长期欲望 name 不能为空")
            if name in known_names:
                self._logger.info("长期欲望重复丢弃（同名） name=%s", name)
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
                        "长期欲望重复丢弃（语义） name=%s", name
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
            raise RuntimeError("长期欲望计划提交必须在数据库事务内执行")
        if not desires:
            return
        current = await self._store.list_long_term()
        if _long_term_snapshot(current) != snapshot:
            raise RuntimeError("长期欲望快照已变化，请重试反思")
        for desire in desires:
            await self._store.insert_long_term_if_available(
                desire, self._config.long_term_capacity
            )
