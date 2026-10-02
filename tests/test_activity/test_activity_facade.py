# pyright: reportPrivateUsage=false
import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest

from nyx import db
from nyx.activity import lifecycle as _activity_lifecycle
from nyx.activity.facade import (
    _CREATION_STYLES,
    ActivityFacade,
    _build_creation_context,
    _build_creation_system,
    _day_start,
    _goal_met,
    _parse_activity_result,
    _path_hash_suffix,
    _pick_creation_style,
    _sanitize_filename,
    _schedule_block_id,
)
from nyx.activity.lifecycle import ActivityLifecycle
from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig, DesireConfig, ExplorationConfig
from nyx.db import Database
from nyx.desire.facade import DesireFacade
from nyx.desire.store import DesireStore
from nyx.enums import (
    ActivityStatus,
    ActivityType,
    AssignedTaskStatus,
    AssignedTaskType,
    DesireStatus,
    DesireType,
    EmotionCategory,
    EnergyState,
    EventType,
    GoalAction,
    MemoryKind,
    MemoryType,
    Source,
)
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.facade import MemoryFacade
from nyx.tools.registry import ToolRegistry
from nyx.types import (
    Activity,
    AssignedTask,
    Book,
    CurrentState,
    DesireState,
    DesireValue,
    Event,
    Goal,
    LLMOutput,
    Memory,
    Personality,
    ReadingProgress,
    ShortTermDesire,
    Values,
)

_PERSONALITY: Personality = {
    "openness": 5.0,
    "conscientiousness": 5.0,
    "extraversion": 5.0,
    "agreeableness": 5.0,
    "neuroticism": 5.0,
}

_VALUES: Values = {
    "attitude_to_human": 5.0,
    "ai_identity_acceptance": 5.0,
    "altruism": 5.0,
    "optimism": 5.0,
}

_READING_JSON = json.dumps({"book": "骑士团历史", "note": "读到了第三章"})
_CREATION_JSON = json.dumps({"title": "小狐狸的日记", "content": "今天也努力了"})
_PLAN_JSON = json.dumps({"focus": "骑士团", "done": False})
_NOTE_JSON = json.dumps({"note": "完整读书笔记"})
_EXPLORATION_FINALIZE_JSON = json.dumps({
    "summary": "弄懂了量子退相干的机制",
    "core_discovery": "退相干来自系统与环境纠缠",
    "knowledge": [{"topic": "退相干", "content": "环境纠缠会抹去相干性"}],
    "strong_new_topics": ["量子纠错"],
    "casual_new_topics": [],
})


def _mk_state(energy: float) -> CurrentState:
    return CurrentState(
        valence=0.0,
        arousal=0.0,
        emotion=EmotionCategory.NEUTRAL,
        personality=_PERSONALITY,
        values=_VALUES,
        aesthetic={
            "ornate": 7.0, "lyrical": 7.0, "classical": 6.0, "somber": 6.0,
        },
        energy=energy,
        energy_state=EnergyState.OKAY,
        current_activity=None,
        active_desires=[],
    )


def _desire(
    id: str,
    type_: DesireType,
    description: str = "读骑士小说",
    goal: Goal | None = None,
    parent_long_term_id: str | None = None,
) -> ShortTermDesire:
    return ShortTermDesire(
        id=id,
        created_at=1000.0,
        type=type_,
        strength=0.9,
        description=description,
        goal=goal,
        status=DesireStatus.PENDING,
        parent_long_term_id=parent_long_term_id,
    )


def _activity(
    id: str,
    type_: ActivityType = ActivityType.READING,
    status: ActivityStatus = ActivityStatus.PENDING,
    started_at: float = 1000.0,
    schedule_block_id: str = "09:00",
    progress: dict[str, Any] | None = None,
) -> Activity:
    return Activity(
        id=id,
        type=type_,
        schedule_block_id=schedule_block_id,
        status=status,
        progress=progress
        if progress is not None
        else {"desire_id": None, "goal": None, "correlation_id": None},
        started_at=started_at,
    )


def _assigned_task(
    id: str, status: AssignedTaskStatus = AssignedTaskStatus.RUNNING
) -> AssignedTask:
    return AssignedTask(
        id=id,
        type=AssignedTaskType.WEB,
        status=status,
        url=f"https://example.com/{id}",
        book_id=None,
        target_paragraph=None,
        checkpoint={},
        error=None,
        created_at=1000.0,
        updated_at=1000.0,
    )


class _FakeLlm:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.correlation_ids: list[str] = []

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
    ) -> LLMOutput:
        self.calls.append(output_type)
        self.correlation_ids.append(correlation_id)
        content = {
            "reading": _READING_JSON,
            "creation": _CREATION_JSON,
            "exploration_plan": _PLAN_JSON,
            "note": _NOTE_JSON,
        }.get(output_type, "{}")
        return LLMOutput(
            module=module,
            type=output_type,
            model="fake",
            content=content,
            correlation_id=correlation_id,
        )


class _ExplorationLlm(_FakeLlm):
    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
    ) -> LLMOutput:
        if output_type == "exploration_finalize":
            self.calls.append(output_type)
            self.correlation_ids.append(correlation_id)
            return LLMOutput(
                module=module,
                type=output_type,
                model="fake",
                content=_EXPLORATION_FINALIZE_JSON,
                correlation_id=correlation_id,
            )
        return await super().complete(
            messages,
            module=module,
            output_type=output_type,
            correlation_id=correlation_id,
            json_mode=json_mode,
        )


class _CapturingLlm(_FakeLlm):
    """记录每次 complete 的 user/system content（用于断言创作上下文与人格注入）。"""

    def __init__(self) -> None:
        super().__init__()
        self.user_contents: list[str] = []
        self.system_contents: list[str] = []

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
    ) -> LLMOutput:
        self.user_contents.append(str(messages[-1]["content"]))
        self.system_contents.append(str(messages[0]["content"]))
        return await super().complete(
            messages,
            module=module,
            output_type=output_type,
            correlation_id=correlation_id,
            json_mode=json_mode,
        )


class _RaisingLlm(_FakeLlm):
    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
    ) -> LLMOutput:
        raise RuntimeError("boom")


class _BlockingLlm(_FakeLlm):
    """complete 挂起在永不 set 的 Event 上，模拟可取消的执行中 LLM 调用。"""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
    ) -> LLMOutput:
        await self.release.wait()
        return await super().complete(
            messages,
            module=module,
            output_type=output_type,
            correlation_id=correlation_id,
            json_mode=json_mode,
        )


class _FakeEvaluator:
    def __init__(self) -> None:
        self.evaluated: list[LLMOutput] = []

    async def evaluate(self, output: LLMOutput) -> None:
        self.evaluated.append(output)


class _FakeDesire:
    def __init__(
        self,
        pending: list[ShortTermDesire] | None = None,
        values: list[DesireValue] | None = None,
    ) -> None:
        self._pending = pending if pending is not None else []
        self._values = values if values is not None else []
        self.mark_active_calls: list[str] = []
        self.mark_suppressed_calls: list[str] = []
        self.release_active_calls: list[str] = []
        self.appended_subtopics: list[tuple[str, list[str]]] = []

    async def get_pending(self) -> list[ShortTermDesire]:
        return self._pending

    async def get_all(self) -> DesireState:
        return DesireState(
            values=self._values, short_term=self._pending, long_term=[]
        )

    async def mark_active(self, desire_id: str) -> None:
        self.mark_active_calls.append(desire_id)

    async def claim_for_activity_in_transaction(self, desire_id: str) -> bool:
        self.mark_active_calls.append(desire_id)
        return True

    async def resume_for_activity_in_transaction(self, desire_id: str) -> bool:
        self.mark_active_calls.append(desire_id)
        return True

    async def mark_suppressed(self, desire_id: str) -> None:
        self.mark_suppressed_calls.append(desire_id)

    async def release_active(self, desire_id: str) -> None:
        self.release_active_calls.append(desire_id)

    async def add_long_term_subtopics(
        self, desire_id: str, subtopics: list[str]
    ) -> bool:
        self.appended_subtopics.append((desire_id, subtopics))
        return True


class _FakeMemory:
    def __init__(self) -> None:
        self.remembered: list[list[dict[str, str]]] = []
        self.remembered_correlation_ids: list[str] = []
        self.digested_blocks: list[tuple[str, str, str]] = []
        self.knowledge: list[Memory] = []
        self.search_queries: list[str] = []

    async def list_memories(
        self,
        kind: MemoryKind | None = None,
        type: MemoryType | None = None,
        limit: int | None = None,
    ) -> list[Memory]:
        return self.knowledge

    async def search(self, query: str) -> list[Memory]:
        self.search_queries.append(query)
        return self.knowledge

    async def remember_knowledge(
        self, items: list[dict[str, str]], correlation_id: str
    ) -> None:
        self.remembered.append(items)
        self.remembered_correlation_ids.append(correlation_id)

    async def digest_source_block(
        self,
        text: str,
        source_name: str,
        correlation_id: str,
        *,
        author: str = "",
        profile: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], list[dict[str, str]]]:
        self.digested_blocks.append((text, source_name, correlation_id))
        return (
            {
                "summary": text[:20] or source_name,
                "themes": [],
                "content_category": "unknown",
            },
            [{"topic": source_name, "content": text[:80]}] if text else [],
        )


class _FakeTools:
    def __init__(self, write_root: Path | None = None) -> None:
        self.write_root = write_root
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if name in ("local_search", "web_search"):
            return ["一条检索结果"]
        if name == "file_io":
            path = str(args["path"])
            content = str(args.get("content") or "")
            if self.write_root is None:
                return {"path": f"workspace/{path}", "written": len(content)}
            target = self.write_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"path": str(target), "written": len(content)}
        return "文件内容"


class _WebTaskTools(_FakeTools):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        if name == "web_fetch":
            self.calls.append((name, args))
            return {"text": self.text, "url": args["url"]}
        return await super().call(name, args)


class _BlockingWebTaskTools(_FakeTools):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        if name == "web_fetch":
            self.calls.append((name, args))
            self.started.set()
            await asyncio.Event().wait()
        return await super().call(name, args)


async def _no_observation() -> dict[str, str]:
    return {"presence": "away", "window_title": ""}


async def _new_facade(
    pending: list[ShortTermDesire] | None = None,
    values: list[DesireValue] | None = None,
    energy: float = 80.0,
    llm: _FakeLlm | None = None,
    evaluator: _FakeEvaluator | None = None,
    get_observation: Callable[[], Awaitable[dict[str, str]]] | None = None,
    desire: _FakeDesire | None = None,
    memory: _FakeMemory | None = None,
    tools: _FakeTools | None = None,
    canon: str = "测试人格",
    exploration_config: ExplorationConfig | None = None,
    list_reader_books: Callable[[], Awaitable[list[Book]]] | None = None,
    read_reader_book: (
        Callable[[str, int | None, str], Awaitable[dict[str, Any]]] | None
    ) = None,
    get_reader_book: Callable[[str], Awaitable[Book]] | None = None,
    get_reader_progress: (
        Callable[[str], Awaitable[ReadingProgress]] | None
    ) = None,
) -> tuple[ActivityFacade, ActivityStore, EventBus, Database]:
    database = await db.connect(":memory:")
    store = ActivityStore(database)
    bus = EventBus(database)

    async def get_state() -> CurrentState:
        return _mk_state(energy)

    facade = ActivityFacade(
        store,
        bus,
        cast(LlmClient, llm if llm is not None else _FakeLlm()),
        cast(Evaluator, evaluator if evaluator is not None else _FakeEvaluator()),
        cast(ToolRegistry, tools if tools is not None else _FakeTools()),
        cast(
            DesireFacade,
            desire if desire is not None else _FakeDesire(pending, values),
        ),
        cast(MemoryFacade, memory if memory is not None else _FakeMemory()),
        get_state,
        get_observation if get_observation is not None else _no_observation,
        ActivityConfig(),
        exploration_config or ExplorationConfig(),
        canon,
        list_reader_books,
        read_reader_book,
        get_reader_book,
        get_reader_progress,
    )
    return facade, store, bus, database


def _subscribe_activity(bus: EventBus) -> list[Event]:
    events: list[Event] = []

    async def record(event: Event) -> None:
        events.append(event)

    for t in (
        EventType.ACTIVITY_START,
        EventType.ACTIVITY_END,
        EventType.ACTIVITY_INTERRUPTED,
    ):
        bus.subscribe(t, record)
    return events


@contextlib.asynccontextmanager
async def _running(bus: EventBus) -> AsyncGenerator[None]:
    task = asyncio.create_task(bus.run())
    try:
        yield
        await asyncio.wait_for(bus._queue.join(), timeout=1.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _await_task(facade: ActivityFacade) -> None:
    task = facade._task
    assert task is not None
    await task


async def test_assigned_task_does_not_preempt_running_activity() -> None:
    facade, store, bus, database = await _new_facade(
        tools=_WebTaskTools("正文"),
        exploration_config=ExplorationConfig(web_enabled=True),
    )
    try:
        await store.insert(
            _activity(
                "busy",
                type_=ActivityType.OBSERVE_USER,
                status=ActivityStatus.RUNNING,
            )
        )
        async with _running(bus):
            assigned = await facade.assign_web_task("https://example.com/article")
        current = await store.get_current()
        assert current is not None and current.id == "busy"
        assert assigned.status is AssignedTaskStatus.PENDING
        assert facade._task is None
    finally:
        await database.conn.close()


async def test_low_energy_rests_before_assigned_task() -> None:
    facade, store, bus, database = await _new_facade(
        energy=0.0,
        tools=_WebTaskTools("正文"),
        exploration_config=ExplorationConfig(web_enabled=True),
    )
    try:
        async with _running(bus):
            assigned = await facade.assign_web_task("https://example.com/article")
            await _await_task(facade)
        saved = await store.get_task(assigned.id)
        activities = await store.list_schedule(0.0)
        assert saved is not None and saved.status is AssignedTaskStatus.PENDING
        assert len(activities) == 1
        assert activities[0].type is ActivityType.REST
    finally:
        await database.conn.close()


async def test_assigned_task_precedes_pending_desire() -> None:
    tools = _WebTaskTools("正文")
    facade, store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.CREATION)],
        tools=tools,
        exploration_config=ExplorationConfig(web_enabled=True),
    )
    try:
        async with _running(bus):
            assigned = await facade.assign_web_task("https://example.com/article")
            await _await_task(facade)
        saved = await store.get_task(assigned.id)
        activities = await store.list_schedule(0.0)
        assert saved is not None and saved.status is AssignedTaskStatus.COMPLETED
        assert activities[0].type is ActivityType.READING
        assert activities[0].progress["task_id"] == assigned.id
    finally:
        await database.conn.close()


async def test_web_task_sediments_every_6000_character_block() -> None:
    text = "甲" * 13001
    tools = _WebTaskTools(text)
    memory = _FakeMemory()
    facade, store, bus, database = await _new_facade(
        tools=tools,
        memory=memory,
        exploration_config=ExplorationConfig(web_enabled=True),
    )
    try:
        async with _running(bus):
            assigned = await facade.assign_web_task("https://example.com/article")
            await _await_task(facade)
        saved = await store.get_task(assigned.id)
        assert [len(item[0]) for item in memory.digested_blocks] == [6000, 6000, 1001]
        assert memory.remembered_correlation_ids == [assigned.id] * 3
        assert saved is not None and saved.checkpoint["cursor"] == len(text)
        assert saved.status is AssignedTaskStatus.COMPLETED
    finally:
        await database.conn.close()


async def test_empty_web_task_fails_without_memory() -> None:
    memory = _FakeMemory()
    facade, store, bus, database = await _new_facade(
        tools=_WebTaskTools("  "),
        memory=memory,
        exploration_config=ExplorationConfig(web_enabled=True),
    )
    try:
        async with _running(bus):
            assigned = await facade.assign_web_task("https://example.com/empty")
            with pytest.raises(ValueError, match="正文"):
                await _await_task(facade)
        saved = await store.get_task(assigned.id)
        assert saved is not None and saved.status is AssignedTaskStatus.FAILED
        assert memory.digested_blocks == []
        assert memory.remembered == []
    finally:
        await database.conn.close()


async def test_interrupted_assigned_task_returns_to_queue() -> None:
    tools = _BlockingWebTaskTools()
    facade, store, bus, database = await _new_facade(
        tools=tools,
        exploration_config=ExplorationConfig(web_enabled=True),
    )
    try:
        async with _running(bus):
            assigned = await facade.assign_web_task("https://example.com/slow")
            await asyncio.wait_for(tools.started.wait(), timeout=1.0)
            current = await store.get_current()
            assert current is not None
            await facade.interrupt(current.id, EventType.USER_MESSAGE)
        saved = await store.get_task(assigned.id)
        interrupted = await store.get(current.id)
        assert saved is not None and saved.status is AssignedTaskStatus.PENDING
        assert interrupted is not None
        assert interrupted.status is ActivityStatus.PAUSED
    finally:
        await database.conn.close()


async def test_assignment_survives_post_commit_scheduling_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, store, bus, database = await _new_facade(
        tools=_WebTaskTools("正文"),
        exploration_config=ExplorationConfig(web_enabled=True),
    )

    async def fail_start() -> None:
        raise RuntimeError("scheduler unavailable")

    monkeypatch.setattr(facade, "_maybe_start_activity", fail_start)
    try:
        async with _running(bus):
            assigned = await facade.assign_web_task("https://example.com/queued")
        saved = await store.get_task(assigned.id)
        assert assigned.status is AssignedTaskStatus.PENDING
        assert saved is not None and saved.status is AssignedTaskStatus.PENDING
    finally:
        await database.conn.close()


async def test_quiesce_cancels_runner_and_returns_task_to_queue() -> None:
    tools = _BlockingWebTaskTools()
    facade, store, bus, database = await _new_facade(
        tools=tools,
        exploration_config=ExplorationConfig(web_enabled=True),
    )
    try:
        async with _running(bus):
            assigned = await facade.assign_web_task("https://example.com/slow")
            await asyncio.wait_for(tools.started.wait(), timeout=1.0)
            await facade.quiesce()
            await facade._maybe_start_activity()
        activities = await store.list_schedule(0.0)
        saved = await store.get_task(assigned.id)
        assert len(activities) == 1
        assert activities[0].status is ActivityStatus.PAUSED
        assert saved is not None and saved.status is AssignedTaskStatus.PENDING
        assert facade._task is not None and facade._task.cancelled()
    finally:
        await database.conn.close()


async def test_quiesce_recovers_even_when_cancel_cleanup_raises() -> None:
    facade, store, _bus, database = await _new_facade()

    async def fail_on_cancel() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            raise RuntimeError("cleanup failed") from error

    try:
        await store.insert(
            _activity(
                "a1",
                type_=ActivityType.CREATION,
                status=ActivityStatus.RUNNING,
            )
        )
        facade._task = asyncio.create_task(fail_on_cancel())
        await asyncio.sleep(0)

        await facade.quiesce()

        activity = await store.get("a1")
        assert activity is not None
        assert activity.status is ActivityStatus.PAUSED
    finally:
        await database.conn.close()


async def test_quiesce_timeout_leaves_live_runner_durably_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, store, _bus, database = await _new_facade()
    release = asyncio.Event()

    async def ignore_first_cancel() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    try:
        await store.create_task(_assigned_task("t1"))
        await store.insert(
            _activity(
                "a1",
                status=ActivityStatus.RUNNING,
                progress={"task_id": "t1", "correlation_id": "t1"},
            )
        )
        facade._task = asyncio.create_task(ignore_first_cancel())
        await asyncio.sleep(0)
        monkeypatch.setattr(
            _activity_lifecycle,
            "_RUNNER_CANCEL_TIMEOUT_SECONDS",
            0.01,
            raising=False,
        )

        await asyncio.wait_for(facade.quiesce(), timeout=0.2)

        activity = await store.get("a1")
        task = await store.get_task("t1")
        assert activity is not None and activity.status is ActivityStatus.RUNNING
        assert task is not None and task.status is AssignedTaskStatus.RUNNING
    finally:
        release.set()
        if facade._task is not None:
            await asyncio.gather(facade._task, return_exceptions=True)
        await database.conn.close()


async def test_recovery_finishes_task_linked_to_completed_activity() -> None:
    facade, store, _bus, database = await _new_facade()
    try:
        await store.create_task(_assigned_task("t1"))
        await store.insert(
            _activity(
                "a1",
                status=ActivityStatus.COMPLETED,
                progress={"task_id": "t1", "correlation_id": "t1"},
            )
        )

        recovered = await facade.recover_stale_running()

        task = await store.get_task("t1")
        assert recovered == []
        assert task is not None and task.status is AssignedTaskStatus.COMPLETED
    finally:
        await database.conn.close()


async def test_assigned_book_task_uses_reader_and_completes() -> None:
    book = Book("b1", "诺斯艾兰", "作者", "book.epub", "h", 20, 1.0, 1.0)
    calls: list[tuple[str, int | None, str]] = []

    async def get_reader_book(book_id: str) -> Book:
        assert book_id == book.id
        return book

    async def get_reader_progress(book_id: str) -> ReadingProgress:
        return ReadingProgress(book_id, 1, 1, 50, 0, 0.0)

    async def read_reader_book(
        book_id: str, target: int | None, correlation_id: str
    ) -> dict[str, Any]:
        calls.append((book_id, target, correlation_id))
        return {"book": book.title, "target_paragraph": target, "completed": True}

    facade, store, bus, database = await _new_facade(
        read_reader_book=read_reader_book,
        get_reader_book=get_reader_book,
        get_reader_progress=get_reader_progress,
    )
    try:
        await database.conn.execute(
            "INSERT INTO books "
            "(id, title, author, filename, content_hash, total_paragraphs, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                book.id,
                book.title,
                book.author,
                book.filename,
                book.content_hash,
                book.total_paragraphs,
                book.created_at,
                book.updated_at,
            ),
        )
        await database.conn.commit()
        async with _running(bus):
            assigned = await facade.assign_book_task(book.id, 12)
            await _await_task(facade)
        saved = await store.get_task(assigned.id)
        assert calls == [(book.id, 12, assigned.id)]
        assert saved is not None and saved.status is AssignedTaskStatus.COMPLETED
    finally:
        await database.conn.close()


async def test_already_read_book_task_completes_without_activity() -> None:
    book = Book("b1", "诺斯艾兰", "作者", "book.epub", "h", 20, 1.0, 1.0)

    async def get_reader_book(book_id: str) -> Book:
        return book

    async def get_reader_progress(book_id: str) -> ReadingProgress:
        return ReadingProgress(book_id, 9, 9, 50, 1, 1.0, revision=2)

    facade, store, bus, database = await _new_facade(
        get_reader_book=get_reader_book,
        get_reader_progress=get_reader_progress,
    )
    try:
        await database.conn.execute(
            "INSERT INTO books "
            "(id, title, author, filename, content_hash, total_paragraphs, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                book.id,
                book.title,
                book.author,
                book.filename,
                book.content_hash,
                book.total_paragraphs,
                book.created_at,
                book.updated_at,
            ),
        )
        await database.conn.commit()
        async with _running(bus):
            assigned = await facade.assign_book_task(book.id, 8)
        assert assigned.status is AssignedTaskStatus.COMPLETED
        assert await store.list_schedule(0.0) == []
        assert facade._task is None
    finally:
        await database.conn.close()


# ---- 纯函数 ----


def test_day_start() -> None:
    local_afternoon = datetime(2026, 9, 23, 15, 30).timestamp()
    local_midnight = datetime(2026, 9, 23).timestamp()
    assert _day_start(local_afternoon) == local_midnight


def test_schedule_block_id_aligns_to_grid() -> None:
    """日程块网格对齐：同网格块内多个 now 返回同标签、跨块/跨小时边界正确进位。"""
    base = datetime(2026, 9, 23, 14, 0).timestamp()
    assert _schedule_block_id(base, 60) == "14:00"
    assert _schedule_block_id(base + 59 * 60, 60) == "14:00"   # 同块内不漂移
    assert _schedule_block_id(base + 60 * 60, 60) == "15:00"   # 跨小时进位
    assert _schedule_block_id(base + 25 * 60, 30) == "14:00"   # 半小时网格 14:00~14:29
    assert _schedule_block_id(base + 30 * 60, 30) == "14:30"


def test_goal_met() -> None:
    assert _goal_met(None, {}) is True
    assert _goal_met({"action": "read"}, {}) is False
    assert _goal_met({"action": "read"}, {"book": "x"}) is False   # 读完整本才算
    assert _goal_met({"action": "read"}, {"completed": True}) is True
    assert _goal_met({"action": "write"}, {"title": "t", "content": "c"}) is True
    assert _goal_met({"action": "write"}, {"title": "t"}) is False
    assert _goal_met({"action": "observe"}, {"presence": "online"}) is True
    assert _goal_met({"action": "observe"}, {}) is False
    assert _goal_met(
        {"action": "read"}, {"type": "free_exploration", "outcome": "won"}
    ) is True
    assert _goal_met(
        {"action": "read"}, {"type": "free_exploration", "outcome": "retreated"}
    ) is False


def test_sanitize_filename() -> None:
    assert _sanitize_filename("小狐狸的日记") == "小狐狸的日记"
    assert _sanitize_filename("a/b:c") == "abc"      # 路径分隔符/非法字符剔除
    assert _sanitize_filename("") == "untitled"       # 空回退
    assert _sanitize_filename("///") == "untitled"


def test_parse_activity_result_valid() -> None:
    assert _parse_activity_result(
        json.dumps({"book": "b", "note": "n"}), "reading"
    ) == {"book": "b", "note": "n"}
    assert _parse_activity_result(
        json.dumps({"title": "t", "content": "c"}), "creation"
    ) == {"title": "t", "content": "c"}


def test_parse_activity_result_missing_key_raises() -> None:
    with pytest.raises(ValueError):
        _parse_activity_result(json.dumps({"book": "b"}), "reading")


def test_parse_activity_result_non_dict_raises() -> None:
    with pytest.raises(ValueError):
        _parse_activity_result("[1, 2, 3]", "reading")


# ---- select_activity ----


async def test_select_activity_empty() -> None:
    facade, _store, _bus, database = await _new_facade()
    try:
        assert facade.select_activity([], _mk_state(80.0)) is None
    finally:
        await database.conn.close()


async def test_select_activity_exploration() -> None:
    facade, _store, _bus, database = await _new_facade()
    try:
        d = _desire(
            "d1", DesireType.EXPLORATION,
            goal=Goal(GoalAction.READ, 3, "骑士团"),
            parent_long_term_id="lt1",
        )
        act = facade.select_activity([d], _mk_state(80.0))
        assert act is not None
        assert act.type is ActivityType.READING
        assert act.progress["desire_id"] == "d1"
        assert act.progress["parent_long_term_id"] == "lt1"
        assert act.progress["description"] == d.description
        assert act.progress["goal"] == {
            "action": "read", "count": 3, "topic": "骑士团",
        }
    finally:
        await database.conn.close()


async def test_select_activity_interaction_returns_none() -> None:
    facade, _store, _bus, database = await _new_facade()
    try:
        d = _desire("d1", DesireType.INTERACTION)
        assert facade.select_activity([d], _mk_state(80.0)) is None
    finally:
        await database.conn.close()


async def test_select_activity_rest_desire() -> None:
    facade, _store, _bus, database = await _new_facade()
    try:
        d = _desire("d1", DesireType.REST)
        act = facade.select_activity([d], _mk_state(80.0))
        assert act is not None
        assert act.type is ActivityType.REST
        assert act.progress["desire_id"] == "d1"
    finally:
        await database.conn.close()


async def test_select_activity_low_energy_rest() -> None:
    facade, _store, _bus, database = await _new_facade()
    try:
        d = _desire("d1", DesireType.EXPLORATION)
        act = facade.select_activity([d], _mk_state(30.0))
        assert act is not None
        assert act.type is ActivityType.REST
        assert act.progress["desire_id"] is None
    finally:
        await database.conn.close()


# ---- 生命周期 ----


async def test_maybe_start_skips_when_running() -> None:
    facade, store, _bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.EXPLORATION)], energy=80.0
    )
    try:
        await store.insert(_activity("run", status=ActivityStatus.RUNNING))
        await facade._maybe_start_activity()
        acts = await store.list_schedule(0.0)
        assert [a.id for a in acts] == ["run"]
    finally:
        await database.conn.close()


async def test_maybe_start_skips_when_task_in_flight() -> None:
    facade, store, _bus, database = await _new_facade()
    try:
        await facade._maybe_start_activity()
        assert facade._task is not None and not facade._task.done()
        await facade._maybe_start_activity()
        acts = await store.list_schedule(0.0)
        assert len(acts) == 1
        await facade._task
    finally:
        await database.conn.close()


async def test_default_idle_reflection_when_tired() -> None:
    facade, store, bus, database = await _new_facade(energy=30.0)
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert len(acts) == 1
        assert acts[0].type is ActivityType.IDLE_REFLECTION
        assert acts[0].progress["desire_id"] is None
    finally:
        await database.conn.close()


async def test_default_observe_user_when_energetic() -> None:
    facade, store, bus, database = await _new_facade(energy=80.0)
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert len(acts) == 1
        assert acts[0].type is ActivityType.OBSERVE_USER
        assert acts[0].progress["desire_id"] is None
    finally:
        await database.conn.close()


async def test_maybe_start_creation_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade, _store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.CREATION)],
        energy=80.0,
        llm=llm,
        evaluator=evaluator,
    )
    try:
        events = _subscribe_activity(bus)
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        starts = [e for e in events if e.type is EventType.ACTIVITY_START]
        ends = [e for e in events if e.type is EventType.ACTIVITY_END]
        assert len(starts) == 1
        assert starts[0].source is Source.INTERNAL
        assert ends[0].content["desire_id"] == "d1"
        assert ends[0].content["energy_delta"] == -25
        assert len(evaluator.evaluated) == 1
    finally:
        await database.conn.close()


async def test_creation_result_has_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """创作落盘：写入 pytest 临时工作区并确认内容，测试后自动清理。"""
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    tools = _FakeTools(tmp_path)
    facade, _store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.CREATION)], energy=80.0,
        llm=_FakeLlm(), evaluator=_FakeEvaluator(),
        tools=tools,
    )
    try:
        events = _subscribe_activity(bus)
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        ends = [e for e in events if e.type is EventType.ACTIVITY_END]
        activity_id = str(ends[0].content["activity_id"])
        filename = f"小狐狸的日记-{_path_hash_suffix(activity_id)}.md"
        output = tmp_path / "creations" / filename
        assert ends[0].content["result"]["path"] == str(output)
        assert tools.calls == [
            (
                "file_io",
                {
                    "action": "write",
                    "path": f"creations/{filename}",
                    "content": "今天也努力了",
                },
            )
        ]
        assert output.read_text(encoding="utf-8") == "今天也努力了"
    finally:
        await database.conn.close()


async def test_creation_resume_uses_checkpoint_without_rewriting(
) -> None:
    llm = _FakeLlm()
    tools = _FakeTools()
    facade, _store, _bus, database = await _new_facade(llm=llm, tools=tools)
    activity = _activity(
        "a1",
        type_=ActivityType.CREATION,
        status=ActivityStatus.RUNNING,
        progress={
            "desire_id": "d1",
            "goal": {"action": "write"},
            "correlation_id": "d1",
            "description": "写日记",
            "creation": {
                "style": "diary",
                "llm_done": True,
                "title": "旧标题",
                "content": "旧内容",
                "file_written": True,
                "path": "workspace/creations/旧标题.md",
            },
        },
    )
    try:
        result = await facade._run_activity(activity)
        assert result["title"] == "旧标题"
        assert result["content"] == "旧内容"
        assert result["path"] == "workspace/creations/旧标题.md"
        assert llm.calls == []
        assert tools.calls == []
    finally:
        await database.conn.close()


async def test_idle_reflection_uses_durable_reflection_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """发呆活动本身无 LLM 副作用，完成事务负责受理 REFLECTION。"""
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)

    facade, store, bus, database = await _new_facade(energy=30.0)
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert acts[0].progress["result"] == {}
        events = await bus.list_events(event_type=EventType.REFLECTION)
        assert len(events) == 1
        assert events[0].content["reason"] == "idle_activity"
        assert events[0].content["activity_id"] == acts[0].id
    finally:
        await database.conn.close()


async def test_observe_user_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """观察用户：result 带 presence/window_title + 确定性 summary。"""
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)

    async def fake_observation() -> dict[str, str]:
        return {"presence": "online", "window_title": "编辑器"}

    facade, store, bus, database = await _new_facade(
        energy=80.0, get_observation=fake_observation
    )
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert acts[0].progress["result"] == {
            "presence": "online",
            "window_title": "编辑器",
            "screen_summary": "",
            "summary": "用户（online）正在浏览 编辑器",
        }
    finally:
        await database.conn.close()


async def test_observe_user_result_with_screen_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """观察用户：screen_summary 非空时折入 summary，且 result 带 screen_summary。"""
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)

    async def fake_observation() -> dict[str, str]:
        return {
            "presence": "busy",
            "window_title": "编辑器",
            "screen_summary": "写代码",
        }

    facade, store, bus, database = await _new_facade(
        energy=80.0, get_observation=fake_observation
    )
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert acts[0].progress["result"]["screen_summary"] == "写代码"
        assert (
            acts[0].progress["result"]["summary"]
            == "用户（busy）正在浏览 编辑器，屏幕：写代码"
        )
    finally:
        await database.conn.close()


async def test_observe_user_result_no_window_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """观察用户：window_title 空则 summary 省略「正在浏览」。"""
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)

    async def fake_observation() -> dict[str, str]:
        return {"presence": "away", "window_title": ""}

    facade, store, bus, database = await _new_facade(
        energy=80.0, get_observation=fake_observation
    )
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert acts[0].progress["result"]["summary"] == "用户（away）"
    finally:
        await database.conn.close()


async def test_execute_failure_marks_incomplete() -> None:
    facade, store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.CREATION)], energy=80.0, llm=_RaisingLlm()
    )
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            with pytest.raises(RuntimeError):
                await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert len(acts) == 1
        assert acts[0].status is ActivityStatus.INCOMPLETE
        assert acts[0].ended_at is not None
    finally:
        await database.conn.close()


async def test_execute_marks_active_desire() -> None:
    """活动真正开始消费：_execute 置 RUNNING 后标 desire ACTIVE 恰一次。"""
    facade, _store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.CREATION)], energy=80.0, llm=_FakeLlm()
    )
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        desire = cast(_FakeDesire, facade._desire)
        assert desire.mark_active_calls == ["d1"]
        assert desire.mark_suppressed_calls == []
    finally:
        await database.conn.close()


async def test_execute_failure_marks_suppressed() -> None:
    """活动异常：标 ACTIVE 后非满足退出，desire 释放到 SUPPRESSED。"""
    facade, store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.CREATION)], energy=80.0, llm=_RaisingLlm()
    )
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            with pytest.raises(RuntimeError):
                await _await_task(facade)
        desire = cast(_FakeDesire, facade._desire)
        assert desire.mark_active_calls == ["d1"]
        assert desire.mark_suppressed_calls == ["d1"]
        acts = await store.list_schedule(0.0)
        assert acts[0].status is ActivityStatus.INCOMPLETE
    finally:
        await database.conn.close()


async def test_execute_free_exploration_failure_marks_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """探索启动失败并入 fail-fast：启动异常也标 INCOMPLETE + 释放欲望。"""
    facade, store, bus, database = await _new_facade()
    try:
        async def boom(activity: Activity) -> dict[str, Any]:
            raise RuntimeError("boom")

        monkeypatch.setattr(facade._exploration, "run", boom)
        a = _activity(
            "a1",
            type_=ActivityType.FREE_EXPLORATION,
            progress={"desire_id": "d1", "goal": None, "correlation_id": "d1"},
        )
        await store.insert(a)
        async with _running(bus):
            with pytest.raises(RuntimeError):
                await facade._execute(a)
        got = await store.get("a1")
        assert got is not None
        assert got.status is ActivityStatus.INCOMPLETE
        assert got.ended_at is not None
        desire = cast(_FakeDesire, facade._desire)
        assert desire.mark_suppressed_calls == ["d1"]
    finally:
        await database.conn.close()


async def test_exploration_completion_commit_failure_recovers_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, store, bus, database = await _new_facade()

    async def finish(activity: Activity) -> dict[str, Any]:
        return {"type": "free_exploration", "outcome": "won"}

    async def fail_append(event: Event) -> tuple[str, ...]:
        if event.type is EventType.ACTIVITY_END:
            raise RuntimeError("event append failed")
        return ()

    try:
        monkeypatch.setattr(facade._exploration, "run", finish)
        monkeypatch.setattr(bus, "append_in_transaction", fail_append)
        activity = _activity(
            "a1",
            type_=ActivityType.FREE_EXPLORATION,
            status=ActivityStatus.RUNNING,
        )
        await store.insert(activity)

        with pytest.raises(RuntimeError, match="event append failed"):
            await facade._execute(activity)

        got = await store.get("a1")
        assert got is not None
        assert got.status is ActivityStatus.PAUSED
        assert got.ended_at is not None
        assert all(
            event.type is not EventType.ACTIVITY_END
            for event in await bus.list_events()
        )
    finally:
        await database.conn.close()


async def test_execute_no_desire_no_mark() -> None:
    """无关联 desire 的活动（默认观察）不调 mark_active/mark_suppressed。"""
    facade, _store, bus, database = await _new_facade(energy=80.0)
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        desire = cast(_FakeDesire, facade._desire)
        assert desire.mark_active_calls == []
        assert desire.mark_suppressed_calls == []
    finally:
        await database.conn.close()


async def test_interrupt_marks_suppressed() -> None:
    """打断 RUNNING 活动：关联 desire 释放到 SUPPRESSED。"""
    facade, store, bus, database = await _new_facade()
    try:
        async with _running(bus):
            await store.insert(
                _activity(
                    "a1",
                    type_=ActivityType.CREATION,
                    status=ActivityStatus.RUNNING,
                    progress={
                        "desire_id": "d1", "goal": None, "correlation_id": "d1",
                    },
                )
            )
            await facade.interrupt("a1", EventType.USER_MESSAGE)
        desire = cast(_FakeDesire, facade._desire)
        assert desire.mark_suppressed_calls == ["d1"]
        assert desire.mark_active_calls == []
    finally:
        await database.conn.close()


async def test_upgrade_to_free_exploration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    tools = _FakeTools()
    facade, store, bus, database = await _new_facade(
        pending=[
            _desire(
                "d1", DesireType.EXPLORATION,
                goal=Goal(GoalAction.READ, 1, "骑士团"),
            )
        ],
        energy=80.0,
        tools=tools,
        exploration_config=ExplorationConfig(web_enabled=True),
    )
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert len(acts) == 1
        assert acts[0].type is ActivityType.FREE_EXPLORATION
        assert tools.calls[0] == ("web_search", {"query": "骑士团"})
    finally:
        await database.conn.close()


async def test_no_material_rate_limited_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    facade, store, bus, database = await _new_facade(
        pending=[
            _desire(
                "d1", DesireType.EXPLORATION,
                goal=Goal(GoalAction.READ, 1, "骑士团"),
            )
        ],
        energy=80.0,
    )
    try:
        await store.insert(
            _activity("prev", type_=ActivityType.FREE_EXPLORATION, started_at=t0)
        )
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        new = [a for a in acts if a.id != "prev"]
        assert len(new) == 1
        # 无书可读 + 限速中：退回默认活动（观察用户），绝不编造读书内容
        assert new[0].type is ActivityType.OBSERVE_USER
    finally:
        await database.conn.close()


async def test_no_material_no_topic_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无书可读 + 欲望无 topic（无 seed 钉死）→ 不转自由探索，退回默认活动。"""
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    facade, store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.EXPLORATION)], energy=80.0
    )
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert len(acts) == 1
        assert acts[0].type is ActivityType.OBSERVE_USER
    finally:
        await database.conn.close()


async def test_complete_activity() -> None:
    facade, store, bus, database = await _new_facade()
    try:
        events = _subscribe_activity(bus)
        a = _activity(
            "a1", type_=ActivityType.READING, status=ActivityStatus.RUNNING
        )
        await store.insert(a)
        async with _running(bus):
            await facade.complete_activity(a)
        got = await store.get("a1")
        assert got is not None
        assert got.status is ActivityStatus.COMPLETED
        assert got.ended_at is not None
        ends = [e for e in events if e.type is EventType.ACTIVITY_END]
        assert len(ends) == 1
        assert ends[0].content["energy_delta"] == -20
    finally:
        await database.conn.close()


async def test_complete_activity_rolls_back_when_event_append_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, store, bus, database = await _new_facade()

    async def fail_append(event: Event) -> tuple[str, ...]:
        raise RuntimeError("event append failed")

    try:
        monkeypatch.setattr(bus, "append_in_transaction", fail_append)
        await store.create_task(_assigned_task("t1"))
        await store.insert(
            _activity(
                "a1",
                type_=ActivityType.READING,
                status=ActivityStatus.RUNNING,
                progress={"task_id": "t1", "correlation_id": "t1"},
            )
        )
        running = await store.get("a1")
        assert running is not None

        with pytest.raises(RuntimeError):
            await facade._lifecycle.complete(running)

        got = await store.get("a1")
        task = await store.get_task("t1")
        assert got is not None
        assert (got.status, got.ended_at) == (ActivityStatus.RUNNING, None)
        assert task is not None and task.status is AssignedTaskStatus.RUNNING
        assert await bus.list_events() == []
    finally:
        await database.conn.close()


async def test_start_activity_rolls_back_when_event_append_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = await db.connect(":memory:")
    store = ActivityStore(database)
    bus = EventBus(database)
    desire_store = DesireStore(database)

    async def list_memories() -> list[Memory]:
        return []

    desire = DesireFacade(
        desire_store,
        bus,
        cast(LlmClient, _FakeLlm()),
        cast(Evaluator, _FakeEvaluator()),
        DesireConfig(),
        list_memories,
    )
    lifecycle = ActivityLifecycle(store, bus, desire, ActivityConfig())

    async def fail_append(event: Event) -> tuple[str, ...]:
        raise RuntimeError("event append failed")

    try:
        monkeypatch.setattr(bus, "append_in_transaction", fail_append)
        await desire_store.add_desire(_desire("d1", DesireType.EXPLORATION))
        activity = _activity(
            "a1",
            type_=ActivityType.READING,
            status=ActivityStatus.PENDING,
            progress={
                "desire_id": "d1",
                "goal": None,
                "correlation_id": "c1",
            },
        )

        with pytest.raises(RuntimeError):
            await lifecycle.start(activity, is_new=True)

        got_activity = await store.get("a1")
        got_desire = await desire_store.get_desire("d1")
        assert got_activity is None
        assert got_desire is not None
        assert got_desire.status is DesireStatus.PENDING
        assert await bus.list_events() == []
    finally:
        await database.conn.close()


async def test_complete_failure_recovers_orphaned_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, store, bus, database = await _new_facade()

    async def fail_append(event: Event) -> tuple[str, ...]:
        raise RuntimeError("event append failed")

    try:
        monkeypatch.setattr(bus, "append_in_transaction", fail_append)
        await store.create_task(_assigned_task("t1"))
        await store.insert(
            _activity(
                "a1",
                type_=ActivityType.READING,
                status=ActivityStatus.RUNNING,
                progress={"task_id": "t1", "correlation_id": "t1"},
            )
        )
        running = await store.get("a1")
        assert running is not None

        with pytest.raises(RuntimeError, match="event append failed"):
            await facade.complete_activity(running)

        got = await store.get("a1")
        task = await store.get_task("t1")
        assert got is not None and got.status is ActivityStatus.PAUSED
        assert task is not None and task.status is AssignedTaskStatus.PENDING
    finally:
        await database.conn.close()


async def test_failed_settlement_recovers_orphaned_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, store, _bus, database = await _new_facade()
    original_set_status = store.set_task_status
    failed_once = False

    async def fail_first_terminal_update(
        task_id: str,
        status: AssignedTaskStatus,
        now: float,
        error: str | None = None,
    ) -> AssignedTask | None:
        nonlocal failed_once
        if status is AssignedTaskStatus.FAILED and not failed_once:
            failed_once = True
            raise RuntimeError("task update failed")
        return await original_set_status(task_id, status, now, error)

    async def fail_run(_activity: Activity) -> dict[str, Any]:
        raise ValueError("runner failed")

    try:
        await store.create_task(_assigned_task("t1"))
        await store.insert(
            _activity(
                "a1",
                status=ActivityStatus.RUNNING,
                progress={"task_id": "t1", "correlation_id": "t1"},
            )
        )
        running = await store.get("a1")
        assert running is not None
        monkeypatch.setattr(store, "set_task_status", fail_first_terminal_update)
        monkeypatch.setattr(facade, "_run_activity", fail_run)

        with pytest.raises(RuntimeError, match="task update failed"):
            await facade._execute(running)

        got = await store.get("a1")
        task = await store.get_task("t1")
        assert got is not None and got.status is ActivityStatus.PAUSED
        assert task is not None and task.status is AssignedTaskStatus.PENDING
    finally:
        await database.conn.close()


async def test_next_admission_recovers_done_runner_before_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, store, _bus, database = await _new_facade()

    async def keep_idle(
        current_task: asyncio.Task[None] | None,
    ) -> asyncio.Task[None] | None:
        return current_task

    try:
        await store.create_task(_assigned_task("t1"))
        await store.insert(
            _activity(
                "a1",
                status=ActivityStatus.RUNNING,
                progress={"task_id": "t1", "correlation_id": "t1"},
            )
        )
        facade._task = asyncio.create_task(asyncio.sleep(0))
        await facade._task
        monkeypatch.setattr(facade._starter, "start_next_if_idle", keep_idle)

        await facade._maybe_start_activity()

        activity = await store.get("a1")
        task = await store.get_task("t1")
        assert activity is not None and activity.status is ActivityStatus.PAUSED
        assert task is not None and task.status is AssignedTaskStatus.PENDING
    finally:
        await database.conn.close()


async def test_fail_activity_rolls_back_when_task_update_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = await db.connect(":memory:")
    store = ActivityStore(database)
    bus = EventBus(database)
    desire_store = DesireStore(database)

    async def list_memories() -> list[Memory]:
        return []

    desire = DesireFacade(
        desire_store,
        bus,
        cast(LlmClient, _FakeLlm()),
        cast(Evaluator, _FakeEvaluator()),
        DesireConfig(),
        list_memories,
    )
    lifecycle = ActivityLifecycle(store, bus, desire, ActivityConfig())

    async def fail_task_update(
        task_id: str,
        status: AssignedTaskStatus,
        now: float,
        error: str | None = None,
    ) -> AssignedTask | None:
        raise RuntimeError("task update failed")

    try:
        active = _desire("d1", DesireType.EXPLORATION)
        active.status = DesireStatus.ACTIVE
        await desire_store.add_desire(active)
        await store.create_task(_assigned_task("t1"))
        await store.insert(
            _activity(
                "a1",
                status=ActivityStatus.RUNNING,
                progress={
                    "desire_id": "d1",
                    "task_id": "t1",
                    "correlation_id": "c1",
                },
            )
        )
        monkeypatch.setattr(store, "set_task_status", fail_task_update)

        with pytest.raises(RuntimeError, match="task update failed"):
            await lifecycle.fail(cast(Activity, await store.get("a1")), "boom")

        got_activity = await store.get("a1")
        got_desire = await desire_store.get_desire("d1")
        got_task = await store.get_task("t1")
        assert (
            got_activity is not None
            and got_activity.status is ActivityStatus.RUNNING
        )
        assert got_desire is not None and got_desire.status is DesireStatus.ACTIVE
        assert got_task is not None and got_task.status is AssignedTaskStatus.RUNNING
    finally:
        await database.conn.close()


async def test_interrupt_activity_rolls_back_when_event_append_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = await db.connect(":memory:")
    store = ActivityStore(database)
    bus = EventBus(database)
    desire_store = DesireStore(database)

    async def list_memories() -> list[Memory]:
        return []

    desire = DesireFacade(
        desire_store,
        bus,
        cast(LlmClient, _FakeLlm()),
        cast(Evaluator, _FakeEvaluator()),
        DesireConfig(),
        list_memories,
    )
    lifecycle = ActivityLifecycle(store, bus, desire, ActivityConfig())

    async def fail_append(event: Event) -> tuple[str, ...]:
        raise RuntimeError("event append failed")

    try:
        monkeypatch.setattr(bus, "append_in_transaction", fail_append)
        active = _desire("d1", DesireType.EXPLORATION)
        active.status = DesireStatus.ACTIVE
        await desire_store.add_desire(active)
        await store.insert(
            _activity(
                "a1",
                type_=ActivityType.READING,
                status=ActivityStatus.RUNNING,
                progress={
                    "desire_id": "d1",
                    "goal": None,
                    "correlation_id": "c1",
                },
            )
        )

        with pytest.raises(RuntimeError):
            await lifecycle.interrupt("a1", EventType.USER_MESSAGE, None)

        got_activity = await store.get("a1")
        got_desire = await desire_store.get_desire("d1")
        assert got_activity is not None
        assert got_activity.status is ActivityStatus.RUNNING
        assert got_desire is not None
        assert got_desire.status is DesireStatus.ACTIVE
        assert await bus.list_events() == []
    finally:
        await database.conn.close()


async def test_interrupt_non_resumable_abandons() -> None:
    """瞬时活动（休息）打断仍置 ABANDONED（无进度可续）。"""
    facade, store, bus, database = await _new_facade()
    try:
        events = _subscribe_activity(bus)
        await store.insert(
            _activity("a1", type_=ActivityType.REST, status=ActivityStatus.RUNNING)
        )
        async with _running(bus):
            await facade.interrupt("a1", EventType.USER_MESSAGE)
        got = await store.get("a1")
        assert got is not None
        assert got.status is ActivityStatus.ABANDONED
        assert got.ended_at is not None
        ints = [e for e in events if e.type is EventType.ACTIVITY_INTERRUPTED]
        assert len(ints) == 1
        assert ints[0].content["by"] == "user_message"
    finally:
        await database.conn.close()


async def test_interrupt_creation_marks_paused() -> None:
    """创作被打断置 PAUSED（保留记录可重跑），非 ABANDONED。"""
    facade, store, bus, database = await _new_facade()
    try:
        events = _subscribe_activity(bus)
        await store.insert(
            _activity("a1", type_=ActivityType.CREATION, status=ActivityStatus.RUNNING)
        )
        async with _running(bus):
            await facade.interrupt("a1", EventType.USER_MESSAGE)
        got = await store.get("a1")
        assert got is not None
        assert got.status is ActivityStatus.PAUSED
        assert got.ended_at is not None
        ints = [e for e in events if e.type is EventType.ACTIVITY_INTERRUPTED]
        assert len(ints) == 1
        assert ints[0].content["by"] == "user_message"
    finally:
        await database.conn.close()


async def test_interrupt_reading_marks_paused() -> None:
    """读书被打断置 PAUSED（read_chars 已 advance 可续读），非 ABANDONED。"""
    facade, store, bus, database = await _new_facade()
    try:
        events = _subscribe_activity(bus)
        await store.insert(_activity("a1", status=ActivityStatus.RUNNING))
        async with _running(bus):
            await facade.interrupt("a1", EventType.USER_MESSAGE)
        got = await store.get("a1")
        assert got is not None
        assert got.status is ActivityStatus.PAUSED
        assert got.ended_at is not None
        ints = [e for e in events if e.type is EventType.ACTIVITY_INTERRUPTED]
        assert len(ints) == 1
        assert ints[0].content["by"] == "user_message"
    finally:
        await database.conn.close()


async def test_interrupt_missing() -> None:
    facade, _store, bus, database = await _new_facade()
    try:
        events = _subscribe_activity(bus)
        async with _running(bus):
            await facade.interrupt("nope", EventType.USER_MESSAGE)
        assert events == []
    finally:
        await database.conn.close()


async def test_interrupt_pauses_in_flight_activity() -> None:
    """竞态回归：执行中可续活动（探索）挂起在可取消 await 上时 interrupt →
    终态 PAUSED，不被随后 complete 覆盖。"""
    facade, store, bus, database = await _new_facade(
        pending=[
            _desire(
                "d1", DesireType.EXPLORATION,
                goal=Goal(GoalAction.READ, 1, "骑士团"),
            )
        ],
        energy=80.0,
        llm=_BlockingLlm(),
    )
    try:
        events = _subscribe_activity(bus)
        async with _running(bus):
            await facade._maybe_start_activity()
            cur = await _await_running(store)
            await facade.interrupt(cur.id, EventType.USER_MESSAGE)
        got = await store.get(cur.id)
        assert got is not None
        assert got.status is ActivityStatus.PAUSED
        assert got.ended_at is not None
        ints = [e for e in events if e.type is EventType.ACTIVITY_INTERRUPTED]
        assert len(ints) == 1
    finally:
        await database.conn.close()


async def _await_running(store: ActivityStore) -> Activity:
    """等后台执行 task 置 RUNNING 后返回当前活动（有界轮询，避免死等）。"""
    for _ in range(100):
        cur = await store.get_current()
        if cur is not None and cur.status is ActivityStatus.RUNNING:
            return cur
        await asyncio.sleep(0)
    raise AssertionError("活动未在预期内进入 RUNNING")


async def test_get_current_delegates() -> None:
    facade, store, _bus, database = await _new_facade()
    try:
        await store.insert(_activity("a1", status=ActivityStatus.RUNNING))
        cur = await facade.get_current()
        assert cur is not None
        assert cur.id == "a1"
    finally:
        await database.conn.close()


async def test_get_schedule_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    facade, store, _bus, database = await _new_facade()
    try:
        await store.insert(_activity("a1", started_at=t0))
        acts = await facade.get_schedule()
        assert [a.id for a in acts] == ["a1"]
    finally:
        await database.conn.close()


async def test_get_results_delegates() -> None:
    """get_results 委托 store.list_results：跨天历史产出（已完成 + 产出类型）。"""
    facade, store, _bus, database = await _new_facade()
    try:
        await store.insert(
            _activity(
                "a1", type_=ActivityType.CREATION, status=ActivityStatus.COMPLETED
            )
        )
        acts = await facade.get_results(
            limit=12, offset=0, activity_type=ActivityType.CREATION
        )
        assert [a.id for a in acts] == ["a1"]
    finally:
        await database.conn.close()


async def test_maybe_start_reading_can_match_uploaded_epub() -> None:
    book = Book(
        "b1",
        "诺斯艾兰",
        "作者",
        "nuosi.epub",
        "hash",
        20,
        1.0,
        1.0,
    )
    calls: list[tuple[str, int | None, str]] = []

    async def list_reader_books() -> list[Book]:
        return [book]

    async def read_reader_book(
        book_id: str, target: int | None, correlation_id: str
    ) -> dict[str, Any]:
        calls.append((book_id, target, correlation_id))
        return {"book": book.title, "target_paragraph": 5, "completed": False}

    facade, store, bus, database = await _new_facade(
        pending=[
            _desire(
                "d1",
                DesireType.EXPLORATION,
                goal=Goal(GoalAction.READ, 1, "诺斯艾兰"),
            )
        ],
        list_reader_books=list_reader_books,
        read_reader_book=read_reader_book,
    )
    try:
        events = _subscribe_activity(bus)
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        activity = (await store.list_schedule(0.0))[0]
        assert activity.progress["book_id"] == book.id
        assert calls == [(book.id, None, "d1")]
        assert activity.progress["result"]["completed"] is False
        assert events[-1].content["goal_met"] is None
        desire = cast(_FakeDesire, facade._desire)
        assert desire.release_active_calls == ["d1"]
    finally:
        await database.conn.close()


# ---- 恢复/续做 ----


async def test_resume_paused_creation_reruns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同日程块内 PAUSED 创作被恢复：同一记录重跑完成，不新建。"""
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    block_id = _schedule_block_id(t0, 60)
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade, store, bus, database = await _new_facade(llm=llm, evaluator=evaluator)
    try:
        await store.insert(
            _activity(
                "p1",
                type_=ActivityType.CREATION,
                status=ActivityStatus.PAUSED,
                started_at=t0,
                schedule_block_id=block_id,
                progress={
                    "desire_id": "d1", "goal": None, "correlation_id": "d1",
                    "description": "写日记",
                },
            )
        )
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert [a.id for a in acts] == ["p1"]      # 恢复同一记录，未新建
        assert acts[0].status is ActivityStatus.COMPLETED
        assert len(evaluator.evaluated) == 1
    finally:
        await database.conn.close()


async def test_resume_skips_different_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不同日程块的 PAUSED 不恢复：保留旧记录，走正常新起。"""
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    facade, store, bus, database = await _new_facade(energy=80.0)
    try:
        await store.insert(
            _activity("p1", status=ActivityStatus.PAUSED, schedule_block_id="00:00")
        )
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        ids = [a.id for a in acts]
        assert "p1" in ids                         # 旧 PAUSED 保留
        assert len(ids) == 2                       # 新起一个活动
        new = next(a for a in acts if a.id != "p1")
        assert new.type is ActivityType.OBSERVE_USER
    finally:
        await database.conn.close()


async def test_interrupt_failure_recovers_cancelled_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, store, bus, database = await _new_facade()

    async def fail_append(event: Event) -> tuple[str, ...]:
        raise RuntimeError("event append failed")

    try:
        monkeypatch.setattr(bus, "append_in_transaction", fail_append)
        await store.create_task(_assigned_task("t1"))
        await store.insert(
            _activity(
                "a1",
                status=ActivityStatus.RUNNING,
                progress={"task_id": "t1", "correlation_id": "t1"},
            )
        )

        with pytest.raises(RuntimeError, match="event append failed"):
            await facade.interrupt("a1", EventType.USER_MESSAGE)

        activity = await store.get("a1")
        task = await store.get_task("t1")
        assert activity is not None and activity.status is ActivityStatus.PAUSED
        assert task is not None and task.status is AssignedTaskStatus.PENDING
    finally:
        await database.conn.close()


async def test_resume_skips_same_block_from_previous_local_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 10, 1, 9, 30).timestamp()
    previous_day = datetime(2026, 9, 30, 9, 30).timestamp()
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: now)
    block_id = _schedule_block_id(now, 60)
    facade, store, bus, database = await _new_facade(energy=80.0)
    try:
        await store.insert(
            _activity(
                "old",
                type_=ActivityType.CREATION,
                status=ActivityStatus.PAUSED,
                started_at=previous_day,
                schedule_block_id=block_id,
            )
        )
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)

        activities = await store.list_schedule(0.0)
        old = next(activity for activity in activities if activity.id == "old")
        new = next(activity for activity in activities if activity.id != "old")
        assert old.status is ActivityStatus.PAUSED
        assert new.type is ActivityType.OBSERVE_USER
    finally:
        await database.conn.close()


# ---- 知识点提取 / 创作上下文 ----


def test_pick_creation_style() -> None:
    assert _pick_creation_style() in _CREATION_STYLES


def _knowledge_mem(summary: str, content: str) -> Memory:
    return Memory(
        id=f"km-{summary}",
        created_at=1.0,
        content=content,
        kind=MemoryKind.KNOWLEDGE,
        summary=summary,
        freshness=1.0,
        type=MemoryType.LONG_TERM,
    )


def test_build_creation_context_full() -> None:
    act = _activity(
        "a1",
        progress={
            "goal": {"action": "write", "count": 1, "topic": "骑士团"},
            "desire_id": "d1",
            "correlation_id": "c1",
        },
    )
    obs = {"presence": "online", "window_title": "编辑器", "screen_summary": "写代码"}
    ctx = _build_creation_context(
        act, "日记体", [_knowledge_mem("骑士团", "成立于 1147 年")], obs
    )
    assert "风格：日记体" in ctx
    assert "主题：骑士团" in ctx
    assert "参考记忆" in ctx
    assert "成立于 1147 年" in ctx
    assert "当前屏幕灵感" in ctx


def test_build_creation_context_empty() -> None:
    ctx = _build_creation_context(
        _activity("a1"), "日记体", [], {"presence": "away", "window_title": ""}
    )
    assert ctx == "风格：日记体"   # 无主题/知识/屏幕 → 只剩风格


def test_build_creation_system() -> None:
    """创作 system prompt：canon 全文 + 此刻心境 + 创作声音指令都注入。"""
    state = _mk_state(70.0)
    state.active_desires = [_desire("d1", DesireType.CREATION, description="写点东西")]
    sys = _build_creation_system("测试人格", state)
    assert "测试人格" in sys
    assert "[此刻心境]" in sys
    assert state.emotion.value in sys
    assert "精力：70/100" in sys
    assert "写点东西" in sys
    assert "[创作要求]" in sys
    assert "按 JSON 输出" in sys


async def test_creation_activity_injects_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    llm = _CapturingLlm()
    memory = _FakeMemory()
    previous = _knowledge_mem("旧作《雨夜》", "她曾在雨里等过一个人")
    previous.kind = MemoryKind.ACTIVITY
    previous.topics = ["creation"]
    memory.knowledge = [
        previous,
        _knowledge_mem("骑士团", "成立于 1147 年"),
    ]

    async def fake_observation() -> dict[str, str]:
        return {
            "presence": "online",
            "window_title": "编辑器",
            "screen_summary": "写代码",
        }

    facade, _store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.CREATION)],
        energy=80.0,
        llm=llm,
        evaluator=_FakeEvaluator(),
        memory=memory,
        get_observation=fake_observation,
    )
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        user = llm.user_contents[0]
        assert memory.search_queries == ["读骑士小说"]
        assert "创作参考" in user
        assert "风格：" in user
        assert "参考记忆" in user
        assert "旧作《雨夜》" in user
        assert "当前屏幕灵感" in user
    finally:
        await database.conn.close()


async def test_creation_activity_injects_canon_system(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """创作 system prompt 注入 canon 人格全文（不再只是「你是尼克斯」）。"""
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    llm = _CapturingLlm()
    facade, _store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.CREATION)],
        energy=80.0,
        llm=llm,
        evaluator=_FakeEvaluator(),
        canon="测试人格全文",
    )
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        assert "测试人格全文" in llm.system_contents[0]
        assert "[此刻心境]" in llm.system_contents[0]
    finally:
        await database.conn.close()


async def test_exploration_appends_parent_subtopics_and_knowledge() -> None:
    fake_desire = _FakeDesire()
    fake_memory = _FakeMemory()
    facade, _store, _bus, database = await _new_facade(
        llm=_ExplorationLlm(), desire=fake_desire, memory=fake_memory
    )
    try:
        activity = _activity(
            "a1",
            type_=ActivityType.FREE_EXPLORATION,
            status=ActivityStatus.RUNNING,
            progress={
                "goal": {"topic": "量子"},
                "correlation_id": "c1",
                "parent_long_term_id": "lt1",
            },
        )
        result = await facade._exploration.run(activity)
        assert fake_desire.appended_subtopics == [("lt1", ["量子纠错"])]
        assert fake_memory.remembered[-1][0]["topic"] == "退相干"
        assert activity.progress["exploration"]["state"] == "completed"
        assert result["outcome"] == "won"
    finally:
        await database.conn.close()
