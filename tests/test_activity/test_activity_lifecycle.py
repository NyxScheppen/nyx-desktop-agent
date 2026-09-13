from typing import cast

from nyx.activity.lifecycle import ActivityLifecycle
from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import ActivityStatus, ActivityType, EventType
from nyx.events.bus import EventBus
from nyx.types import Activity, Event


class _Store:
    def __init__(self) -> None:
        self.updated: list[Activity] = []

    async def update(self, activity: Activity) -> None:
        self.updated.append(activity)


class _Bus:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.events.append(event)


class _Desire:
    def __init__(self) -> None:
        self.active: list[str] = []
        self.suppressed: list[str] = []

    async def mark_active(self, desire_id: str) -> None:
        self.active.append(desire_id)

    async def mark_suppressed(self, desire_id: str) -> None:
        self.suppressed.append(desire_id)


def _activity(status: ActivityStatus = ActivityStatus.PENDING) -> Activity:
    return Activity(
        id="a1",
        type=ActivityType.READING,
        schedule_block_id="09:00",
        status=status,
        progress={
            "desire_id": "d1",
            "correlation_id": "c1",
            "goal": {"action": "read"},
        },
        started_at=1.0,
    )


def _lifecycle() -> tuple[ActivityLifecycle, _Store, _Bus, _Desire]:
    store = _Store()
    bus = _Bus()
    desire = _Desire()
    lifecycle = ActivityLifecycle(
        cast(ActivityStore, store),
        cast(EventBus, bus),
        cast(DesireFacade, desire),
        ActivityConfig(),
    )
    return lifecycle, store, bus, desire


async def test_start_marks_running_and_publishes_event() -> None:
    lifecycle, store, bus, desire = _lifecycle()
    activity = _activity()

    await lifecycle.start(activity)

    assert activity.status is ActivityStatus.RUNNING
    assert store.updated == [activity]
    assert desire.active == ["d1"]
    assert bus.events[0].type is EventType.ACTIVITY_START
    assert bus.events[0].correlation_id == "c1"


async def test_complete_marks_completed_and_publishes_goal_result() -> None:
    lifecycle, store, bus, _desire = _lifecycle()
    activity = _activity(ActivityStatus.RUNNING)
    activity.progress["result"] = {"completed": True, "book": "b", "note": "n"}

    await lifecycle.complete(activity)

    assert activity.status is ActivityStatus.COMPLETED
    assert store.updated == [activity]
    assert bus.events[0].type is EventType.ACTIVITY_END
    assert bus.events[0].content["goal_met"] is True
    assert bus.events[0].content["energy_delta"] == -20


async def test_fail_marks_incomplete_and_suppresses_desire() -> None:
    lifecycle, store, _bus, desire = _lifecycle()
    activity = _activity(ActivityStatus.RUNNING)

    await lifecycle.fail(activity)

    assert activity.status is ActivityStatus.INCOMPLETE
    assert store.updated == [activity]
    assert desire.suppressed == ["d1"]
