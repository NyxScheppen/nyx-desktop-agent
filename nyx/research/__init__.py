"""Discussion tool adapter. All research executes the shared research graph."""

from collections.abc import Awaitable, Callable
from typing import Any

from nyx.policy import policy
from nyx.types import Tool


def build_deep_research_tool(
    *,
    runner: Callable[..., Awaitable[dict[str, Any]]],
) -> Tool:
    async def handler(
        query: str,
        run_id: str | None = None,
        answer: str | None = None,
    ) -> dict[str, Any]:
        return await runner(query, run_id=run_id, answer=answer)

    return Tool(
        "deep_research",
        policy("research", "__init__.copy.build_deep_research_tool_text", str),
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": policy(
                        "research", "__init__.copy.build_deep_research_tool_text_2", str
                    ),
                },
                "run_id": {
                    "type": "string",
                    "description": policy(
                        "research", "__init__.copy.build_deep_research_tool_text_3", str
                    ),
                },
                "answer": {
                    "type": "string",
                    "description": policy(
                        "research", "__init__.copy.build_deep_research_tool_text_4", str
                    ),
                },
            },
            "required": ["query"],
        },
        handler,
    )
