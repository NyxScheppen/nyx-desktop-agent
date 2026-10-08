import asyncio
from typing import cast

import pytest

from nyx import db
from nyx.enums import BoundaryResult, EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.facade import MemoryFacade
from nyx.reading.integration import ReadingIntegration
from nyx.reading.segmenter import Segment
from nyx.reading.store import ReadingStore
from nyx.types import Event, LLMOutput


class _Llm:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
        tools: list[dict[str, object]] | None = None,
    ) -> LLMOutput:
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        return LLMOutput(
            module=module,
            type=output_type,
            model="fake",
            content='{"content":"记住了","summary":"章末"}',
            correlation_id=correlation_id,
        )


class _Evaluator:
    async def evaluate(self, output: LLMOutput) -> None:
        return None


class _Memory:
    def __init__(self) -> None:
        self.remembered: list[tuple[str, str, str]] = []
        self.digest_calls = 0
        self.knowledge: list[list[dict[str, str]]] = []

    async def remember_reading(
        self, content: str, summary: str, correlation_id: str,
        source_name: str | None = None, *, source_topic: str | None = None,
    ) -> None:
        self.remembered.append((content, summary, correlation_id))

    async def digest_source_block(
        self,
        text: str,
        source_name: str,
        correlation_id: str,
        *,
        author: str = "",
        profile: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], list[dict[str, str]]]:
        self.digest_calls += 1
        return (
            {
                "summary": f"摘要{self.digest_calls}",
                "themes": ["主题"],
                "content_category": "fiction",
            },
            [{"topic": "主题", "content": f"事实{self.digest_calls}"}],
        )

    async def remember_knowledge(
        self, items: list[dict[str, str]], correlation_id: str
    ) -> None:
        self.knowledge.append(items)

    async def record_reading_evidence(
        self, source_topic: str, source_name: str, content: str, block_key: str,
        *, book_id: str | None = None,
    ) -> None:
        return None


class _Bus:
    def __init__(self) -> None:
        self.published: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event)


class _FailingBus(_Bus):
    async def publish(self, event: Event) -> None:
        raise RuntimeError("reflection admission failed")


async def test_integrate_consumes_buffer_after_memory_is_saved() -> None:
    memory = _Memory()
    integration = ReadingIntegration(
        cast(LlmClient, _Llm()),
        cast(Evaluator, _Evaluator()),
        cast(MemoryFacade, memory),
        cast(EventBus, _Bus()),
    )
    await integration.record("book-1", 2, "这里让我停了一下", "mutter")

    await integration.integrate("book-1", BoundaryResult.CHAPTER_END, 0)

    assert memory.remembered == [("记住了", "章末", "book-1")]
    assert integration.buffer.get("book-1") == []


async def test_integrate_revisit_publishes_reflection_event() -> None:
    memory = _Memory()
    bus = _Bus()
    integration = ReadingIntegration(
        cast(LlmClient, _Llm()),
        cast(Evaluator, _Evaluator()),
        cast(MemoryFacade, memory),
        cast(EventBus, bus),
    )
    await integration.record("book-1", 2, "这里让我停了一下", "mutter")

    await integration.integrate("book-1", BoundaryResult.BOOK_FINISHED, 1)

    assert len(bus.published) == 1
    assert bus.published[0].type is EventType.REFLECTION
    assert bus.published[0].correlation_id == "book-1"
    assert bus.published[0].content == {
        "reason": "reading_revisit",
        "book_id": "book-1",
        "evidence": "章末",
    }


async def test_integrate_keeps_buffer_when_reflection_admission_fails() -> None:
    memory = _Memory()
    integration = ReadingIntegration(
        cast(LlmClient, _Llm()),
        cast(Evaluator, _Evaluator()),
        cast(MemoryFacade, memory),
        cast(EventBus, _FailingBus()),
    )
    await integration.record("book-1", 2, "这里让我停了一下", "mutter")

    await integration.integrate("book-1", BoundaryResult.CHAPTER_END, 1)

    assert memory.remembered == [("记住了", "章末", "book-1")]
    assert len(integration.buffer["book-1"]) == 1


async def test_sediment_splits_long_paragraph_and_flushes_remainder() -> None:
    database = await db.connect(":memory:")
    store = ReadingStore(database)
    memory = _Memory()
    try:
        book, _created = await store.insert_book_with_paragraphs(
            "长段书",
            "作者",
            "long.epub",
            "h" * 64,
            [Segment(text="a" * 6001, is_chapter_start=True)],
        )
        integration = ReadingIntegration(
            cast(LlmClient, _Llm()),
            cast(Evaluator, _Evaluator()),
            cast(MemoryFacade, memory),
            cast(EventBus, _Bus()),
            store,
        )
        await integration.sediment(book.id, 1, flush=False)
        first = await store.get_memory_state(book.id)
        await integration.sediment(book.id, 1, flush=True)
        final = await store.get_memory_state(book.id)
    finally:
        await database.close()
    assert first["cursor"] == {"paragraph_index": 1, "char_offset": 6000}
    assert final["cursor"] == {"paragraph_index": 2, "char_offset": 0}
    assert memory.digest_calls == 2
    assert memory.knowledge[0][0]["source_topic"].startswith("book:")


async def test_sediment_reuses_pending_without_digest_call() -> None:
    database = await db.connect(":memory:")
    store = ReadingStore(database)
    memory = _Memory()
    try:
        book, _created = await store.insert_book_with_paragraphs(
            "书",
            "作者",
            "book.epub",
            "p" * 64,
            [Segment(text="正文", is_chapter_start=True)],
        )
        await store.update_memory_state(
            book.id,
            {
                "cursor": {"paragraph_index": 1, "char_offset": 0},
                "profile": {},
                "pending": {
                    "cursor": {"paragraph_index": 2, "char_offset": 0},
                    "profile": {
                        "summary": "已提取",
                        "themes": [],
                        "content_category": "unknown",
                    },
                    "knowledge": [{"topic": "t", "content": "c"}],
                },
            },
        )
        integration = ReadingIntegration(
            cast(LlmClient, _Llm()),
            cast(Evaluator, _Evaluator()),
            cast(MemoryFacade, memory),
            cast(EventBus, _Bus()),
            store,
        )
        await integration.sediment(book.id, 1, flush=True)
        state = await store.get_memory_state(book.id)
    finally:
        await database.close()
    assert memory.digest_calls == 0
    assert len(memory.knowledge) == 1
    assert state["pending"] is None


@pytest.mark.parametrize("evict", [False, True])
async def test_integrate_only_consumes_supplied_entries(evict: bool) -> None:
    llm = _Llm()
    llm.release = asyncio.Event()
    integration = ReadingIntegration(
        cast(LlmClient, llm), cast(Evaluator, _Evaluator()),
        cast(MemoryFacade, _Memory()), cast(EventBus, _Bus()),
    )
    await integration.record("book", 1, "a" * 12001, "mutter")
    await integration.record("book", 2, "not supplied", "question")
    task = asyncio.create_task(
        integration.integrate("book", BoundaryResult.CHAPTER_END, 0)
    )
    await llm.started.wait()
    for i in range(100 if evict else 1):
        await integration.record("book", i + 3, "new", "mutter")
    llm.release.set()
    await task
    contents = [entry.content for entry in integration.buffer["book"]]
    assert contents[-1] == "new"
    if evict:
        assert len(contents) == 100
    else:
        assert "not supplied" in contents
        assert 0 < len(contents[0]) < 12001
