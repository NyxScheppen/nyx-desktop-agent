from dataclasses import dataclass

from nyx.enums import EventType, TickType

_DELIVERY_MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class RouteSpec:
    """One durable consumer route for an event or a clock tick."""

    event_type: EventType
    consumer_id: str
    module: str
    handler_key: str
    tick_type: TickType | None = None
    max_attempts: int = _DELIVERY_MAX_ATTEMPTS


# The route list is the runtime source of truth. ROUTING and TICK_ROUTING below
# are derived compatibility views used by docs, diagnostics, and existing callers.
ROUTE_SPECS: tuple[RouteSpec, ...] = (
    RouteSpec(
        EventType.USER_MESSAGE,
        "expression.user_message",
        "expression",
        "on_user_message",
    ),
    RouteSpec(
        EventType.OBSERVATION_STATE,
        "inner_life.observation_state",
        "inner_life",
        "apply_observation_state",
    ),
    RouteSpec(
        EventType.OBSERVATION_STATE,
        "desire.observation_state",
        "desire",
        "apply_observation_state",
    ),
    RouteSpec(
        EventType.DESIRE_GENERATED,
        "activity.desire_generated",
        "activity",
        "on_desire_generated",
    ),
    RouteSpec(
        EventType.DESIRE_SATISFIED,
        "inner_life.desire_satisfied",
        "inner_life",
        "apply_desire_satisfied",
    ),
    RouteSpec(
        EventType.ACTIVITY_END,
        "desire.activity_end",
        "desire",
        "apply_activity_end",
    ),
    RouteSpec(
        EventType.ACTIVITY_END,
        "inner_life.activity_end",
        "inner_life",
        "apply_activity_end",
    ),
    RouteSpec(
        EventType.ACTIVITY_END,
        "memory.activity_end",
        "memory",
        "remember_activity",
    ),
    RouteSpec(
        EventType.REFLECTION,
        "inner_life.reflection",
        "inner_life",
        "apply_reflection",
    ),
    RouteSpec(
        EventType.CLOCK_TICK,
        "activity.schedule_block_start",
        "activity",
        "on_schedule_block_start",
        TickType.SCHEDULE_BLOCK_START,
    ),
    RouteSpec(
        EventType.CLOCK_TICK,
        "desire.desire_eval",
        "desire",
        "on_desire_eval",
        TickType.DESIRE_EVAL,
    ),
    RouteSpec(
        EventType.CLOCK_TICK,
        "expression.mutter_check",
        "expression",
        "on_mutter_check",
        TickType.MUTTER_CHECK,
    ),
    RouteSpec(
        EventType.CLOCK_TICK,
        "expression.initiate_chat_check",
        "expression",
        "on_initiate_chat_check",
        TickType.INITIATE_CHAT_CHECK,
    ),
    RouteSpec(
        EventType.CLOCK_TICK,
        "inner_life.reflection_check",
        "inner_life",
        "on_reflection_check",
        TickType.REFLECTION_CHECK,
    ),
)


def routes_for_event(
    event_type: EventType, tick_type: TickType | None = None
) -> tuple[RouteSpec, ...]:
    """Return the durable consumers matching an event payload."""
    return tuple(
        spec
        for spec in ROUTE_SPECS
        if spec.event_type is event_type
        and (spec.tick_type is None or spec.tick_type is tick_type)
    )


ROUTING: dict[EventType, list[str]] = {
    event_type: [
        spec.module
        for spec in ROUTE_SPECS
        if spec.event_type is event_type and spec.tick_type is None
    ]
    for event_type in EventType
    if event_type is not EventType.CLOCK_TICK
}

TICK_ROUTING: dict[TickType, list[str]] = {
    tick_type: [
        spec.module for spec in ROUTE_SPECS if spec.tick_type is tick_type
    ]
    for tick_type in TickType
}
