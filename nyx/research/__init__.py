"""Discussion tool adapter. All research executes the shared research graph."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from nyx.eval.evaluator import Evaluator
from nyx.llm.client import LlmClient
from nyx.types import Tool


async def run_research(
    query: str,
    *,
    search: Callable[..., Awaitable[Any]] | None,
    fetch: Callable[..., Awaitable[Any]] | None,
    llm: LlmClient,
    ingest: Callable[..., Awaitable[dict[str, Any]]],
    budget: Mapping[str, float] | None = None,
    evaluator: Evaluator | None = None,
) -> dict[str, Any]:
    """Standalone injected-handler entry, using exactly the production graph.

    The composition root uses ResearchService for role-local durable execution.
    Standalone probes own their checkpoint lifetime and do not evolve skills.
    """
    from nyx.research.standalone import run_standalone

    return await run_standalone(
        query,
        search=search,
        fetch=fetch,
        llm=llm,
        ingest=ingest,
        budget=budget,
        evaluator=evaluator,
    )


def build_deep_research_tool(
    *,
    search: Callable[..., Awaitable[Any]] | None = None,
    fetch: Callable[..., Awaitable[Any]] | None = None,
    llm: LlmClient | None = None,
    ingest: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    evaluator: Evaluator | None = None,
    runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
) -> Tool:
    async def handler(
        query: str,
        run_id: str | None = None,
        answer: str | None = None,
    ) -> dict[str, Any]:
        if runner is not None:
            return await runner(query, run_id=run_id, answer=answer)
        if llm is None or ingest is None:
            raise ValueError("研究执行器未初始化")
        return await run_research(
            query,
            search=search,
            fetch=fetch,
            llm=llm,
            ingest=ingest,
            evaluator=evaluator,
        )

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
