"""应用后台运行循环：tick、EventBus 监督与屏幕视觉采样。"""

# pyright: reportPrivateUsage=false
import asyncio
import inspect
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from nyx.enums import (
    ActivityStatus,
    ActivityType,
    DesireType,
    EventType,
    Source,
    TickType,
)
from nyx.events.event import internal_event
from nyx.expression.mutter import should_initiate_chat
from nyx.types import ActivityContext, Event

if TYPE_CHECKING:
    from nyx.app_context import _Host

DEFAULT_REFLECT_MIN_INTERVAL = 21600.0
DEFAULT_REFLECT_MIN_NEW_MEMORIES = 3


def root_event(
    type_: EventType,
    content: dict[str, Any],
    source: Source = Source.EXTERNAL,
) -> Event:
    """Build a root event with its own correlation id."""
    event_id = str(uuid.uuid4())
    return Event(
        id=event_id,
        timestamp=time.time(),
        source=source,
        type=type_,
        content=content,
        correlation_id=event_id,
    )


async def on_user_message(app: Any, event: Event) -> None:
    """Interrupt a running activity, then hand the message to expression."""
    previous = await app.bus.list_events(correlation_id=event.correlation_id)
    if any(item.type in (EventType.SPEAK, EventType.ASK) for item in previous):
        return
    if getattr(app, "host", None) is None:
        await app.record_user_online(event.timestamp)
    current = await app.activity.get_current()
    interrupted = (
        current
        if (
            current is not None
            and current.status is ActivityStatus.RUNNING
            and current.type is not ActivityType.BROWSER_SURVEY
        )
        else None
    )
    if interrupted is not None:
        try:
            await app.activity.interrupt(interrupted.id, EventType.USER_MESSAGE)
        except Exception:
            logging.getLogger(__name__).exception(
                "用户消息打断活动失败，表达上下文不得声称已暂停 activity_id=%s",
                interrupted.id,
            )
    after_interrupt = await app.activity.get_current()
    activity_context = ActivityContext(
        current=after_interrupt,
        interrupted=interrupted,
        observed_at=time.time(),
    )
    reply_to = event.content.get("reply_to")
    reply_method = app.expression.reply
    supports_context = False
    supports_discussion = False
    supports_annotation = False
    try:
        signature = inspect.signature(reply_method)
        supports_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        supports_context = "activity_context" in signature.parameters or supports_kwargs
        supports_discussion = "discussion" in signature.parameters or supports_kwargs
        supports_annotation = "annotation" in signature.parameters or supports_kwargs
    except (TypeError, ValueError):
        supports_context = True
        supports_discussion = True
        supports_annotation = True
    discussion = event.content.get("discussion")
    discussion_kwargs: dict[str, Any] = (
        {"discussion": discussion}
        if isinstance(discussion, bool) and supports_discussion else {}
    )
    annotation = event.content.get("annotation")
    if isinstance(annotation, dict) and supports_annotation:
        discussion_kwargs["annotation"] = annotation
    if isinstance(reply_to, str):
        if supports_context:
            await reply_method(
                event.content["message"],
                event.correlation_id,
                reply_to,
                activity_context=activity_context,
                **discussion_kwargs,
            )
        else:
            await reply_method(
                event.content["message"], event.correlation_id, reply_to,
                **discussion_kwargs,
            )
    else:
        if supports_context:
            await reply_method(
                event.content["message"],
                event.correlation_id,
                activity_context=activity_context,
                **discussion_kwargs,
            )
        else:
            await reply_method(
                event.content["message"], event.correlation_id,
                **discussion_kwargs,
            )


async def on_schedule_block_start(app: Any, event: Event) -> None:
    """Consume a schedule tick for the activity subsystem."""
    _require_tick(event, TickType.SCHEDULE_BLOCK_START)
    await app.activity.on_tick(TickType.SCHEDULE_BLOCK_START)


async def on_desire_eval(app: Any, event: Event) -> None:
    """Consume a desire evaluation tick."""
    _require_tick(event, TickType.DESIRE_EVAL)
    state = await app.inner_life.get_state()
    await app.desire.evaluate(state.energy, event_id=event.id)


async def on_mutter_check(app: Any, event: Event) -> None:
    """Consume a mutter check tick."""
    _require_tick(event, TickType.MUTTER_CHECK)
    state = await app.inner_life.get_state()
    if callable(getattr(app, "can_initiate", None)) and not app.can_initiate():
        return
    await app.expression.mutter(state, event.correlation_id)


async def on_initiate_chat_check(app: Any, event: Event) -> None:
    """Consume an initiative check tick."""
    _require_tick(event, TickType.INITIATE_CHAT_CHECK)
    await check_initiate_chat(app)


async def on_reflection_check(app: Any, event: Event) -> None:
    """Consume a reflection check tick."""
    _require_tick(event, TickType.REFLECTION_CHECK)
    await check_reflect(app, event.correlation_id)


async def check_initiate_chat(app: Any) -> None:
    """Start a desire-driven chat when presence and energy permit it."""
    pending_method = cast(
        Callable[[], Awaitable[list[Any]]] | None,
        getattr(app.expression, "pending_questions", None),
    )
    if callable(pending_method) and await pending_method():
        return
    desires = await app.desire.get_pending()
    interaction = next(
        (desire for desire in desires if desire.type is DesireType.INTERACTION), None
    )
    if interaction is None:
        return
    state = await app.inner_life.get_state()
    online = app.last_presence in ("online", "busy")
    busy = app.last_presence == "busy"
    latest_method = cast(
        Callable[[], Awaitable[float | None]] | None,
        getattr(app.expression, "latest_initiate_chat_at", None),
    )
    latest = await latest_method() if callable(latest_method) else None
    last_chat_at = app.last_chat_at if latest is None else latest
    if not should_initiate_chat(
        desires, online, busy, state.energy, time.time() - last_chat_at
    ):
        return
    claim = cast(
        Callable[[str], Awaitable[bool]] | None,
        getattr(app.desire, "claim_for_interaction", None),
    )
    claimed = await claim(interaction.id) if callable(claim) else True
    if not claimed:
        return
    if callable(getattr(app, "can_initiate", None)) and not app.can_initiate():
        release = cast(
            Callable[[str], Awaitable[bool]] | None,
            getattr(app.desire, "release_interaction_claim", None),
        )
        if callable(release):
            await release(interaction.id)
        return
    try:
        committed = await app.expression.initiate_chat(interaction, state)
    except Exception:
        release = cast(
            Callable[[str], Awaitable[bool]] | None,
            getattr(app.desire, "release_interaction_claim", None),
        )
        if callable(release):
            await release(interaction.id)
        raise
    if not committed:
        release = cast(
            Callable[[str], Awaitable[bool]] | None,
            getattr(app.desire, "release_interaction_claim", None),
        )
        if callable(release):
            await release(interaction.id)
        return
    current = await app.activity.get_current()
    if (
        current is not None
        and current.status is ActivityStatus.RUNNING
        and current.type is not ActivityType.BROWSER_SURVEY
    ):
        try:
            await app.activity.interrupt(current.id, EventType.INITIATE_CHAT)
        except Exception:
            logging.getLogger(__name__).exception(
                "主动搭话已提交但活动打断失败，等待后续补偿 desire_id=%s",
                interaction.id,
            )
    app.last_chat_at = time.time()


async def check_reflect(
    app: Any,
    correlation_id: str,
    *,
    min_interval: float = DEFAULT_REFLECT_MIN_INTERVAL,
    min_new_memories: int = DEFAULT_REFLECT_MIN_NEW_MEMORIES,
) -> None:
    """Run the reflection gate for a reflection check tick."""
    narrative = await app.inner_life.get_narrative()
    if time.time() - narrative.updated_at < min_interval:
        return
    new_count = await app.memory.count_new(None, narrative.updated_at)
    if new_count >= min_new_memories:
        await app.bus.publish(
            internal_event(
                EventType.REFLECTION,
                {
                    "reason": "periodic",
                    "narrative_updated_at": narrative.updated_at,
                    "min_interval": min_interval,
                    "min_new_memories": min_new_memories,
                    "new_memory_count": new_count,
                    "evidence": (
                        f"距上次反思已满 {min_interval:g} 秒，期间新增 "
                        f"{new_count} 条记忆"
                    ),
                },
                correlation_id,
            )
        )


def _require_tick(event: Event, expected: TickType) -> None:
    actual = event.content.get("tick_type")
    if actual != expected.value:
        raise ValueError(f"tick consumer {expected.value!r} received {actual!r}")


async def tick_loop(
    app: Any,
    *,
    tick_interval: float,
    mutter_check_interval: float,
    initiate_chat_interval: float,
    reflect_check_interval: float,
    root_event_factory: Callable[..., Event] = root_event,
) -> None:
    """Publish clock ticks and run expression timeout maintenance."""
    grid = app.config.activity.grid_minutes * 60.0
    last_block = 0.0
    last_mutter = last_chat = last_reflect = time.time()
    while True:
        now = time.time()
        if now - last_block >= grid:
            await app.bus.publish(
                root_event_factory(
                    EventType.CLOCK_TICK,
                    {"tick_type": TickType.SCHEDULE_BLOCK_START.value},
                    Source.INTERNAL,
                )
            )
            await app.bus.publish(
                root_event_factory(
                    EventType.CLOCK_TICK,
                    {"tick_type": TickType.DESIRE_EVAL.value},
                    Source.INTERNAL,
                )
            )
            last_block = now
        if now - last_mutter >= mutter_check_interval:
            await app.bus.publish(
                root_event_factory(
                    EventType.CLOCK_TICK,
                    {"tick_type": TickType.MUTTER_CHECK.value},
                    Source.INTERNAL,
                )
            )
            last_mutter = now
        if now - last_chat >= initiate_chat_interval:
            await app.bus.publish(
                root_event_factory(
                    EventType.CLOCK_TICK,
                    {"tick_type": TickType.INITIATE_CHAT_CHECK.value},
                    Source.INTERNAL,
                )
            )
            last_chat = now
        if now - last_reflect >= reflect_check_interval:
            await app.bus.publish(
                root_event_factory(
                    EventType.CLOCK_TICK,
                    {"tick_type": TickType.REFLECTION_CHECK.value},
                    Source.INTERNAL,
                )
            )
            last_reflect = now
        await app.expression.check_timeouts(now)
        await asyncio.sleep(tick_interval)


async def memory_loop(app: Any, *, interval: float = 60.0) -> None:
    """Settle conversation memory independently of chat and clock ticks."""
    while True:
        await asyncio.sleep(interval)
        try:
            await app.memory.maintain_conversation_segments()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).exception("对话记忆定时维护失败")


async def supervise_bus(
    app: Any,
    *,
    backoff_base: float,
    backoff_max: float,
    max_failures: int,
    recovery_streak: int,
) -> None:
    """Restart EventBus with exponential backoff and a fatal-failure cutoff."""
    failures = 0
    delay = backoff_base
    last_persisted = app.bus.persisted_count
    while True:
        try:
            await app.bus.run()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            if app.bus.persisted_count - last_persisted >= recovery_streak:
                failures = 0
                delay = backoff_base
            last_persisted = app.bus.persisted_count
            failures += 1
            if failures >= max_failures:
                logging.getLogger(__name__).critical(
                    "角色总线连续 %d 次异常，停止该运行时", failures
                )
                raise
            logging.getLogger(__name__).exception(
                "总线 run() 异常终止，%.1fs 后重启（第 %d/%d 次）",
                delay,
                failures,
                max_failures,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, backoff_max)


async def vision_loop(app: Any) -> None:
    """Run the optional screen observer and update the app's latest summary."""

    def update(summary: str) -> None:
        app.last_screen_summary = summary

    observer = app.screen_observer
    if observer is not None:
        await observer.run(update)


async def run_character(host: "_Host", identity: str) -> None:
    """A fatal role loop closes admission for this role, without exiting the host."""
    app = host.apps[identity]
    tasks = [
        asyncio.create_task(
            supervise_bus(
                app,
                backoff_base=1,
                backoff_max=30,
                max_failures=8,
                recovery_streak=3,
            )
        ),
        asyncio.create_task(
            tick_loop(
                app,
                tick_interval=60,
                mutter_check_interval=150,
                initiate_chat_interval=300,
                reflect_check_interval=3600,
            )
        ),
        asyncio.create_task(memory_loop(app)),
    ]
    app.tick_task = tasks[1]
    app.memory_task = tasks[2]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        stopping = host.closing or host.records[identity]["status"] == "STOPPING"
        for task in done:
            if stopping and task in (tasks[1], tasks[2]) and task.cancelled():
                continue
            task.result()
        if stopping:
            # Stopping the clock must leave accepted deliveries alive for drain.
            await tasks[0]
        elif host.records[identity]["status"] == "RUNNING":
            raise RuntimeError("角色后台循环意外结束")
    except asyncio.CancelledError:
        raise
    except Exception as error:
        record = dict(host.records[identity])
        if record["status"] == "RUNNING":
            record.update(status="FAILED", error=str(error))
            await host.save_record(record)
        await app.reading.quiesce()
        await app.activity.quiesce()
        logging.getLogger(__name__).exception("角色运行失败 character_id=%s", identity)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def stop_character(host: "_Host", identity: str) -> bool:
    """Bounded stop; never close a DB underneath a cancellation-resistant writer."""
    app = host.apps[identity]
    if app.tick_task is not None:
        app.tick_task.cancel()
        _, pending = await asyncio.wait([app.tick_task], timeout=5)
        if pending:
            return False
    if app.memory_task is not None:
        app.memory_task.cancel()
        _, pending = await asyncio.wait([app.memory_task], timeout=5)
        if pending:
            return False
    await app.reading.quiesce()
    await app.activity.quiesce()
    live = set(host.requests.get(identity, set())) | set(app.reading._background_tasks)
    if app.activity._task is not None:
        live.add(app.activity._task)
    live = {task for task in live if not task.done()}
    if live:
        _, pending = await asyncio.wait(live, timeout=10)
        for task in pending:
            task.cancel()
        if pending:
            _, pending = await asyncio.wait(pending, timeout=5)
            if pending:
                return False
    await app.bus.quiesce()
    if app.bus._running and not await app.bus.drain(timeout=15):
        return False
    lifecycle = host.lifecycle.get(identity)
    if lifecycle is not None and not lifecycle.done():
        lifecycle.cancel()
        _, pending = await asyncio.wait([lifecycle], timeout=5)
        if pending:
            return False
    # A cancelled consumer can still have a handler unwinding.
    if any(not task.done() for task in app.bus._workers.values()):
        return False
    # Admitted requests/handlers can create a runner after the first snapshot.
    # Their producers have now stopped; collect and settle their final children.
    await app.reading.quiesce()
    await app.activity.quiesce()
    children = set(app.reading._background_tasks)
    if app.activity._task is not None:
        children.add(app.activity._task)
    children = {task for task in children if not task.done()}
    for task in children:
        task.cancel()
    if children:
        _, pending = await asyncio.wait(children, timeout=5)
        if pending:
            return False
    # This instance has no remaining writers. Unlike a process crash, its old
    # processing leases can be released now, before an immediate restore.
    assert app.database is not None
    async with app.database.transaction() as conn:
        await conn.execute(
            "UPDATE event_delivery SET status='pending', started_at=NULL, "
            "lease_until=NULL WHERE status='processing'"
        )
    return True


async def host_presence_loop(host: "_Host") -> None:
    """Fanout recipients independently and finish interrupted unloads."""
    while True:
        for identity in list(host.apps):
            if host.records[identity]["status"] == "RUNNING":
                previous = host.presence_tasks.get(identity)
                if previous is None or previous.done():
                    if previous is not None and not previous.cancelled():
                        try:
                            previous.result()
                        except Exception:
                            logging.getLogger(__name__).exception(
                                "在线补投任务失败 character_id=%s", identity
                            )
                    host.presence_tasks[identity] = asyncio.create_task(
                        host.flush_presence(identity)
                    )
        for identity in list(host.records):
            if host.records[identity]["status"] == "STOPPING":
                previous = host.stopping.get(identity)
                if previous is None or previous.done():
                    if previous is not None and not previous.cancelled():
                        try:
                            previous.result()
                        except Exception:
                            logging.getLogger(__name__).exception(
                                "角色停止任务失败 character_id=%s", identity
                            )
                    host.stopping[identity] = asyncio.create_task(host.unload(identity))
        await asyncio.sleep(0.5)


async def host_vision_loop(host: "_Host") -> None:
    """Discard a screen result if selection changed during generation."""
    from nyx.activity.screen import ScreenObserver, capture_screen
    from nyx.llm.vision import VisionClient

    vision = VisionClient.from_config(host.config.vision)
    observer = ScreenObserver(
        capture_screen, vision.describe, host.config.vision.interval_seconds
    )
    while True:
        identity, revision = host.current_id, host.revision
        summary = await observer.sample_once()
        if summary and (identity, revision) == (host.current_id, host.revision):
            app = host.apps.get(identity)
            if app is not None and app.can_initiate():
                app.last_screen_summary = summary
        await asyncio.sleep(host.config.vision.interval_seconds)
