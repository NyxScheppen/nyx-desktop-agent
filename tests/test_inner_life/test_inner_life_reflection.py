# pyright: reportPrivateUsage=false
import json
from dataclasses import replace
from typing import Any, cast

import pytest

from nyx import db
from nyx.config import DesireConfig
from nyx.desire.facade import DesireFacade
from nyx.desire.store import DesireStore
from nyx.enums import DesireType, MemoryKind, MemoryType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.inner_life.reflection import Reflection
from nyx.inner_life.store import InnerLifeStore
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.facade import MemoryFacade
from nyx.memory.store import MemoryStore
from nyx.types import (
    ConversationSegment,
    DesireState,
    InnerDocument,
    InnerTopic,
    LLMOutput,
    LongTermDesire,
    Memory,
    MemoryFact,
    ReadingEvidence,
)

_REFLECTION_JSON = json.dumps(
    {
        "summary": "今天对用户了解更多",
        "patches": [
            {
                "op": "set",
                "section": "relationships",
                "id": "trust",
                "title": "信任",
                "content": "我愿意表达自己的不同意见。",
                "reason": "用户愿意听见我的想法",
                "evidence": [{"id": "fact:fixture", "quote": "愿意听我表达不同意见"}],
            }
        ],
        "long_term_desires": [
            {
                "type": "exploration",
                "name": "探索骑士团",
                "description": "了解骑士团历史",
                "subtopics": ["骑士团"],
            }
        ],
    }
)


_NARRATIVE = InnerDocument(
    topics=[
        InnerTopic("identity", "self-understanding", "自我理解", "尼克斯"),
        InnerTopic("narrative", "experience-0", "个人经历", "初始故事"),
        InnerTopic("personality", "self-view-0", "自信", "中等"),
        InnerTopic("narrative", "understanding-0", "认知变化", "初始认知"),
    ],
    revision=1,
    updated_at=1000.0,
)


class _FakeLlm:
    def __init__(self, response: str = _REFLECTION_JSON) -> None:
        self._response = response
        self.calls: list[str] = []
        self.correlation_ids: list[str] = []
        self.user_contents: list[str] = []

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
        self.user_contents.append(messages[-1]["content"])
        return LLMOutput(
            module=module,
            type=output_type,
            model="fake",
            content=self._response,
            correlation_id=correlation_id,
        )


class _FakeEvaluator:
    def __init__(self) -> None:
        self.evaluated: list[LLMOutput] = []

    async def evaluate(self, output: LLMOutput) -> None:
        self.evaluated.append(output)


class _FakeMemoryFacade:
    def __init__(self, memories: list[Memory] | None = None) -> None:
        self._memories = memories if memories is not None else []
        self.evidence_store: MemoryStore | None = None

    async def recent_facts(self) -> list[MemoryFact]:
        return [
            MemoryFact(
                "fixture",
                "用户",
                "沟通偏好",
                "愿意听我表达不同意见",
                1.0,
                None,
                None,
                1.0,
            )
        ]

    async def browse_conversations(
        self, *, limit: int = 6
    ) -> list[ConversationSegment]:
        return []

    async def prepare_reflection_record(self, content: str, summary: str) -> Memory:
        return Memory(
            id="reflection",
            created_at=1,
            content=content,
            summary=summary,
            kind=MemoryKind.REFLECTION,
            type=MemoryType.LONG_TERM,
            freshness=1,
        )

    async def commit_reflection_record(
        self, memory: Memory, correlation_id: str
    ) -> None:
        pass

    async def pending_reading_evidence(self, limit: int = 3) -> list[ReadingEvidence]:
        if self.evidence_store is None:
            return []
        return await self.evidence_store.pending_reading_evidence(limit)

    async def consume_reading_evidence(self, ids: tuple[str, ...]) -> None:
        assert self.evidence_store is not None
        await self.evidence_store.consume_reading_evidence(list(ids))

    async def list_memories(
        self,
        kind: MemoryKind | None = None,
        type: MemoryType | None = None,
        limit: int | None = None,
    ) -> list[Memory]:
        del type, limit
        if kind is None:
            return self._memories
        return [m for m in self._memories if m.kind is kind]

    async def count_new(self, kind: MemoryKind | None, since: float) -> int:
        return sum(
            1
            for m in self._memories
            if (kind is None or m.kind is kind) and m.created_at > since
        )

    async def inner_reflection_memories(self) -> list[Memory]:
        return [
            m for m in self._memories
            if m.kind not in {MemoryKind.REFLECTION, MemoryKind.LESSON}
        ]


class _FakeDesireFacade:
    def __init__(
        self,
        long_term: list[LongTermDesire] | None = None,
        capacity: int = 5,
    ) -> None:
        self._long_term = long_term if long_term is not None else []
        self._capacity = capacity
        self.added: list[LongTermDesire] = []
        self.pressured: list[float] = []
        self.prepared = 0
        self.committed = 0

    async def get_all(self) -> DesireState:
        return DesireState(values=[], short_term=[], long_term=self._long_term)

    async def prepare_long_term_candidates(
        self, desires: tuple[LongTermDesire, ...]
    ) -> tuple[tuple[LongTermDesire, ...], tuple[tuple[str, str, str], ...]]:
        self.prepared += 1
        remaining = max(0, self._capacity - len(self._long_term))
        snapshot = tuple((d.id, d.name, d.description) for d in self._long_term)
        return desires[:remaining], snapshot

    async def add_prepared_long_terms_in_transaction(
        self,
        desires: tuple[LongTermDesire, ...],
        snapshot: tuple[tuple[str, str, str], ...],
    ) -> None:
        del snapshot
        self.committed += 1
        self.added.extend(desires)

    async def pressure_creation(self, delta: float) -> None:
        self.pressured.append(delta)


def _make_reflection(
    store: InnerLifeStore,
    llm: _FakeLlm,
    evaluator: _FakeEvaluator,
    memory: _FakeMemoryFacade,
    desire: _FakeDesireFacade,
    config: DesireConfig | None = None,
) -> Reflection:
    return Reflection(
        store,
        cast(MemoryFacade, memory),
        cast(DesireFacade, desire),
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        config if config is not None else DesireConfig(),
    )


async def _seed(store: InnerLifeStore) -> None:
    await store.upsert_document(_NARRATIVE)


# ---- reflection.run ----


async def test_prepare_includes_trigger_evidence_and_uses_creation_recency() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    old = Memory(
        id="old",
        created_at=100.0,
        content="旧内容",
        kind=MemoryKind.EPISODE,
        summary="高新鲜度旧记忆",
        freshness=1.0,
        type=MemoryType.LONG_TERM,
    )
    new = Memory(
        id="new",
        created_at=200.0,
        content="新内容",
        kind=MemoryKind.EPISODE,
        summary="低新鲜度新记忆",
        freshness=0.1,
        type=MemoryType.SHORT_TERM,
    )
    llm = _FakeLlm()
    reflection = _make_reflection(
        store,
        llm,
        _FakeEvaluator(),
        _FakeMemoryFacade([
            old, new,
            replace(new, id="strategy", kind=MemoryKind.LESSON, content="检索方法经验"),
        ]),
        _FakeDesireFacade(),
    )
    try:
        await _seed(store)
        await reflection.prepare(
            "cid",
            {
                "reason": "memory_contradiction",
                "memory_id": "new",
                "conflicts_with": "old",
                "evidence": [
                    {"memory_id": "new", "summary": "低新鲜度新记忆"},
                    {"memory_id": "old", "summary": "高新鲜度旧记忆"},
                ],
            },
        )
        prompt = llm.user_contents[0]
        assert "触发原因：记忆矛盾" in prompt
        assert "低新鲜度新记忆" in prompt
        assert prompt.index("[memory:new]") < prompt.index("[memory:old]")
        assert "[memory:strategy]" not in prompt and "检索方法经验" not in prompt
    finally:
        await database.conn.close()


async def test_run_writes_back() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    desire = _FakeDesireFacade()
    reflection = _make_reflection(store, llm, evaluator, _FakeMemoryFacade(), desire)
    try:
        await _seed(store)
        await reflection.run("cid")

        assert llm.calls == ["reflection"]
        assert llm.correlation_ids == ["cid"]
        assert [o.type for o in evaluator.evaluated] == ["reflection"]

        n = await store.get_document()
        assert n is not None and n.revision == 2
        assert any(t.id == "trust" for t in n.topics)
        assert all(t in n.topics for t in _NARRATIVE.topics)

        assert len(desire.added) == 1
        assert desire.prepared == 1
        assert desire.committed == 1
    finally:
        await database.conn.close()


async def test_run_pressures_creation() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    desire = _FakeDesireFacade()
    reflection = _make_reflection(
        store, _FakeLlm(), _FakeEvaluator(), _FakeMemoryFacade(), desire
    )
    try:
        await _seed(store)
        await reflection.run()
        assert desire.pressured == [0.2]  # 反思成功 → 创造欲 +0.2
    finally:
        await database.conn.close()


async def test_run_without_patches_preserves_document() -> None:
    # 空修订保留正文和版本，只更新最近反思时间
    response = json.dumps(
        {"summary": "初始故事", "patches": [], "long_term_desires": []}
    )
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm(response)
    desire = _FakeDesireFacade()
    reflection = _make_reflection(
        store, llm, _FakeEvaluator(), _FakeMemoryFacade(), desire
    )
    try:
        await _seed(store)
        await reflection.run()
        n = await store.get_document()
        assert n is not None
        assert n.topics == _NARRATIVE.topics
        assert n.revision == 1
        assert n.updated_at > _NARRATIVE.updated_at
    finally:
        await database.conn.close()


async def test_run_returns_outcome_document_changed() -> None:
    # 有主题修订时返回文档变化标志
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm()
    reflection = _make_reflection(
        store, llm, _FakeEvaluator(), _FakeMemoryFacade(), _FakeDesireFacade()
    )
    try:
        await _seed(store)
        outcome = await reflection.run("cid")
        assert outcome is not None
        assert outcome.summary == "今天对用户了解更多"
        assert outcome.document_changed is True
    finally:
        await database.conn.close()


async def test_run_returns_outcome_without_document_change() -> None:
    # 空修订仍返回摘要，但不产生文档变化
    response = json.dumps(
        {"summary": "初始故事", "patches": [], "long_term_desires": []}
    )
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm(response)
    reflection = _make_reflection(
        store, llm, _FakeEvaluator(), _FakeMemoryFacade(), _FakeDesireFacade()
    )
    try:
        await _seed(store)
        outcome = await reflection.run()
        assert outcome is not None
        assert outcome.summary == "初始故事"
        assert outcome.document_changed is False
    finally:
        await database.conn.close()


async def test_run_generates_correlation_id() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm()
    desire = _FakeDesireFacade()
    reflection = _make_reflection(
        store, llm, _FakeEvaluator(), _FakeMemoryFacade(), desire
    )
    try:
        await _seed(store)
        await reflection.run(None)
        assert llm.correlation_ids[0] != ""
    finally:
        await database.conn.close()


async def test_run_long_term_capacity() -> None:
    candidates: list[dict[str, Any]] = [
        {
            "type": "interaction",
            "name": f"n{i}",
            "description": "d",
            "subtopics": [],
        }
        for i in range(3)
    ]
    response = json.dumps(
        {
            "summary": "s",
            "patches": [
                {
                    "op": "set",
                    "section": "relationships",
                    "id": "trust",
                    "title": "信任",
                    "content": "我愿意表达自己的不同意见。",
                    "reason": "用户愿意听见我的想法",
                    "evidence": [
                        {"id": "fact:fixture", "quote": "愿意听我表达不同意见"}
                    ],
                }
            ],
            "long_term_desires": candidates,
        }
    )
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm(response)
    desire = _FakeDesireFacade(capacity=2)
    reflection = _make_reflection(
        store,
        llm,
        _FakeEvaluator(),
        _FakeMemoryFacade(),
        desire,
        DesireConfig(long_term_capacity=2),
    )
    try:
        await _seed(store)
        await reflection.run()
        assert len(desire.added) == 2
    finally:
        await database.conn.close()


async def test_run_survives_bad_candidate() -> None:
    response = json.dumps(
        {
            "summary": "s",
            "patches": [
                {
                    "op": "set",
                    "section": "relationships",
                    "id": "trust",
                    "title": "信任",
                    "content": "我愿意表达自己的不同意见。",
                    "reason": "用户愿意听见我的想法",
                    "evidence": [
                        {"id": "fact:fixture", "quote": "愿意听我表达不同意见"}
                    ],
                }
            ],
            "long_term_desires": [
                {
                    "type": "exploration",
                    "name": "n",
                    "description": "d",
                    "subtopics": ["x"],
                },
                {
                    "type": "fly",
                    "name": "bad",
                    "description": "d",
                    "subtopics": [],
                },
            ],
        }
    )
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm(response)
    desire = _FakeDesireFacade()
    reflection = _make_reflection(
        store, llm, _FakeEvaluator(), _FakeMemoryFacade(), desire
    )
    try:
        await _seed(store)
        await reflection.run()
        # 核心慢变量不受坏候选影响，照常回写
        n = await store.get_document()
        assert n is not None and n.revision == 2
        # 只有好候选被新增
        assert len(desire.added) == 1
        assert desire.added[0].name == "n"
    finally:
        await database.conn.close()


async def test_run_real_desire_facade_commits_without_nested_transaction() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    desire_store = DesireStore(database)
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    embed_transaction_states: list[bool] = []

    async def list_memories() -> list[Memory]:
        return []

    async def embed(text: str) -> list[float]:
        del text
        embed_transaction_states.append(database.in_transaction)
        return [1.0, 0.0]

    desire = DesireFacade(
        desire_store,
        EventBus(database),
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        DesireConfig(),
        list_memories,
        embed,
    )
    reflection = Reflection(
        store,
        cast(MemoryFacade, _FakeMemoryFacade()),
        desire,
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        DesireConfig(),
    )
    try:
        await _seed(store)
        await reflection.run("cid")
        saved = await desire_store.list_long_term()
        assert saved[0].name == "探索骑士团"
        assert embed_transaction_states == [False]
    finally:
        await database.conn.close()


async def test_run_embedding_failure_preserves_all_reflection_state() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    desire_store = DesireStore(database)
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()

    async def list_memories() -> list[Memory]:
        return []

    async def embed(text: str) -> list[float]:
        del text
        raise RuntimeError("embed down")

    desire = DesireFacade(
        desire_store,
        EventBus(database),
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        DesireConfig(),
        list_memories,
        embed,
    )
    reflection = Reflection(
        store,
        cast(MemoryFacade, _FakeMemoryFacade()),
        desire,
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        DesireConfig(),
    )
    try:
        await _seed(store)
        with pytest.raises(RuntimeError, match="embed down"):
            await reflection.run("cid")
        assert await store.get_document() == _NARRATIVE
        assert await desire_store.list_long_term() == []
        assert await desire_store.get_value(DesireType.CREATION) is None
    finally:
        await database.conn.close()


async def test_run_commit_failure_rolls_back_long_term_and_slow_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    desire_store = DesireStore(database)
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()

    async def list_memories() -> list[Memory]:
        return []

    async def fail_pressure(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("pressure write failed")

    desire = DesireFacade(
        desire_store,
        EventBus(database),
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        DesireConfig(),
        list_memories,
    )
    reflection = Reflection(
        store,
        cast(MemoryFacade, _FakeMemoryFacade()),
        desire,
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        DesireConfig(),
    )
    monkeypatch.setattr(desire_store, "apply_value_delta", fail_pressure)
    try:
        await _seed(store)
        with pytest.raises(RuntimeError, match="pressure write failed"):
            await reflection.run("cid")
        assert await store.get_document() == _NARRATIVE
        assert await desire_store.list_long_term() == []
        assert await desire_store.get_value(DesireType.CREATION) is None
    finally:
        await database.conn.close()


async def test_apply_snapshot_conflict_rolls_back_reflection() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    desire_store = DesireStore(database)
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()

    async def list_memories() -> list[Memory]:
        return []

    desire = DesireFacade(
        desire_store,
        EventBus(database),
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        DesireConfig(),
        list_memories,
    )
    reflection = Reflection(
        store,
        cast(MemoryFacade, _FakeMemoryFacade()),
        desire,
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        DesireConfig(),
    )
    external = LongTermDesire(
        id="external",
        created_at=1.0,
        type=DesireType.EXPLORATION,
        name="外部新增",
        description="并发变化",
        strength=0.5,
        progress=0.0,
        subtopics=[],
    )
    try:
        await _seed(store)
        plan = await reflection.prepare("cid")
        await desire_store.insert_long_term(external)
        with pytest.raises(RuntimeError, match="快照"):
            async with database.transaction():
                await reflection.apply(plan)
        assert await store.get_document() == _NARRATIVE
        assert [d.id for d in await desire_store.list_long_term()] == ["external"]
        assert await desire_store.get_value(DesireType.CREATION) is None
    finally:
        await database.conn.close()


async def test_apply_slow_variable_conflict_rolls_back_reflection() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm()
    desire = _FakeDesireFacade()
    reflection = _make_reflection(
        store, llm, _FakeEvaluator(), _FakeMemoryFacade(), desire
    )
    try:
        await _seed(store)
        plan = await reflection.prepare("cid")
        await store.upsert_document(InnerDocument(_NARRATIVE.topics, 2, 2000.0))

        with pytest.raises(RuntimeError, match="文档快照"):
            async with database.transaction():
                await reflection.apply(plan)

        narrative = await store.get_document()
        assert narrative is not None
        assert narrative.revision == 2
        assert narrative.updated_at == 2000.0
        assert desire.pressured == []
    finally:
        await database.conn.close()


async def test_run_rejects_invalid_json_for_delivery_retry() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm("[")  # 截断的非法 JSON
    desire = _FakeDesireFacade()
    reflection = _make_reflection(
        store, llm, _FakeEvaluator(), _FakeMemoryFacade(), desire
    )
    try:
        await _seed(store)
        with pytest.raises(ValueError):
            await reflection.run("cid")
        assert llm.calls == ["reflection"]
        assert await store.get_document() == _NARRATIVE
        assert desire.added == []
        assert desire.pressured == []  # 失败路径不触发创造欲加压
    finally:
        await database.conn.close()


async def test_run_unseeded_raises() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm()
    reflection = _make_reflection(store, llm, _FakeEvaluator(), _FakeMemoryFacade(),
                                  _FakeDesireFacade())
    try:
        with pytest.raises(RuntimeError):
            await reflection.run()
        assert llm.calls == []
    finally:
        await database.conn.close()


@pytest.mark.parametrize("mode", ["change", "no-change", "invalid", "rollback"])
@pytest.mark.parametrize("section", ["worldview", "aesthetic"])
async def test_document_and_reading_evidence_commit_together(
    mode: str,
    section: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    memory_store = MemoryStore(database)
    memory = _FakeMemoryFacade()
    memory.evidence_store = memory_store
    desire = _FakeDesireFacade()
    try:
        await _seed(store)
        evidence_id = await memory_store.record_reading_evidence(
            "book:fixture",
            "小说原文",
            "她没有说安慰的话，只把灯留在了窗边。",
            "p1",
        )
        assert evidence_id is not None
        patches = [
            {
                "op": "set",
                "section": section,
                "id": "quiet-care",
                "title": "细节中的关心",
                "content": "我开始留意不直说关心的小动作。",
                "reason": "留灯这个动作承载了感情",
                "evidence": [
                    {"id": f"reading:{evidence_id}", "quote": "把灯留在了窗边"}
                ],
            }
        ]
        consumed: list[str] = []
        if mode == "no-change":
            patches = []
            consumed = [f"reading:{evidence_id}"]
        elif mode == "invalid":
            patches[0]["evidence"] = [
                {"id": f"reading:{evidence_id}", "quote": "原文没有这句话"}
            ]
        llm = _FakeLlm(
            json.dumps(
                {
                    "summary": "我想了想这段文字。",
                    "patches": patches,
                    "consumed_reading_ids": consumed,
                }
            )
        )
        reflection = _make_reflection(store, llm, _FakeEvaluator(), memory, desire)
        if mode == "invalid":
            with pytest.raises(ValueError, match="引用"):
                await reflection.run()
            assert await store.get_document() == _NARRATIVE
            assert len(await memory_store.pending_reading_evidence()) == 1
            return
        plan = await reflection.prepare("reading-test")
        if mode == "rollback":
            original = desire.pressure_creation

            async def fail(_delta: float) -> None:
                raise RuntimeError("commit failed")

            monkeypatch.setattr(desire, "pressure_creation", fail)
            with pytest.raises(RuntimeError, match="commit failed"):
                async with database.transaction():
                    await reflection.apply(plan)
            assert await store.get_document() == _NARRATIVE
            assert len(await memory_store.pending_reading_evidence()) == 1
            monkeypatch.setattr(desire, "pressure_creation", original)
        async with database.transaction():
            await reflection.apply(plan)
        document = await store.get_document()
        assert document is not None
        assert document.revision == (1 if mode == "no-change" else 2)
        assert await memory_store.pending_reading_evidence() == []
        with pytest.raises(RuntimeError, match="快照"):
            async with database.transaction():
                await reflection.apply(plan)
        assert llm.calls == ["reflection"]
    finally:
        await database.close()


@pytest.mark.parametrize("section", ["worldview", "aesthetic"])
async def test_document_topics_can_change_from_conversation(section: str) -> None:
    conversation = "我喜欢你把不确定的地方也说出来，不用为了好听修饰它。"
    memory = _FakeMemoryFacade([
        Memory(
            id="feedback", created_at=1100, content=conversation,
            summary="用户希望我保留真实的不确定性", freshness=1.0,
            kind=MemoryKind.USER_PROFILE, type=MemoryType.SHORT_TERM,
        )
    ])
    llm = _FakeLlm(json.dumps({
        "summary": "我想保留表达中的真实。",
        "patches": [{
            "op": "set", "section": section, "id": "honest-expression",
            "title": "表达中的真实",
            "content": "我更愿意保留不确定，也在意朴素的真实。",
            "reason": "用户的反馈让我重新看待修饰和诚实的关系",
            "evidence": [{"id": "memory:feedback", "quote": conversation}],
        }],
    }))
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    try:
        await _seed(store)
        reflection = _make_reflection(
            store, llm, _FakeEvaluator(), memory, _FakeDesireFacade()
        )
        outcome = await reflection.run()
        document = await store.get_document()
        assert document is not None and document.revision == 2
        assert outcome.document_changed
        assert document.topics[-1].section == section
        assert document.topics[:-1] == _NARRATIVE.topics
        assert llm.calls == ["reflection"]
    finally:
        await database.close()
