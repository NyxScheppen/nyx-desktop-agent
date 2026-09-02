import logging

from nyx.config import DesireConfig
from nyx.desire.lifecycle import DesireLifecycle, ListMemories
from nyx.desire.store import DesireStore
from nyx.enums import EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient
from nyx.memory.retrieval import EmbedFn, cosine
from nyx.types import DesireState, Event, LongTermDesire, ShortTermDesire

_LT_DEDUP_SIM_THRESHOLD = 0.9  # 长期欲望语义重复判定阈值（embedding 余弦）


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
        self._lifecycle = DesireLifecycle(
            store, bus, llm, evaluator, config, list_memories, embed
        )

    async def add_value(self, source: Event) -> None:
        """事件入口：OBSERVATION_STATE 加压互动欲，ACTIVITY_END 满足回写。"""
        if source.type is EventType.OBSERVATION_STATE:
            await self._lifecycle.pressure_from_observation(source)
        elif source.type is EventType.ACTIVITY_END:
            await self._lifecycle.satisfy_from_activity_end(source)

    async def evaluate(self, energy: float = 100.0) -> list[ShortTermDesire]:
        return await self._lifecycle.run_eval(energy)

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

    async def mark_suppressed(self, desire_id: str) -> None:
        await self._lifecycle.mark_suppressed(desire_id)

    async def add_long_term(self, desire: LongTermDesire) -> None:
        """新增长期欲望入口：容量检查 + 精确/语义去重，命中/超容则跳过。

        去重与容量下沉到此处，探索（14）与反思（12）两个调用方统一走；
        满不新增（不淘汰）。
        """
        existing = await self._store.list_long_term()
        if len(existing) >= self._config.long_term_capacity:
            return
        name = desire.name.strip()
        for d in existing:
            if d.name.strip() == name:
                self._logger.info("长期欲望重复丢弃（同名） name=%s", name)
                return
        if self._embed is not None:
            try:
                vec = await self._embed(f"{desire.name} {desire.description}")
                for d in existing:
                    other = await self._embed(f"{d.name} {d.description}")
                    if cosine(vec, other) >= _LT_DEDUP_SIM_THRESHOLD:
                        self._logger.info("长期欲望重复丢弃（语义） name=%s", name)
                        return
            except Exception:
                self._logger.exception("长期欲望去重 embedding 失败，跳过去重")
        await self._store.insert_long_term(desire)
