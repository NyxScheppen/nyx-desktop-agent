"""Discussion tool adapter. All research executes the shared research graph."""

from collections.abc import Awaitable, Callable
from typing import Any

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
        "研究问题并保存合格来源与结论；返回研究 ID、状态及证据摘要。",
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "完整问题及需要继续的研究缺口",
                },
                "run_id": {
                    "type": "string",
                    "description": "恢复已有研究的 ID，可省略",
                },
                "answer": {
                    "type": "string",
                    "description": "用户对待澄清问题的补充原文",
                },
            },
            "required": ["query"],
        },
        handler,
    )
