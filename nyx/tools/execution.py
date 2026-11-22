"""Shared durable tool attempts and failure transitions for chat and reading."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import uuid4

from nyx.policy import policy
from nyx.tools.registry import ToolRegistry, is_read_only_call

_MAX_TOOL_ATTEMPTS = policy("expression", "tool_pipeline._MAX_TOOL_ATTEMPTS", int)


async def execute_attempt(
    registry: ToolRegistry,
    pending: dict[str, Any],
    correlation_id: str,
    schemas: list[dict[str, Any]],
) -> tuple[dict[str, Any], Any]:
    name, args = pending["name"], pending["args"]
    read_only = is_read_only_call(name, args)
    result: Any = None
    error = None
    # Receipt I/O is outside capability error handling: never retry on a
    # persistence failure after the handler may already have produced effects.
    receipt = await registry.start_execution(
        pending["log_id"],
        correlation_id,
    )
    if receipt is not None and receipt["status"] == "success":
        result = json.loads(receipt["result_json"])
    elif receipt is not None and not read_only and name != "deep_research":
        pending.update(
            status="uncertain",
            errors=[*pending["errors"], receipt["error"]]
            if receipt["error"]
            else pending["errors"],
            text=policy("expression", "tool_pipeline.copy.execute_text_4", str)
            + (receipt["error"] or ""),
        )
        return pending, result
    elif receipt is not None and receipt["status"] == "error":
        error = receipt["error"]
    else:
        # Disabling a tool forbids new execution, not reuse of an outcome
        # already committed before the restart.
        allowed = {schema["function"]["name"] for schema in registry.schema()}
        allowed &= {schema["function"]["name"] for schema in schemas}
        if name not in allowed:
            pending.update(
                status="skipped",
                text=policy("expression", "tool_pipeline.copy.execute_text_5", str),
            )
            return pending, result
        try:
            result = await registry.call(
                name,
                args,
                correlation_id=correlation_id,
                call_id=pending["call_id"],
                log_id=pending["log_id"],
            )
        except Exception as exc:  # 取消继续传播，running 回执保留。
            error = f"{type(exc).__name__}: {exc}"[
                : policy(
                    "expression",
                    "tool_pipeline.parameters.execute_error_chars_or_items",
                    int,
                )
            ]
        await registry.finish_execution(pending["log_id"], result, error)
    return receive_result(pending, result, error), result


def receive_result(
    pending: dict[str, Any],
    result: Any,
    error: str | None,
) -> dict[str, Any]:
    name = pending["name"]
    read_only = is_read_only_call(name, pending["args"])
    if error is None:
        if isinstance(result, dict) and cast(dict[str, Any], result).get("error"):
            error = str(cast(dict[str, Any], result)["error"])[
                : policy(
                    "expression",
                    "tool_pipeline.parameters.receive_result_error_chars_or_items",
                    int,
                )
            ]
    if error is not None:
        known_research_failure = (
            name == "deep_research"
            and isinstance(result, dict)
            and bool(cast(dict[str, Any], result).get("run_id"))
            and cast(dict[str, Any], result).get("status") in {"failed", "cancelled"}
        )
        pending.update(
            status="failed"
            if known_research_failure
            else "uncertain"
            if not read_only
            else "retry_wait"
            if pending["attempts"] < _MAX_TOOL_ATTEMPTS
            else "exhausted",
            errors=[*pending["errors"], error],
            retry_at=time.time() + pending["attempts"],
            text=(
                policy(
                    "expression",
                    "tool_pipeline.templates.receive_result_text_2",
                    str,
                ).format(p0=name, p1=error)
                if read_only or known_research_failure
                else policy(
                    "expression", "tool_pipeline.templates.receive_result_text", str
                ).format(p0=name, p1=error)
            ),
        )
    else:
        empty = result is None or result == [] or result == {} or result == ""
        text = json.dumps(result, ensure_ascii=False)
        if len(text) > policy("expression", "prompt._MATERIAL_ITEM_MAX_CHARS", int):
            text = (
                text[: policy("expression", "prompt._MATERIAL_ITEM_MAX_CHARS", int) - 1]
                + "…"
            )
        pending.update(status="empty" if empty else "success", text=text)
        if name == "memory_recall" and isinstance(result, dict):
            segments = cast(dict[str, Any], result).get("segments", [])
            pending["recall_candidates"] = {
                entry["segment_id"]: json.dumps(entry, ensure_ascii=False)
                for entry in cast(list[Any], segments)
                if isinstance(entry, dict)
                and isinstance(cast(dict[str, Any], entry).get("segment_id"), str)
            }
    pending["received_at"] = time.time()
    return pending


async def read_tool(
    registry: ToolRegistry,
    name: str,
    args: dict[str, Any],
    correlation_id: str,
    check_current: Callable[[], Awaitable[None]],
) -> tuple[Any, dict[str, Any]]:
    """Use the chat attempt/receipt path; give exhausted reads back to the planner."""
    if not is_read_only_call(name, args):
        raise ValueError(f"not a read-only tool: {name}")
    pending: dict[str, Any] = {
        "name": name,
        "args": args,
        "attempts": 0,
        "errors": [],
        "status": "pending",
        "call_id": None,
    }
    while True:
        await check_current()
        if pending["status"] == "retry_wait":
            await asyncio.sleep(max(0.0, pending["retry_at"] - time.time()))
            await check_current()
        pending.update(
            attempts=pending["attempts"] + 1,
            status="running",
            log_id=str(uuid4()),
            receipt_required=True,
            started_at=time.time(),
        )
        pending, result = await execute_attempt(
            registry,
            pending,
            correlation_id,
            registry.schema(),
        )
        if pending["status"] != "retry_wait":
            if pending["status"] in ("success", "empty"):
                await registry.mark_adopted(pending["log_id"])
            return result, pending
