"""Injected-handler probe adapter; no alternate research algorithm."""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast
from uuid import uuid4

from nyx.config import ExplorationConfig
from nyx.enums import ActivityStatus, ActivityType
from nyx.eval.evaluator import Evaluator
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.research.engine import ResearchEngine, ResearchWaiting
from nyx.tools.registry import ToolRegistry
from nyx.types import Activity, Memory, Tool


class _Checkpoint:
    async def update(self, activity: Activity) -> None:
        return None


class _IngestAdapter:
    def __init__(self, ingest: Callable[..., Awaitable[dict[str, Any]]]) -> None:
        self.ingest = ingest
        self.sources: dict[str, tuple[str, str, str]] = {}
        self.ingested: list[dict[str, Any]] = []

    async def search(self, _query: str) -> list[Memory]:
        return []

    async def search_knowledge(self, _query: str) -> dict[str, Any]:
        return {"facts": [], "sources": []}

    async def record_reading_evidence(
        self,
        topic: str,
        name: str,
        text: str,
        key: str,
        **_kwargs: Any,
    ) -> str:
        self.sources[key] = (topic, name, text)
        return key

    async def digest_source_block(
        self,
        _text: str,
        _name: str,
        _cid: str,
        *,
        source_chunk_id: str,
    ) -> tuple[dict[str, object], list[dict[str, str]]]:
        # The supplied sink owns digestion; invoke it only after admission.
        return {}, [{"source_key": source_chunk_id}]

    async def remember_knowledge(
        self,
        _items: list[dict[str, str]],
        cid: str,
        *,
        source_chunk_id: str,
    ) -> None:
        topic, name, text = self.sources[source_chunk_id]
        result = await self.ingest(
            text,
            topic,
            name,
            cid,
            location=source_chunk_id,
            committed_chunks=self.ingested,
        )
        if result.get("errors"):
            raise ValueError(str(result["errors"]))
        self.ingested.extend(result.get("chunks", []))


async def run_standalone(
    query: str,
    *,
    search: Callable[..., Awaitable[Any]] | None,
    fetch: Callable[..., Awaitable[Any]] | None,
    llm: LlmClient,
    ingest: Callable[..., Awaitable[dict[str, Any]]],
    budget: Mapping[str, float] | None,
    evaluator: Evaluator | None,
) -> dict[str, Any]:
    if search is None or fetch is None or not query.strip():
        return {"error": "联网没开或研究问题为空", "report": [], "ingested": []}
    tools = ToolRegistry()
    tools.register(Tool("web_search", "search", {}, search))
    tools.register(Tool("web_fetch", "fetch", {}, fetch))
    adapter = _IngestAdapter(ingest)
    memory = getattr(ingest, "__self__", None)
    if not isinstance(memory, MemoryFacade):
        memory = cast(MemoryFacade, adapter)
    checkpoint: dict[str, Any] = {}
    if budget:
        checkpoint["limits"] = {
            name: value for name, value in budget.items() if math.isfinite(value)
        }
    activity = Activity(
        str(uuid4()),
        ActivityType.FREE_EXPLORATION,
        "standalone",
        ActivityStatus.RUNNING,
        {"intent": query, "exploration": checkpoint},
        time.time(),
    )
    engine = ResearchEngine(
        llm,
        evaluator or Evaluator(),
        tools,
        _Checkpoint(),
        None,
        memory,
        ExplorationConfig(web_enabled=True),
        fetch_source=fetch,
    )
    try:
        result = await engine.run(activity)
    except ResearchWaiting as waiting:
        return {
            "status": "waiting_user",
            "question": str(waiting),
            "report": [],
            "ingested": adapter.ingested,
        }
    except asyncio.CancelledError:
        raise
    except Exception as error:
        return {
            "error": str(error),
            "report": [],
            "ingested": adapter.ingested,
            "checkpoint": activity.progress.get("exploration", {}),
        }
    return {**result, "report": result.get("report", []), "ingested": adapter.ingested}
