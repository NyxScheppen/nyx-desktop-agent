"""Per-call attribution inherited by nested tools, never shared across roles."""

from contextvars import ContextVar
from typing import Any

behavior_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "nyx_behavior_context", default=None
)
tool_execution_id: ContextVar[str | None] = ContextVar(
    "nyx_tool_execution_id", default=None
)
