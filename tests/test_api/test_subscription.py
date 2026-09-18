# pyright: reportPrivateUsage=false
import asyncio
import contextlib
from typing import cast

from nyx.activity.facade import ActivityFacade
from nyx.browsing.facade import BrowsingFacade
from nyx.config import Config
from nyx.db import connect
from nyx.desire.facade import DesireFacade
from nyx.enums import EventType, Source
from nyx.eval.evaluator import Evaluator
from nyx.eval.store import EvalStore
from nyx.events.bus import EventBus
from nyx.events.routing import ROUTING
from nyx.expression.facade import ExpressionFacade
from nyx.inner_life.facade import InnerLifeFacade
from nyx.main import _App, _root_event, _subscribe
from nyx.memory.facade import MemoryFacade
from nyx.reading.facade import ReadingFacade
from nyx.types import Event


class _FakeInnerLife:
    def __init__(self) -> None:
        self.applied: list[tuple[Event, str | None]] = []

    async def apply_event(self, event: Event, consumer_id: str | None = None) -> None:
        self.applied.append((event, consumer_id))


class _FakeDesire:
    def __init__(self) -> None:
        self.added: list[Event] = []

    async def add_value(self, event: Event, consumer_id: str | None = None) -> None:
        del consumer_id
        self.added.append(event)


class _FakeActivity:
    def __init__(self) -> None:
        self.generated: list[Event] = []

    async def get_current(self) -> None:
        return None

    async def on_desire_generated(self, event: Event) -> None:
        self.generated.append(event)


class _FakeExpression:
    def __init__(self) -> None:
        self.replied: list[tuple[str, str]] = []
        self.app: _App | None = None
        self.return_seen_at_reply: dict[str, float] | None = None

    async def reply(
        self, msg: str, correlation_id: str, reply_to: str | None = None,
        *, browsing_context: dict[str, str] | None = None,
    ) -> None:
        del reply_to, browsing_context
        if self.app is not None:
            self.return_seen_at_reply = self.app.pending_return
        self.replied.append((msg, correlation_id))


class _FakeMemory:
    def __init__(self) -> None:
        self.remembered: list[Event] = []

    async def remember_activity(
        self, event: Event, consumer_id: str | None = None
    ) -> None:
        del consumer_id
        self.remembered.append(event)


class _InvalidBrowsingContext:
    async def get_prompt_context(self, page_id: str) -> None:
        del page_id
        return None


class _RecordingBus:
    def __init__(self) -> None:
        self.published: list[Event] = []

    async def list_events(self, **kwargs: object) -> list[Event]:
        del kwargs
        return self.published

    async def publish(self, event: Event) -> None:
        self.published.append(event)


def _content(event_type: EventType) -> dict[str, str]:
    if event_type is EventType.USER_MESSAGE:
        return {"message": "hi"}
    return {}


async def test_subscription_consistency() -> None:
    database = await connect(":memory:")
    bus = EventBus(database)
    inner_life = _FakeInnerLife()
    desire = _FakeDesire()
    activity = _FakeActivity()
    expression = _FakeExpression()
    memory = _FakeMemory()
    app = _App(
        bus=bus,
        inner_life=cast(InnerLifeFacade, inner_life),
        desire=cast(DesireFacade, desire),
        memory=cast(MemoryFacade, memory),
        activity=cast(ActivityFacade, activity),
        expression=cast(ExpressionFacade, expression),
        reading=cast(ReadingFacade, object()),
        evaluator=cast(Evaluator, object()),
        eval_store=cast(EvalStore, object()),
        config=Config(),
    )
    _subscribe(app)

    task = asyncio.create_task(bus.run())
    try:
        for event_type, consumers in ROUTING.items():
            if consumers:
                await bus.publish(_root_event(event_type, _content(event_type)))
        assert await bus.drain(timeout=1.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await database.conn.close()

    assert len(expression.replied) == 1
    assert len(inner_life.applied) == 4
    assert {
        (event.type, consumer_id) for event, consumer_id in inner_life.applied
    } == {
        (EventType.OBSERVATION_STATE, "inner_life.observation_state"),
        (EventType.DESIRE_SATISFIED, "inner_life.desire_satisfied"),
        (EventType.ACTIVITY_END, "inner_life.activity_end"),
        (EventType.REFLECTION, "inner_life.reflection"),
    }
    assert len(desire.added) == 2
    assert len(activity.generated) == 1
    assert len(memory.remembered) == 1


async def test_user_message_replay_skips_after_reply_event_exists() -> None:
    database = await connect(":memory:")
    bus = EventBus(database)
    expression = _FakeExpression()
    app = _App(
        bus=bus,
        inner_life=cast(InnerLifeFacade, _FakeInnerLife()),
        desire=cast(DesireFacade, _FakeDesire()),
        memory=cast(MemoryFacade, _FakeMemory()),
        activity=cast(ActivityFacade, _FakeActivity()),
        expression=cast(ExpressionFacade, expression),
        reading=cast(ReadingFacade, object()),
        evaluator=cast(Evaluator, object()),
        eval_store=cast(EvalStore, object()),
        config=Config(),
    )
    event = _root_event(EventType.USER_MESSAGE, {"message": "hi"})
    await bus.publish(
        Event(
            id="reply-1",
            timestamp=1.0,
            source=Source.INTERNAL,
            type=EventType.SPEAK,
            content={"content": "hello"},
            correlation_id=event.correlation_id,
        )
    )

    from nyx.runtime import on_user_message

    await on_user_message(app, event)

    assert expression.replied == []
    await database.close()


async def test_expired_browsing_context_returns_explicit_failure() -> None:
    bus = _RecordingBus()
    expression = _FakeExpression()
    app = _App(
        bus=cast(EventBus, bus),
        inner_life=cast(InnerLifeFacade, _FakeInnerLife()),
        desire=cast(DesireFacade, _FakeDesire()),
        memory=cast(MemoryFacade, _FakeMemory()),
        activity=cast(ActivityFacade, _FakeActivity()),
        expression=cast(ExpressionFacade, expression),
        reading=cast(ReadingFacade, object()),
        evaluator=cast(Evaluator, object()),
        eval_store=cast(EvalStore, object()),
        config=Config(),
        browsing=cast(BrowsingFacade, _InvalidBrowsingContext()),
    )
    event = _root_event(
        EventType.USER_MESSAGE,
        {"message": "这页说了什么？", "browsing_page_id": "stale-page"},
    )

    from nyx.runtime import on_user_message

    await on_user_message(app, event)
    await on_user_message(app, event)

    assert expression.replied == []
    assert [item.type for item in bus.published] == [EventType.SPEAK]
    assert bus.published[0].correlation_id == event.correlation_id


async def test_user_message_marks_away_user_returned_before_reply() -> None:
    database = await connect(":memory:")
    bus = EventBus(database)
    expression = _FakeExpression()
    app = _App(
        bus=bus,
        inner_life=cast(InnerLifeFacade, _FakeInnerLife()),
        desire=cast(DesireFacade, _FakeDesire()),
        memory=cast(MemoryFacade, _FakeMemory()),
        activity=cast(ActivityFacade, _FakeActivity()),
        expression=cast(ExpressionFacade, expression),
        reading=cast(ReadingFacade, object()),
        evaluator=cast(Evaluator, object()),
        eval_store=cast(EvalStore, object()),
        config=Config(),
        presence_initialized=True,
        last_presence="away",
        presence_changed_at=100.0,
        away_started_at=100.0,
    )
    expression.app = app
    event = Event(
        id="user-return",
        timestamp=700.0,
        source=Source.EXTERNAL,
        type=EventType.USER_MESSAGE,
        content={"message": "我回来了"},
        correlation_id="user-return",
    )

    from nyx.runtime import on_user_message

    await on_user_message(app, event)
    first_return = app.pending_return
    observation = Event(
        id="observe-online",
        timestamp=701.0,
        source=Source.EXTERNAL,
        type=EventType.OBSERVATION_STATE,
        content={"presence": "online", "window_title": "编辑器"},
        correlation_id="observe-online",
    )
    await app.publish_observation(observation, 0.0)
    await bus.publish(
        Event(
            id="reply-return",
            timestamp=702.0,
            source=Source.INTERNAL,
            type=EventType.SPEAK,
            content={"content": "欢迎回来"},
            correlation_id="user-return",
        )
    )
    await on_user_message(app, event)

    assert expression.return_seen_at_reply == {
        "returned_at": 700.0,
        "away_duration_seconds": 600.0,
    }
    assert app.pending_return is first_return
    assert expression.replied == [("我回来了", "user-return")]
    await database.close()


async def test_first_user_message_only_establishes_presence_baseline() -> None:
    database = await connect(":memory:")
    bus = EventBus(database)
    expression = _FakeExpression()
    app = _App(
        bus=bus,
        inner_life=cast(InnerLifeFacade, _FakeInnerLife()),
        desire=cast(DesireFacade, _FakeDesire()),
        memory=cast(MemoryFacade, _FakeMemory()),
        activity=cast(ActivityFacade, _FakeActivity()),
        expression=cast(ExpressionFacade, expression),
        reading=cast(ReadingFacade, object()),
        evaluator=cast(Evaluator, object()),
        eval_store=cast(EvalStore, object()),
        config=Config(),
    )
    event = Event(
        id="first-user",
        timestamp=700.0,
        source=Source.EXTERNAL,
        type=EventType.USER_MESSAGE,
        content={"message": "你好"},
        correlation_id="first-user",
    )

    from nyx.runtime import on_user_message

    await on_user_message(app, event)

    assert app.last_presence == "online"
    assert app.pending_return is None
    await database.close()
