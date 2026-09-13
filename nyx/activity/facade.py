import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

from nyx.activity import creation as _creation
from nyx.activity import lifecycle as _activity_lifecycle
from nyx.activity import paths as _activity_paths
from nyx.activity.exploration import Exploration
from nyx.activity.llm_result import parse_activity_result as _parse_activity_result
from nyx.activity.material_store import MaterialStore
from nyx.activity.observe import build_observation_summary
from nyx.activity.reading_runner import ReadingActivityRunner
from nyx.activity.starter import ActivityStarter, schedule_block_id
from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig, ExplorationConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import ActivityType, DesireType, EventType, TickType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_DAY
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.tools.file_io import file_io
from nyx.tools.registry import ToolRegistry
from nyx.types import (
    Activity,
    CurrentState,
    Event,
    LongTermDesire,
    Material,
    ReflectionOutcome,
    ShortTermDesire,
)

_CREATION_STYLES = _creation.CREATION_STYLES
_build_creation_context = _creation.build_creation_context
_build_creation_system = _creation.build_creation_system
_pick_creation_style = _creation.pick_creation_style
_correlation_id = _activity_lifecycle.correlation_id
_goal_met = _activity_lifecycle.goal_met
_path_hash_suffix = _activity_paths.path_hash_suffix
_sanitize_filename = _activity_paths.sanitize_filename

_logger = logging.getLogger(__name__)

def _day_start(now: float) -> float:
    """当日零点（UTC 日边界，MVP 可推翻为本地时区）。纯函数。"""
    return now - now % SECONDS_PER_DAY


_schedule_block_id = schedule_block_id


class ActivityFacade:
    """活动模块门面：消费欲望 → 选活动 → 后台执行 → 完成/打断 → 发布事件。

    依赖注入解环：不持有 InnerLifeFacade，注入 get_state 回调
    （组合根绑 inner_life.get_state）。
    """

    def __init__(
        self,
        store: ActivityStore,
        material_store: MaterialStore,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        tools: ToolRegistry,
        desire: DesireFacade,
        memory: MemoryFacade,
        get_state: Callable[[], Awaitable[CurrentState]],
        reflect: Callable[[str | None], Awaitable[ReflectionOutcome | None]],
        get_observation: Callable[[], Awaitable[dict[str, str]]],
        config: ActivityConfig,
        exploration_config: ExplorationConfig,
        canon: str,
    ) -> None:
        self._store = store
        self._material_store = material_store
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._desire = desire
        self._memory = memory
        self._get_state = get_state
        self._reflect = reflect
        self._get_observation = get_observation
        self._config = config
        self._canon = canon
        self._exploration = Exploration(
            llm,
            evaluator,
            tools,
            exploration_config,
            search_memories=self._memory.search,
        )
        self._reading_runner = ReadingActivityRunner(
            material_store, llm, evaluator, memory, file_io
        )
        self._lifecycle = _activity_lifecycle.ActivityLifecycle(
            store, bus, desire, config
        )
        self._starter = ActivityStarter(
            store,
            material_store,
            desire,
            get_state,
            self._execute,
            config,
            exploration_config,
            time.time,
        )
        self._task: asyncio.Task[None] | None = None

    # ---- 事件入口 ----

    async def on_tick(self, tick_type: TickType) -> None:
        """SCHEDULE_BLOCK_START：日程块开始，有空闲就消费欲望。"""
        if tick_type is TickType.SCHEDULE_BLOCK_START:
            await self._maybe_start_activity()

    async def on_desire_generated(self, event: Event) -> None:
        """DESIRE_GENERATED：欲望刚生成，有空闲就立即消费。"""
        await self._maybe_start_activity()

    # ---- 决策 ----

    def select_activity(
        self, desires: list[ShortTermDesire], state: CurrentState
    ) -> Activity | None:
        return self._starter.select_activity(desires, state)

    def _default_activity(self, state: CurrentState) -> Activity:
        return self._starter.default_activity(state)

    # ---- 生命周期 ----

    async def complete_activity(self, activity: Activity) -> None:
        """完成：goal 判定 + 收尾 + 发布 activity_end（desire/inner_life 消费）。"""
        await self._lifecycle.complete(activity)

    async def interrupt(self, activity_id: str, by_event: EventType) -> None:
        """抢占即暂停：校验目标 RUNNING → cancel 执行 task 并 await 其彻底结束
        → 重读守卫（窗口内已自行完成/失败则不覆盖）→ 置终态落库
        （可续活动 PAUSED，其余 ABANDONED）+ 发布 activity_interrupted。

        执行中的 result 尚未写入，故仅落终态（不持久化部分进度）；
        读书的 read_chars 已 advance 进 material 层，恢复时从那里续读。
        """
        await self._lifecycle.interrupt(activity_id, by_event, self._task)

    # ---- 读 ----

    async def get_current(self) -> Activity | None:
        return await self._store.get_current()

    async def get_schedule(self) -> list[Activity]:
        return await self._store.list_schedule(_day_start(time.time()))

    async def get_results(self, limit: int = 100) -> list[Activity]:
        """跨天历史产出（读书笔记/探索发现/创作内容），按结束时间倒序。"""
        return await self._store.list_results(limit)

    async def list_materials(self) -> list[Material]:
        """书库全量（含已读进度），供资料面板展示「读到哪了」。"""
        return await self._material_store.list_all()

    async def register_material(
        self, path: str, filename: str, total_chars: int
    ) -> None:
        """注册一本读物进书库（只登记，不立即读）。

        读书由欲望驱动的 _maybe_start_activity 在活动时按 find_by_topic /
        next_readable 选书决定读不读；本方法不建活动、不发事件。
        """
        await self._material_store.upsert(path, filename, total_chars, time.time())

    # ---- 内部 ----

    async def _maybe_start_activity(self) -> None:
        self._task = await self._starter.start_next_if_idle(self._task)

    async def _execute(self, activity: Activity) -> None:
        await self._lifecycle.start(activity)
        try:
            if activity.type is ActivityType.FREE_EXPLORATION:
                await self._start_exploration_run(activity)
                return
            result = await self._run_activity(activity)
        except Exception:
            # fail-fast：失败态落库后仍上抛（不吞异常），但活动不卡 RUNNING
            await self._lifecycle.fail(activity)
            _logger.exception(
                "活动执行失败 activity_id=%s type=%s",
                activity.id,
                activity.type.value,
            )
            raise
        activity.progress["result"] = result
        await self.complete_activity(activity)

    async def _start_exploration_run(self, activity: Activity) -> None:
        """探索启动：线性「搜 → 抓正文 → 总结」跑完即结算（无决策点/托管/广播）。"""
        seed_topic = self._exploration_seed(activity)
        correlation_id = _correlation_id(activity)
        result = await self._exploration.run(seed_topic, correlation_id)
        activity.progress["result"] = result
        await self.complete_activity(activity)
        await self._finalize_exploration_sink(result, correlation_id)

    def _exploration_seed(self, activity: Activity) -> str:
        """种子话题：优先 goal.topic（探索欲的真实方向），退 description，
        退 activity.id。"""
        goal = activity.progress.get("goal")
        if isinstance(goal, dict):
            topic = cast(dict[str, Any], goal).get("topic")
            if isinstance(topic, str) and topic:
                return topic
        desc = activity.progress.get("description")
        if isinstance(desc, str) and desc:
            return desc
        return activity.id

    async def _finalize_exploration_sink(
        self, result: dict[str, Any], correlation_id: str
    ) -> None:
        """探索终局回写（best-effort）：强烈新兴趣→长期欲望，知识→长期记忆。

        「满足探索欲」由 ACTIVITY_END → satisfy_from_activity_end 走 goal_met 驱动，
        这里只管新增长期欲望与知识。
        """
        strong = result.get("strong_new_topics")
        if isinstance(strong, list):
            for topic in cast(list[Any], strong):
                if not isinstance(topic, str) or not topic.strip():
                    continue
                await self._desire.add_long_term(
                    LongTermDesire(
                        id=str(uuid.uuid4()),
                        created_at=time.time(),
                        type=DesireType.EXPLORATION,
                        name=topic,
                        description=f"想弄懂「{topic}」",
                        strength=0.5,
                        progress=0.0,
                        subtopics=[],
                    )
                )
        knowledge = result.get("knowledge")
        if isinstance(knowledge, list):
            items: list[dict[str, str]] = []
            for k in cast(list[Any], knowledge):
                if not isinstance(k, dict):
                    continue
                item_map = cast(dict[str, Any], k)
                if not str(item_map.get("content", "")).strip():
                    continue
                items.append(
                    {
                        "topic": str(item_map.get("topic", "")),
                        "content": str(item_map.get("content", "")),
                    }
                )
            if items:
                await self._memory.remember_knowledge(items, correlation_id)

    async def _run_activity(self, activity: Activity) -> dict[str, Any]:
        t = activity.type
        if t is ActivityType.READING:
            source = activity.progress.get("source")
            if source is None:
                # READING 必须有真实读物；缺 source 说明上游决策出错，fail-fast
                raise ValueError("读书活动缺 source：已禁止凭空编造")
            return await self._run_reading_source(activity, str(source))
        if t is ActivityType.CREATION:
            style = _pick_creation_style()
            knowledge = await self._memory.list_memories(tag="knowledge", limit=3)
            obs = await self._get_observation()
            state = await self._get_state()
            context = _build_creation_context(activity, style, knowledge, obs)
            system = _build_creation_system(self._canon, state)
            result = await self._run_llm_activity(
                activity,
                "creation",
                extra_context=context,
                context_label="创作参考",
                system=system,
            )
            title = str(result["title"])
            path = f"creations/{_sanitize_filename(title)}.md"
            written = await file_io("write", path, str(result["content"]))
            result["path"] = written["path"]
            result["tools"] = [
                {
                    "name": "file_io",
                    "args": {"action": "write", "path": path},
                    "ok": True,
                }
            ]
            return result
        if t is ActivityType.IDLE_REFLECTION:
            outcome = await self._reflect(_correlation_id(activity))
            return {"summary": outcome.story if outcome is not None else None}
        if t is ActivityType.FREE_EXPLORATION:
            raise ValueError("自由探索改走 _execute 的 _start_exploration_run 分叉")
        if t is ActivityType.OBSERVE_USER:
            obs = await self._get_observation()
            presence = obs.get("presence", "")
            window_title = obs.get("window_title", "")
            screen_summary = obs.get("screen_summary", "")
            return {
                "presence": presence,
                "window_title": window_title,
                "screen_summary": screen_summary,
                "summary": build_observation_summary(
                    presence, window_title, screen_summary
                ),
            }
        if t is ActivityType.REST:
            return {}
        raise ValueError(f"未知活动类型 {t!r}")

    async def _run_reading_source(
        self, activity: Activity, source: str
    ) -> dict[str, Any]:
        """兼容旧内部调用；读书实现位于 `ReadingActivityRunner`。"""
        return await self._reading_runner.run(activity, source)

    async def _extract_knowledge(
        self, activity: Activity, filename: str, content: str
    ) -> None:
        """兼容旧测试入口；知识点提取由 runner 执行。"""
        await self._reading_runner.extract_knowledge(activity, filename, content)

    async def _run_llm_activity(
        self,
        activity: Activity,
        output_type: str,
        extra_context: str | None = None,
        context_label: str = "读物信息",
        system: str | None = None,
    ) -> dict[str, Any]:
        user_msg = f"活动类型：{activity.type.value}"
        if extra_context:
            user_msg += f"\n{context_label}：\n{extra_context}"
        output = await self._llm.complete(
            [
                {"role": "system", "content": system or _ACTIVITY_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            module="activity",
            output_type=output_type,
            correlation_id=_correlation_id(activity),
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        return _parse_activity_result(output.content, output_type)


_ACTIVITY_SYSTEM = (
    "你是尼克斯，正在读书。只输出 JSON，键："
    "book（书名，非空字符串）、note（本次读书笔记，非空字符串）。"
    "note 自然承接已读片段，不重复概括已读部分、只续写本次新读内容；"
    "note 正文里不要写「上次读到第 X 字」这类位置字样。"
)
