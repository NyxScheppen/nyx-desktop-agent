from typing import cast

from nyx.activity.lifecycle import ActivityLifecycle, activity_goal_signal
from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import ActivityStatus, ActivityType, EventType
from nyx.events.bus import EventBus
from nyx.types import Activity, Event


class _Store:
    def __init__(self) -> None:
        self.updated: list[Activity] = []
        self.activities: dict[str, Activity] = {}

    async def update(self, activity: Activity) -> None:
        self.updated.append(activity)
        self.activities[activity.id] = activity

    async def get(self, activity_id: str) -> Activity | None:
        return self.activities.get(activity_id)

    async def list_running(self) -> list[Activity]:
        return [
            activity for activity in self.activities.values()
            if activity.status is ActivityStatus.RUNNING
        ]

    async def list_unfinished(self) -> list[Activity]:
        return [
            activity for activity in self.activities.values()
            if activity.status in (ActivityStatus.PENDING, ActivityStatus.RUNNING)
        ]


class _Bus:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.events.append(event)


class _Desire:
    def __init__(self) -> None:
        self.active: list[str] = []
        self.suppressed: list[str] = []
        self.released: list[str] = []

    async def mark_active(self, desire_id: str) -> None:
        self.active.append(desire_id)

    async def mark_suppressed(self, desire_id: str) -> None:
        self.suppressed.append(desire_id)

    async def release_active(self, desire_id: str) -> None:
        self.released.append(desire_id)


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


def _typed_activity(
    id: str,
    type_: ActivityType,
    status: ActivityStatus = ActivityStatus.RUNNING,
) -> Activity:
    return Activity(
        id=id,
        type=type_,
        schedule_block_id="09:00",
        status=status,
        progress={
            "desire_id": f"d-{id}",
            "correlation_id": f"c-{id}",
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


async def test_recover_stale_running_pauses_resumable_and_suppresses_desire() -> None:
    lifecycle, store, _bus, desire = _lifecycle()
    reading = _typed_activity("reading", ActivityType.READING)
    creation = _typed_activity("creation", ActivityType.CREATION)
    exploration = _typed_activity("exploration", ActivityType.FREE_EXPLORATION)
    store.activities = {
        activity.id: activity for activity in (reading, creation, exploration)
    }

    recovered = await lifecycle.recover_stale_running()

    assert [a.id for a in recovered] == ["reading", "creation", "exploration"]
    assert all(a.status is ActivityStatus.PAUSED for a in recovered)
    assert desire.suppressed == ["d-reading", "d-creation", "d-exploration"]


async def test_recover_stale_running_abandons_non_resumable() -> None:
    lifecycle, store, _bus, desire = _lifecycle()
    rest = _typed_activity("rest", ActivityType.REST)
    observe = _typed_activity("observe", ActivityType.OBSERVE_USER)
    reflect = _typed_activity("reflect", ActivityType.IDLE_REFLECTION)
    store.activities = {activity.id: activity for activity in (rest, observe, reflect)}

    recovered = await lifecycle.recover_stale_running()

    assert [a.id for a in recovered] == ["rest", "observe", "reflect"]
    assert all(a.status is ActivityStatus.ABANDONED for a in recovered)
    assert desire.suppressed == ["d-rest", "d-observe", "d-reflect"]


async def test_recover_stale_pending_abandons_and_releases_desire() -> None:
    lifecycle, store, _bus, desire = _lifecycle()
    pending = _typed_activity("pending", ActivityType.READING, ActivityStatus.PENDING)
    store.activities[pending.id] = pending

    recovered = await lifecycle.recover_stale_running()

    assert recovered == [pending]
    assert pending.status is ActivityStatus.ABANDONED
    assert desire.released == ["d-pending"]


def test_activity_goal_signal_uses_explicit_none() -> None:
    activity = _activity(ActivityStatus.RUNNING)
    activity.progress["goal_signal"] = None
    activity.progress["result"] = {"completed": True}

    assert activity_goal_signal(activity) is None


async def test_interrupt_uses_activity_correlation_id() -> None:
    lifecycle, store, bus, _desire = _lifecycle()
    activity = _activity(ActivityStatus.RUNNING)
    store.activities[activity.id] = activity

    await lifecycle.interrupt(activity.id, EventType.USER_MESSAGE, None)

    assert bus.events[0].type is EventType.ACTIVITY_INTERRUPTED
    assert bus.events[0].correlation_id == "c1"
