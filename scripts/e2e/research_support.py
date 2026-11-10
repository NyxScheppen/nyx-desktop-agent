"""E2E setup for the durable production research service; owns no research logic."""

import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import uuid4

from nyx.behavior.service import BehaviorService
from nyx.behavior.store import BehaviorStore, SkillStore
from nyx.config import ExplorationConfig, MemoryConfig
from nyx.db import connect
from nyx.enums import ActivityStatus, ActivityType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.memory.retrieval import MemoryRetrieval
from nyx.memory.store import MemoryStore
from nyx.research.service import ResearchService
from nyx.tools.registry import ToolRegistry
from nyx.types import Activity, Skill, Tool


async def run_research(
    query: str,
    *,
    search: Callable[..., Awaitable[Any]] | None,
    fetch: Callable[..., Awaitable[Any]] | None,
    llm: LlmClient,
    memory: MemoryFacade | None = None,
    evaluator: Evaluator | None = None,
    budget: Mapping[str, float] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    owned = memory is None
    evaluator = evaluator or Evaluator()
    if memory is None:
        database = await connect(":memory:")
        bus = EventBus(database)
        store = MemoryStore(database)
        memory = MemoryFacade(
            store, MemoryRetrieval(store), bus, llm, evaluator, MemoryConfig(),
        )
    else:
        database, bus = memory._store.db, memory._bus
    try:
        behavior = memory.behavior
        if behavior is None:
            skills = SkillStore(database)
            await skills.seed({
                "discussion": Skill("discussion", "按证据研究并引用原文", ()),
                "conversation": Skill("conversation", "忠实回复", ()),
            })
            behavior = BehaviorService(BehaviorStore(database), skills, bus, llm)
            memory.behavior = behavior
        tools = ToolRegistry()
        if search is not None:
            tools.register(Tool("web_search", "search", {}, search))
        service = ResearchService(
            behavior.store, behavior.skills, behavior, memory, tools, llm,
            evaluator, ExplorationConfig(web_enabled=search is not None and fetch is not None),
            "隔离研究验收角色", fetch,
        )
        key = run_id or f"probe:{uuid4()}"
        activity = Activity(
            key, ActivityType.FREE_EXPLORATION, "probe", ActivityStatus.RUNNING,
            {"intent": query, "exploration": {"limits": dict(budget or {})}}, time.time(),
        )
        result = await service.run(key, query, "dialogue", key, activity=activity)
        record = await behavior.store.get("research_run", key)
        checkpoint = record["activity"]["progress"]["exploration"]
        chunks = await memory._store.list_knowledge_chunks()
        return {
            **result, "report": result.get("report", []), "checkpoint": checkpoint,
            "ingested": [{"chunk_id": chunk.id, "source_topic": chunk.source_topic}
                         for chunk in chunks],
        }
    finally:
        if owned:
            await bus.close()
            await database.close()
