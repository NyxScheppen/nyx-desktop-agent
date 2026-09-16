import asyncio

from nyx import db
from nyx.enums import InteractionKind, InteractionStatus
from nyx.expression.store import ExpressionInteractionStore
from nyx.types import InteractionAttempt


def _attempt(
    attempt_id: str,
    *,
    created_at: float = 1.0,
    expires_at: float = 10.0,
) -> InteractionAttempt:
    return InteractionAttempt(
        id=attempt_id,
        kind=InteractionKind.CHAT_ASK,
        source_id=f"source-{attempt_id}",
        correlation_id=f"corr-{attempt_id}",
        text=f"question-{attempt_id}",
        created_at=created_at,
        expires_at=expires_at,
    )


async def test_attempt_state_transitions_are_conditional() -> None:
    database = await db.connect(":memory:")
    store = ExpressionInteractionStore(database)
    try:
        await store.create(_attempt("a1"))
        claimed = await store.claim_reply("reply-1")
        assert claimed is not None and claimed.status is InteractionStatus.CLAIMED
        assert await store.claim_reply("reply-2") is None
        assert await store.finish_answer("a1", "reply-1") is True
        assert await store.finish_answer("a1", "reply-2") is False
        got = await store.get("a1")
        assert got is not None and got.status is InteractionStatus.ANSWERED
        assert got.answer_event_id == "reply-1"
    finally:
        await database.close()


async def test_concurrent_reply_claims_only_claim_once() -> None:
    database = await db.connect(":memory:")
    store = ExpressionInteractionStore(database)
    try:
        await store.create(_attempt("a1"))
        results = await asyncio.gather(
            store.claim_reply("reply-1"),
            store.claim_reply("reply-2"),
        )
        assert sum(result is not None for result in results) == 1
    finally:
        await database.close()


async def test_latest_waiting_claim_prefers_newest_created_at() -> None:
    database = await db.connect(":memory:")
    store = ExpressionInteractionStore(database)
    try:
        await store.create(_attempt("old", created_at=1.0))
        await store.create(_attempt("new", created_at=2.0))
        claimed = await store.claim_reply("reply-1")
        assert claimed is not None and claimed.id == "new"
    finally:
        await database.close()


async def test_expired_claim_can_be_released_and_retried() -> None:
    database = await db.connect(":memory:")
    store = ExpressionInteractionStore(database)
    try:
        await store.create(_attempt("a1", expires_at=5.0))
        claimed = await store.claim_expired(5.0)
        assert claimed is not None and claimed.id == "a1"
        assert await store.release_claim("a1") is True
        claimed_again = await store.claim_expired(5.0)
        assert claimed_again is not None and claimed_again.id == "a1"
        assert await store.finish_expired("a1") is True
        assert await store.claim_expired(5.0) is None
    finally:
        await database.close()


async def test_attempt_insert_rolls_back_with_outer_transaction() -> None:
    database = await db.connect(":memory:")
    store = ExpressionInteractionStore(database)
    try:
        try:
            async with database.transaction():
                await store.create(_attempt("a1"))
                raise RuntimeError("event append failed")
        except RuntimeError:
            pass
        assert await store.get("a1") is None
    finally:
        await database.close()


async def test_latest_initiative_time_ignores_failed_attempts() -> None:
    database = await db.connect(":memory:")
    store = ExpressionInteractionStore(database)
    try:
        failed = _attempt("failed", created_at=5.0)
        failed.kind = InteractionKind.INITIATE_CHAT
        failed.status = InteractionStatus.FAILED
        current = _attempt("current", created_at=3.0)
        current.kind = InteractionKind.INITIATE_CHAT
        await store.create(failed)
        await store.create(current)
        assert await store.latest_created_at(InteractionKind.INITIATE_CHAT) == 3.0
    finally:
        await database.close()
