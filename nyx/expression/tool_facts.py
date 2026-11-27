# pyright: reportPrivateUsage=false
"""Human-readable facts from tool receipts for the final reply."""

import re
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nyx.expression.prompt import _WEEKDAYS, _bounded_text, format_dialogue_time
from nyx.policy import policy
from nyx.timezone import USER_TIMEZONE

_SCHEDULE_TEXT = policy(
    "expression", "tool_pipeline.reply.schedule.text", dict[str, str]
)
_SCHEDULE_LABELS = policy(
    "expression", "tool_pipeline.reply.schedule.labels", dict[str, dict[str, str]]
)


def is_schedule_tool(name: str) -> bool:
    return name in (
        "list_interactions", "schedule_interaction",
        "modify_interaction", "cancel_interaction",
    )


def _schedule_operation(name: str, args: dict[str, Any]) -> str:
    if name == "schedule_interaction":
        return "create"
    if name == "cancel_interaction":
        return "cancel"
    return str(args.get("operation", "")) if name == "modify_interaction" else ""


def _schedule_time(value: str, zone_name: str, template: str, zone_label: str) -> str:
    if not value or not zone_name:
        return ""
    try:
        zone = USER_TIMEZONE if zone_name == USER_TIMEZONE.tzname(None) else ZoneInfo(
            zone_name
        )
        local = datetime.fromisoformat(value)
        local = (
            local.replace(tzinfo=zone)
            if local.tzinfo is None
            else local.astimezone(zone)
        )
    except (ValueError, OverflowError, ZoneInfoNotFoundError):
        return ""
    return template.format(
        year=local.year, month=local.month, day=local.day,
        weekday=_WEEKDAYS[local.weekday()], clock=local.strftime("%H:%M"),
        zone=zone_label if zone is USER_TIMEZONE else zone_name,
    )


def _text(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) and value.strip() else ""


async def schedule_facts(
    name: str,
    args: dict[str, Any],
    result: object,
    book_title: Callable[[str], Awaitable[str | None]] | None,
) -> tuple[str, list[str]]:
    rules = _SCHEDULE_TEXT
    labels = _SCHEDULE_LABELS
    rows = list(_rows(result))
    if not rows:
        return "", []
    header = (
        rules["header"].format(count=len(rows)) if name == "list_interactions" else ""
    )
    operation = labels["operations"].get(_schedule_operation(name, args), "")
    titles: dict[str, str | None] = {}
    facts: list[str] = []
    for row in rows:
        action_key = _text(row, "action")
        action = labels["actions"].get(action_key, rules["unknown_action"])
        if action_key == "reading":
            book_id = _text(row, "book_id")
            if book_title is not None and book_id and book_id not in titles:
                titles[book_id] = await book_title(book_id)
            title = titles.get(book_id)
            action = (
                rules["book"].format(title=title) if title else rules["unknown_book"]
            )
            paragraph = row.get("paragraph")
            if isinstance(paragraph, int) and not isinstance(paragraph, bool):
                action += rules["paragraph"].format(paragraph=paragraph)
        if note := _text(row, "note"):
            action += rules["note"].format(note=note)
        status_key = _text(row, "status")
        status = labels["states"].get(status_key, rules["unknown_status"])
        recurrence = labels["recurrences"].get(_text(row, "recurrence"), "")
        when = _schedule_time(
            _text(row, "next_local_time"), _text(row, "timezone"),
            rules["time"], rules["zone_label"],
        )
        timing = ""
        if status_key in ("active", "paused"):
            template = rules["paused_time"] if status_key == "paused" else (
                rules["next_time"] if recurrence else rules["active_time"]
            )
            timing = template.format(time=when) if when else rules["unknown_time"]
        facts.append(rules["item"].format(
            operation=operation, action=action, timing=timing, status=status,
            recurrence=recurrence, outcome=labels["outcomes"].get(
                _text(row, "last_outcome"), ""
            ),
        ))
    return header, facts


def schedule_failure(record: dict[str, Any], tool: str) -> str | None:
    name, status = record["name"], record["status"]
    if status in ("skipped", "retry_wait"):
        return None
    if name == "list_interactions":
        timeout = policy(
            "expression", "tool_pipeline.reply.timeout_errors", re.Pattern[str]
        )
        if status == "empty":
            return _SCHEDULE_TEXT["empty"]
        if any(timeout.search(str(error)) for error in record["errors"]):
            return _SCHEDULE_TEXT["timeout"]
        return _SCHEDULE_TEXT["query_failure"]
    if status in ("uncertain", "failed", "exhausted"):
        key = "uncertain" if status == "uncertain" else "failed"
        operation = _SCHEDULE_LABELS["operation_names"].get(
            _schedule_operation(name, record["args"]), tool
        )
        return _SCHEDULE_TEXT[key].format(operation=operation)
    return None


def _rows(value: object) -> Iterator[dict[str, object]]:
    if isinstance(value, dict):
        yield cast(dict[str, object], value)
    elif isinstance(value, list):
        for item in cast(list[object], value):
            if isinstance(item, dict):
                yield cast(dict[str, object], item)


def _with_excerpt(header: str, label: str, excerpt: str, limit: int) -> str:
    if not excerpt or len(header) >= limit:
        return header.rstrip()
    prefix = header + label
    remaining = limit - len(prefix)
    if len(excerpt) <= remaining:
        return prefix + excerpt
    partial = policy("expression", "tool_pipeline.reply.partial", str)
    available = remaining - len(partial) - len("\n")
    body = _bounded_text(excerpt, available) + "\n" if available > 1 else ""
    return prefix + body + partial


def reply_recall_segment(row: dict[str, object]) -> str:
    rules = policy("expression", "tool_pipeline.reply.sources", dict[str, str])
    clocks: list[str] = []
    for key in ("started_at", "ended_at"):
        value = row.get(key)
        clocks.append(
            format_dialogue_time(value if isinstance(value, (int, float)) else None)
        )
    head = rules["segment"].format(start=clocks[0], end=clocks[1])
    return head + rules["dialogue_quote"] + _text(row, "content")


def source_facts(name: str, result: object, limit: int) -> list[str] | None:
    rules = policy("expression", "tool_pipeline.reply.sources", dict[str, str])
    if name in ("web_search", "web_fetch"):
        fetched = name == "web_fetch"
        template = rules["page"] if fetched else rules["search"]
        quote = rules["web_quote"] if fetched else rules["search_quote"]
        return [
            _with_excerpt(
                template.format(
                    title=_text(row, "title") or rules["unknown_title"], url=url,
                ),
                quote, _text(row, "text" if fetched else "snippet"), limit,
            )
            for row in _rows(result)
            if (url := _text(row, "source_url") or _text(row, "url"))
        ]
    if name in ("local_search", "file_io"):
        facts: list[str] = []
        for row in _rows(result):
            path = _text(row, "path")
            if not path:
                continue
            header = rules["local"].format(path=path)
            excerpt = _text(row, "snippet") or _text(row, "content")
            if excerpt:
                facts.append(_with_excerpt(header, rules["file_quote"], excerpt, limit))
            elif "written" in row:
                facts.append(rules["local_written"].format(path=path))
            elif isinstance(entries := row.get("entries"), list):
                facts.append(_with_excerpt(
                    rules["local_listed"].format(path=path) + "\n",
                    rules["directory_entries"],
                    "\n".join(
                        item
                        for item in cast(list[object], entries)
                        if isinstance(item, str)
                    ),
                    limit,
                ))
            else:
                facts.append(header.rstrip())
        return facts
    if name == "memory_recall" and isinstance(result, dict):
        recall = cast(dict[str, object], result)
        return [
            *(reply_recall_segment(row) for row in _rows(recall.get("segments"))
              if _text(row, "content")),
            *(rules["fact"].format(**{
                key: _text(row, key) for key in ("subject", "predicate", "object_value")
            }) for row in _rows(recall.get("facts"))),
        ]
    return None
