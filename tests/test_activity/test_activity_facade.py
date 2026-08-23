# pyright: reportPrivateUsage=false
import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import pytest

from nyx import db
from nyx.activity.facade import (
    ActivityFacade,
    _day_start,
    _elapsed_hours,
    _goal_met,
    _parse_activity_result,
    _sanitize_filename,
)
from nyx.activity.material_store import MaterialStore
from nyx.activity.scheduler import format_time_label
from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig, ExplorationConfig
from nyx.db import Database
from nyx.desire.facade import DesireFacade
from nyx.enums import (
    ActivityStatus,
    ActivityType,
    DesireStatus,
    DesireType,
    EmotionCategory,
    EnergyState,
    EventType,
    GoalAction,
    Source,
)
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient, LlmMessage
from nyx.tools.registry import ToolRegistry
from nyx.types import (
    Activity,
    CurrentState,
    DesireState,
    DesireValue,
    Event,
    Goal,
    LLMOutput,
    Personality,
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


def _mk_state(energy: float) -> CurrentState:
    return CurrentState(
        valence=0.0,
        arousal=0.0,
        emotion=EmotionCategory.NEUTRAL,
        personality=_PERSONALITY,
        values=_VALUES,
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
) -> ShortTermDesire:
    return ShortTermDesire(
        id=id,
        created_at=1000.0,
        type=type_,
        strength=0.9,
        description=description,
        goal=goal,
        status=DesireStatus.PENDING,
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
            id=f"llm-{len(self.calls)}",
            module=module,
            type=output_type,
            model="fake",
            content=content,
            token_usage={"input": 1, "output": 1},
            correlation_id=correlation_id,
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

    async def get_pending(self) -> list[ShortTermDesire]:
        return self._pending

    async def get_all(self) -> DesireState:
        return DesireState(
            values=self._values, short_term=self._pending, long_term=[]
        )

    async def mark_active(self, desire_id: str) -> None:
        self.mark_active_calls.append(desire_id)

    async def mark_suppressed(self, desire_id: str) -> None:
        self.mark_suppressed_calls.append(desire_id)


class _FakeTools:
    async def call(self, name: str, args: dict[str, Any]) -> Any:
        if name in ("local_search", "web_search"):
            return ["一条检索结果"]
        return "文件内容"


async def _no_reflect(correlation_id: str | None) -> str | None:
    return None


async def _no_observation() -> dict[str, str]:
    return {"presence": "away", "window_title": ""}


async def _new_facade(
    pending: list[ShortTermDesire] | None = None,
    values: list[DesireValue] | None = None,
    energy: float = 80.0,
    llm: _FakeLlm | None = None,
    evaluator: _FakeEvaluator | None = None,
    reflect: Callable[[str | None], Awaitable[str | None]] | None = None,
    get_observation: Callable[[], Awaitable[dict[str, str]]] | None = None,
) -> tuple[ActivityFacade, ActivityStore, EventBus, Database]:
    database = await db.connect(":memory:")
    store = ActivityStore(database)
    material_store = MaterialStore(database)
    bus = EventBus(database)

    async def get_state() -> CurrentState:
        return _mk_state(energy)

    facade = ActivityFacade(
        store,
        material_store,
        bus,
        cast(LlmClient, llm if llm is not None else _FakeLlm()),
        cast(Evaluator, evaluator if evaluator is not None else _FakeEvaluator()),
        cast(ToolRegistry, _FakeTools()),
        cast(DesireFacade, _FakeDesire(pending, values)),
        get_state,
        reflect if reflect is not None else _no_reflect,
        get_observation if get_observation is not None else _no_observation,
        ActivityConfig(),
        ExplorationConfig(),
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


# ---- 纯函数 ----


def test_day_start() -> None:
    assert _day_start(86400.0 * 1.5) == 86400.0


def test_elapsed_hours() -> None:
    assert _elapsed_hours(5400.0) == 1.5


def test_goal_met() -> None:
    assert _goal_met(None, {}) is None
    assert _goal_met({"action": "read"}, {}) is False
    assert _goal_met({"action": "read"}, {"book": "x"}) is False   # 读完整本才算
    assert _goal_met({"action": "read"}, {"completed": True}) is True
    assert _goal_met({"action": "write"}, {"title": "t", "content": "c"}) is True
    assert _goal_met({"action": "write"}, {"title": "t"}) is False
    assert _goal_met({"action": "observe"}, {"presence": "online"}) is True
    assert _goal_met({"action": "observe"}, {}) is False


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
            "d1", DesireType.EXPLORATION, goal=Goal(GoalAction.READ, 3, "骑士团")
        )
        act = facade.select_activity([d], _mk_state(80.0))
        assert act is not None
        assert act.type is ActivityType.READING
        assert act.progress["desire_id"] == "d1"
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
) -> None:
    """创作落盘：LLM 产 {title, content} 后写进 workspace/creations，result 带 path。"""
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    captured: dict[str, Any] = {}

    async def fake_file_io(
        action: str,
        path: str,
        content: str | None = None,
        write_root: Path = Path("workspace"),
    ) -> dict[str, Any]:
        captured["path"] = path
        captured["content"] = content
        return {"path": f"workspace/{path}", "written": len(content or "")}

    monkeypatch.setattr("nyx.activity.facade.file_io", fake_file_io)
    facade, _store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.CREATION)], energy=80.0,
        llm=_FakeLlm(), evaluator=_FakeEvaluator(),
    )
    try:
        events = _subscribe_activity(bus)
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        ends = [e for e in events if e.type is EventType.ACTIVITY_END]
        assert (
            ends[0].content["result"]["path"]
            == "workspace/creations/小狐狸的日记.md"
        )
        assert captured["path"] == "creations/小狐狸的日记.md"
        assert captured["content"] == "今天也努力了"
    finally:
        await database.conn.close()


async def test_idle_reflection_result_has_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """发呆反思：回带 story 作为 result.summary（不发 REFLECTION 事件）。"""
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)

    async def fake_reflect(correlation_id: str | None) -> str | None:
        return "今天的故事"

    facade, store, bus, database = await _new_facade(
        energy=30.0, reflect=fake_reflect
    )
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert acts[0].progress["result"] == {"summary": "今天的故事"}
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
    facade, store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.EXPLORATION)], energy=80.0
    )
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert len(acts) == 1
        assert acts[0].type is ActivityType.FREE_EXPLORATION
    finally:
        await database.conn.close()


async def test_no_material_rate_limited_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    facade, store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.EXPLORATION)], energy=80.0
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
        pending=[_desire("d1", DesireType.EXPLORATION)], energy=80.0,
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
        acts = await facade.get_results()
        assert [a.id for a in acts] == ["a1"]
    finally:
        await database.conn.close()


async def test_read_material_reads_real_file(tmp_path: Path) -> None:
    """用户投喂资料：READING 活动带 source，读真实文件分块产出 {book, note}。"""
    source = tmp_path / "book.txt"
    source.write_text("甲" * 7000, encoding="utf-8")  # 7000 字符，一块读不尽
    facade, store, bus, database = await _new_facade()
    try:
        events = _subscribe_activity(bus)
        async with _running(bus):
            await facade.read_material(str(source), "book.txt", 7000, "c1")
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert len(acts) == 1
        assert acts[0].type is ActivityType.READING
        assert acts[0].progress["source"] == str(source)
        assert acts[0].progress["result"] == {
            "book": "骑士团历史",
            "note": "读到了第三章",
            "read_chars": 6000,
            "total_chars": 7000,
        }
        assert [e.type for e in events] == [
            EventType.ACTIVITY_START,
            EventType.ACTIVITY_END,
        ]
    finally:
        await database.conn.close()


async def test_reading_completion_aggregates_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """读完整本书：最后一块后聚合片段 → 完整笔记落盘 + completed=True。"""
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    captured: dict[str, Any] = {}

    async def fake_file_io(
        action: str,
        path: str,
        content: str | None = None,
        write_root: Path = Path("workspace"),
    ) -> dict[str, Any]:
        captured["path"] = path
        captured["content"] = content
        return {"path": f"workspace/{path}", "written": len(content or "")}

    monkeypatch.setattr("nyx.activity.facade.file_io", fake_file_io)
    source = tmp_path / "book.txt"
    source.write_text("骑士团的历史", encoding="utf-8")  # 6 字符，一块读尽
    llm = _FakeLlm()
    facade, store, bus, database = await _new_facade(
        llm=llm, evaluator=_FakeEvaluator()
    )
    try:
        async with _running(bus):
            await facade.read_material(str(source), "book.txt", 6, "c1")
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        result = acts[0].progress["result"]
        assert result["completed"] is True
        assert result["note"] == "完整读书笔记"
        assert result["path"] == "workspace/notes/book.txt.md"
        assert captured["path"] == "notes/book.txt.md"
        assert llm.calls == ["reading", "note"]
    finally:
        await database.conn.close()


async def test_read_material_skips_when_busy() -> None:
    """忙时跳过：执行中任务未结束时投喂资料不新增活动（文件已落盘，不排队）。"""
    facade, store, _bus, database = await _new_facade()
    try:
        await facade._maybe_start_activity()
        assert facade._task is not None and not facade._task.done()
        await facade.read_material("/no/such/file.txt", "book.txt", 100, "c1")
        acts = await store.list_schedule(0.0)
        assert len(acts) == 1
        await facade._task
    finally:
        await database.conn.close()


async def test_desire_reading_reads_latest_material(tmp_path: Path) -> None:
    """探索欲触发：读最近未读完的那本（分块 + 推进度），而非凭空编造。"""
    source = tmp_path / "book.txt"
    source.write_text("甲" * 7000, encoding="utf-8")
    facade, store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.EXPLORATION)], energy=80.0
    )
    try:
        await facade._material_store.upsert(str(source), "book.txt", 7000, 1000.0)
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert len(acts) == 1
        assert acts[0].type is ActivityType.READING
        assert acts[0].progress["source"] == str(source)
        assert acts[0].progress["read_chars"] == 0
        assert acts[0].progress["total_chars"] == 7000
        assert acts[0].progress["result"]["read_chars"] == 6000
        assert acts[0].progress["result"]["total_chars"] == 7000
        # 书库进度推进但未读完，下次探索欲会续读
        mat = await facade._material_store.next_readable()
        assert mat is not None and mat.read_chars == 6000
    finally:
        await database.conn.close()


async def test_reading_relays_prior_fragments(tmp_path: Path) -> None:
    """滚动摘要接力：续读第二块时把「上次读到哪里 + 已读片段笔记」喂给 LLM。"""

    class RecordingLlm(_FakeLlm):
        def __init__(self) -> None:
            super().__init__()
            self.user_contents: list[str] = []

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
            return await super().complete(
                messages, module=module, output_type=output_type,
                correlation_id=correlation_id, json_mode=json_mode,
            )

    source = tmp_path / "book.txt"
    source.write_text("甲" * 13000, encoding="utf-8")  # 两块以上，第二块读不尽
    llm = RecordingLlm()
    facade, _store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.EXPLORATION)], energy=80.0, llm=llm
    )
    try:
        await facade._material_store.upsert(str(source), "book.txt", 13000, 1000.0)
        # 模拟已读完第一块：进度 6000 + 留下一篇片段笔记
        await facade._material_store.append_fragment(
            str(source), "上一块的笔记", 1000.0
        )
        await facade._material_store.advance(str(source), 6000, 1000.0)
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        # 只发生一次 reading 调用（12000 < 13000 未读完，不聚合）
        assert llm.calls == ["reading"]
        user = llm.user_contents[0]
        assert "上一块的笔记" in user  # 已读片段被带上
        assert "第 6000 字" in user      # 「上次读到哪里」位置被带上
        assert "本次新读" in user        # 本次新读块
    finally:
        await database.conn.close()


async def test_maybe_start_reading_uses_topic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """读书按 topic 选料：goal.topic 命中 filename 时读那本，而非最近一本。"""
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    target = tmp_path / "骑士团历史.txt"
    target.write_text("甲" * 7000, encoding="utf-8")
    newer = tmp_path / "other.txt"
    newer.write_text("无关", encoding="utf-8")
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
        # target 更早入书库（created_at 更小）；newer 是「最近一本」
        await facade._material_store.upsert(
            str(target), "骑士团历史.txt", 7000, t0 - 10
        )
        await facade._material_store.upsert(str(newer), "other.txt", 2, t0)
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert len(acts) == 1
        assert acts[0].progress["source"] == str(target)
        assert acts[0].progress["filename"] == "骑士团历史.txt"
    finally:
        await database.conn.close()


# ---- 恢复/续做 ----


async def test_resume_paused_creation_reruns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同日程块内 PAUSED 创作被恢复：同一记录重跑完成，不新建。"""
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    block_id = format_time_label(0, 60, _elapsed_hours(t0))
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade, store, bus, database = await _new_facade(llm=llm, evaluator=evaluator)
    try:
        await store.insert(
            _activity(
                "p1",
                type_=ActivityType.CREATION,
                status=ActivityStatus.PAUSED,
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


async def test_resume_paused_reading_refreshes_read_chars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """读书恢复：read_chars 从 material 层刷新（而非 progress 里的旧值），续读。"""
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    block_id = format_time_label(0, 60, _elapsed_hours(t0))
    source = tmp_path / "book.txt"
    source.write_text("甲" * 7000, encoding="utf-8")
    facade, store, bus, database = await _new_facade()
    try:
        await facade._material_store.upsert(str(source), "book.txt", 7000, t0)
        # 模拟已读 6000：material 层进度领先 progress 里的旧 read_chars=0
        await facade._material_store.advance(str(source), 6000, t0)
        await store.insert(
            _activity(
                "p1",
                status=ActivityStatus.PAUSED,
                schedule_block_id=block_id,
                progress={
                    "source": str(source), "filename": "book.txt",
                    "read_chars": 0, "total_chars": 7000,
                    "correlation_id": "c1",
                },
            )
        )
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert [a.id for a in acts] == ["p1"]
        assert acts[0].progress["read_chars"] == 6000   # 已刷新到 material 层进度
        assert acts[0].progress["result"]["read_chars"] == 7000
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
