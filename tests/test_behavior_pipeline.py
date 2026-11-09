"""Real SQLite/MemoryFacade paths, including interrupted commit boundaries."""

import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, cast

import pytest

from nyx.behavior.service import BehaviorService
from nyx.behavior.store import BehaviorStore, SkillStore, skill_snapshot
from nyx.config import ExplorationConfig, MemoryConfig
from nyx.db import Database, connect
from nyx.enums import EventType, MemoryKind
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.memory.retrieval import MemoryRetrieval
from nyx.memory.store import MemoryStore
from nyx.research.service import ResearchService
from nyx.tools.registry import ToolRegistry
from nyx.types import LLMOutput, Memory, Skill, Tool

TEXT = "随机分配可减少混杂偏差，但仍需检查依从性和失访。"
URL = "https://example.test/causal"


class Script:
    def __init__(self) -> None:
        self.reject = False
        self.calls: list[str] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        **_kwargs: Any,
    ) -> LLMOutput:
        self.calls.append(output_type)
        data = json.loads(messages[1]["content"])
        result: dict[str, Any]
        if output_type == "exploration_decide":
            if data["results"] and not data["processed_sources"]:
                result = {"action": "read", "index": 0}
            elif data["remaining"]["searches"] == 4:
                result = {"action": "search", "query": data["question"]}
            else:
                result = {"action": "finish", "reason": "证据已检查"}
        elif output_type == "exploration_source_check":
            result = {
                "relevant": not self.reject,
                "usable": True,
                "reason": "资料偏题" if self.reject else "回答当前问题",
            }
        elif output_type == "source_digest":
            result = {
                "summary": TEXT,
                "content_category": "knowledge",
                "points": [{"topic": "因果判断", "content": TEXT}],
            }
        elif output_type == "exploration_finalize":
            result = {
                "summary": TEXT,
                "core_discovery": TEXT,
                "report": [
                    {
                        "section": "因果识别",
                        "claim": TEXT,
                        "citations": [{"quote": TEXT, "source_url": URL}],
                    }
                ],
            }
        elif output_type == "exploration_claim_support":
            result = {"supported": [True]}
        elif output_type == "behavior_reflection":
            result = {
                "observation": "本次使用了限定研究类型的搜索词。",
                "cause_analysis": "这次返回的资料与问题相关，不能推广到所有主题。",
                "effective_strategy": "限定研究类型。",
                "next_strategy": "搜索前先明确研究类型和判断目标。",
                "applicability": "外部资料检索",
                "evidence_refs": ["actions"],
                "skill_change_decision": "update",
                "skill_change_reason": "本次策略有证据支持。",
                "proposed_skill_text": "先明确研究类型和判断目标，再检索资料。",
            }
        else:
            result = {"facts": []}
        return LLMOutput(
            module,
            output_type,
            "script",
            json.dumps(result, ensure_ascii=False),
            correlation_id,
            call_id=f"call-{len(self.calls)}",
        )


@dataclass
class Harness:
    db: Database
    bus: EventBus
    memory: MemoryFacade
    memory_store: MemoryStore
    skills: SkillStore
    behavior: BehaviorService
    research: ResearchService
    script: Script
    fetches: list[str]


@pytest.fixture
async def harness() -> AsyncGenerator[Harness, None]:
    database = await connect(":memory:")
    bus = EventBus(database)
    script = Script()
    llm = cast(LlmClient, script)
    evaluator = Evaluator()
    memory_store = MemoryStore(database)
    memory = MemoryFacade(
        memory_store,
        MemoryRetrieval(memory_store),
        bus,
        llm,
        evaluator,
        MemoryConfig(),
    )
    skills = SkillStore(database)
    await skills.seed(
        {
            "discussion": Skill("discussion", "研究方法初版", ("deep_research",)),
            "conversation": Skill("conversation", "对话方法初版", ()),
        }
    )
    behavior = BehaviorService(BehaviorStore(database), skills, bus, llm)
    memory.behavior = behavior
    tools = ToolRegistry()
    fetches: list[str] = []

    async def search(**_kwargs: Any) -> list[dict[str, str]]:
        return [{"title": "因果研究", "url": URL}]

    async def fetch(url: str) -> dict[str, str]:
        fetches.append(url)
        return {"text": TEXT}

    tools.register(Tool("web_search", "search", {}, search))
    research = ResearchService(
        behavior.store,
        skills,
        behavior,
        memory,
        tools,
        llm,
        evaluator,
        ExplorationConfig(web_enabled=True),
        "角色设定",
        fetch,
    )
    yield Harness(
        database, bus, memory, memory_store, skills, behavior, research, script, fetches
    )
    await bus.close()
    await database.close()


async def test_research_reflection_revision_and_replay(harness: Harness) -> None:
    h = harness
    result = await h.research.run("run1", "怎样判断因果关系", "dialogue", "chat1")
    assert result["status"] == "completed" and result["report"]
    assert (await h.skills.load("discussion")).version == 1
    requested = await h.bus.list_events(event_type=EventType.BEHAVIOR_REFLECTION)
    assert len(requested) == 1
    await h.behavior.apply_event(requested[0], h.memory)
    assert (await h.skills.load("discussion")).version == 2
    lessons = await h.memory_store.list_memories(kind=MemoryKind.LESSON)
    assert len(lessons) == 1
    assert TEXT in lessons[0].content and "【原文证据】" in lessons[0].content
    await h.behavior.apply_event(requested[0], h.memory)
    replay = await h.research.run("run1", "怎样判断因果关系", "dialogue", "chat1")
    assert replay == result and len(h.fetches) == 1
    assert len(await h.skills.history("discussion")) == 2
    assert len(await h.memory_store.list_memories(kind=MemoryKind.LESSON)) == 1
    assert (await h.skills.load("conversation")).version == 1


async def test_rejected_source_never_enters_knowledge(harness: Harness) -> None:
    h = harness
    h.script.reject = True
    await h.research.run("reject", "因果关系", "desire", "activity1")
    assert not await h.memory_store.list_knowledge_chunks()
    assert not await h.memory_store.list_memories(kind=MemoryKind.KNOWLEDGE)
    records = await h.behavior.store.list_records("behavior_reflection")
    assert records[0]["evidence"]["source_excerpts_raw"][0]["source_text_raw"] == TEXT


async def test_fetch_failure_is_terminal_and_does_not_use_snippets(
    harness: Harness,
) -> None:
    h = harness

    async def fail(**_kwargs: Any) -> dict[str, str]:
        return {"error": "fetch refused"}

    h.research.fetch_source = fail
    result = await h.research.run("failure", "因果关系", "dialogue", "chat2")
    assert result["status"] == "failed" and "fetch refused" in result["error"]
    assert not await h.memory_store.list_knowledge_chunks()
    assert "exploration_finalize" not in h.script.calls
    assert await h.research.run("failure", "因果关系", "dialogue", "chat2") == result


async def test_interrupt_digest_resumes_without_refetch(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness
    original = h.memory.digest_source_block

    async def interrupt(*_args: Any, **_kwargs: Any) -> Any:
        raise asyncio.CancelledError

    monkeypatch.setattr(h.memory, "digest_source_block", interrupt)
    with pytest.raises(asyncio.CancelledError):
        await h.research.run("pause", "因果关系", "dialogue", "chat3")
    assert not await h.behavior.store.list_records("behavior_reflection")
    monkeypatch.setattr(h.memory, "digest_source_block", original)
    result = await h.research.resume("pause")
    assert result["status"] == "completed" and len(h.fetches) == 1


async def test_experience_rollback_prevents_skill_update(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness
    event = await h.behavior.submit(
        "rollback",
        "discussion",
        {
            "evidence_ids": ["actions"],
            "topic_raw": "测试问题",
            "skill_snapshot_raw": skill_snapshot(await h.skills.load("discussion")),
        },
        "rollback",
    )
    original = h.memory.commit_behavior_experience

    async def fail_after_write(memory: Memory, cid: str) -> None:
        await original(memory, cid)
        raise RuntimeError("commit probe")

    monkeypatch.setattr(h.memory, "commit_behavior_experience", fail_after_write)
    with pytest.raises(RuntimeError, match="commit probe"):
        await h.behavior.apply_event(event, h.memory)
    assert (await h.skills.load("discussion")).version == 1
    assert not await h.memory_store.list_memories(kind=MemoryKind.LESSON)
    monkeypatch.setattr(h.memory, "commit_behavior_experience", original)
    await h.behavior.apply_event(event, h.memory)
    assert (await h.skills.load("discussion")).version == 2
    assert h.script.calls.count("behavior_reflection") == 1


async def test_execution_snapshot_stays_pinned_and_direct_skill_write_rejected(
    harness: Harness,
) -> None:
    h = harness
    pinned = await h.skills.for_execution("turn", "discussion", "原问题")
    async with h.db.transaction():
        with pytest.raises(ValueError, match="行为反思"):
            await h.skills.apply("missing", "discussion", 1, "越过反思")
    event = await h.behavior.submit(
        "update",
        "discussion",
        {
            "evidence_ids": ["actions"],
            "topic_raw": "问题",
        },
        "update",
    )
    await h.behavior.apply_event(event, h.memory)
    resumed = await h.skills.for_execution("turn", "discussion", "恢复问题")
    assert pinned == resumed and pinned.version == 1
    assert (await h.skills.for_execution("next", "discussion", "新问题")).version == 2


@pytest.mark.parametrize("error_type", ["lost_ack", "db_timeout"])
async def test_uncertain_knowledge_commit_reuses_receipt(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    error_type: str,
) -> None:
    import sqlite3

    h = harness
    original = h.memory.remember_research_knowledge

    async def lost_ack(*args: Any, **kwargs: Any) -> Any:
        await original(*args, **kwargs)
        if error_type == "db_timeout":
            from nyx.db import DatabaseOperationTimeout

            raise DatabaseOperationTimeout("DB commit acknowledgement timed out")
        raise sqlite3.OperationalError("lost commit acknowledgement")

    monkeypatch.setattr(h.memory, "remember_research_knowledge", lost_ack)
    result = await h.research.run("uncertain", "因果关系", "dialogue", "uncertain")
    assert result["status"] == "uncertain"
    assert not await h.behavior.store.list_records("behavior_reflection")
    assert len(await h.memory_store.list_memories(kind=MemoryKind.KNOWLEDGE)) == 1
    monkeypatch.setattr(h.memory, "remember_research_knowledge", original)
    result = await h.research.resume("uncertain")
    assert result["status"] == "completed" and len(h.fetches) == 1
    assert len(await h.memory_store.list_memories(kind=MemoryKind.KNOWLEDGE)) == 1


async def test_wait_answer_and_explicit_cancel_are_durable(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness
    original = h.script.complete

    async def ask(messages: list[dict[str, Any]], **kwargs: Any) -> LLMOutput:
        data = json.loads(messages[-1]["content"])
        if kwargs["output_type"] == "exploration_decide" and not any(
            a["name"] == "user_answer" for a in data["actions"]
        ):
            return LLMOutput(
                "activity",
                kwargs["output_type"],
                "script",
                '{"action":"ask_user","question":"哪类因果研究？"}',
                kwargs["correlation_id"],
            )
        return await original(messages, **kwargs)

    monkeypatch.setattr(h.script, "complete", ask)
    result = await h.research.run("wait", "因果关系", "dialogue", "wait")
    assert result["status"] == "waiting_user"
    assert not await h.behavior.store.list_records("behavior_reflection")
    with pytest.raises(ValueError, match="回答"):
        await h.research.resume("wait")
    result = await h.research.resume("wait", "随机试验")
    assert result["status"] == "completed"
    record = await h.behavior.store.get("research_run", "wait")
    assert record and any(
        a.get("text") == "随机试验"
        for a in record["activity"]["progress"]["exploration"]["actions"]
    )
    await h.research.run("cancel", "因果关系", "dialogue", "cancel")
    result = await h.research.cancel("cancel")
    assert result["status"] == "cancelled"
    assert await h.research.resume("cancel") == result
    assert await h.research.cancel("cancel") == result


async def test_active_cancel_settles_and_keeps_actual_trace(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness
    reached = asyncio.Event()

    async def pause(**_kwargs: Any) -> dict[str, str]:
        reached.set()
        await asyncio.Event().wait()
        return {}

    monkeypatch.setattr(h.research, "fetch_source", pause)
    task = asyncio.create_task(
        h.research.run("active", "因果关系", "dialogue", "active")
    )
    await asyncio.wait_for(reached.wait(), 5)
    cancelled = await h.research.cancel("active")
    assert cancelled["status"] == "cancelled"
    assert (await task)["status"] == "cancelled"
    record = await h.behavior.store.get("research_run", "active")
    assert record and record["settled"]
    assert any(
        a["status"] == "interrupted"
        for a in record["activity"]["progress"]["exploration"]["actions"]
    )


async def test_stale_skill_proposal_requires_new_behavior_reflection(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness
    evidence = {"evidence_ids": ["actions"], "topic_raw": "研究问题"}
    first = await h.behavior.submit("first", "discussion", evidence, "first")
    apply = h.behavior._apply_skill  # pyright: ignore[reportPrivateUsage]

    async def interrupt(_record: dict[str, Any]) -> None:
        raise RuntimeError("stop after persisted reflection")

    monkeypatch.setattr(h.behavior, "_apply_skill", interrupt)
    with pytest.raises(RuntimeError):
        await h.behavior.apply_event(first, h.memory)
    monkeypatch.setattr(h.behavior, "_apply_skill", apply)
    second = await h.behavior.submit("second", "discussion", evidence, "second")
    await h.behavior.apply_event(second, h.memory)
    await h.behavior.apply_event(first, h.memory)
    old = await h.behavior.store.get(
        "behavior_reflection", first.content["reflection_id"]
    )
    assert old and old["skill_update_status"] == "conflict"
    events = await h.bus.list_events(event_type=EventType.BEHAVIOR_REFLECTION)
    recheck = next(e for e in events if e.id not in {first.id, second.id})
    await h.behavior.apply_event(recheck, h.memory)
    assert (await h.skills.load("discussion")).version == 2
    latest = await h.behavior.store.get(
        "behavior_reflection", recheck.content["reflection_id"]
    )
    assert latest and latest["skill_update_status"] == "no_change"


async def test_ooc_attribution_filters_non_reply_outputs(harness: Harness) -> None:
    from nyx.behavior.context import behavior_context

    h = harness
    context = {
        "scenario": "reply",
        "output_module": "expression",
        "output_type": "speak",
        "topic_raw": "聊天",
        "assistant_output_raw": "不符合角色的答复",
        "skill_snapshot_raw": skill_snapshot(await h.skills.load("conversation")),
    }
    token = behavior_context.set(context)
    try:
        await h.behavior.record_signal("ooc", "voice", "需要检查", "voice")
        context["output_module"] = "behavior"
        await h.behavior.record_signal("ooc", "voice", "不能递归", "reflection")
        context["scenario"] = "research"
        await h.behavior.record_signal(
            "tool_failure", "research", "不能串线", "research"
        )
    finally:
        behavior_context.reset(token)
    records = await h.behavior.store.list_records("behavior_reflection")
    assert len(records) == 1 and records[0]["target_skill"] == "conversation"
    assert records[0]["evidence"]["assistant_output_raw"] == "不符合角色的答复"


async def test_invalid_reflection_keeps_originals_and_stops_after_three_attempts(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness

    async def invalid(*_args: Any, **kwargs: Any) -> LLMOutput:
        return LLMOutput(
            "behavior",
            "behavior_reflection",
            "script",
            "{invalid",
            kwargs["correlation_id"],
        )

    monkeypatch.setattr(h.script, "complete", invalid)
    event = await h.behavior.submit(
        "invalid", "conversation", {"evidence_ids": []}, "invalid"
    )
    await h.behavior.apply_event(event, h.memory)
    await h.behavior.apply_event(event, h.memory)
    record = await h.behavior.store.get(
        "behavior_reflection", event.content["reflection_id"]
    )
    assert record and record["skill_update_status"] == "invalid_output"
    assert len(record["model_attempts"]) == 3
    assert all(a["output_raw"] == "{invalid" for a in record["model_attempts"])
    assert (await h.skills.load("conversation")).version == 1
    assert not await h.memory_store.list_memories(kind=MemoryKind.LESSON)
