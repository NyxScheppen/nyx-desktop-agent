import asyncio
import contextlib
import hashlib
import json
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from nyx.activity.exploration import Exploration, should_explore
from nyx.activity.material_store import MaterialStore
from nyx.activity.observe import build_observation_summary
from nyx.activity.reading_note_store import ReadingNoteStore
from nyx.activity.scheduler import (
    build_schedule,
    desire_to_activity,
    format_time_label,
    rank_desires,
)
from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig, ExplorationConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import ActivityStatus, ActivityType, DesireType, EventType, TickType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_DAY, internal_event
from nyx.inner_life.emotion import ENERGY_REST_THRESHOLD
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.tools.file_io import file_io
from nyx.tools.registry import ToolRegistry
from nyx.types import (
    Activity,
    Annotation,
    CurrentState,
    Event,
    Material,
    Memory,
    ReadingNote,
    ReflectionOutcome,
    ShortTermDesire,
)

_logger = logging.getLogger(__name__)

_READ_CONTEXT_CHARS = 6000  # 读物喂 LLM 的字符预算（decision，可推翻）
_KNOWLEDGE_REF_CHARS = 80   # 创作知识参考单条截断字符数（decision，可推翻）
_KNOWLEDGE_MAX_POINTS = 5   # 每本书知识点入记忆上限（decision，可推翻）
_KNOWLEDGE_MAX_CHUNKS = 16  # 知识点提取分块上限，防一次打满全书（decision，可推翻）

# 可续活动：打断置 PAUSED、同日程块内恢复同一记录（读书续读，创作/探索重跑）。
# 发呆/观察/休息瞬时无进度，仍抢占即废弃置 ABANDONED。
_RESUMABLE_TYPES = (
    ActivityType.READING,
    ActivityType.CREATION,
    ActivityType.FREE_EXPLORATION,
)


def _day_start(now: float) -> float:
    """当日零点（UTC 日边界，MVP 可推翻为本地时区）。纯函数。"""
    return now - now % SECONDS_PER_DAY


def _schedule_block_id(now: float, grid_minutes: int) -> str:
    """当前日程块 id（网格标签）：块序号 = 当日已过分钟 // grid_minutes。

    复用 scheduler.format_time_label(block_index, grid_minutes, 0.0) 产出
    design §3.3 约定的网格标签（如 14:00），与 main._tick_loop 的 grid 边界一致。
    纯函数。
    """
    block_index = int(now % SECONDS_PER_DAY) // 60 // grid_minutes
    return format_time_label(block_index, grid_minutes, 0.0)


def _goal_met(goal: dict[str, Any] | None, result: dict[str, Any]) -> bool | None:
    """Goal 完成判定（纯函数，C3 精确版）。

    goal None → None；否则按 action 判「本次是否完成一个单位」：
    read → result.completed（读完整本）；write → 有 title+content；
    observe → 有 presence。goal None 的欲望由 desire 层按单次 goal_met 满足。
    """
    if goal is None:
        return None
    action = goal.get("action")
    if action == "read":
        return bool(result.get("completed"))
    if action == "write":
        return bool(result.get("title") and result.get("content"))
    if action == "observe":
        return bool(result.get("presence"))
    return False


def _sanitize_filename(name: str) -> str:
    """把创作标题清洗成安全文件名（去路径分隔符/控制字符，空则回退 untitled）。

    对称读书读 workspace：创作落盘进 workspace/creations/<safe>.md。
    """
    cleaned = "".join(
        c for c in name if c not in '\\/:*?"<>|' and c.isprintable()
    ).strip()
    return cleaned or "untitled"


def _path_hash_suffix(path: str) -> str:
    """读物绝对路径 → 8 位短哈希，作落盘文件名后缀（跨路径同名书不互相覆盖）。"""
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:8]


def _empty_progress() -> dict[str, Any]:
    """活动 progress 初始模板（desire_id/goal/correlation_id 三键为空）。"""
    return {"desire_id": None, "goal": None, "correlation_id": None}


def _correlation_id(activity: Activity) -> str:
    """活动事件/LLM 溯源 id：优先欲望 correlation_id，退活动自身 id。"""
    return str(activity.progress.get("correlation_id") or activity.id)


def _harvest_task_exception(task: asyncio.Task[None]) -> None:
    """收割后台 task 异常，避免 asyncio 'Task exception was never retrieved' 警告。

    真正的失败详情已在 _execute 的 except 块里经 logger.exception 记录；
    这里只负责把异常标记为「已检索」，不重复记录。
    """
    if not task.cancelled():
        task.exception()


def _parse_activity_result(raw: str, output_type: str) -> dict[str, Any]:
    """LLM 活动结果解析 + 结构校验（对齐 11/12 的 _parse_* fail-fast 风格）。

    reading 需 {book, note}、creation 需 {title, content}；
    缺键 raise（非法结构不静默吞）。
    """
    data: Any = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"活动结果 JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    required = ("book", "note") if output_type == "reading" else ("title", "content")
    if not all(k in parsed for k in required):
        raise ValueError(f"活动结果 JSON 缺键：{required}")
    return parsed


_CREATION_STYLES = ("日记体", "随笔", "微型小说", "散文诗", "书信体", "观察笔记")


def _pick_creation_style() -> str:
    """创作风格随机池：6 种里随机抽一种（W1）。"""
    return random.choice(_CREATION_STYLES)


def _build_creation_context(
    activity: Activity,
    style: str,
    knowledge: list[Memory],
    observation: dict[str, str],
) -> str:
    """创作参考上下文：风格 + 主题 + 知识库参考 + 当前屏幕灵感（W1/W2/W3）。

    各段有内容才拼、空则省略；纯确定性拼接，不调 LLM。
    """
    goal = activity.progress.get("goal")
    topic = ""
    if isinstance(goal, dict):
        t = cast(dict[str, Any], goal).get("topic")
        if isinstance(t, str):
            topic = t
    if not topic:
        desc = activity.progress.get("description")
        if isinstance(desc, str):
            topic = desc
    parts = [f"风格：{style}"]
    if topic:
        parts.append(f"主题：{topic}")
    if knowledge:
        refs = "\n".join(
            f"- {m.summary}：{m.content[:_KNOWLEDGE_REF_CHARS]}"
            for m in knowledge[:3]
        )
        parts.append(f"知识库参考（可引用，勿编造）：\n{refs}")
    window = observation.get("window_title", "").strip()
    screen = observation.get("screen_summary", "").strip()
    if window or screen:
        insp = "；".join(x for x in (window, screen) if x)
        parts.append(f"当前屏幕灵感：{insp}")
    return "\n\n".join(parts)


def _build_creation_system(canon: str, state: CurrentState) -> str:
    """创作 system prompt：canon 人格全文 + 此刻心境 + 创作声音指令 + JSON 约束。

    补上创作路径此前缺失的人格声音（canon 只进对话，不进 _ACTIVITY_SYSTEM）；
    「此刻心境」让文字有情绪底色，而非任意模型平铺直叙的套话。纯函数。
    """
    desires = "、".join(d.description for d in state.active_desires) or "无"
    mood = (
        "[此刻心境]\n"
        f"情感：{state.emotion.value}"
        f"（valence={state.valence:.2f}，arousal={state.arousal:.2f}）\n"
        f"精力：{state.energy:.0f}/100\n"
        f"惦记：{desires}"
    )
    voice = (
        "[创作要求]\n"
        "以尼克斯的说话风格写：温柔克制安静真诚、带一点羞涩犹豫停顿、偶尔轻微自我修正，"
        "不要客服腔、不要堆砌华丽词藻，让文字有你的情绪底色。"
        "遵循给定风格，可引用知识库参考，但绝不编造不存在的知识；"
        "当前屏幕灵感只作启发，勿照搬。按 JSON 输出 {title, content}。"
    )
    return f"{canon}\n\n{mood}\n\n{voice}"


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
        reading_notes: ReadingNoteStore,
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
        self._reading_notes = reading_notes
        self._get_state = get_state
        self._reflect = reflect
        self._get_observation = get_observation
        self._config = config
        self._canon = canon
        self._exploration = Exploration(
            llm, evaluator, tools, exploration_config
        )
        self._exploration_config = exploration_config
        self._start_lock = asyncio.Lock()
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
        """选下一个活动（纯决策，无 I/O）。

        desires 已由 _maybe_start_activity 排序（rank_desires）。
        """
        if not desires:
            return None
        target = next(
            (d for d in desires if desire_to_activity(d.type) is not None), None
        )
        if target is None:
            return None
        schedule = build_schedule(desires, state.energy, self._config.energy_delta)
        if not schedule:
            return None
        activity_type = schedule[0]
        if activity_type is ActivityType.REST and target.type is not DesireType.REST:
            # 精力恢复穿插的 REST（队首非休息欲却产出 REST = 精力不足），
            # 无关联 desire
            target = None
        now = time.time()
        progress = _empty_progress()
        if target is not None:
            progress["desire_id"] = target.id
            progress["correlation_id"] = target.id
            progress["description"] = target.description
            if target.goal is not None:
                progress["goal"] = {
                    "action": target.goal.action.value,
                    "count": target.goal.count,
                    "topic": target.goal.topic,
                }
        return Activity(
            id=str(uuid.uuid4()),
            type=activity_type,
            schedule_block_id=_schedule_block_id(now, self._config.grid_minutes),
            status=ActivityStatus.PENDING,
            progress=progress,
            started_at=now,
        )

    def _default_activity(self, state: CurrentState) -> Activity:
        """空槽默认（design §8.2 观察/发呆，13 §30 委托 14）：无欲望可消费时，
        精力疲惫 → 发呆反思（+10 恢复 + 反思），否则 → 观察用户（-10 情报收集）。
        纯决策，无 I/O。
        """
        activity_type = (
            ActivityType.IDLE_REFLECTION
            if state.energy < ENERGY_REST_THRESHOLD
            else ActivityType.OBSERVE_USER
        )
        now = time.time()
        return Activity(
            id=str(uuid.uuid4()),
            type=activity_type,
            schedule_block_id=_schedule_block_id(now, self._config.grid_minutes),
            status=ActivityStatus.PENDING,
            progress=_empty_progress(),
            started_at=now,
        )

    # ---- 生命周期 ----

    async def complete_activity(self, activity: Activity) -> None:
        """完成：goal 判定 + 收尾 + 发布 activity_end（desire/inner_life 消费）。"""
        activity.status = ActivityStatus.COMPLETED
        activity.ended_at = time.time()
        await self._store.update(activity)
        goal = activity.progress.get("goal")
        result = activity.progress.get("result", {})
        await self._bus.publish(
            internal_event(
                EventType.ACTIVITY_END,
                {
                    "activity_id": activity.id,
                    "type": activity.type.value,
                    "desire_id": activity.progress.get("desire_id"),
                    "goal_met": _goal_met(goal, result),
                    "energy_delta": getattr(
                        self._config.energy_delta, activity.type.value
                    ),
                    "result": result,
                },
                _correlation_id(activity),
            )
        )

    async def interrupt(self, activity_id: str, by_event: EventType) -> None:
        """抢占即暂停：校验目标 RUNNING → cancel 执行 task 并 await 其彻底结束
        → 重读守卫（窗口内已自行完成/失败则不覆盖）→ 置终态落库
        （可续活动 PAUSED，其余 ABANDONED）+ 发布 activity_interrupted。

        执行中的 result 尚未写入，故仅落终态（不持久化部分进度）；
        读书的 read_chars 已 advance 进 material 层，恢复时从那里续读。
        """
        activity = await self._store.get(activity_id)
        if activity is None or activity.status is not ActivityStatus.RUNNING:
            return
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            # 等 _execute 收尾即可，无论取消还是异常终；失败已由 _execute
            # 记日志 + done_callback 收割，终态交给下方重读守卫，不跳过 ABANDONED
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        activity = await self._store.get(activity_id)   # 重读：已终态则不动
        if activity is None or activity.status is not ActivityStatus.RUNNING:
            return
        # 可续活动（读书/创作/探索）打断置 PAUSED 保留记录，同日程块内恢复；
        # 其余（发呆/观察/休息瞬时无进度）抢占即废弃，仍置 ABANDONED。
        activity.status = (
            ActivityStatus.PAUSED
            if activity.type in _RESUMABLE_TYPES
            else ActivityStatus.ABANDONED
        )
        activity.ended_at = time.time()
        await self._store.update(activity)
        desire_id = activity.progress.get("desire_id")
        if isinstance(desire_id, str):
            await self._desire.mark_suppressed(desire_id)  # 中断：ACTIVE → SUPPRESSED
        await self._bus.publish(
            internal_event(
                EventType.ACTIVITY_INTERRUPTED,
                {"activity_id": activity_id, "by": by_event.value},
                activity_id,
            )
        )

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

    async def list_reading_notes(self, limit: int = 50) -> list[ReadingNote]:
        """读书笔记清单（含批注数），供读书笔记面板 CRUD。"""
        return await self._reading_notes.list_notes(limit)

    async def delete_reading_note(self, note_id: str) -> None:
        """删一条读书笔记（级联删其批注；已落盘 notes/*.md 文件不动）。"""
        await self._reading_notes.delete(note_id)

    async def list_annotations(self, target_id: str) -> list[Annotation]:
        """某笔记的全部批注，按时间升序。"""
        return await self._reading_notes.list_annotations(target_id)

    async def add_annotation(self, target_id: str, content: str) -> Annotation:
        """给笔记加一条用户批注（author 固定 'user'）。"""
        annotation = Annotation(
            id=str(uuid.uuid4()),
            target_id=target_id,
            author="user",
            content=content,
            created_at=time.time(),
        )
        await self._reading_notes.add_annotation(annotation)
        return annotation

    async def delete_annotation(self, annotation_id: str) -> None:
        """删一条批注。"""
        await self._reading_notes.delete_annotation(annotation_id)

    async def read_material(
        self, path: str, filename: str, total_chars: int, correlation_id: str
    ) -> None:
        """用户投喂资料：注册进书库 → 立即发起一次 READING 活动读第一块。

        与 _maybe_start_activity 共用 _start_lock 串行守卫；忙时跳过（文件已落盘、
        且已入书库，探索欲后续会自行续读，不排队）。结果经 activity_start/activity_end
        SSE 可见。
        """
        await self._material_store.upsert(path, filename, total_chars, time.time())
        async with self._start_lock:
            if self._task is not None and not self._task.done():
                return
            now = time.time()
            activity = Activity(
                id=str(uuid.uuid4()),
                type=ActivityType.READING,
                schedule_block_id=_schedule_block_id(now, self._config.grid_minutes),
                status=ActivityStatus.PENDING,
                progress={
                    "source": path,
                    "filename": filename,
                    "description": filename,
                    "read_chars": 0,
                    "total_chars": total_chars,
                    "correlation_id": correlation_id,
                },
                started_at=now,
            )
            await self._store.insert(activity)
            self._task = asyncio.create_task(self._execute(activity))
            self._task.add_done_callback(_harvest_task_exception)

    # ---- 内部 ----

    async def _maybe_start_activity(self) -> None:
        async with self._start_lock:
            if self._task is not None and not self._task.done():
                return
            current = await self._store.get_current()
            if current is not None and current.status is ActivityStatus.RUNNING:
                return
            # 同日程块内恢复 PAUSED 记录（design §3.3）：读书从 material 层刷新
            # read_chars 续读；创作/探索无中间态、重跑。恢复同一 id，不新建。
            block_id = _schedule_block_id(time.time(), self._config.grid_minutes)
            resumed = await self._store.get_paused_in_block(block_id)
            if resumed is not None:
                if resumed.type is ActivityType.READING:
                    source = resumed.progress.get("source")
                    if isinstance(source, str):
                        material = await self._material_store.get_by_path(source)
                        if material is not None:
                            resumed.progress["read_chars"] = material.read_chars
                            resumed.progress["total_chars"] = material.total_chars
                resumed.ended_at = None
                self._task = asyncio.create_task(self._execute(resumed))
                self._task.add_done_callback(_harvest_task_exception)
                return
            desires = await self._desire.get_pending()
            values = (await self._desire.get_all()).values
            ranked = rank_desires(desires, values)
            state = await self._get_state()
            activity = self.select_activity(ranked, state)
            if activity is None:
                activity = self._default_activity(state)
            if activity.type is ActivityType.READING:
                # 先按 goal.topic 选料（C2），命中读那本；否则最近未读完；
                # 再否则转自由探索（下方 else 分支）。绝不凭空编造。
                goal = cast(dict[str, Any] | None, activity.progress.get("goal"))
                topic = goal.get("topic") if goal is not None else None
                material = None
                if isinstance(topic, str) and topic:
                    material = await self._material_store.find_by_topic(topic)
                if material is None:
                    material = await self._material_store.next_readable()
                if material is not None:
                    # 读最近未读完的那本，从它的进度续读（绝不编造）
                    activity.progress["source"] = material.path
                    activity.progress["filename"] = material.filename
                    activity.progress["description"] = material.filename
                    activity.progress["read_chars"] = material.read_chars
                    activity.progress["total_chars"] = material.total_chars
                else:
                    # 无书可读：转自由探索（沿用限速）；限速中则退回默认活动，
                    # 任何情况都不让 LLM 凭空编造读书内容
                    last = await self._store.get_last_exploration()
                    if should_explore(
                        state.energy,
                        last,
                        self._exploration_config.rate_limit_hours,
                        time.time(),
                    ):
                        activity.type = ActivityType.FREE_EXPLORATION
                    else:
                        activity = self._default_activity(state)
            await self._store.insert(activity)
            self._task = asyncio.create_task(self._execute(activity))
            self._task.add_done_callback(_harvest_task_exception)

    async def _execute(self, activity: Activity) -> None:
        activity.status = ActivityStatus.RUNNING
        await self._store.update(activity)
        desire_id = activity.progress.get("desire_id")
        if isinstance(desire_id, str):
            await self._desire.mark_active(desire_id)  # 消费开始：PENDING → ACTIVE
        await self._bus.publish(
            internal_event(
                EventType.ACTIVITY_START,
                {
                    "activity_id": activity.id,
                    "type": activity.type.value,
                    "schedule_block_id": activity.schedule_block_id,
                },
                _correlation_id(activity),
            )
        )
        try:
            result = await self._run_activity(activity)
        except Exception:
            # fail-fast：失败态落库后仍上抛（不吞异常），但活动不卡 RUNNING
            activity.status = ActivityStatus.INCOMPLETE
            activity.ended_at = time.time()
            await self._store.update(activity)
            desire_id = activity.progress.get("desire_id")
            if isinstance(desire_id, str):
                # 异常退出：ACTIVE → SUPPRESSED
                await self._desire.mark_suppressed(desire_id)
            _logger.exception(
                "活动执行失败 activity_id=%s type=%s",
                activity.id,
                activity.type.value,
            )
            raise
        activity.progress["result"] = result
        await self.complete_activity(activity)

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
                activity, "creation", extra_context=context,
                context_label="创作参考", system=system,
            )
            title = str(result["title"])
            path = f"creations/{_sanitize_filename(title)}.md"
            written = await file_io("write", path, str(result["content"]))
            result["path"] = written["path"]
            return result
        if t is ActivityType.IDLE_REFLECTION:
            outcome = await self._reflect(_correlation_id(activity))
            return {"summary": outcome.story if outcome is not None else None}
        if t is ActivityType.FREE_EXPLORATION:
            return await self._exploration.run(
                seed=str(activity.progress.get("description") or activity.id),
                correlation_id=_correlation_id(activity),
            )
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
        """分块读真实文件：切 [read_chars, read_chars+6000) 一块喂 LLM 产 {book, note}，
        推进书库进度并把 note 追加进片段；读到最后一块时聚合全部片段 → 完整笔记
        落盘 + completed=True（一本=一次单位）。绝不凭空编造。"""
        content = await asyncio.to_thread(
            Path(source).read_text, encoding="utf-8", errors="replace"
        )
        read_chars = int(activity.progress.get("read_chars", 0))
        chunk = content[read_chars : read_chars + _READ_CONTEXT_CHARS]
        filename = str(activity.progress.get("filename") or Path(source).name)
        if chunk == "":
            # 已读到末尾（或文件比注册时短）：无新块可读，聚合已有片段（不编造）
            full = await self._finalize_reading(
                activity, source, filename, len(content)
            )
            await self._extract_knowledge(activity, filename, content)
            return full
        # 滚动摘要接力：把「上次读到哪里 + 已读片段笔记」一起喂给 LLM，让本次 note
        # 自然承接已读部分、只续写本块新内容（避免几篇之间不连贯）。
        prior = await self._material_store.get_fragments(source)
        prior_block = ""
        if prior:
            prior_block = (
                f"上次已读到第 {read_chars} 字，此前片段笔记：\n"
                + "\n---\n".join(prior)
                + "\n"
            )
        context = (
            f"书名：{filename}\n"
            f"{prior_block}"
            f"本次新读（第 {read_chars}～{read_chars + len(chunk)} 字）：\n{chunk}"
        )
        result = await self._run_llm_activity(
            activity, "reading", extra_context=context
        )
        new_read_chars = read_chars + len(chunk)
        await self._material_store.append_fragment(
            source, str(result.get("note", "")), time.time()
        )
        await self._material_store.advance(source, new_read_chars, time.time())
        result["read_chars"] = new_read_chars
        result["total_chars"] = len(content)
        if new_read_chars >= len(content):
            # 读到最后一块：聚合全部片段（含本块）→ 完整笔记落盘 + completed
            full = await self._finalize_reading(
                activity, source, filename, len(content)
            )
            await self._extract_knowledge(activity, filename, content)
            full["read_chars"] = new_read_chars
            full["total_chars"] = len(content)
            return full
        return result

    async def _finalize_reading(
        self, activity: Activity, source: str, filename: str, content_len: int
    ) -> dict[str, Any]:
        """读完整本书：聚合 note_fragments → 完整笔记落盘 workspace/notes/<safe>.md。"""
        fragments = await self._material_store.get_fragments(source)
        full_note = await self._aggregate_note(activity, filename, fragments)
        note_path = (
            f"notes/{_sanitize_filename(filename)}"
            f"-{_path_hash_suffix(source)}.md"
        )
        written = await file_io("write", note_path, full_note)
        # 同路径重读（read_material 重传会重置进度）原地更新保留批注，不累积重复
        # 笔记；不同路径同名书互不误删（path 是去重键，book 仅 filename 展示）。
        await self._reading_notes.upsert_by_path(
            ReadingNote(
                id=str(uuid.uuid4()),
                book=filename,
                content=full_note,
                created_at=time.time(),
                path=source,
            )
        )
        return {
            "book": filename,
            "note": full_note,
            "path": written["path"],
            "completed": True,
            "read_chars": content_len,
            "total_chars": content_len,
        }

    async def _aggregate_note(
        self, activity: Activity, filename: str, fragments: list[str]
    ) -> str:
        """把各块片段笔记聚合成一篇完整读书笔记（1 次 LLM，output_type=note）。"""
        joined = "\n---\n".join(fragments) if fragments else "（无片段）"
        output = await self._llm.complete(
            [
                {"role": "system", "content": _AGGREGATE_SYSTEM},
                {
                    "role": "user",
                    "content": f"书名：{filename}\n各片段笔记：\n{joined}",
                },
            ],
            module="activity",
            output_type="note",
            correlation_id=_correlation_id(activity),
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        data: Any = json.loads(output.content)
        if not isinstance(data, dict):
            raise ValueError(f"聚合笔记 JSON 应是对象，得到 {type(data).__name__}")
        note = cast(dict[str, Any], data).get("note")
        if not isinstance(note, str) or not note:
            raise ValueError("聚合笔记 JSON 缺 note 或非空字符串")
        return note

    async def _extract_knowledge(
        self, activity: Activity, filename: str, content: str
    ) -> None:
        """读完一本书后分块提取知识点入长期记忆（R1）。

        正文按 _READ_CONTEXT_CHARS 分块逐个喂 LLM（对齐阅读循环的字符预算，
        避免整本书超上下文被静默截断）；跨块累积去重，最多 _KNOWLEDGE_MAX_POINTS
        条、块数上限 _KNOWLEDGE_MAX_CHUNKS。best-effort：任一失败只记日志、
        不冒泡——读书笔记已落盘，知识点是增强旁路，主流程正确性不依赖它
        （CLAUDE.md 豁免：LLM/eval 失败吞异常）。
        """
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        budget_chars = _KNOWLEDGE_MAX_CHUNKS * _READ_CONTEXT_CHARS
        for start in range(0, min(len(content), budget_chars), _READ_CONTEXT_CHARS):
            chunk = content[start : start + _READ_CONTEXT_CHARS]
            if not chunk.strip():
                continue
            for point in await self._extract_knowledge_points(
                activity, filename, chunk
            ):
                if len(items) >= _KNOWLEDGE_MAX_POINTS:
                    break
                content_pt = point.get("content", "")
                if content_pt and content_pt not in seen:
                    seen.add(content_pt)
                    items.append(point)
            if len(items) >= _KNOWLEDGE_MAX_POINTS:
                break
        if items:
            try:
                await self._memory.remember_knowledge(
                    items, _correlation_id(activity)
                )
            except Exception:
                _logger.exception("知识点入库失败 activity_id=%s", activity.id)

    async def _extract_knowledge_points(
        self, activity: Activity, filename: str, chunk: str
    ) -> list[dict[str, str]]:
        """对单个正文分块（≤_READ_CONTEXT_CHARS）提取知识点，返回 [{topic, content}]。

        best-effort：LLM/解析失败返回空列表不冒泡，调用方继续下一块。
        """
        try:
            output = await self._llm.complete(
                [
                    {"role": "system", "content": _KNOWLEDGE_SYSTEM},
                    {"role": "user", "content": f"书名：{filename}\n正文：\n{chunk}"},
                ],
                module="activity",
                output_type="knowledge",
                correlation_id=_correlation_id(activity),
                json_mode=True,
            )
            await self._evaluator.evaluate(output)
            data: Any = json.loads(output.content)
            if not isinstance(data, dict):
                return []
            raw_points = cast(dict[str, Any], data).get("points")
            if not isinstance(raw_points, list):
                return []
            items: list[dict[str, str]] = []
            for point in cast(list[Any], raw_points)[:_KNOWLEDGE_MAX_POINTS]:
                if not isinstance(point, dict):
                    continue
                point_map = cast(dict[str, Any], point)
                topic = point_map.get("topic")
                content_pt = point_map.get("content")
                if isinstance(content_pt, str) and content_pt.strip():
                    items.append(
                        {
                            "topic": topic if isinstance(topic, str) else "",
                            "content": content_pt.strip(),
                        }
                    )
            return items
        except Exception:
            _logger.exception("知识点提取失败 activity_id=%s", activity.id)
            return []

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
    "你是尼克斯。按 JSON 输出活动结果，键随活动类型："
    "读书 {book, note}、创作 {title, content}。"
    "读书时：note 自然承接已读片段，不重复概括已读部分、只续写本次新读内容；"
    "note 正文里不要写「上次读到第 X 字」这类位置字样。"
    "创作时：遵循给定风格，可引用知识库参考，但绝不编造不存在的知识；"
    "当前屏幕灵感只作启发，勿照搬。"
)


_KNOWLEDGE_SYSTEM = (
    "你是尼克斯，正在阅读一本书。从下面的文本中提取 1-5 个客观、可复用的知识点"
    "（事实、概念、方法）。只输出 JSON，键：points（数组，每项 {topic, content}，"
    "topic 是主题/概念名、content 是一句完整自洽的知识陈述）。"
    "没有值得提取的知识就输出 {\"points\": []}。"
)


_AGGREGATE_SYSTEM = (
    "你是尼克斯。把读书各片段笔记聚合成一篇完整读书笔记，"
    "只输出 JSON，键：note（完整笔记，非空字符串）。"
)
