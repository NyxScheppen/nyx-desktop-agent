# pyright: reportPrivateUsage=false
import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from nyx.activity.store import ActivityStore
from nyx.config import ExplorationConfig
from nyx.enums import ActivityStatus, ActivityType, MemoryKind, MemoryType
from nyx.eval.evaluator import Evaluator
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.facade import MemoryFacade
from nyx.research.engine import ResearchEngine, ResearchWaiting, should_explore
from nyx.tools.registry import ToolRegistry
from nyx.types import Activity, LLMOutput, Memory

_FINALIZE_JSON = json.dumps(
    {
        "summary": "弄懂了量子退相干的机制",
        "core_discovery": "退相干是量子系统与环境纠缠导致的表观坍缩",
        "knowledge": [{"topic": "退相干", "content": "环境纠缠抹去相干性"}],
        "strong_new_topics": ["量子纠错"],
        "casual_new_topics": ["退火算法"],
    }
)


class _FakeLlm:
    def __init__(self, content: str = _FINALIZE_JSON) -> None:
        self._content = content
        self.calls: list[str] = []
        self.correlation_ids: list[str] = []
        self.user_contents: list[str] = []
        self.system_contents: list[str] = []

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
    ) -> LLMOutput:
        self.calls.append(output_type)
        self.correlation_ids.append(correlation_id)
        self.user_contents.append(messages[1]["content"])
        self.system_contents.append(messages[0]["content"])
        content = self._content
        if output_type == "exploration_decide":
            data = json.loads(messages[1]["content"])
            unread = [i for i, hit in enumerate(data["results"])
                      if (hit.get("url") or hit.get("path"))
                      not in data["processed_sources"]]
            decision = (
                {"action": "read", "index": unread[0]} if unread else
                {"action": "search", "query": data["question"]}
                if data["remaining"]["searches"] == 4 else {"action": "finish"}
            )
            content = json.dumps(decision)
        elif output_type == "exploration_source_check":
            content = json.dumps({"relevant": True, "usable": True, "reason": "相关"})
        return LLMOutput(
            module=module,
            type=output_type,
            model="fake",
            content=content,
            correlation_id=correlation_id,
        )


class _FakeEvaluator:
    def __init__(self) -> None:
        self.evaluated: list[LLMOutput] = []

    async def evaluate(self, output: LLMOutput) -> None:
        self.evaluated.append(output)


class _FakeTools:
    def __init__(self, fetch_raises: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._fetch_raises = fetch_raises

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if name == "local_search":
            # 真实 local_search 返回 {path, snippet}（无 title/url）
            return [{"path": "C:/notes/量子.txt", "snippet": "环境纠缠"}]
        if name == "web_search":
            return [
                {
                    "title": "量子退相干",
                    "url": "https://example.com/a",
                    "snippet": "环境纠缠",
                }
            ]
        if name == "web_fetch":
            if self._fetch_raises:
                raise RuntimeError("download fail")
            # 真实 web_fetch 返回 {text, url}
            return {"text": "抓取的正文", "url": "https://example.com/a"}
        if name == "file_io":
            return {"path": args["path"], "content": "本地文件的完整正文"}
        return "其他"


class _FakeStore:
    def __init__(self) -> None:
        self.progress_updates: list[dict[str, Any]] = []

    async def update(self, activity: Activity) -> None:
        self.progress_updates.append(deepcopy(activity.progress))


class _FakeDesire:
    def __init__(self) -> None:
        self.appended_subtopics: list[tuple[str, list[str]]] = []

    async def add_long_term_subtopics(
        self, desire_id: str, subtopics: list[str]
    ) -> bool:
        self.appended_subtopics.append((desire_id, subtopics))
        return True


class _FakeMemory:
    def __init__(self) -> None:
        self.knowledge: list[Memory] = []
        self.remembered: list[tuple[list[dict[str, str]], str]] = []
        self.digested: list[tuple[str, str]] = []
        self.evidence: list[str] = []

    async def search(self, query: str) -> list[Memory]:
        return self.knowledge

    async def search_behavior_experiences(
        self,
        query: str,
        target_skill: str,
    ) -> list[Memory]:
        return []

    async def remember_research_knowledge(
        self,
        receipt_id: str,
        items: list[dict[str, str]],
        correlation_id: str,
        source_chunk_id: str | None,
    ) -> None:
        await self.remember_knowledge(
            items,
            correlation_id,
            source_chunk_id=source_chunk_id,
        )

    async def remember_knowledge(
        self, items: list[dict[str, str]], correlation_id: str,
        *, source_chunk_id: str | None = None,
    ) -> None:
        self.remembered.append((items, correlation_id))

    async def record_reading_evidence(
        self, source_topic: str, source_name: str, content: str, block_key: str,
        *, location: str | None = None,
    ) -> str | None:
        self.evidence.append(content)
        return "test-chunk"

    async def digest_source_block(
        self,
        text: str,
        source_name: str,
        correlation_id: str,
        *,
        author: str = "",
        profile: dict[str, object] | None = None,
        source_chunk_id: str | None = None,
    ) -> tuple[dict[str, object], list[dict[str, str]]]:
        self.digested.append((source_name, text))
        return (
            {
                "summary": text[:20],
                "themes": [],
                "content_category": "unknown",
            },
            [{"topic": "来源知识", "content": f"来自正文：{text[:20]}"}],
        )


def _make_exploration(
    llm: _FakeLlm | None = None,
    tools: _FakeTools | None = None,
    web_enabled: bool = False,
    store: _FakeStore | None = None,
    desire: _FakeDesire | None = None,
    memory: _FakeMemory | None = None,
) -> ResearchEngine:
    return ResearchEngine(
        cast(LlmClient, llm if llm is not None else _FakeLlm()),
        cast(Evaluator, _FakeEvaluator()),
        cast(ToolRegistry, tools if tools is not None else _FakeTools()),
        cast(ActivityStore, store if store is not None else _FakeStore()),
        cast(MemoryFacade, memory if memory is not None else _FakeMemory()),
        ExplorationConfig(web_enabled=web_enabled),
    )


def _activity(progress: dict[str, Any] | None = None) -> Activity:
    return Activity(
        id="a1",
        type=ActivityType.FREE_EXPLORATION,
        schedule_block_id="09:00",
        status=ActivityStatus.RUNNING,
        progress=progress
        if progress is not None
        else {
            "goal": {"topic": "量子"},
            "description": "理解量子退相干",
            "correlation_id": "c1",
            "parent_long_term_id": "lt1",
        },
        started_at=1000.0,
    )


# ---- should_explore ----


def test_should_explore_rate_limited() -> None:
    # 频率未过（now - last < 1h*3600）→ False，与精力无关
    assert should_explore(1_000.0, 1, 1_000.0 + 3_599.0) is False


def test_should_explore_ok() -> None:
    # last=0（从未探索）+ 频率已过 → True；无 energy 入参（精力交 build_schedule 兜底）
    assert should_explore(0.0, 1, 20_000.0) is True


# ---- run：搜 → 抓正文 → 总结 ----


async def test_run_won_when_core_discovery() -> None:
    store = _FakeStore()
    desire = _FakeDesire()
    memory = _FakeMemory()
    expl = _make_exploration(
        web_enabled=True, store=store, desire=desire, memory=memory
    )
    result = await expl.run(_activity())
    assert result["type"] == "free_exploration"
    assert result["outcome"] == "won"
    assert result["core_discovery"] != ""
    assert result["knowledge"][0]["topic"] == "来源知识"
    assert result["strong_new_topics"] == ["量子纠错"]
    assert store.progress_updates[-1]["exploration"]["state"] == "completed"
    assert desire.appended_subtopics == []  # Engine has no desire side effects.
    assert memory.remembered[-1][0][0]["topic"] == "来源知识"
    assert len(memory.remembered) == 1  # Finalization does not ingest again.


async def test_run_without_parent_still_persists_knowledge() -> None:
    desire = _FakeDesire()
    memory = _FakeMemory()
    expl = _make_exploration(desire=desire, memory=memory)
    activity = _activity(
        {
            "goal": {"topic": "量子"},
            "description": "理解量子退相干",
            "correlation_id": "c1",
        }
    )
    await expl.run(activity)
    assert desire.appended_subtopics == []
    assert memory.remembered[-1][0][0]["topic"] == "来源知识"


async def test_run_web_disabled_uses_local_search() -> None:
    tools = _FakeTools()
    expl = _make_exploration(tools=tools, web_enabled=False)
    await expl.run(_activity())
    assert tools.calls[0][0] == "local_search"


async def test_run_local_search_results_flow_into_findings() -> None:
    # 回归：local_search 返回 {path, snippet}，name 取文件名、不再被 _result_parts 丢弃
    tools = _FakeTools()
    expl = _make_exploration(tools=tools, web_enabled=False)
    result = await expl.run(_activity())
    assert len(result["findings"]) == 1
    assert "量子.txt" in result["findings"][0]
    assert "本地文件的完整正文" in result["findings"][0]
    assert [name for name, _args in tools.calls][:2] == [
        "local_search",
        "file_io",
    ]


async def test_source_knowledge_keeps_web_url_scope() -> None:
    memory = _FakeMemory()
    expl = _make_exploration(web_enabled=True, memory=memory)
    await expl.run(_activity())
    source_items = memory.remembered[0][0]
    assert source_items[0]["source_topic"].startswith("web:")
    assert source_items[0]["source_name"] == "量子退相干"


async def test_run_web_enabled_uses_web_search() -> None:
    tools = _FakeTools()
    expl = _make_exploration(tools=tools, web_enabled=True)
    await expl.run(_activity())
    assert tools.calls[0][0] == "web_search"


async def test_run_fetch_failure_stops_without_ingestion() -> None:
    tools = _FakeTools(fetch_raises=True)
    memory = _FakeMemory()
    activity = _activity()
    expl = _make_exploration(tools=tools, web_enabled=True, memory=memory)
    with pytest.raises(RuntimeError, match="download fail"):
        await expl.run(activity)
    checkpoint = activity.progress["exploration"]
    assert checkpoint["state"] == "failed"
    assert checkpoint["tool_calls"][-1]["ok"] is False
    assert checkpoint["findings"] == []
    assert memory.digested == [] and memory.remembered == []
    assert "已读取" not in activity.progress["description"]


async def test_run_empty_source_skips_memory_digest() -> None:
    class EmptySourceTools(_FakeTools):
        async def call(self, name: str, args: dict[str, Any]) -> Any:
            self.calls.append((name, args))
            if name == "web_search":
                return [
                    {
                        "title": "空页面",
                        "url": "https://example.com/empty",
                        "snippet": "",
                    }
                ]
            if name == "web_fetch":
                return {"text": "", "url": args["url"]}
            return []

    memory = _FakeMemory()
    with pytest.raises(ValueError, match="正文"):
        await _make_exploration(
            tools=EmptySourceTools(), web_enabled=True, memory=memory
        ).run(_activity())

    assert memory.digested == []
    assert memory.remembered == []


async def test_run_exhausted_when_no_core_discovery() -> None:
    llm = _FakeLlm(content=json.dumps({"summary": "没啥发现"}))
    expl = _make_exploration(llm=llm, web_enabled=True)
    result = await expl.run(_activity())
    assert result["outcome"] == "exhausted"
    assert result["core_discovery"] == ""


async def test_run_llm_failure_retains_evidence_and_marks_failure() -> None:
    llm = _FakeLlm(content="不是 JSON")
    expl = _make_exploration(llm=llm, web_enabled=True)
    activity = _activity()
    with pytest.raises(ValueError):
        await expl.run(activity)
    assert activity.progress["exploration"]["state"] == "failed"
    assert activity.progress["exploration"]["findings"]


async def test_summarize_injects_related_memories() -> None:
    async def search(_topic: str) -> list[Memory]:
        return [
            Memory(
                id="m1",
                created_at=1.0,
                content="旧认知",
                kind=MemoryKind.KNOWLEDGE,
                summary="之前想过退相干",
                freshness=1.0,
                type=MemoryType.SHORT_TERM,
            )
        ]

    llm = _FakeLlm()
    memory = _FakeMemory()
    memory.knowledge = await search("量子")
    expl = _make_exploration(llm=llm, web_enabled=False, memory=memory)
    await expl.run(_activity())
    assert "之前想过退相干" in llm.user_contents[-1]


async def test_legacy_resume_rechecks_sources_without_admission_receipts() -> None:
    tools = _FakeTools()
    activity = _activity(
        {
            "goal": {"topic": "量子"},
            "correlation_id": "c1",
            "exploration": {
                "state": "reading_results",
                "topic": "量子",
                "raw_results": [
                    {
                        "title": "已读",
                        "url": "https://example.com/old",
                        "snippet": "旧",
                    },
                    {
                        "title": "新读",
                        "url": "https://example.com/new",
                        "snippet": "新",
                    },
                ],
                "cursor": 1,
                "findings": ["已读：旧正文"],
                "tool_calls": [
                    {
                        "name": "web_fetch",
                        "args": {"url": "https://example.com/old"},
                        "ok": True,
                    }
                ],
                "summary_done": False,
                "judged": None,
                "sink_done": False,
            },
        }
    )
    expl = _make_exploration(tools=tools, web_enabled=True)
    result = await expl.run(activity)
    fetched_urls = [args["url"] for name, args in tools.calls if name == "web_fetch"]
    assert fetched_urls[:2] == ["https://example.com/old", "https://example.com/new"]
    assert fetched_urls.count("https://example.com/old") == 1
    assert "旧正文" not in result["findings"][0]


async def test_resume_sinking_with_sink_done_skips_memory_and_desire() -> None:
    desire = _FakeDesire()
    memory = _FakeMemory()
    activity = _activity(
        {
            "goal": {"topic": "量子"},
            "correlation_id": "c1",
            "exploration": {
                "state": "sinking",
                "topic": "量子",
                "raw_results": [],
                "cursor": 0,
                "findings": ["量子：正文"],
                "tool_calls": [],
                "summary_done": True,
                "judged": json.loads(_FINALIZE_JSON),
                "sink_done": True,
            },
        }
    )
    expl = _make_exploration(desire=desire, memory=memory)
    result = await expl.run(activity)
    assert result["outcome"] == "won"
    assert desire.appended_subtopics == []
    assert memory.remembered == []


async def test_exploration_uses_card_identity() -> None:
    llm = _FakeLlm()
    exploration = ResearchEngine(
        cast(LlmClient, llm),
        cast(Evaluator, _FakeEvaluator()),
        cast(ToolRegistry, _FakeTools()),
        cast(ActivityStore, _FakeStore()),
        cast(MemoryFacade, _FakeMemory()),
        ExplorationConfig(),
        canon="你是 Alice，说话直接活泼。",
    )
    await exploration.run(_activity())
    assert "Alice，说话直接活泼" in llm.system_contents[0]
    assert "尼克斯" not in llm.system_contents[0]


async def test_exploration_excludes_self_sources_in_search_and_old_checkpoint() -> None:
    prompt = str(Path("nyx/prompts/ask.md").resolve())
    creation = str(Path("data/characters/nyx/workspace/creations/letter.md").resolve())

    class SelfOnlyTools(_FakeTools):
        async def call(self, name: str, args: dict[str, Any]) -> Any:
            self.calls.append((name, args))
            return [{"path": prompt, "snippet": "程序提示词"},
                    {"path": creation, "snippet": "自己的信"}]

    tools, memory, llm = SelfOnlyTools(), _FakeMemory(), _FakeLlm()
    exploration = _make_exploration(tools=tools, memory=memory, llm=llm)
    result = await exploration.run(_activity())
    assert result["outcome"] == "exhausted" and result["knowledge"] == []
    assert memory.digested == [] and memory.remembered == []
    assert "exploration_finalize" not in llm.calls
    activity = _activity()
    activity.progress["exploration"] = {
        "state": "sinking", "topic": "信", "cursor": 1,
        "raw_results": [{"path": creation}], "findings": ["自己的旧信"],
        "source_pending": {"source_text": "自己的旧信"},
        "summary_done": True, "judged": {"knowledge": [{"content": "旧信知识"}]},
    }
    await exploration.run(activity)
    assert memory.digested == [] and memory.remembered == []
    assert not any(name == "file_io" for name, _args in tools.calls)


async def test_reject_then_rewrite_and_follow_up_only_ingests_accepted_pages() -> None:
    class EvidenceTools(_FakeTools):
        async def call(self, name: str, args: dict[str, Any]) -> Any:
            self.calls.append((name, args))
            if name == "web_search":
                return [{"title": args["query"], "url": f'https://example.com/{len(self.calls)}'}]
            return {"text": "偏题广告" if len(self.calls) == 2 else "相关原文证据"}

    class EvidenceLlm(_FakeLlm):
        def __init__(self) -> None:
            super().__init__()
            self.decisions = iter([
                {"action": "search", "query": "理解量子退相干"},
                {"action": "read", "index": 0},
                {"action": "rewrite", "question": "环境怎样抑制相干性"},
                {"action": "search", "query": "环境 退相干 机制"},
                {"action": "read", "index": 1},
                {"action": "follow_up", "question": "退相干为何不等于坍缩"},
                {"action": "search", "query": "退相干 坍缩 区别"},
                {"action": "read", "index": 2},
                {"action": "finish", "unresolved_questions": ["量子纠错如何保相干"]},
            ])

        async def complete(
            self, messages: list[LlmMessage], **kwargs: Any,
        ) -> LLMOutput:
            output = await super().complete(messages, **kwargs)
            if kwargs["output_type"] == "exploration_decide":
                output.content = json.dumps(next(self.decisions), ensure_ascii=False)
            elif kwargs["output_type"] == "exploration_source_check":
                relevant = "偏题广告" not in messages[1]["content"]
                output.content = json.dumps({
                    "relevant": relevant, "usable": relevant, "reason": "资料准入",
                })
            return output

    tools, memory, store = EvidenceTools(), _FakeMemory(), _FakeStore()
    activity = _activity()
    result = await _make_exploration(
        tools=tools, memory=memory, store=store, llm=EvidenceLlm(), web_enabled=True,
    ).run(activity)
    assert len(memory.evidence) == len(memory.digested) == len(memory.remembered) == 2
    assert all("偏题广告" not in text for text in memory.evidence)
    assert len(result["knowledge"]) == 2
    assert [args["query"] for name, args in tools.calls if name == "web_search"] == [
        "理解量子退相干", "环境 退相干 机制", "退相干 坍缩 区别",
    ]
    assert "follow_up" in [action["name"] for action in result["actions"]]
    assert result["unresolved_questions"] == ["量子纠错如何保相干"]
    for progress in store.progress_updates:
        if progress["description"].startswith("已读取网页"):
            assert any(
                call.get("ok") is True and call["name"] == "web_fetch"
                for call in progress["exploration"]["tool_calls"]
            )


@pytest.mark.parametrize("payload", [{"error": "403 拒绝访问"}, {"text": ""}])
async def test_fetch_business_failures_stop_even_with_a_good_snippet(
    payload: dict[str, str],
) -> None:
    class ErrorTools(_FakeTools):
        async def call(self, name: str, args: dict[str, Any]) -> Any:
            if name == "web_fetch":
                self.calls.append((name, args))
                return payload
            return await super().call(name, args)

    activity, memory = _activity(), _FakeMemory()
    with pytest.raises(ValueError):
        await _make_exploration(
            tools=ErrorTools(), memory=memory, web_enabled=True,
        ).run(activity)
    assert memory.evidence == memory.remembered == []
    assert activity.progress["exploration"]["tool_calls"][-1]["ok"] is False


async def test_cancel_during_digest_resumes_admitted_source_without_refetch() -> None:
    class InterruptedMemory(_FakeMemory):
        interrupted = False

        async def digest_source_block(
            self, text: str, source_name: str, correlation_id: str, **kwargs: Any,
        ) -> tuple[dict[str, object], list[dict[str, str]]]:
            if not self.interrupted:
                self.interrupted = True
                raise asyncio.CancelledError
            return await super().digest_source_block(
                text, source_name, correlation_id, **kwargs,
            )

    tools, memory, llm = _FakeTools(), InterruptedMemory(), _FakeLlm()
    exploration = _make_exploration(
        tools=tools, memory=memory, llm=llm, web_enabled=True,
    )
    activity = _activity()
    with pytest.raises(asyncio.CancelledError):
        await exploration.run(activity)
    saved = activity.progress["exploration"]
    assert saved["source_pending"]["assessment"]["relevant"] is True
    assert saved["sources"] == saved["searches"] == 1
    result = await exploration.run(activity)
    assert result["outcome"] == "won"
    assert [name for name, _ in tools.calls] == ["web_search", "web_fetch"]
    assert len(memory.evidence) == len(memory.remembered) == 1
    assert llm.calls.count("exploration_source_check") == 1
    assert activity.progress["exploration"]["sources"] == 1


async def test_repeated_decisions_stop_at_durable_step_budget() -> None:
    class RepeatingLlm(_FakeLlm):
        async def complete(
            self, messages: list[LlmMessage], **kwargs: Any,
        ) -> LLMOutput:
            output = await super().complete(messages, **kwargs)
            if kwargs["output_type"] == "exploration_decide":
                output.content = '{"action":"search","query":"重复词"}'
            return output

    tools, activity = _FakeTools(), _activity()
    result = await _make_exploration(tools=tools, llm=RepeatingLlm()).run(activity)
    assert len(tools.calls) == 1
    assert activity.progress["exploration"]["steps"] == 12
    assert "预算" in result["stop_reason"]
    assert result["outcome"] == "exhausted"
    assert result["unresolved_questions"]


async def test_partial_ingestion_survives_a_later_source_failure() -> None:
    class PartialTools(_FakeTools):
        async def call(self, name: str, args: dict[str, Any]) -> Any:
            if name == "web_search":
                self.calls.append((name, args))
                return [{"title": "可读来源", "url": "https://example.com/a"},
                        {"title": "失败来源", "url": "https://example.com/b"}]
            if name == "web_fetch" and args["url"].endswith("/b"):
                self.calls.append((name, args))
                return {"error": "403"}
            return await super().call(name, args)

    memory, activity = _FakeMemory(), _activity()
    with pytest.raises(ValueError, match="403"):
        await _make_exploration(
            tools=PartialTools(), memory=memory, web_enabled=True,
        ).run(activity)
    assert len(memory.remembered) == len(memory.evidence) == 1
    assert len(activity.progress["exploration"]["knowledge"]) == 1
    assert activity.progress["exploration"]["state"] == "failed"


async def test_resumed_elapsed_budget_stops_before_more_tools() -> None:
    tools, activity = _FakeTools(), _activity()
    activity.progress["exploration"] = {
        "version": 2, "state": "deciding", "elapsed_seconds": 120.0,
        "searches": 3, "sources": 2, "steps": 7,
    }
    with pytest.raises(TimeoutError, match="时间预算"):
        await _make_exploration(tools=tools).run(activity)
    assert tools.calls == []
    assert activity.progress["exploration"]["searches"] == 3
    assert "时间预算" in str(activity.progress["exploration"]["failure_reason"])


@pytest.mark.parametrize("interrupted_tool", ["web_search", "web_fetch"])
async def test_cancelled_tool_resumes_the_pending_action_once(
    interrupted_tool: str,
) -> None:
    class InterruptedTools(_FakeTools):
        interrupted = False

        async def call(self, name: str, args: dict[str, Any]) -> Any:
            if name == interrupted_tool and not self.interrupted:
                self.interrupted = True
                self.calls.append((name, args))
                raise asyncio.CancelledError
            return await super().call(name, args)

    tools, memory, activity = InterruptedTools(), _FakeMemory(), _activity()
    exploration = _make_exploration(tools=tools, memory=memory, web_enabled=True)
    with pytest.raises(asyncio.CancelledError):
        await exploration.run(activity)
    # A process restart serializes the checkpoint, losing shared dict references.
    activity.progress = json.loads(json.dumps(activity.progress))
    result = await exploration.run(activity)
    assert result["outcome"] == "won"
    assert len(memory.evidence) == len(memory.remembered) == 1
    assert activity.progress["exploration"]["searches"] == 1
    assert activity.progress["exploration"]["sources"] == 1
    assert [name for name, _ in tools.calls].count(interrupted_tool) == 2
    assert not any(action.get("status") == "rejected" for action in result["actions"])


class _DecisionLlm(_FakeLlm):
    def __init__(self, decisions: list[str | BaseException]) -> None:
        super().__init__()
        self.decisions = iter(decisions)

    async def complete(
        self, messages: list[LlmMessage], **kwargs: Any,
    ) -> LLMOutput:
        output = await super().complete(messages, **kwargs)
        if kwargs["output_type"] == "exploration_decide":
            decision = next(self.decisions)
            if isinstance(decision, BaseException):
                raise decision
            output.content = decision
            output.prompt_tokens = 11
            output.completion_tokens = 7
        return output


@pytest.mark.parametrize(
    ("invalid", "error"),
    [
        ('{"action":', "无效 JSON"),
        ('[]', "JSON 对象"),
        ('{"action":[]}', "available_actions"),
        ('{"action":"unknown"}', "available_actions"),
        ('{"action":"ask_user"}', "ask_user.question"),
        ('{"action":"ask_user","question":null}', "ask_user.question"),
        ('{"action":"ask_user","question":"  "}', "ask_user.question"),
        ('{"action":"ask_user","question":123}', "ask_user.question"),
        ('{"action":"ask_user","question":[]}', "ask_user.question"),
        ('{"action":"rewrite","question":""}', "rewrite.question"),
        ('{"action":"follow_up"}', "follow_up.question"),
        ('{"action":"search","query":""}', "search.query"),
        ('{"action":"knowledge_search","query":null}', "knowledge_search.query"),
        ('{"action":"read","index":true}', "read.index"),
        ('{"action":"read","index":3}', "read.index"),
        ('{"action":"read","index":"0"}', "read.index"),
        ('{"action":"finish","unresolved_questions":[1]}', "字符串数组"),
        ('{"action":"finish","reason":null}', "reason"),
        ('{"action":"search","query":"词","budget_reserved":true}', "未知字段"),
    ],
)
async def test_invalid_decision_is_corrected_before_any_action(
    invalid: str, error: str,
) -> None:
    llm = _DecisionLlm([
        invalid, '{"action":"ask_user","question":"  您想了解哪个方面？  "}',
    ])
    activity, tools, store = _activity(), _FakeTools(), _FakeStore()
    activity.progress["exploration"] = {
        "version": 2,
        "raw_results": [{"url": "https://example.com/a"}],
    }
    exploration = _make_exploration(llm=llm, tools=tools, store=store)
    with pytest.raises(ResearchWaiting, match="您想了解哪个方面？"):
        await exploration.run(activity)
    feedback = json.loads(llm.user_contents[-1])["validation_feedback"]
    assert feedback == [{"output": invalid, "error": feedback[0]["error"]}]
    assert error in feedback[0]["error"]
    checkpoint: dict[str, Any] = activity.progress["exploration"]
    assert checkpoint["state"] == "waiting_user"
    assert checkpoint["waiting_question"] == "您想了解哪个方面？"
    assert checkpoint["steps"] == 1 and checkpoint["tokens"] == 36
    assert checkpoint["model_calls"][0]["validation_error"] == feedback[0]["error"]
    assert len(cast(_FakeEvaluator, exploration._evaluator).evaluated) == 2
    assert tools.calls == []
    assert all(p["exploration"]["state"] != "failed" for p in store.progress_updates)


async def test_decision_can_succeed_on_second_regeneration() -> None:
    llm = _DecisionLlm([
        '{"action":"ask_user"}',
        '{"action":"ask_user","question":null}',
        '{"action":"ask_user","question":"需要哪种资料？"}',
    ])
    activity = _activity()
    with pytest.raises(ResearchWaiting):
        await _make_exploration(llm=llm).run(activity)
    assert len(llm.calls) == 3
    assert len(json.loads(llm.user_contents[-1])["validation_feedback"]) == 2
    assert activity.progress["exploration"]["state"] == "waiting_user"


async def test_invalid_decision_fails_only_after_two_regenerations() -> None:
    llm = _DecisionLlm(['{"action":"ask_user"}'] * 4)
    activity, store, tools = _activity(), _FakeStore(), _FakeTools()
    exploration = _make_exploration(llm=llm, store=store, tools=tools)
    with pytest.raises(ValueError, match="2 次重新生成.*ask_user.question"):
        await exploration.run(activity)
    assert len(llm.calls) == 3
    checkpoint = activity.progress["exploration"]
    assert checkpoint["state"] == "failed" and checkpoint["steps"] == 0
    assert checkpoint["tokens"] == 54
    assert "ask_user.question" in checkpoint["failure_reason"]
    assert tools.calls == []
    assert all(
        len(p["exploration"]["decision_errors"]) == 3
        for p in store.progress_updates if p["exploration"]["state"] == "failed"
    )
    with pytest.raises(ValueError):
        await exploration.run(activity)
    assert len(llm.calls) == 3


async def test_decision_correction_budget_survives_cancellation_and_restart() -> None:
    llm = _DecisionLlm([
        '{"action":"ask_user"}', asyncio.CancelledError(),
        '{"action":"ask_user","question":""}', '{"action":"ask_user"}',
        '{"action":"ask_user","question":"不该再调用"}',
    ])
    activity = _activity()
    exploration = _make_exploration(llm=llm)
    with pytest.raises(asyncio.CancelledError):
        await exploration.run(activity)
    assert activity.progress["exploration"]["state"] != "failed"
    activity.progress = json.loads(json.dumps(activity.progress))
    with pytest.raises(ValueError, match="2 次重新生成"):
        await exploration.run(activity)
    assert len(activity.progress["exploration"]["model_calls"]) == 3
    assert len(llm.calls) == 4  # The cancelled request returned no model output.


async def test_model_transport_error_is_not_an_output_correction() -> None:
    llm = _DecisionLlm([ValueError("provider rejected request")])
    activity = _activity()
    with pytest.raises(ValueError, match="provider rejected request"):
        await _make_exploration(llm=llm).run(activity)
    assert len(llm.calls) == 1
    assert activity.progress["exploration"]["decision_errors"] == []
