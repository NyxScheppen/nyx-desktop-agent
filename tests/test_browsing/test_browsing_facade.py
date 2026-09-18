# pyright: reportPrivateUsage=false
import asyncio
import time
from collections.abc import AsyncGenerator
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from nyx.browsing.companions import BrowsingCompanion
from nyx.browsing.facade import BrowsingFacade
from nyx.browsing.integration import BrowsingIntegration
from nyx.browsing.store import BrowsingStore
from nyx.config import MemoryConfig
from nyx.db import connect
from nyx.enums import EventType, MemoryKind, MemoryType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.expression.facade import ExpressionFacade
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.memory.retrieval import MemoryRetrieval
from nyx.memory.store import MemoryStore
from nyx.types import BrowserPageSnapshot, BrowsingPage, LLMOutput, Memory


@pytest.fixture
async def facade() -> AsyncGenerator[BrowsingFacade, None]:
    database = await connect(":memory:")
    companion = Mock(spec=BrowsingCompanion)
    companion.dispatch = AsyncMock()
    integration = Mock(spec=BrowsingIntegration)
    integration.integrate = AsyncMock(return_value=("Note", "Summary", []))
    memory = Mock(spec=MemoryFacade)
    browsing = BrowsingFacade(
        BrowsingStore(database),
        companion,
        integration,
        memory,
        bootstrap_secret="paired-secret",
    )
    try:
        yield browsing
    finally:
        await browsing.quiesce()
        await browsing.drain(1.0)
        await database.close()


def snapshot(navigation: str, tainted: bool = False) -> BrowserPageSnapshot:
    return BrowserPageSnapshot(
        navigation,
        1,
        "https://example.com/article",
        None,
        "Title",
        "Text",
        None,
        tainted,
    )


async def test_tainted_origin_requires_exact_probe_and_explicit_grant(
    facade: BrowsingFacade,
) -> None:
    session = await facade.start_session()
    await facade.navigation_started(session.id, "nav")
    with pytest.raises(ValueError, match="origin_authorization_required"):
        await facade.authorization_probe(session.id, "nav", "https://example.com")
    with pytest.raises(ValueError, match="stale_navigation"):
        await facade.allow_origin(session.id, "old", "https://example.com")
    await facade.allow_origin(session.id, "nav", "https://example.com")
    page = await facade.capture_page(session.id, snapshot("nav", True))
    assert (await facade.get_prompt_context(page.id)) is not None
    await facade.revoke_origin(session.id, "https://example.com")
    await asyncio.sleep(0.05)
    assert await facade.get_prompt_context(page.id) is None
    _, pages = await facade.get_session(session.id)
    assert pages[0].status == "pending"


async def test_checkpoint_precedes_companion_and_retry_does_not_repeat(
    facade: BrowsingFacade,
) -> None:
    session = await facade.start_session()
    await facade.navigation_started(session.id, "nav")
    page = await facade.capture_page(session.id, snapshot("nav"))
    again = await facade.capture_page(session.id, snapshot("nav"))
    await asyncio.sleep(0.05)
    assert page.id == again.id
    context = await facade.get_prompt_context(page.id)
    assert context is not None and context["text"] == "Text"
    companion = cast(Mock, facade._companion)
    companion.dispatch.assert_awaited_once()


async def test_stale_sensitive_capture_does_not_revoke_current_page(
    facade: BrowsingFacade,
) -> None:
    session = await facade.start_session()
    await facade.navigation_started(session.id, "old")
    await facade.navigation_started(session.id, "current")
    page = await facade.capture_page(session.id, snapshot("current"))
    late = snapshot("old")
    late.raw_url = "https://example.com/login"
    with pytest.raises(ValueError, match="stale_navigation"):
        await facade.capture_page(session.id, late)
    assert await facade.get_prompt_context(page.id) is not None


async def test_bootstrap_is_paired_and_token_stable(facade: BrowsingFacade) -> None:
    with pytest.raises(ValueError, match="invalid_bridge_token"):
        await facade.bootstrap("wrong")
    session, token = await facade.bootstrap("paired-secret")
    same, again = await facade.bootstrap("paired-secret")
    assert same.id == session.id and token == again
    await facade.close_session(session.id)
    facade.validate_bridge(token, session.id, closing=True)
    with pytest.raises(ValueError, match="state_conflict"):
        facade.validate_bridge(token, session.id)


async def test_closed_page_reaches_one_durable_long_term_memory() -> None:
    database = await connect(":memory:")
    bus = EventBus(database)
    llm = Mock(spec=LlmClient)
    llm.complete = AsyncMock(
        return_value=LLMOutput(
            module="browsing",
            type="browsing_note",
            model="fixture",
            content='{"content":"We read an article","summary":"Article","topics":[]}',
            correlation_id="page",
        )
    )
    evaluator = Mock(spec=Evaluator)
    evaluator.evaluate = AsyncMock()
    memory_store = MemoryStore(database)
    memory = MemoryFacade(
        memory_store, MemoryRetrieval(memory_store), bus, llm, evaluator, MemoryConfig()
    )
    companion = Mock(spec=BrowsingCompanion)
    companion.dispatch = AsyncMock()
    browsing = BrowsingFacade(
        BrowsingStore(database),
        companion,
        BrowsingIntegration(llm, evaluator, bus),
        memory,
    )
    try:
        await browsing.recover_pending()
        session = await browsing.start_session()
        await browsing.navigation_started(session.id, "nav")
        page = await browsing.capture_page(session.id, snapshot("nav"))
        await browsing.close_session(session.id)
        for _ in range(200):
            if (await browsing.get_page(page.id)).status == "remembered":
                break
            await asyncio.sleep(0.01)
        result = await browsing.get_page(page.id)
        assert result.status == "remembered" and result.memory_id == page.id
        assert len(await memory.list_memories(kind=MemoryKind.BROWSING)) == 1
        events = await bus.list_events(event_type=EventType.MEMORY_CREATED)
        assert len(events) == 1 and events[0].correlation_id == page.id
        assert (await browsing._store.read_snapshot(page.id))["content_text"] == ""
        llm.complete.assert_awaited_once()
    finally:
        await browsing.quiesce()
        await browsing.drain(1.0)
        await database.close()


async def test_companion_eval_failure_preserves_valid_associations(
    facade: BrowsingFacade,
) -> None:
    session = await facade.start_session()
    await facade.navigation_started(session.id, "nav")
    page = await facade.capture_page(session.id, snapshot("nav"))
    llm = Mock(spec=LlmClient)
    llm.complete = AsyncMock(
        return_value=LLMOutput(
            "browsing",
            "browsing_companion",
            "fixture",
            '{"action":"association","query":"article"}',
            page.id,
        )
    )
    evaluator = Mock(spec=Evaluator)
    evaluator.evaluate = AsyncMock(side_effect=RuntimeError("fixture eval failure"))
    memory = Mock(spec=MemoryFacade)
    first = Memory(
        "first", 1.0, "x" * 600, MemoryKind.BROWSING, "   ", 1.0, MemoryType.LONG_TERM
    )
    memories = [first, first] + [
        Memory(
            str(index),
            1.0,
            "Body",
            MemoryKind.BROWSING,
            " Summary\ntext ",
            1.0,
            MemoryType.LONG_TERM,
        )
        for index in range(3)
    ]
    memory.search = AsyncMock(return_value=memories)
    expression = Mock(spec=ExpressionFacade)
    bus = EventBus(facade._store._db)
    companion = BrowsingCompanion(llm, evaluator, bus, memory, expression, "Canon")
    await companion.dispatch(page, "Article", None)
    events = await bus.list_events(event_type=EventType.BROWSING_ASSOCIATION)
    snippets = {
        event.content["memory_id"]: event.content["snippet"] for event in events
    }
    assert len(events) == 3 and snippets["first"] == "x" * 500
    assert snippets["0"] == "Summary text"
    assert expression.record_proactive_turn.call_count == 3


async def test_memory_stage_retry_reuses_integrated_checkpoint(
    facade: BrowsingFacade,
) -> None:
    session = await facade.start_session()
    await facade.navigation_started(session.id, "nav")
    page = await facade.capture_page(session.id, snapshot("nav"))
    await facade.close_session(session.id)
    await facade.drain(1.0)
    store = facade._store
    now = time.time()
    await store.claim_next("worker", "summary-token", now, now + 300)
    await store.finish_summary(page.id, "summary-token", "Note", "Summary", [], now)
    claimed = await store.claim_next("worker", "memory-token", now, now + 300)
    assert claimed is not None
    memory = cast(Mock, facade._memory)
    memory.remember_browsing = AsyncMock(
        side_effect=[RuntimeError("fixture"), Mock(id=page.id)]
    )
    await facade._integrate_page(claimed, "memory-token")
    assert (await store.read_snapshot(page.id))["integrated_content"] == "Note"
    await MemoryStore(store._db).add(
        Memory(
            page.id,
            now,
            "Note",
            MemoryKind.BROWSING,
            "Summary",
            1.0,
            MemoryType.LONG_TERM,
        )
    )
    later = time.time() + 2
    retried = await store.claim_next("worker", "retry-token", later, later + 300)
    assert retried is not None
    await facade._integrate_page(retried, "retry-token")
    assert (await store.get_page(page.id)).status == "remembered"
    cast(Mock, facade._integration).integrate.assert_not_awaited()


async def test_heartbeat_db_failure_cancels_owned_integration_task(
    facade: BrowsingFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await facade.start_session()
    await facade.navigation_started(session.id, "nav")
    page = await facade.capture_page(session.id, snapshot("nav"))
    running: list[asyncio.Task[object]] = []
    cancelled = asyncio.Event()

    async def integrate(page: BrowsingPage, token: str) -> None:
        task = asyncio.current_task()
        assert task is not None
        running.append(cast(asyncio.Task[object], task))
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def heartbeat_wait(
        *args: object, **kwargs: object
    ) -> tuple[set[object], set[object]]:
        await asyncio.sleep(0)
        return set(), set()

    monkeypatch.setattr(facade, "_integrate_page", AsyncMock(side_effect=integrate))
    monkeypatch.setattr(
        facade._store, "renew_claim", AsyncMock(side_effect=TimeoutError)
    )
    monkeypatch.setattr(asyncio, "wait", heartbeat_wait)
    try:
        with pytest.raises(TimeoutError):
            await facade._process_claim(page, "token")
        assert cancelled.is_set()
    finally:
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
