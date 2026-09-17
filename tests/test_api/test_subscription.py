# pyright: reportPrivateUsage=false
import asyncio
import contextlib
from typing import cast

from nyx.activity.facade import ActivityFacade
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

    async def reply(self, msg: str, correlation_id: str) -> None:
        self.replied.append((msg, correlation_id))


class _FakeMemory:
    def __init__(self) -> None:
        self.remembered: list[Event] = []

    async def remember_activity(
        self, event: Event, consumer_id: str | None = None
    ) -> None:
        del consumer_id
        self.remembered.append(event)


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
