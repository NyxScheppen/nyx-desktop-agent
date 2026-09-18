import json

import pytest

from nyx.browsing.companions import parse_action
from nyx.browsing.integration import page_outputs, parse_note
from nyx.enums import EventType, Source
from nyx.types import Event


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        '{"action":"none","text":"extra"}',
        '{"action":"mutter","text":"   "}',
        '{"action":"question","text":"A statement."}',
        json.dumps({"action": "association", "query": "x" * 501}),
    ],
)
def test_invalid_actions_are_none(raw: str) -> None:
    assert parse_action(raw) == ("none", "")


def test_action_normalizes_text() -> None:
    assert parse_action('{"action":"mutter","text":"  hello\\n world "}') == (
        "mutter",
        "hello world",
    )


def test_note_rejects_invalid_topics() -> None:
    with pytest.raises(ValueError):
        parse_note('{"content":"Note","summary":"Summary","topics":[1]}')
    assert parse_note('{"content":"Note","summary":"Summary","topics":[]}') == (
        "Note",
        "Summary",
        [],
    )


def test_outputs_use_payload_mapping_and_reject_corruption() -> None:
    event = Event(
        "id",
        1.0,
        Source.INTERNAL,
        EventType.BROWSING_ASSOCIATION,
        {"page_id": "page", "snippet": " A memory "},
        "page",
    )
    assert page_outputs("page", [event]) == ["A memory"]
    event.content = {"page_id": "different", "snippet": "not this page"}
    with pytest.raises(ValueError):
        page_outputs("page", [event])
