import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, cast
from urllib.parse import urlsplit

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
from nyx.enums import (
    ActivityStatus,
    ActivityType,
    AssignedTaskStatus,
    AssignedTaskType,
    EventType,
    MemoryKind,
    TickType,
)
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import internal_event
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade, build_source_topic
from nyx.tools.file_io import file_io
from nyx.tools.registry import ToolRegistry
from nyx.types import (
    Activity,
    AssignedTask,
    Book,
    CurrentState,
    Event,
    Material,
    ReadingProgress,
    ReflectionOutcome,
    ShortTermDesire,
)

_CREATION_STYLES = _creation.CREATION_STYLES
_build_creation_context = _creation.build_creation_context
_build_creation_system = _creation.build_creation_system
_creation_subject = _creation.creation_subject
_pick_creation_style = _creation.pick_creation_style
_creation_output_path = _activity_paths.creation_output_path
_correlation_id = _activity_lifecycle.correlation_id
_goal_met = _activity_lifecycle.goal_met
_path_hash_suffix = _activity_paths.path_hash_suffix
_sanitize_filename = _activity_paths.sanitize_filename

_logger = logging.getLogger(__name__)


class WebTasksDisabledError(ValueError):
    """Explicit web tasks are unavailable while networking is disabled."""

def _day_start(now: float) -> float:
    """Return the Unix timestamp for midnight in the system local timezone."""
    local_now = datetime.fromtimestamp(now)
    return local_now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _creation_checkpoint(activity: Activity) -> dict[str, Any]:
    raw = activity.progress.get("creation")
    if isinstance(raw, dict):
        checkpoint = cast(dict[str, Any], raw)
    else:
        checkpoint = {}
    return {
        "style": checkpoint.get("style"),
        "llm_done": bool(checkpoint.get("llm_done")),
        "title": checkpoint.get("title"),
        "content": checkpoint.get("content"),
        "file_written": bool(checkpoint.get("file_written")),
        "path": checkpoint.get("path"),
    }


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
        list_reader_books: Callable[[], Awaitable[list[Book]]] | None = None,
        read_reader_book: (
            Callable[[str, int | None, str], Awaitable[dict[str, Any]]] | None
        ) = None,
        get_reader_book: Callable[[str], Awaitable[Book]] | None = None,
        get_reader_progress: (
            Callable[[str], Awaitable[ReadingProgress]] | None
        ) = None,
    ) -> None:
        self._store = store
        self._material_store = material_store
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._tools = tools
        self._desire = desire
        self._memory = memory
        self._get_state = get_state
        self._reflect = reflect
        self._get_observation = get_observation
        self._config = config
        self._canon = canon
        self._read_reader_book = read_reader_book
        self._get_reader_book = get_reader_book
        self._get_reader_progress = get_reader_progress
        self._web_enabled = exploration_config.web_enabled
        self._exploration = Exploration(
            llm,
            evaluator,
            tools,
            store,
            desire,
            memory,
            exploration_config,
        )
        self._reading_runner = ReadingActivityRunner(
            material_store, llm, evaluator, memory, file_io, store.update
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
            list_reader_books,
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
        activity = await self._store.get(activity_id)
        await self._lifecycle.interrupt(activity_id, by_event, self._task)
        task_id = activity.progress.get("task_id") if activity is not None else None
        if isinstance(task_id, str):
            updated = await self._store.get(activity_id)
            if updated is not None and updated.status in {
                ActivityStatus.PAUSED,
                ActivityStatus.ABANDONED,
            }:
                await self._set_task_status(task_id, AssignedTaskStatus.PENDING)

    async def recover_stale_running(self) -> list[Activity]:
        """启动恢复：清理 DB 中没有后台 task 承接的 RUNNING 活动。"""
        recovered = await self._lifecycle.recover_stale_running()
        await self._store.recover_running_tasks(time.time())
        return recovered

    # ---- 读 ----

    async def get_current(self) -> Activity | None:
        return await self._store.get_current()

    async def get_schedule(self) -> list[Activity]:
        return await self._store.list_schedule(_day_start(time.time()))

    async def get_results(
        self,
        limit: int = 100,
        offset: int = 0,
        activity_type: ActivityType | None = None,
    ) -> list[Activity]:
        """跨天历史产出（读书笔记/探索发现/创作内容），按结束时间倒序。"""
        return await self._store.list_results(limit, offset, activity_type)

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

    async def list_tasks(self) -> list[AssignedTask]:
        return await self._store.list_tasks()

    async def assign_web_task(self, url: str) -> AssignedTask:
        if not self._web_enabled:
            raise WebTasksDisabledError("联网探索已关闭")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("只支持 HTTP(S) URL")
        now = time.time()
        task = await self._store.create_task(
            AssignedTask(
                id=str(uuid.uuid4()),
                type=AssignedTaskType.WEB,
                status=AssignedTaskStatus.PENDING,
                url=url,
                book_id=None,
                target_paragraph=None,
                checkpoint={},
                error=None,
                created_at=now,
                updated_at=now,
            )
        )
        await self._publish_task_update(task)
        await self._maybe_start_activity()
        return await self._require_task(task.id)

    async def assign_book_task(
        self, book_id: str, target_paragraph: int
    ) -> AssignedTask:
        if self._get_reader_book is None or self._get_reader_progress is None:
            raise RuntimeError("reading facade 尚未绑定")
        book = await self._get_reader_book(book_id)
        if target_paragraph < 1 or target_paragraph > book.total_paragraphs:
            raise ValueError("段落越界")
        progress = await self._get_reader_progress(book_id)
        now = time.time()
        already_read = (
            progress.revision > 0 and target_paragraph <= progress.nyx_position
        )
        task = await self._store.create_task(
            AssignedTask(
                id=str(uuid.uuid4()),
                type=AssignedTaskType.BOOK,
                status=(
                    AssignedTaskStatus.COMPLETED
                    if already_read
                    else AssignedTaskStatus.PENDING
                ),
                url=None,
                book_id=book_id,
                target_paragraph=target_paragraph,
                checkpoint={},
                error=None,
                created_at=now,
                updated_at=now,
            )
        )
        await self._publish_task_update(task)
        if task.status is AssignedTaskStatus.PENDING:
            await self._maybe_start_activity()
        return await self._require_task(task.id)

    # ---- 内部 ----

    async def _maybe_start_activity(self) -> None:
        self._task = await self._starter.start_next_if_idle(self._task)

    async def _execute(self, activity: Activity) -> None:
        await self._lifecycle.start(activity)
        task_id = activity.progress.get("task_id")
        if isinstance(task_id, str):
            await self._set_task_status(task_id, AssignedTaskStatus.RUNNING)
        try:
            if activity.type is ActivityType.FREE_EXPLORATION:
                await self._start_exploration_run(activity)
                return
            result = await self._run_activity(activity)
        except Exception as error:
            # fail-fast：失败态落库后仍上抛（不吞异常），但活动不卡 RUNNING
            await self._lifecycle.fail(activity)
            task_id = activity.progress.get("task_id")
            if isinstance(task_id, str):
                await self._set_task_status(
                    task_id, AssignedTaskStatus.FAILED, str(error)[:500]
                )
            _logger.exception(
                "活动执行失败 activity_id=%s type=%s",
                activity.id,
                activity.type.value,
            )
            raise
        activity.progress["result"] = result
        await self.complete_activity(activity)
        if isinstance(task_id, str):
            await self._set_task_status(task_id, AssignedTaskStatus.COMPLETED)

    async def _start_exploration_run(self, activity: Activity) -> None:
        """探索启动：交给 Exploration 状态机跑完并结算。"""
        result = await self._exploration.run(activity)
        activity.progress["result"] = result
        await self.complete_activity(activity)

    async def _run_activity(self, activity: Activity) -> dict[str, Any]:
        t = activity.type
        if t is ActivityType.READING:
            task_id = activity.progress.get("task_id")
            if isinstance(task_id, str):
                task = await self._require_task(task_id)
                if task.type is AssignedTaskType.WEB:
                    return await self._run_web_task(task)
            book_id = activity.progress.get("book_id")
            if isinstance(book_id, str):
                if self._read_reader_book is None:
                    raise RuntimeError("EPUB activity reader 尚未绑定")
                target_raw = activity.progress.get("target_paragraph")
                target = target_raw if isinstance(target_raw, int) else None
                result = await self._read_reader_book(
                    book_id, target, _correlation_id(activity)
                )
                if target is None and result.get("completed") is False:
                    activity.progress["goal_signal"] = None
                return result
            source = activity.progress.get("source")
            if source is None:
                # READING 必须有真实读物；缺 source 说明上游决策出错，fail-fast
                raise ValueError("读书活动缺 source：已禁止凭空编造")
            return await self._run_reading_source(activity, str(source))
        if t is ActivityType.CREATION:
            return await self._run_creation(activity)
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

    async def _run_web_task(self, task: AssignedTask) -> dict[str, Any]:
        if task.url is None:
            raise ValueError("网页任务缺 URL")
        fetched = await self._tools.call("web_fetch", {"url": task.url})
        if not isinstance(fetched, dict):
            raise ValueError("网页正文抓取失败")
        text = cast(dict[str, Any], fetched).get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("网页正文抓取失败或为空")
        cursor_raw = task.checkpoint.get("cursor", 0)
        cursor = cursor_raw if isinstance(cursor_raw, int) else 0
        while cursor < len(text):
            pending_raw = task.checkpoint.get("pending")
            if isinstance(pending_raw, dict):
                pending = cast(dict[str, Any], pending_raw)
            else:
                block = text[cursor:cursor + 6000]
                profile_raw = task.checkpoint.get("profile")
                profile = (
                    cast(dict[str, object], profile_raw)
                    if isinstance(profile_raw, dict)
                    else None
                )
                next_profile, items = await self._memory.digest_source_block(
                    block, task.url, task.id, profile=profile
                )
                source_topic = build_source_topic("web", task.url)
                for item in items:
                    item["source_topic"] = source_topic
                    item["source_name"] = task.url
                pending = {
                    "cursor": cursor + len(block),
                    "profile": next_profile,
                    "knowledge": items,
                }
                task.checkpoint["pending"] = pending
                task.updated_at = time.time()
                await self._store.save_task(task)
            raw_items = pending.get("knowledge", [])
            items = (
                [
                    cast(dict[str, str], item)
                    for item in cast(list[object], raw_items)
                    if isinstance(item, dict)
                ]
                if isinstance(raw_items, list)
                else []
            )
            if items:
                await self._memory.remember_knowledge(items, task.id)
            next_cursor = pending.get("cursor")
            cursor = next_cursor if isinstance(next_cursor, int) else cursor
            profile = pending.get("profile")
            task.checkpoint["cursor"] = cursor
            task.checkpoint["profile"] = profile if isinstance(profile, dict) else {}
            task.checkpoint["pending"] = None
            task.updated_at = time.time()
            await self._store.save_task(task)
        profile_raw = task.checkpoint.get("profile")
        profile = (
            cast(dict[str, object], profile_raw)
            if isinstance(profile_raw, dict)
            else {}
        )
        summary = profile.get("summary")
        return {
            "book": task.url,
            "note": summary if isinstance(summary, str) else "",
            "url": task.url,
            "completed": True,
        }

    async def _require_task(self, task_id: str) -> AssignedTask:
        task = await self._store.get_task(task_id)
        if task is None:
            raise ValueError(f"任务不存在：{task_id}")
        return task

    async def _set_task_status(
        self,
        task_id: str,
        status: AssignedTaskStatus,
        error: str | None = None,
    ) -> None:
        task = await self._store.set_task_status(
            task_id, status, time.time(), error
        )
        if task is not None:
            await self._publish_task_update(task)

    async def _publish_task_update(self, task: AssignedTask) -> None:
        try:
            await self._bus.publish(
                internal_event(
                    EventType.TASK_UPDATED,
                    {"task_id": task.id, "status": task.status.value},
                    task.id,
                )
            )
        except Exception:
            _logger.exception("委派任务状态广播失败 task_id=%s", task.id)

    async def _run_creation(self, activity: Activity) -> dict[str, Any]:
        checkpoint = _creation_checkpoint(activity)
        if not checkpoint.get("style"):
            checkpoint["style"] = _pick_creation_style()
            await self._save_creation_checkpoint(activity, checkpoint)
        if not bool(checkpoint.get("llm_done")):
            subject = _creation_subject(activity)
            recalled = await self._memory.search(subject) if subject else []
            references = [
                memory
                for memory in recalled
                if memory.kind is MemoryKind.KNOWLEDGE
                or (
                    memory.kind is MemoryKind.ACTIVITY
                    and ActivityType.CREATION.value in memory.topics
                )
            ][:3]
            if len(references) < 3:
                fresh_knowledge = await self._memory.list_memories(
                    kind=MemoryKind.KNOWLEDGE, limit=3
                )
                seen = {memory.id for memory in references}
                references.extend(
                    memory
                    for memory in fresh_knowledge
                    if memory.id not in seen
                )
                references = references[:3]
            obs = await self._get_observation()
            state = await self._get_state()
            context = _build_creation_context(
                activity, str(checkpoint["style"]), references, obs
            )
            system = _build_creation_system(self._canon, state)
            result = await self._run_llm_activity(
                activity,
                "creation",
                extra_context=context,
                context_label="创作参考",
                system=system,
            )
            checkpoint["title"] = str(result["title"])
            checkpoint["content"] = str(result["content"])
            checkpoint["llm_done"] = True
            await self._save_creation_checkpoint(activity, checkpoint)
        title = str(checkpoint["title"])
        content = str(checkpoint["content"])
        write_path = _creation_output_path(title, activity.id)
        if not bool(checkpoint.get("file_written")):
            written_raw = await self._tools.call(
                "file_io",
                {"action": "write", "path": write_path, "content": content},
            )
            if not isinstance(written_raw, dict):
                raise ValueError("file_io.write 应返回对象")
            written = cast(dict[str, Any], written_raw)
            path_value = written.get("path")
            if not isinstance(path_value, str) or not path_value:
                raise ValueError("file_io.write 返回缺少非空 path")
            checkpoint["path"] = path_value
            checkpoint["file_written"] = True
            await self._save_creation_checkpoint(activity, checkpoint)
        path_value = str(checkpoint["path"])
        return {
            "title": title,
            "content": content,
            "path": path_value,
            "tools": [
                {
                    "name": "file_io",
                    "args": {
                        "action": "write",
                        "path": write_path,
                    },
                    "ok": True,
                }
            ],
        }

    async def _save_creation_checkpoint(
        self, activity: Activity, checkpoint: dict[str, Any]
    ) -> None:
        activity.progress["creation"] = checkpoint
        await self._store.update(activity)

    async def _run_reading_source(
        self, activity: Activity, source: str
    ) -> dict[str, Any]:
        """兼容旧内部调用；读书实现位于 `ReadingActivityRunner`。"""
        return await self._reading_runner.run(activity, source)

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
