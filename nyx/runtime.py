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
from nyx.events.bus import EventAdmissionBusyError
from nyx.events.event import internal_event
from nyx.expression.mutter import should_initiate_chat
from nyx.initiative import Initiative, ScheduledInteraction, Topic, scheduled_topic
from nyx.policy import policy
from nyx.types import ActivityContext, Event

if TYPE_CHECKING:
    from nyx.app_context import _Host

DEFAULT_REFLECT_MIN_INTERVAL = policy(
    "runtime", "runtime.DEFAULT_REFLECT_MIN_INTERVAL", float
)
DEFAULT_REFLECT_MIN_NEW_MEMORIES = policy(
    "runtime", "runtime.DEFAULT_REFLECT_MIN_NEW_MEMORIES", int
)


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
        await app.expression._archive_user_reply(event.id)
        return
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
                policy("runtime", "runtime.copy.on_user_message_text", str),
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
    service = getattr(app, "initiative", None)
    if not isinstance(service, Initiative):
        await app.desire.evaluate(state.energy, event_id=event.id)
        return
    topic = await service.candidate(time.time())
    created = await app.desire.evaluate(
        state.energy,
        event_id=event.id,
        initiative_ref=topic.encode() if topic else None,
    )
    if topic is not None:
        stage = (
            "desire_generated"
            if any(item.initiative_ref for item in created)
            else "no_desire"
        )
        _log_initiative(stage, topic.key)


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


def _log_initiative(stage: str, reference: str | None) -> None:
    logging.getLogger(__name__).info(
        policy("initiative", "copy.admission_log", str), stage, reference
    )


async def on_scheduled_interaction_check(app: Any, event: Event) -> None:
    _require_tick(event, TickType.SCHEDULED_INTERACTION_CHECK)
    service = getattr(app, "initiative", None)
    if isinstance(service, Initiative):
        async with service.expression_lock:
            await _process_scheduled(app, service)


async def _process_scheduled(app: Any, service: Initiative) -> None:
    for pending in await service.due(time.time()):
        current: ScheduledInteraction | None = pending
        while current is not None:
            item = await service.claim(current, time.time())
            current = None
            if item is None:
                break
            try:
                current = await _run_occurrence(app, service, item)
            except Exception:
                logging.getLogger(__name__).exception(
                    policy("initiative", "copy.schedule_error", str),
                    item.id,
                )
            finally:
                await service.release(item)


async def _run_occurrence(
    app: Any,
    service: Initiative,
    item: ScheduledInteraction,
) -> ScheduledInteraction | None:
    now = time.time()
    topic = scheduled_topic(item)
    outcome = None
    if now >= item.deadline:
        outcome = "missed"
    elif topic is not None and await service.topic_evidence(topic) is None:
        outcome = "invalid_source"
    elif item.origin == "autonomous" and (
        not service.autonomous_enabled
        or (topic is not None and await service.was_sent(topic))
    ):
        outcome = "skipped"
    elif (
        topic is not None
        and (await service.already_read(item, topic))
    ):
        outcome = "already_read"
    if outcome is not None:
        async with service.db.transaction():
            advanced = await service.finish(item, outcome, now)
            if advanced and outcome == "missed":
                updated = await service.get(item.id)
                if updated.status == "active" and updated.due_at <= now:
                    return updated
        _log_initiative(outcome, item.id)
        return None

    busy = bool(await app.expression.pending_questions() or await service.busy())
    can_initiate = app.can_initiate()
    if item.origin == "autonomous" and (busy or not can_initiate):
        _log_initiative("waiting_for_chat", item.id)
        return None

    state = await app.inner_life.get_state()
    if topic is not None and can_initiate and not busy and await service.eligible(now):
        desires = await app.desire.get_pending()
        match = next(
            (
                d
                for d in desires
                if d.type is DesireType.INTERACTION
                and d.initiative_ref
                and topic.matches(Topic.decode(d.initiative_ref))
            ),
            None,
        )
        latest = await app.expression.latest_initiate_chat_at()
        if (
            match is not None
            and not await service.was_sent(
                Topic.decode(match.initiative_ref), reading_questions=True,
            )
            and should_initiate_chat(
                [match],
                state.energy,
                now - (latest or 0),
                (await service.cooldown()) * 60,
            )
            and await app.desire.claim_for_interaction(match.id)
        ):
            try:
                if await app.expression.initiate_chat(match, state, scheduled=item):
                    _log_initiative("merged_committed", item.id)
                    return None
            finally:
                # A committed attempt owns the desire through reply or timeout.
                if not await app.expression.has_committed_desire(match.id):
                    await app.desire.release_interaction_claim(match.id)
    if await app.expression.initiate_chat(None, state, scheduled=item):
        _log_initiative("scheduled_committed", item.id)
    return None


async def on_reading_followup(app: Any, event: Event) -> None:
    service = getattr(app, "initiative", None)
    if (
        not isinstance(service, Initiative)
        or not service.autonomous_enabled
        or event.content.get("type") != ActivityType.READING.value
        or not app.can_initiate()
    ):
        return
    result = event.content.get("result")
    if not isinstance(result, dict):
        return
    reading = cast(dict[str, object], result)
    if reading.get("completed"):
        return
    book, paragraph = reading.get("book_id"), reading.get("target_paragraph")
    if not isinstance(book, str) or not isinstance(paragraph, int):
        return
    topic = Topic(book_id=book, paragraph=paragraph)
    if (
        await service.has_autonomous()
        or await service.user_reached(topic)
        or await service.was_sent(topic)
    ):
        return
    evidence = await service.topic_evidence(topic)
    if evidence is None:
        return
    try:
        await app.expression.consider_reading_followup(event, topic, evidence)
    except Exception:
        logging.getLogger(__name__).exception(
            policy("initiative", "copy.activity_schedule_error", str),
        )


async def check_initiate_chat(app: Any) -> None:
    """Start a desire-driven chat independently of the user's presence."""
    service = getattr(app, "initiative", None)
    if isinstance(service, Initiative):
        async with service.expression_lock:
            if await service.due(time.time()):
                await _process_scheduled(app, service)
            await _check_desire_chat(app, service)
    else:
        await _check_desire_chat(app, None)


async def _check_desire_chat(app: Any, service: Initiative | None) -> None:
    pending_method = cast(
        Callable[[], Awaitable[list[Any]]] | None,
        getattr(app.expression, "pending_questions", None),
    )
    if callable(pending_method) and await pending_method():
        _log_initiative("waiting_question", None)
        return
    if service is not None and await service.busy():
        _log_initiative("active_chat", None)
        return
    desires = await app.desire.get_pending()
    interaction = None
    for desire in desires:
        if desire.type is not DesireType.INTERACTION:
            continue
        if service is not None and desire.initiative_ref:
            topic = Topic.decode(desire.initiative_ref)
            if await service.was_sent(topic, reading_questions=True) or not (
                await service.eligible(time.time())
            ):
                _log_initiative("topic_unavailable", desire.id)
                continue
        interaction = desire
        break
    if interaction is None:
        _log_initiative("no_interaction_desire", None)
        return
    state = await app.inner_life.get_state()
    latest_method = cast(
        Callable[[], Awaitable[float | None]] | None,
        getattr(app.expression, "latest_initiate_chat_at", None),
    )
    latest = await latest_method() if callable(latest_method) else None
    last_chat_at = app.last_chat_at if latest is None else latest
    cooldown = await service.cooldown() * 60 if service is not None else None
    args = () if cooldown is None else (cooldown,)
    if not should_initiate_chat(
        desires, state.energy, time.time() - last_chat_at, *args
    ):
        stage = (
            "cooldown"
            if should_initiate_chat(desires, state.energy, 0, 0)
            else "energy"
        )
        _log_initiative(stage, interaction.id)
        return
    claim = cast(
        Callable[[str], Awaitable[bool]] | None,
        getattr(app.desire, "claim_for_interaction", None),
    )
    claimed = await claim(interaction.id) if callable(claim) else True
    if not claimed:
        _log_initiative("claim_unavailable", interaction.id)
        return
    if callable(getattr(app, "can_initiate", None)) and not app.can_initiate():
        release = cast(
            Callable[[str], Awaitable[bool]] | None,
            getattr(app.desire, "release_interaction_claim", None),
        )
        if callable(release):
            await release(interaction.id)
        _log_initiative("role_admission_closed", interaction.id)
        return
    try:
        committed = await app.expression.initiate_chat(interaction, state)
    except BaseException:
        release = cast(
            Callable[[str], Awaitable[bool]] | None,
            getattr(app.desire, "release_interaction_claim", None),
        )
        if callable(release) and (
            service is None
            or not await app.expression.has_committed_desire(interaction.id)
        ):
            await release(interaction.id)
        raise
    if not committed:
        _log_initiative("expression_skipped", interaction.id)
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
                policy("runtime", "runtime.copy.check_initiate_chat_text", str),
                interaction.id,
            )
    app.last_chat_at = time.time()
    _log_initiative("desire_committed", interaction.id)


async def check_reflect(
    app: Any,
    correlation_id: str,
    *,
    min_interval: float = DEFAULT_REFLECT_MIN_INTERVAL,
    min_new_memories: int = DEFAULT_REFLECT_MIN_NEW_MEMORIES,
) -> None:
    """Run the reflection gate for a reflection check tick."""
    narrative = await app.inner_life.get_document()
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
                        policy(
                            "runtime", "runtime.templates.check_reflect_text", str
                        ).format(p0=min_interval, p1=new_count)
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

    async def publish_tick(tick_type: TickType) -> None:
        event = root_event_factory(
            EventType.CLOCK_TICK, {"tick_type": tick_type.value}, Source.INTERNAL
        )
        attempt = 0
        while True:
            try:
                await app.bus.publish(event)
                return
            except EventAdmissionBusyError:
                delay = min(
                    policy("runtime", "runtime.admission_backoff_base", int)
                    ** min(
                        attempt,
                        policy(
                            "runtime", "runtime.admission_backoff_exponent_max", int
                        ),
                    ),
                    policy("runtime", "runtime.admission_backoff_max_seconds", int),
                )
                attempt += 1
                logging.getLogger(__name__).warning(
                    "Tick admission lock retry: tick=%s attempt=%d wait=%ds",
                    tick_type.value,
                    attempt,
                    delay,
                )
                await asyncio.sleep(delay)

    grid = app.config.activity.grid_minutes * 60.0
    last_block = 0.0
    last_mutter = last_chat = last_reflect = time.time()
    while True:
        now = time.time()
        service = getattr(app, "initiative", None)
        if (
            isinstance(service, Initiative)
            and not service.expression_lock.locked()
            and await service.due(now)
        ):
            await publish_tick(TickType.SCHEDULED_INTERACTION_CHECK)
        if now - last_block >= grid:
            await publish_tick(TickType.SCHEDULE_BLOCK_START)
            await publish_tick(TickType.DESIRE_EVAL)
            last_block = now
        if now - last_mutter >= mutter_check_interval:
            await publish_tick(TickType.MUTTER_CHECK)
            last_mutter = now
        if now - last_chat >= initiate_chat_interval:
            await publish_tick(TickType.INITIATE_CHAT_CHECK)
            last_chat = now
        if now - last_reflect >= reflect_check_interval:
            await publish_tick(TickType.REFLECTION_CHECK)
            last_reflect = now
        await app.expression.check_timeouts(now)
        await asyncio.sleep(tick_interval)


async def memory_loop(
    app: Any,
    *,
    interval: float = policy(
        "runtime", "runtime.parameters.memory_loop_interval", float
    ),
) -> None:
    """Settle conversation memory independently of chat and clock ticks."""
    while True:
        await asyncio.sleep(interval)
        try:
            await app.memory.maintain_conversation_segments()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).exception(
                policy("runtime", "runtime.copy.memory_loop_text", str)
            )


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
                    policy("runtime", "runtime.copy.supervise_bus_text_2", str),
                    failures,
                )
                raise
            logging.getLogger(__name__).exception(
                policy("runtime", "runtime.copy.supervise_bus_text", str),
                delay,
                failures,
                max_failures,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, backoff_max)


async def vision_loop(app: Any) -> None:
    """Keep legacy standalone vision dormant until a native desktop connects."""
    from nyx.activity.screen import ScreenObserver, capture_screen
    from nyx.llm.vision import VisionClient

    if not app.config.vision.enabled:
        return
    while True:
        if not app.observation_enabled():
            app.clear_desktop_observation()
            app.screen_observer = None
            await asyncio.sleep(policy("runtime", "runtime.idle_poll_seconds", int))
            continue
        if app.screen_observer is None:
            vision = VisionClient.from_config(app.config.vision)
            app.screen_observer = ScreenObserver(
                capture_screen, vision.describe, app.config.vision.interval_seconds
            )
        generation = app.desktop_generation
        summary = await app.screen_observer.sample_once()
        if (
            summary and app.observation_enabled()
            and generation == app.desktop_generation
        ):
            app.last_screen_summary = summary
        await asyncio.sleep(app.config.vision.interval_seconds)


async def run_character(host: "_Host", identity: str) -> None:
    """A fatal role loop closes admission for this role, without exiting the host."""
    app = host.apps[identity]
    tasks = [
        asyncio.create_task(
            supervise_bus(
                app,
                backoff_base=1,
                backoff_max=policy(
                    "runtime", "runtime.parameters.run_character_backoff_max", int
                ),
                max_failures=policy(
                    "runtime", "runtime.parameters.run_character_max_failures", int
                ),
                recovery_streak=policy(
                    "runtime", "runtime.parameters.run_character_recovery_streak", int
                ),
            )
        ),
        asyncio.create_task(
            tick_loop(
                app,
                tick_interval=policy(
                    "runtime", "runtime.parameters.run_character_tick_interval", int
                ),
                mutter_check_interval=policy(
                    "runtime",
                    "runtime.parameters.run_character_mutter_check_interval",
                    int,
                ),
                initiate_chat_interval=policy(
                    "runtime",
                    "runtime.parameters.run_character_initiate_chat_interval",
                    int,
                ),
                reflect_check_interval=policy(
                    "runtime",
                    "runtime.parameters.run_character_reflect_check_interval",
                    int,
                ),
            )
        ),
        asyncio.create_task(memory_loop(app)),
    ]
    app.tick_task = tasks[1]
    app.memory_task = tasks[2]
    if app.transport is not None:
        app.transport_task = asyncio.create_task(app.transport.run())
        tasks.append(app.transport_task)
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        stopping = host.closing or host.records[identity]["status"] == "STOPPING"
        for task in done:
            if stopping and task is not tasks[0] and task.cancelled():
                continue
            task.result()
        if stopping:
            # Stopping the clock must leave accepted deliveries alive for drain.
            await tasks[0]
        elif host.records[identity]["status"] == "RUNNING":
            raise RuntimeError(
                policy("runtime", "runtime.copy.run_character_text_2", str)
            )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        record = dict(host.records[identity])
        if record["status"] == "RUNNING":
            record.update(status="FAILED", error=f"{type(error).__name__}: {error}")
            await host.save_record(record)
        await app.reading.quiesce()
        await app.activity.quiesce()
        logging.getLogger(__name__).exception(
            policy("runtime", "runtime.copy.run_character_text", str), identity
        )
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def stop_character(host: "_Host", identity: str) -> bool:
    """Bounded stop; never close a DB underneath a cancellation-resistant writer."""
    app = host.apps[identity]
    if app.transport is not None:
        await app.transport.quiesce()
    if app.transport_task is not None:
        app.transport_task.cancel()
        _, pending = await asyncio.wait(
            [app.transport_task],
            timeout=policy("runtime", "runtime.parameters.stop_character_timeout", int),
        )
        if pending:
            return False
    if app.tick_task is not None:
        app.tick_task.cancel()
        _, pending = await asyncio.wait(
            [app.tick_task],
            timeout=policy(
                "runtime", "runtime.parameters.stop_character_timeout_2", int
            ),
        )
        if pending:
            return False
    if app.memory_task is not None:
        app.memory_task.cancel()
        _, pending = await asyncio.wait(
            [app.memory_task],
            timeout=policy(
                "runtime", "runtime.parameters.stop_character_timeout_3", int
            ),
        )
        if pending:
            return False
    await app.reading.quiesce()
    await app.activity.quiesce()
    live = set(host.requests.get(identity, set())) | set(app.reading._background_tasks)
    if app.activity._task is not None:
        live.add(app.activity._task)
    live = {task for task in live if not task.done()}
    if live:
        _, pending = await asyncio.wait(
            live,
            timeout=policy(
                "runtime", "runtime.parameters.stop_character_timeout_4", int
            ),
        )
        for task in pending:
            task.cancel()
        if pending:
            _, pending = await asyncio.wait(
                pending,
                timeout=policy(
                    "runtime", "runtime.parameters.stop_character_timeout_7", int
                ),
            )
            if pending:
                return False
    await app.bus.quiesce()
    if app.bus._running and not await app.bus.drain(
        timeout=policy("runtime", "runtime.parameters.stop_character_timeout_8", int)
    ):
        return False
    lifecycle = host.lifecycle.get(identity)
    if lifecycle is not None and not lifecycle.done():
        lifecycle.cancel()
        _, pending = await asyncio.wait(
            [lifecycle],
            timeout=policy(
                "runtime", "runtime.parameters.stop_character_timeout_5", int
            ),
        )
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
        _, pending = await asyncio.wait(
            children,
            timeout=policy(
                "runtime", "runtime.parameters.stop_character_timeout_6", int
            ),
        )
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
        host.expire_desktop()
        for identity in list(host.apps):
            if host.records[identity]["status"] == "RUNNING":
                previous = host.presence_tasks.get(identity)
                if previous is None or previous.done():
                    if previous is not None and not previous.cancelled():
                        try:
                            previous.result()
                        except Exception:
                            logging.getLogger(__name__).exception(
                                policy(
                                    "runtime",
                                    "runtime.copy.host_presence_loop_text",
                                    str,
                                ),
                                identity,
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
                                policy(
                                    "runtime",
                                    "runtime.copy.host_presence_loop_text_2",
                                    str,
                                ),
                                identity,
                            )
                    host.stopping[identity] = asyncio.create_task(host.unload(identity))
        await asyncio.sleep(
            policy(
                "runtime", "runtime.parameters.host_presence_loop_value_factor", float
            )
        )


async def host_vision_loop(host: "_Host") -> None:
    """Discard a screen result if selection changed during generation."""
    from nyx.activity.screen import ScreenObserver, capture_screen
    from nyx.llm.vision import VisionClient

    observer: ScreenObserver | None = None
    while True:
        if not host.desktop_connected():
            observer = None
            await asyncio.sleep(policy("runtime", "runtime.idle_poll_seconds", int))
            continue
        if observer is None:
            vision = VisionClient.from_config(host.config.vision)
            observer = ScreenObserver(
                capture_screen, vision.describe, host.config.vision.interval_seconds
            )
        identity, revision = host.current_id, host.revision
        generation = host.desktop_generation
        summary = await observer.sample_once()
        if (
            summary and host.desktop_connected()
            and generation == host.desktop_generation
            and (identity, revision) == (host.current_id, host.revision)
        ):
            app = host.apps.get(identity)
            if app is not None and app.can_initiate():
                app.last_screen_summary = summary
        await asyncio.sleep(host.config.vision.interval_seconds)
