"""应用后台运行循环：tick、EventBus 监督与屏幕视觉采样。"""
import asyncio
import logging
import time
import uuid
from typing import Any, Callable

from nyx.enums import EventType, Source, TickType
from nyx.types import Event


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
