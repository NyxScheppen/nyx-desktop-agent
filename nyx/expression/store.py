import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import aiosqlite

from nyx.db import Database
from nyx.enums import InteractionKind, InteractionStatus
from nyx.types import InteractionAttempt


class ExpressionInteractionStore:
    """Durable waiting/attempt state for chat, reading questions, and initiative."""

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def db(self) -> Database:
        return self._db

    @asynccontextmanager
    async def _operation(self) -> AsyncGenerator[bool, None]:
        if self._db.in_transaction:
            yield False
            return
        async with self._db.lock:
            yield True

    async def create(self, attempt: InteractionAttempt) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "INSERT INTO expression_interaction_attempt "
                "(id, kind, source_id, correlation_id, text, created_at, "
                "expires_at, status, answered_at, answer_event_id, failure_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attempt.id,
                    attempt.kind.value,
                    attempt.source_id,
                    attempt.correlation_id,
                    attempt.text,
                    attempt.created_at,
                    attempt.expires_at,
                    attempt.status.value,
                    attempt.answered_at,
                    attempt.answer_event_id,
                    attempt.failure_reason,
                ),
            )
            if should_commit:
                await self._db.conn.commit()

    async def get(self, attempt_id: str) -> InteractionAttempt | None:
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT * FROM expression_interaction_attempt WHERE id = ?",
                (attempt_id,),
            )
            row = await cursor.fetchone()
        return _row_to_attempt(row) if row is not None else None

    async def claim_reply(
        self, reply_event_id: str, reply_to: str | None = None
    ) -> InteractionAttempt | None:
        async with self._operation() as should_commit:
            if reply_to is None:
                cursor = await self._db.conn.execute(
                    "SELECT * FROM expression_interaction_attempt "
                    "WHERE answer_event_id = ? AND status IN (?, ?) "
                    "ORDER BY created_at DESC, id DESC LIMIT 1",
                    (
                        reply_event_id,
                        InteractionStatus.CLAIMED.value,
                        InteractionStatus.ANSWERED.value,
                    ),
                )
                owned = await cursor.fetchone()
                if owned is not None:
                    return _row_to_attempt(owned)
            if reply_to is not None:
                target = reply_to
                cursor = await self._db.conn.execute(
                    "SELECT * FROM expression_interaction_attempt WHERE id = ?",
                    (target,),
                )
                selected = await cursor.fetchone()
                if selected is None:
                    return None
                if (
                    selected["answer_event_id"] == reply_event_id
                    and selected["status"]
                    in (
                        InteractionStatus.CLAIMED.value,
                        InteractionStatus.ANSWERED.value,
                    )
                ):
                    return _row_to_attempt(selected)
            else:
                cursor = await self._db.conn.execute(
                    "SELECT id FROM expression_interaction_attempt "
                    "WHERE status = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                    (InteractionStatus.WAITING.value,),
                )
                row = await cursor.fetchone()
                target = row["id"] if row is not None else None
            if target is None:
                return None
            cursor = await self._db.conn.execute(
                "UPDATE expression_interaction_attempt "
                "SET status = ?, answer_event_id = ? "
                "WHERE id = ? AND status = ?",
                (
                    InteractionStatus.CLAIMED.value,
                    reply_event_id,
                    target,
                    InteractionStatus.WAITING.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
            if should_commit:
                await self._db.conn.commit()
            cursor = await self._db.conn.execute(
                "SELECT * FROM expression_interaction_attempt WHERE id = ?",
                (target,),
            )
            row = await cursor.fetchone()
        return _row_to_attempt(row) if row is not None else None

    async def claim_expired(self, now: float) -> InteractionAttempt | None:
        async with self._operation() as should_commit:
            cursor = await self._db.conn.execute(
                "SELECT id FROM expression_interaction_attempt "
                "WHERE status = ? AND expires_at <= ? "
                "ORDER BY expires_at ASC, id ASC LIMIT 1",
                (InteractionStatus.WAITING.value, now),
            )
            row = await cursor.fetchone()
            target = row["id"] if row is not None else None
            if target is None:
                return None
            cursor = await self._db.conn.execute(
                "UPDATE expression_interaction_attempt SET status = ? "
                "WHERE id = ? AND status = ?",
                (
                    InteractionStatus.CLAIMED.value,
                    target,
                    InteractionStatus.WAITING.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
            cursor = await self._db.conn.execute(
                "SELECT * FROM expression_interaction_attempt WHERE id = ?",
                (target,),
            )
            row = await cursor.fetchone()
            if should_commit:
                await self._db.conn.commit()
        return _row_to_attempt(row) if row is not None else None

    async def finish_answer(self, attempt_id: str, reply_event_id: str) -> bool:
        async with self._operation() as should_commit:
            cursor = await self._db.conn.execute(
                "UPDATE expression_interaction_attempt SET status = ?, "
                "answered_at = ?, answer_event_id = ? "
                "WHERE id = ? AND status = ? AND answer_event_id = ?",
                (
                    InteractionStatus.ANSWERED.value,
                    time.time(),
                    reply_event_id,
                    attempt_id,
                    InteractionStatus.CLAIMED.value,
                    reply_event_id,
                ),
            )
            if should_commit:
                await self._db.conn.commit()
            if cursor.rowcount == 1:
                return True
            cursor = await self._db.conn.execute(
                "SELECT 1 FROM expression_interaction_attempt "
                "WHERE id = ? AND status = ? AND answer_event_id = ?",
                (
                    attempt_id,
                    InteractionStatus.ANSWERED.value,
                    reply_event_id,
                ),
            )
            return await cursor.fetchone() is not None

    async def finish_expired(self, attempt_id: str) -> bool:
        async with self._operation() as should_commit:
            cursor = await self._db.conn.execute(
                "UPDATE expression_interaction_attempt SET status = ? "
                "WHERE id = ? AND status = ? AND answer_event_id IS NULL",
                (
                    InteractionStatus.EXPIRED.value,
                    attempt_id,
                    InteractionStatus.CLAIMED.value,
                ),
            )
            if should_commit:
                await self._db.conn.commit()
        return cursor.rowcount == 1

    async def release_claim(
        self, attempt_id: str, reply_event_id: str | None = None
    ) -> bool:
        async with self._operation() as should_commit:
            owner_clause = (
                "answer_event_id IS NULL"
                if reply_event_id is None
                else "answer_event_id = ?"
            )
            params: tuple[object, ...] = (
                InteractionStatus.WAITING.value,
                attempt_id,
                InteractionStatus.CLAIMED.value,
            )
            if reply_event_id is not None:
                params += (reply_event_id,)
            cursor = await self._db.conn.execute(
                "UPDATE expression_interaction_attempt "
                "SET status = ?, answer_event_id = NULL "
                f"WHERE id = ? AND status = ? AND {owner_clause}",
                params,
            )
            if should_commit:
                await self._db.conn.commit()
        return cursor.rowcount == 1

    async def latest_created_at(self, kind: InteractionKind) -> float | None:
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT created_at FROM expression_interaction_attempt "
                "WHERE kind = ? AND status != ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (kind.value, InteractionStatus.FAILED.value),
            )
            row = await cursor.fetchone()
        return float(row["created_at"]) if row is not None else None


def _row_to_attempt(row: aiosqlite.Row) -> InteractionAttempt:
    return InteractionAttempt(
        id=row["id"],
        kind=InteractionKind(row["kind"]),
        source_id=row["source_id"],
        correlation_id=row["correlation_id"],
        text=row["text"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        status=InteractionStatus(row["status"]),
        answered_at=row["answered_at"],
        answer_event_id=row["answer_event_id"],
        failure_reason=row["failure_reason"],
    )
