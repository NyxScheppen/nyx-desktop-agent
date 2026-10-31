# pyright: reportPrivateUsage=false
"""Runtime subscription assembly derived from plugin declarations."""
from __future__ import annotations

from typing import TYPE_CHECKING

from nyx.events.routing import ROUTE_SPECS
from nyx.plugins import route_handlers

if TYPE_CHECKING:
    from nyx.app_context import _App


def subscribe(app: _App) -> None:
    """Register plugin handlers in the existing durable route order."""
    handlers = route_handlers(app)
    for spec in ROUTE_SPECS:
        app.bus.subscribe(spec, handlers[spec.consumer_id])
    app.bus.validate_routes()
