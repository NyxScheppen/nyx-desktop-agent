"""事件构造 + 时间单位常量（跨模块共享原语）。

内部事件构造与时间单位常量在 desire/memory/inner_life/activity 四个 Facade
重复，抽出到此统一维护——Event 结构或时间戳语义一变，只改这一处。
"""
import time
from typing import Any
from uuid import uuid4

from nyx.enums import EventType, Source
from nyx.types import Event

SECONDS_PER_DAY = 86400.0
SECONDS_PER_HOUR = 3600.0


def internal_event(
    type_: EventType, content: dict[str, Any], correlation_id: str
) -> Event:
    """构造内部事件：新 uuid4 + 当前时间戳 + Source.INTERNAL。"""
    return Event(
        id=str(uuid4()),
        timestamp=time.time(),
        source=Source.INTERNAL,
        type=type_,
        content=content,
        correlation_id=correlation_id,
    )
