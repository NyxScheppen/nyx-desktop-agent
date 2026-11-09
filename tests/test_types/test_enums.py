import re
from pathlib import Path

from nyx.enums import EventType


def test_frontend_sse_listeners_cover_all_event_types() -> None:
    source = Path("frontend/src/hooks/useSSE.ts").read_text(encoding="utf-8")
    match = re.search(r"const EVENT_TYPES = \[(.*?)\];", source, re.DOTALL)
    assert match is not None
    frontend_types = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    # This legacy value remains readable in old event logs but is no longer emitted.
    assert frontend_types == {
        event_type.value for event_type in EventType
        if event_type != EventType.SCENE_MEMORY_REQUESTED
    }
