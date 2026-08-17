from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from nyx.tools.registry import ToolRegistry
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
        {"name": "a", "description": "a desc", "parameters": {"type": "object"}},
        {"name": "b", "description": "b desc", "parameters": {"type": "object"}},
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
    with pytest.raises(KeyError):
        await registry.call("nope", {})
