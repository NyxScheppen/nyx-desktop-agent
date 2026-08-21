from nyx.config import DesireConfig
from nyx.desire.lifecycle import DesireLifecycle, ListMemories
from nyx.desire.store import DesireStore
from nyx.enums import EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient
from nyx.types import DesireState, Event, LongTermDesire, ShortTermDesire


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
    ) -> None:
        self._store = store
        self._lifecycle = DesireLifecycle(
            store, bus, llm, evaluator, config, list_memories
        )

    async def add_value(self, source: Event) -> None:
        """事件入口：OBSERVATION_STATE 加压互动欲，ACTIVITY_END 满足回写。"""
        if source.type is EventType.OBSERVATION_STATE:
            await self._lifecycle.pressure_from_observation(source)
        elif source.type is EventType.ACTIVITY_END:
            await self._lifecycle.satisfy_from_activity_end(source)

    async def evaluate(self) -> list[ShortTermDesire]:
        return await self._lifecycle.run_eval()

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

    async def add_long_term(self, desire: LongTermDesire) -> None:
        """反思新增/强化长期欲望入口：直接插入（容量检查归 12 反思）。"""
        await self._store.insert_long_term(desire)
