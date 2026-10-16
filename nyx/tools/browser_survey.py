"""Backward-compatible browser survey tool import."""

from collections.abc import Awaitable, Callable
from typing import Any

from nyx.tools.activity_tasks import build_activity_task_tools
from nyx.types import AssignedTask, Tool


def build_browser_survey_tool(
    assign: Callable[[str], Awaitable[AssignedTask]],
) -> Tool:
    """Return the consolidated browser survey task tool."""
    async def unsupported(*_: Any, **__: Any) -> AssignedTask:
        raise RuntimeError("该兼容入口只支持 browser_survey")

    return build_activity_task_tools(assign, unsupported, unsupported, assign)[3]
