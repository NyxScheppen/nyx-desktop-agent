"""Conversation tools for the role's inner clock."""

import re
from typing import Any

from nyx.behavior.context import tool_execution_id
from nyx.enums import EventType
from nyx.initiative import Action, Initiative, Origin, Recurrence, Topic
from nyx.policy import policy
from nyx.types import Tool


def build_initiative_tools(service: Initiative) -> list[Tool]:
    async def schedule(
        origin: Origin,
        action: Action,
        local_time: str,
        recurrence: Recurrence = "once",
        note: str = "",
        book: str | None = None,
        paragraph: int | None = None,
        skip_if_read: bool = False,
        skip_if_read_today: bool | None = None,
    ) -> dict[str, object]:
        log_id = tool_execution_id.get()
        async with service.db.lock:
            row = await (
                await service.db.conn.execute(
                    "SELECT correlation_id FROM tool_execution WHERE id=?", (log_id,)
                )
            ).fetchone()
        source = (
            await service.bus.get_event(str(row["correlation_id"])) if row else None
        )
        if source is None or source.type is not EventType.USER_MESSAGE:
            raise ValueError(policy("initiative", "copy.missing_source", str))
        # The tool label alone cannot authorize a recurring commitment.
        if origin == "user":
            candidates = await service.bus.list_events(
                event_type=EventType.USER_MESSAGE,
                correlation_id=source.correlation_id,
            )
            explicit = policy("initiative", "patterns.explicit", re.Pattern[str])
            source = next(
                (
                    event
                    for event in candidates
                    if explicit.search(str(event.content.get("message", "")))
                ),
                source,
            )
        if book:
            book = book.strip() or book
        book_id = await service.resolve_book(book) if book else None
        if book and book_id is None:
            label = policy("initiative", "patterns.book_label", re.Pattern[str])
            if quoted := label.fullmatch(book):
                book_id = await service.resolve_book(quoted[1].strip())
        if (book and book_id is None) or (paragraph is not None and book_id is None):
            raise ValueError(policy("initiative", "copy.missing_source", str))
        if action == "reading" and book_id is None and origin == "autonomous":
            context = await service.latest_book_topic()
            if context is not None:
                book_id, paragraph = context.book_id, paragraph or context.paragraph
        topic = Topic(
            book_id=book_id,
            paragraph=paragraph,
            event_id=source.id if action == "continue_chat" else None,
        )
        recurring_book = (
            origin == "user" and action == "reading" and recurrence != "once"
            and book_id is not None
        )
        # A repeating book reminder without a paragraph can only check the day.
        if recurring_book and paragraph is None and skip_if_read:
            if skip_if_read_today is None:
                skip_if_read_today = True
            skip_if_read = False
        if skip_if_read_today is None:
            skip_if_read_today = recurring_book and policy(
                "initiative", "defaults.recurring_reading_skip_if_read_today", bool,
            )
        item = await service.create(
            origin, action, local_time, recurrence, note, topic, source, skip_if_read,
            skip_if_read_today,
        )
        return item.result()

    async def modify(
        id: str,
        version: int,
        operation: str,
        local_time: str | None = None,
    ) -> dict[str, object]:
        return (await service.modify(id, version, operation, local_time)).result()

    async def cancel(id: str, version: int) -> dict[str, object]:
        return (await service.modify(id, version, "cancel")).result()

    def tool(
        name: str,
        key: str,
        properties: dict[str, Any],
        required: list[str],
        handler: Any,
    ) -> Tool:
        return Tool(
            name=name,
            description=policy("initiative", "tools", dict[str, str])[key],
            schema={
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            handler=handler,
        )

    text = {"type": "string"}
    versioned = {"id": text, "version": {"type": "integer"}}
    return [
        tool(
            "schedule_interaction",
            "schedule",
            {
                "origin": {"type": "string", "enum": ["user", "autonomous"]},
                "action": {
                    "type": "string",
                    "enum": ["reminder", "reading", "continue_chat"],
                },
                "local_time": text,
                "recurrence": {
                    "type": "string",
                    "enum": ["once", "daily", "weekly", "workdays"],
                },
                "note": text,
                "book": text,
                "paragraph": {"type": "integer"},
                "skip_if_read": {"type": "boolean"},
                "skip_if_read_today": {"type": "boolean"},
            },
            ["origin", "action", "local_time"],
            schedule,
        ),
        tool("list_interactions", "list", {}, [], service.list_items),
        tool(
            "modify_interaction",
            "modify",
            {
                **versioned,
                "operation": {
                    "type": "string",
                    "enum": ["reschedule", "pause", "resume", "skip", "skip_today"],
                },
                "local_time": text,
            },
            [*versioned, "operation"],
            modify,
        ),
        tool("cancel_interaction", "cancel", versioned, list(versioned), cancel),
    ]
