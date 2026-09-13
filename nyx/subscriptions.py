# pyright: reportPrivateUsage=false
from __future__ import annotations

from typing import TYPE_CHECKING

from nyx.enums import EventType

if TYPE_CHECKING:
    from nyx.app_context import _App


def subscribe(app: _App) -> None:
    """按事件类型注册 Facade handler；保持组合根的单一订阅清单。"""
    from nyx.main import _on_clock_tick, _on_user_message

    bus = app.bus
    bus.subscribe(EventType.USER_MESSAGE, lambda e: _on_user_message(app, e))
    bus.subscribe(EventType.OBSERVATION_STATE, app.inner_life.apply_event)
    bus.subscribe(EventType.OBSERVATION_STATE, app.desire.add_value)
    bus.subscribe(EventType.DESIRE_GENERATED, app.activity.on_desire_generated)
    bus.subscribe(EventType.DESIRE_SATISFIED, app.inner_life.apply_event)
    bus.subscribe(EventType.ACTIVITY_END, app.desire.add_value)
    bus.subscribe(EventType.ACTIVITY_END, app.inner_life.apply_event)
    bus.subscribe(EventType.ACTIVITY_END, app.memory.remember_activity)
    bus.subscribe(EventType.REFLECTION, app.inner_life.apply_event)
    bus.subscribe(EventType.CLOCK_TICK, lambda e: _on_clock_tick(app, e))
