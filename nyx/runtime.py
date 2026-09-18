"""应用后台运行循环：tick、EventBus 监督与屏幕视觉采样。"""
import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

from nyx.enums import ActivityStatus, DesireType, EventType, Source, TickType
from nyx.events.event import internal_event
from nyx.expression.mutter import should_initiate_chat
from nyx.types import Event

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
    await app.record_user_online(event.timestamp)
    current = await app.activity.get_current()
    if current is not None and current.status is ActivityStatus.RUNNING:
        await app.activity.interrupt(current.id, EventType.USER_MESSAGE)
    reply_to = event.content.get("reply_to")
    page_id = event.content.get("browsing_page_id")
    if isinstance(page_id, str):
        context = await app.browsing.get_prompt_context(page_id)
        await app.expression.reply(
            event.content["message"], event.correlation_id,
            reply_to if isinstance(reply_to, str) else None,
            browsing_context=context,
        )
        return
    if isinstance(reply_to, str):
        await app.expression.reply(
            event.content["message"], event.correlation_id, reply_to
        )
    else:
        await app.expression.reply(event.content["message"], event.correlation_id)


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
    await app.expression.mutter(
        await app.inner_life.get_state(), event.correlation_id
    )


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
    if current is not None and current.status is ActivityStatus.RUNNING:
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
            internal_event(EventType.REFLECTION, {}, correlation_id)
        )


def _require_tick(event: Event, expected: TickType) -> None:
    actual = event.content.get("tick_type")
    if actual != expected.value:
        raise ValueError(
            f"tick consumer {expected.value!r} received {actual!r}"
        )


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
            await app.bus.publish(root_event_factory(
                EventType.CLOCK_TICK,
                {"tick_type": TickType.SCHEDULE_BLOCK_START.value},
                Source.INTERNAL,
            ))
            await app.bus.publish(root_event_factory(
                EventType.CLOCK_TICK,
                {"tick_type": TickType.DESIRE_EVAL.value},
                Source.INTERNAL,
            ))
            last_block = now
        if now - last_mutter >= mutter_check_interval:
            await app.bus.publish(root_event_factory(
                EventType.CLOCK_TICK,
                {"tick_type": TickType.MUTTER_CHECK.value},
                Source.INTERNAL,
            ))
            last_mutter = now
        if now - last_chat >= initiate_chat_interval:
            await app.bus.publish(root_event_factory(
                EventType.CLOCK_TICK,
                {"tick_type": TickType.INITIATE_CHAT_CHECK.value},
                Source.INTERNAL,
            ))
            last_chat = now
        if now - last_reflect >= reflect_check_interval:
            await app.bus.publish(root_event_factory(
                EventType.CLOCK_TICK,
                {"tick_type": TickType.REFLECTION_CHECK.value},
                Source.INTERNAL,
            ))
            last_reflect = now
        await app.expression.check_timeouts(now)
        await asyncio.sleep(tick_interval)


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
                    "总线连续 %d 次异常，判定致命，终止进程", failures
                )
                raise
            logging.getLogger(__name__).exception(
                "总线 run() 异常终止，%.1fs 后重启（第 %d/%d 次）",
                delay, failures, max_failures,
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
