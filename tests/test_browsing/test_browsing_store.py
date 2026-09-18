# pyright: reportPrivateUsage=false
from collections.abc import AsyncGenerator

import pytest

from nyx.browsing.store import BrowsingStore
from nyx.db import connect
from nyx.types import BrowserPageSnapshot


@pytest.fixture
async def store() -> AsyncGenerator[BrowsingStore, None]:
    database = await connect(":memory:")
    try:
        yield BrowsingStore(database)
    finally:
        await database.close()


def snapshot(navigation: str, seq: int = 1) -> BrowserPageSnapshot:
    return BrowserPageSnapshot(
        navigation,
        seq,
        "https://example.com/article?tracking=1",
        None,
        "Article",
        "Some visible text",
        None,
        False,
    )


async def test_capture_retry_is_idempotent(store: BrowsingStore) -> None:
    session = await store.get_or_create_active_session(1.0)
    await store.begin_navigation(session.id, "nav", 2.0)
    page, created = await store.upsert_capture(session.id, snapshot("nav"), 3.0)
    retried, repeated = await store.upsert_capture(session.id, snapshot("nav"), 4.0)
    assert created and not repeated
    assert retried.id == page.id and retried.revision == page.revision
    assert page.url == "https://example.com/article"


async def test_pending_requires_finalized_outputs(store: BrowsingStore) -> None:
    session = await store.get_or_create_active_session(1.0)
    await store.begin_navigation(session.id, "nav", 2.0)
    page, _ = await store.upsert_capture(session.id, snapshot("nav"), 3.0)
    await store.freeze_page(page.id, "nav", page.revision, 4.0)
    assert await store.claim_next("worker", "token", 5.0, 305.0) is None
    await store.finalize_page_outputs(page.id, "nav", page.revision, 5.0)
    claimed = await store.claim_next("worker", "token", 6.0, 306.0)
    assert claimed is not None and claimed.status == "integrating"


async def test_old_worker_cannot_finish_after_lease_expiry(
    store: BrowsingStore,
) -> None:
    session = await store.get_or_create_active_session(1.0)
    await store.begin_navigation(session.id, "nav", 2.0)
    page, _ = await store.upsert_capture(session.id, snapshot("nav"), 3.0)
    await store.freeze_page(page.id, "nav", 1, 4.0)
    await store.finalize_page_outputs(page.id, "nav", 1, 5.0)
    await store.claim_next("old", "old-token", 6.0, 7.0)
    assert not await store.finish_summary(
        page.id, "old-token", "memory", "summary", [], 8.0
    )
    await store.recover_expired(8.0)
    claimed = await store.claim_next("new", "new-token", 8.0, 308.0)
    assert claimed is not None
    assert await store.finish_summary(
        page.id, "new-token", "memory", "summary", [], 9.0
    )


async def test_origin_revoke_returns_page_until_finalized(
    store: BrowsingStore,
) -> None:
    session = await store.get_or_create_active_session(1.0)
    await store.begin_navigation(session.id, "nav", 2.0)
    page, _ = await store.upsert_capture(session.id, snapshot("nav"), 3.0)
    frozen, revision = await store.revoke_current_origin(
        session.id, "https://example.com", 4.0
    )
    again, _ = await store.revoke_current_origin(session.id, "https://example.com", 5.0)
    assert frozen is not None and revision == page.revision
    assert again is not None and again.id == page.id
    assert (await store.get_session(session.id)).current_page_id is None
    assert (await store.read_snapshot(page.id))["content_text"] == ""


async def test_navigation_start_clears_old_context(store: BrowsingStore) -> None:
    session = await store.get_or_create_active_session(1.0)
    await store.begin_navigation(session.id, "a", 2.0)
    page, _ = await store.upsert_capture(session.id, snapshot("a"), 3.0)
    frozen, revision = await store.begin_navigation(session.id, "b", 4.0)
    assert frozen is not None and frozen.id == page.id and revision == 1
    assert (await store.get_session(session.id)).current_page_id is None
    with pytest.raises(ValueError, match="stale_navigation"):
        await store.upsert_capture(session.id, snapshot("a", 2), 5.0)


async def test_focus_retry_and_new_action(store: BrowsingStore) -> None:
    session = await store.get_or_create_active_session(1.0)
    await store.begin_navigation(session.id, "nav", 2.0)
    page, _ = await store.upsert_capture(session.id, snapshot("nav"), 3.0)
    assert await store.append_focus(page.id, "nav", 1, "first", "selection")
    assert not await store.append_focus(page.id, "nav", 1, "first", "selection")
    assert await store.append_focus(page.id, "nav", 1, "second", "selection")


async def test_focus_cap_preserves_old_operation_ids(store: BrowsingStore) -> None:
    session = await store.get_or_create_active_session(1.0)
    await store.begin_navigation(session.id, "nav", 2.0)
    page, _ = await store.upsert_capture(session.id, snapshot("nav"), 3.0)
    for index in range(20):
        await store.append_focus(page.id, "nav", 1, str(index), "selection")
    with pytest.raises(ValueError, match="state_conflict"):
        await store.append_focus(page.id, "nav", 1, "overflow", "selection")
    assert not await store.append_focus(page.id, "nav", 1, "0", "selection")


async def test_startup_closes_session_and_seals_orphan_page(
    store: BrowsingStore,
) -> None:
    session = await store.get_or_create_active_session(1.0)
    await store.begin_navigation(session.id, "nav", 2.0)
    page, _ = await store.upsert_capture(session.id, snapshot("nav"), 3.0)
    await store.recover_startup(4.0)
    assert (await store.get_session(session.id)).ended_at == 4.0
    assert (await store.read_snapshot(page.id))["outputs_finalized"] == 1
    assert await store.claim_next("worker", "claim", 5.0, 305.0) is not None


async def test_capacity_clears_oldest_failed_raw_not_pending(
    store: BrowsingStore,
) -> None:
    session = await store.get_or_create_active_session(1.0)
    async with store._db.transaction():
        await store._db.conn.executemany(
            "INSERT INTO browsing_page(id,session_id,navigation_id,last_capture_seq,"
            "url,canonical_url,origin,title,content_text,content_hash,capture_source,"
            "status,captured_at,frozen_at,updated_at,raw_retained_until) "
            "VALUES (?,?,?,1,?,?,?,?,?,?,'dom','failed',1,?,1,99999999999)",
            [
                (
                    str(index),
                    session.id,
                    "old",
                    f"https://example.com/{index}",
                    f"https://example.com/{index}",
                    "https://example.com",
                    "Title",
                    "x" * 200000,
                    str(index),
                    float(index),
                )
                for index in range(262)
            ],
        )
    await store.begin_navigation(session.id, "nav", 2.0)
    capture = snapshot("nav")
    capture.text = "y" * 200000
    page, _ = await store.upsert_capture(session.id, capture, 3.0)
    assert page.status == "open"
    assert (await store.read_snapshot("0"))["content_text"] == ""
    assert (await store.read_snapshot("1"))["content_text"] != ""
    with pytest.raises(ValueError, match="snapshot_expired"):
        await store.retry_page("0", 4.0)


@pytest.mark.parametrize("writer", ["focus", "summary"])
async def test_raw_budget_applies_to_focus_and_summary(
    store: BrowsingStore, writer: str
) -> None:
    session = await store.get_or_create_active_session(1.0)
    await store.begin_navigation(session.id, "nav", 2.0)
    page, _ = await store.upsert_capture(session.id, snapshot("nav"), 3.0)
    raw = await store.read_snapshot(page.id)
    remaining = 50 * 1024 * 1024 - len(str(raw["content_text"]).encode()) - 2
    rows: list[tuple[str, str, str, str]] = []
    for index in range(263):
        size = min(200000, remaining - 2)
        rows.append((str(index), session.id, "x" * size, str(index)))
        remaining -= size + 2
    async with store._db.transaction():
        await store._db.conn.executemany(
            "INSERT INTO browsing_page(id,session_id,navigation_id,last_capture_seq,"
            "url,canonical_url,origin,title,content_text,content_hash,capture_source,"
            "status,captured_at,updated_at) VALUES (?,?,'old',1,"
            "'https://example.com/old','https://example.com/old',"
            "'https://example.com','Old',?,?,'dom','pending',1,1)",
            rows,
        )
    if writer == "summary":
        await store.freeze_page(page.id, "nav", 1, 4.0)
        await store.finalize_page_outputs(page.id, "nav", 1, 5.0)
        await store.claim_next("worker", "token", 6.0, 306.0)
    with pytest.raises(ValueError, match="browsing_storage_limit"):
        if writer == "focus":
            await store.append_focus(page.id, "nav", 1, "focus", "selection")
        else:
            await store.finish_summary(page.id, "token", "note", "summary", [], 7.0)
    retained = await store.read_snapshot(page.id)
    assert retained["focus_entries"] == "[]"
    assert retained["integrated_content"] is None
