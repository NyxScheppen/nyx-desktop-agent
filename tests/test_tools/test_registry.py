from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from nyx.db import connect
from nyx.tools.registry import ToolRegistry
from nyx.tools.tool_call_store import ToolCallStore
from nyx.types import Tool


async def _noop(**kwargs: Any) -> None:
    return None


def _make_tool(
    name: str,
    handler: Callable[..., Awaitable[Any]] | None = None,
) -> Tool:
    return Tool(
        name=name,
        description=f"{name} desc",
        schema={"type": "object"},
        handler=handler if handler is not None else _noop,
    )


def test_register_and_schema_in_order() -> None:
    registry = ToolRegistry()
    registry.register(_make_tool("a"))
    registry.register(_make_tool("b"))
    assert registry.schema() == [
        {
            "type": "function",
            "function": {
                "name": "a",
                "description": "a desc",
                "parameters": {"type": "object"},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "b",
                "description": "b desc",
                "parameters": {"type": "object"},
            },
        },
    ]


def test_register_duplicate_raises() -> None:
    registry = ToolRegistry()
    registry.register(_make_tool("a"))
    with pytest.raises(ValueError):
        registry.register(_make_tool("a"))


async def test_call_invokes_handler_with_kwargs() -> None:
    received: dict[str, Any] = {}

    async def handler(query: str, n: int) -> str:
        received["query"] = query
        received["n"] = n
        return "ok"

    registry = ToolRegistry()
    registry.register(_make_tool("search", handler))
    assert await registry.call("search", {"query": "x", "n": 2}) == "ok"
    assert received == {"query": "x", "n": 2}


async def test_call_unknown_name_raises() -> None:
    registry = ToolRegistry()
    with pytest.raises(KeyError, match="nope"):
        await registry.call("nope", {})


async def test_call_logs_result_and_marks_adopted() -> None:
    db = await connect(":memory:")
    registry = ToolRegistry(ToolCallStore(db))

    async def handler(query: str) -> str:
        return query * 3000

    registry.register(_make_tool("search", handler))
    try:
        result = await registry.call(
            "search", {"query": "猫"}, correlation_id="corr", call_id="call",
            log_id="log",
        )
        row = await (await db.conn.execute("SELECT * FROM tool_call_log")).fetchone()
        assert row is not None
        assert (row["id"], row["correlation_id"], row["call_id"], row["tool_name"]) == (
            "log", "corr", "call", "search",
        )
        assert result == "猫" * 3000 and len(row["result_json"]) == 2000
        assert row["status"] == "success" and row["duration_ms"] >= 0
        assert row["adopted"] == 0
        await registry.mark_adopted("log")
        await registry.mark_adopted("log")
        row = await (await db.conn.execute(
            "SELECT adopted FROM tool_call_log"
        )).fetchone()
        assert row is not None and row["adopted"] == 1
    finally:
        await db.close()


async def test_call_logs_error_with_bounded_args_and_idempotent_log_id() -> None:
    db = await connect(":memory:")
    registry = ToolRegistry(ToolCallStore(db))

    async def handler(query: str) -> None:
        raise ValueError("失败" * 1000)

    registry.register(_make_tool("search", handler))
    try:
        for _ in range(2):
            with pytest.raises(ValueError):
                await registry.call("search", {"query": "x" * 3000}, log_id="same")
        rows = list(await (
            await db.conn.execute("SELECT * FROM tool_call_log")
        ).fetchall())
        assert len(rows) == 1
        assert rows[0]["status"] == "error" and rows[0]["result_json"] is None
        assert len(rows[0]["args_json"]) == 2000 and len(rows[0]["error"]) == 1000
    finally:
        await db.close()


async def test_call_generates_log_id_without_changing_result() -> None:
    db = await connect(":memory:")
    registry = ToolRegistry(ToolCallStore(db))
    registry.register(_make_tool("noop"))
    try:
        assert await registry.call("noop", {}) is None
        assert await registry.call("noop", {}) is None
        rows = await (await db.conn.execute("SELECT * FROM tool_call_log")).fetchall()
        assert len({row["id"] for row in rows}) == 2
        assert all(row["result_json"] == "null" and row["adopted"] == 0 for row in rows)
    finally:
        await db.close()


async def test_execution_receipt_keeps_full_result_and_claims_once() -> None:
    db = await connect(":memory:")
    store = ToolCallStore(db)
    try:
        assert await store.start_execution("attempt", "corr") is None
        claimed = await store.start_execution("attempt", "corr")
        assert claimed is not None and claimed["status"] == "running"
        await store.finish_execution("attempt", {"content": "猫" * 3000}, None)
        saved = await store.start_execution("attempt", "corr")
        assert saved is not None
        assert saved["result_json"] == '{"content": "' + "猫" * 3000 + '"}'
    finally:
        await db.close()
