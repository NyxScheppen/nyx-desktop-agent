from nyx.enums import EventType, Source
from nyx.events.event import SECONDS_PER_DAY, SECONDS_PER_HOUR, internal_event


def test_time_constants() -> None:
    assert SECONDS_PER_DAY == 86400.0
    assert SECONDS_PER_HOUR == 3600.0


def test_internal_event_shape() -> None:
    event = internal_event(EventType.THINK, {"text": "hi"}, "corr-1")
    assert event.source is Source.INTERNAL
    assert event.type is EventType.THINK
    assert event.content == {"text": "hi"}
    assert event.correlation_id == "corr-1"
    assert event.id != ""
    assert isinstance(event.timestamp, float)
