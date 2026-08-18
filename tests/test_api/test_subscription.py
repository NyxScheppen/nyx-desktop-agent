# pyright: reportPrivateUsage=false
import asyncio
import contextlib
from typing import cast

from nyx.activity.facade import ActivityFacade
from nyx.config import Config
from nyx.db import connect
from nyx.desire.facade import DesireFacade
from nyx.enums import EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.routing import ROUTING
from nyx.expression.facade import ExpressionFacade
from nyx.inner_life.facade import InnerLifeFacade
from nyx.main import _App, _root_event, _subscribe
from nyx.memory.facade import MemoryFacade
from nyx.types import Event


class _FakeInnerLife:
    def __init__(self) -> None:
        self.applied: list[Event] = []

    async def apply_event(self, event: Event) -> None:
        self.applied.append(event)


class _FakeDesire:
    def __init__(self) -> None:
        self.added: list[Event] = []

    async def add_value(self, event: Event) -> None:
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
    app = _App(
        bus=bus,
        inner_life=cast(InnerLifeFacade, inner_life),
        desire=cast(DesireFacade, desire),
        memory=cast(MemoryFacade, object()),
        activity=cast(ActivityFacade, activity),
        expression=cast(ExpressionFacade, expression),
        evaluator=cast(Evaluator, object()),
        config=Config(),
    )
    _subscribe(app)

    task = asyncio.create_task(bus.run())
    try:
        for event_type, consumers in ROUTING.items():
            if consumers:
                await bus.publish(_root_event(event_type, _content(event_type)))
        await asyncio.wait_for(bus._queue.join(), timeout=1.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await database.conn.close()

    assert len(expression.replied) == 1
    assert len(inner_life.applied) == 4
    assert len(desire.added) == 2
    assert len(activity.generated) == 1
