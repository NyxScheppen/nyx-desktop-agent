# pyright: reportPrivateUsage=false
"""Runtime subscription assembly derived from the route specification."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from nyx.events.routing import ROUTE_SPECS, RouteSpec
from nyx.types import Event

if TYPE_CHECKING:
    from nyx.app_context import _App

Handler = Callable[[Event], Awaitable[None]]


def subscribe(app: _App) -> None:
    """Resolve and register every declared route against the application."""
    for spec in ROUTE_SPECS:
        app.bus.subscribe(spec, _resolve_handler(app, spec))
    app.bus.validate_routes()


def _resolve_handler(app: _App, spec: RouteSpec) -> Handler:
    if spec.handler_key == "on_user_message":
        from nyx.runtime import on_user_message

        return lambda event: on_user_message(app, event)
    if spec.handler_key == "apply_observation_state":
        if spec.module == "inner_life":
            return lambda event: app.inner_life.apply_event(event, spec.consumer_id)
        return lambda event: app.desire.add_value(event, spec.consumer_id)
    if spec.handler_key == "on_desire_generated":
        return app.activity.on_desire_generated
    if spec.handler_key == "apply_desire_satisfied":
        return lambda event: app.inner_life.apply_event(event, spec.consumer_id)
    if spec.handler_key == "apply_activity_end":
        if spec.module == "desire":
            return lambda event: app.desire.add_value(event, spec.consumer_id)
        return lambda event: app.inner_life.apply_event(event, spec.consumer_id)
    if spec.handler_key == "remember_activity":
        return lambda event: app.memory.remember_activity(event, spec.consumer_id)
    if spec.handler_key == "apply_reflection":
        return app.inner_life.apply_event

    from nyx.runtime import (
        on_desire_eval,
        on_initiate_chat_check,
        on_mutter_check,
        on_reflection_check,
        on_schedule_block_start,
    )

    tick_handlers: dict[str, Callable[[_App, Event], Awaitable[None]]] = {
        "on_schedule_block_start": on_schedule_block_start,
        "on_desire_eval": on_desire_eval,
        "on_mutter_check": on_mutter_check,
        "on_initiate_chat_check": on_initiate_chat_check,
        "on_reflection_check": on_reflection_check,
    }
    try:
        tick_handler = tick_handlers[spec.handler_key]
    except KeyError as error:
        raise RuntimeError(
            f"没有为 route {spec.consumer_id!r} 配置 handler "
            f"{spec.handler_key!r}"
        ) from error
    return lambda event: tick_handler(app, event)
