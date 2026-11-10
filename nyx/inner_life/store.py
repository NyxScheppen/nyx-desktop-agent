import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import asdict

from nyx.db import Database, PostgresConnection
from nyx.enums import EnergyState
from nyx.inner_life.document import topics_from_data, validate_topics
from nyx.types import InnerDocument


class InnerLifeStore:
    """Atomic current inner document and operational energy storage."""

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def db(self) -> Database:
        """Return the shared database for local transaction orchestration."""
        return self._db

    @asynccontextmanager
    async def _operation(self) -> AsyncGenerator[bool, None]:
        """Yield whether this method owns the commit for its SQL block."""
        if self._db.in_transaction:
            yield False
            return
        async with self._db.lock:
            yield True

    async def get_energy(self) -> tuple[float, EnergyState] | None:
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT value, state FROM energy WHERE id = 'self'"
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return row["value"], EnergyState(row["state"])

    async def upsert_energy(self, value: float, state: EnergyState) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "INSERT INTO energy (id, value, state) VALUES ('self', ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET value = excluded.value, "
                "state = excluded.state",
                (value, state.value),
            )
            if should_commit:
                await self._db.conn.commit()

    async def get_document(self) -> InnerDocument | None:
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT topics, revision, updated_at FROM inner_document "
                "WHERE id='self'"
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return InnerDocument(
            topics_from_data(json.loads(row["topics"])),
            row["revision"],
            row["updated_at"],
        )

    async def assert_document_updated_at(self, expected: float) -> None:
        async with self._operation():
            lock = (
                " FOR UPDATE" if isinstance(self._db.conn, PostgresConnection) else ""
            )
            row = await (
                await self._db.conn.execute(
                    "SELECT updated_at FROM inner_document WHERE id='self'" + lock
                )
            ).fetchone()
        if row is None or row["updated_at"] != expected:
            raise RuntimeError("反思文档快照已变化，请重新准备")

    async def upsert_document(self, n: InnerDocument) -> None:
        validate_topics(n.topics)
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "INSERT INTO inner_document (id, topics, revision, updated_at) "
                "VALUES ('self', ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "topics=excluded.topics, revision=excluded.revision, "
                "updated_at=excluded.updated_at",
                (
                    json.dumps([asdict(t) for t in n.topics], ensure_ascii=False),
                    n.revision,
                    n.updated_at,
                ),
            )
            if should_commit:
                await self._db.conn.commit()
