# pyright: reportPrivateUsage=false
import asyncio
import sqlite3
import threading
from pathlib import Path
from typing import cast

import aiosqlite
import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver, empty_checkpoint

from nyx import db
from nyx.tools.tool_call_store import ToolCallStore

# 角色数据库的业务表（不含 schema_version / SQLite 内部表）。
BUSINESS_TABLES = {
    "skill_execution",
    "skill_revision",
    "behavior_reflection",
    "research_run",
    "research_knowledge_receipt",
    "transport_checkpoint",
    "transport_outbox",
    "personality",
    "value_system",
    "energy",
    "self_narrative",
    "aesthetic",
    "memory",
    "memory_edge",
    "memory_recall_use",
    "short_term_desire",
    "desire_value",
    "long_term_desire",
    "activity",
    "event_log",
    "books",
    "paragraphs",
    "reading_progress",
    "user_notes",
    "annotations",
    "annotation_feedback",
    "bookmarks",
    "eval_log",
    "eval_prompt",
    "tool_call_log",
    "tool_execution",
    "lesson_signal",
    "checkpoints",
    "writes",
    "event_delivery",
    "event_effect",
    "desire_generation_attempt",
    "expression_interaction_attempt",
    "desire_eval_applied",
    "memory_entity",
    "memory_entity_alias",
    "memory_fact",
    "assigned_task",
    "assigned_task_source",
    "character_initialization",
    "presence_checkpoint",
    "conversation_segment",
    "conversation_segment_recall_use",
    "reading_evidence",
    "knowledge_chunk_change",
}


def test_resolve_postgres_url_keeps_sqlite_as_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NYX_DATABASE_URL", raising=False)
    assert db.resolve_postgres_url() is None
    monkeypatch.setenv("NYX_DATABASE_URL", "")
    assert db.resolve_postgres_url() is None


@pytest.mark.parametrize("scheme", ["postgres://", "postgresql://"])
def test_resolve_postgres_url_accepts_postgres_urls(
    monkeypatch: pytest.MonkeyPatch, scheme: str
) -> None:
    url = f"{scheme}nyx:secret@localhost/nyx"
    monkeypatch.setenv("NYX_DATABASE_URL", url)
    assert db.resolve_postgres_url() == url


def test_resolve_postgres_url_rejects_other_schemes_without_echoing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NYX_DATABASE_URL", "mysql://nyx:secret@localhost/nyx")
    with pytest.raises(ValueError, match="NYX_DATABASE_URL") as error:
        db.resolve_postgres_url()
    assert "secret" not in str(error.value)


def test_qmark_to_pyformat_preserves_quoted_question_marks() -> None:
    sql = "SELECT '?' AS literal, \"?\" AS identifier WHERE id=? AND note='??'"
    assert db._qmark_to_pyformat(sql) == (
        "SELECT '?' AS literal, \"?\" AS identifier WHERE id=%s AND note='??'"
    )
    assert db._sqlite_sql_to_postgres(
        "INSERT OR IGNORE INTO event_effect (event_id) VALUES (?)"
    ) == (
        "INSERT INTO event_effect (event_id) VALUES (%s) ON CONFLICT DO NOTHING"
    )
    assert db._sqlite_sql_to_postgres(
        "SELECT 1 FROM memory, json_each(memory.topics) "
        "WHERE json_each.value = ?"
    ) == (
        "SELECT 1 FROM memory, "
        "jsonb_array_elements_text(memory.topics::jsonb) AS memory_topics "
        "WHERE memory_topics.value = %s"
    )
    assert db._sqlite_sql_to_postgres(
        "SELECT id FROM activity WHERE json_extract(progress, '$.task_id') = ?"
    ) == "SELECT id FROM activity WHERE (progress::jsonb ->> 'task_id') = %s"
    assert db._sqlite_sql_to_postgres(
        "UPDATE memory SET freshness=MAX(0.0, freshness-?), "
        "strength=MIN(1.0, MAX(0.0, strength+?))"
    ) == (
        "UPDATE memory SET freshness=GREATEST(0.0, freshness-%s), "
        "strength=LEAST(1.0, GREATEST(0.0, strength+%s))"
    )
    assert db._sqlite_sql_to_postgres(
        "SELECT id FROM conversation_segment LIMIT -1 OFFSET ?"
    ) == "SELECT id FROM conversation_segment LIMIT ALL OFFSET %s"


def test_postgres_migrations_keep_schema_and_skip_existing_data_work() -> None:
    migrations = dict(db.postgres_migrations())
    schema_sql = "\n".join(
        statement for statements in migrations.values() for statement in statements
    )

    assert "CREATE TABLE personality" in schema_sql
    assert "event_seq INTEGER" in schema_sql
    assert "UPDATE event_log SET event_seq = rowid" not in schema_sql
    assert "json_each" not in schema_sql
    assert "nyx_raw_sha256" not in schema_sql


def test_postgres_migrations_translate_sqlite_types_and_triggers() -> None:
    migrations = dict(db.postgres_migrations())
    schema_sql = "\n".join(
        statement for statements in migrations.values() for statement in statements
    )

    assert "CREATE TABLE checkpoints" not in schema_sql
    assert "CREATE TABLE writes" not in schema_sql
    assert "valid_from DOUBLE PRECISION NOT NULL" in schema_sql
    assert "seq BIGSERIAL PRIMARY KEY" in schema_sql
    assert "CREATE TRIGGER knowledge_chunk_insert AFTER INSERT ON" in schema_sql
    assert "CREATE TRIGGER knowledge_chunk_delete AFTER DELETE ON" in schema_sql
    assert "CREATE TRIGGER knowledge_chunk_update AFTER UPDATE OF" in schema_sql
    assert "EXECUTE FUNCTION nyx_record_knowledge_chunk_change()" in schema_sql


def test_postgres_migrations_compile_explicit_character_migrations() -> None:
    migrations = dict(db.postgres_migrations(db._MIGRATIONS))
    schema_sql = "\n".join(
        statement for statements in migrations.values() for statement in statements
    )

    assert "CREATE TRIGGER knowledge_chunk_insert AFTER INSERT ON" in schema_sql


def test_postgres_migrations_preserve_foreign_key_table_order() -> None:
    statements = db.postgres_migrations(
        [
            (
                1,
                [
                    "CREATE TABLE a_child (id TEXT PRIMARY KEY, parent_id TEXT "
                    "REFERENCES z_parent(id))",
                    "CREATE TABLE z_parent (id TEXT PRIMARY KEY)",
                ],
            )
        ]
    )[0][1]

    assert statements.index("CREATE TABLE z_parent (id TEXT PRIMARY KEY)") < (
        statements.index(
            "CREATE TABLE a_child (id TEXT PRIMARY KEY, parent_id TEXT "
            "REFERENCES z_parent(id))"
        )
    )


def test_postgres_migrations_compile_custom_host_schema() -> None:
    migrations = db.postgres_migrations(
        [
            (1, ["CREATE TABLE characters (id TEXT PRIMARY KEY)"]),
            (2, ["INSERT INTO characters VALUES ('nyx')"]),
        ]
    )
    schema_sql = "\n".join(migrations[0][1])

    assert migrations[0][0] == 2
    assert "CREATE TABLE characters" in schema_sql
    assert "nyx" not in schema_sql
    assert "knowledge_chunk" not in schema_sql


@pytest.mark.asyncio
async def test_migrate_postgres_initializes_schema_and_rolls_back_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Raw:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.commits = 0
            self.rollbacks = 0
            self.fail = False

        async def execute(self, sql: str, parameters: object = None) -> "_Raw":
            self.calls.append(sql)
            if self.fail and sql == "CREATE TABLE broken (id TEXT PRIMARY KEY)":
                raise RuntimeError("broken migration")
            return self

        async def fetchone(self) -> None:
            return None

        async def commit(self) -> None:
            self.commits += 1

        async def rollback(self) -> None:
            self.rollbacks += 1

    def fake_postgres_migrations(
        _migrations: list[tuple[int, list[str]]] | None = None,
    ) -> list[tuple[int, list[str]]]:
        return [(1, ["CREATE TABLE broken (id TEXT PRIMARY KEY)"])]

    monkeypatch.setattr(db, "postgres_migrations", fake_postgres_migrations)
    raw = _Raw()
    raw.fail = True
    connection = db.PostgresConnection(raw)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="broken migration"):
        await db.migrate_postgres(connection)
    assert raw.calls[:3] == [
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)",
        "SELECT version FROM schema_version",
        "INSERT INTO schema_version (version) VALUES (0)",
    ]
    assert raw.rollbacks == 1


@pytest.mark.asyncio
async def test_connect_uses_postgres_schema_and_closes_on_migration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Raw:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.closed = False

        async def execute(self, sql: str, parameters: object = None) -> None:
            self.calls.append(sql)

        async def close(self) -> None:
            self.closed = True

    raw = _Raw()
    connection = db.PostgresConnection(raw)  # type: ignore[arg-type]
    migrated: list[tuple[db.PostgresConnection, object]] = []

    async def fake_connect(url: str) -> db.PostgresConnection:
        assert url == "postgresql://nyx:secret@localhost/nyx"
        return connection

    async def fake_migrate(
        conn: db.PostgresConnection,
        *,
        migrations: list[tuple[int, list[str]]] | None = None,
    ) -> None:
        migrated.append((conn, migrations))
        raise RuntimeError("migration failed")

    monkeypatch.setenv("NYX_DATABASE_URL", "postgresql://nyx:secret@localhost/nyx")
    monkeypatch.setattr(db, "connect_postgres", fake_connect)
    monkeypatch.setattr(db, "migrate_postgres", fake_migrate)
    with pytest.raises(RuntimeError, match="migration failed"):
        await db.connect("ignored.sqlite", schema="character_nyx")
    assert raw.calls == [
        'CREATE SCHEMA IF NOT EXISTS "character_nyx"',
        'SET search_path TO "character_nyx"',
    ]
    assert migrated == [(connection, None)]
    assert raw.closed


@pytest.mark.asyncio
async def test_connect_postgres_initializes_independent_checkpoint_saver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Raw:
        def __init__(self, label: str) -> None:
            self.label = label
            self.calls: list[str] = []
            self.closed = False

        async def execute(self, sql: str, parameters: object = None) -> None:
            self.calls.append(sql)

        async def close(self) -> None:
            self.closed = True

    business_raw = _Raw("business")
    checkpoint_raw = _Raw("checkpoint")
    business = db.PostgresConnection(business_raw)  # type: ignore[arg-type]

    async def fake_connect(url: str) -> db.PostgresConnection:
        return business

    async def fake_migrate(
        conn: db.PostgresConnection,
        *,
        migrations: list[tuple[int, list[str]]] | None = None,
    ) -> None:
        return None

    class _AsyncConnection:
        @staticmethod
        async def connect(*args: object, **kwargs: object) -> _Raw:
            return checkpoint_raw

    class _Saver:
        def __init__(self, conn: _Raw) -> None:
            self.conn = conn

        async def setup(self) -> None:
            self.conn.calls.append("setup")

    monkeypatch.setenv("NYX_DATABASE_URL", "postgresql://nyx:secret@localhost/nyx")
    monkeypatch.setattr(db, "connect_postgres", fake_connect)
    monkeypatch.setattr(db, "migrate_postgres", fake_migrate)
    monkeypatch.setattr(db, "AsyncConnection", _AsyncConnection)
    monkeypatch.setattr(db, "AsyncPostgresSaver", _Saver)

    database = await db.connect(schema="character_nyx")
    assert database.checkpointer is not None
    assert checkpoint_raw.calls == ['SET search_path TO "character_nyx"', "setup"]
    await database.close()
    assert business_raw.closed and checkpoint_raw.closed


@pytest.mark.asyncio
async def test_postgres_connection_translates_execute_and_forwards_lifecycle() -> None:
    class _Raw:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []
            self.executemany_calls: list[tuple[str, object]] = []
            self.committed = False
            self.rolled_back = False
            self.closed = False

        async def execute(self, sql: str, parameters: object = None) -> object:
            self.calls.append((sql, parameters))
            return "cursor"

        def cursor(self) -> "_Raw":
            return self

        async def executemany(self, sql: str, parameters: object) -> None:
            self.executemany_calls.append((sql, parameters))

        async def commit(self) -> None:
            self.committed = True

        async def rollback(self) -> None:
            self.rolled_back = True

        async def close(self) -> None:
            self.closed = True

    raw = _Raw()
    connection = db.PostgresConnection(raw)  # type: ignore[arg-type]
    assert await connection.execute("SELECT ?", (1,)) == "cursor"
    await connection.execute("INSERT OR IGNORE INTO t VALUES (?)", (1,))
    await connection.executemany("INSERT INTO t VALUES (?)", [(1,), (2,)])
    await connection.commit()
    await connection.rollback()
    await connection.close()
    assert raw.calls == [
        ("SELECT %s", (1,)),
        ("INSERT INTO t VALUES (%s) ON CONFLICT DO NOTHING", (1,)),
    ]
    assert raw.executemany_calls == [("INSERT INTO t VALUES (%s)", [(1,), (2,)])]
    assert raw.committed and raw.rolled_back and raw.closed


@pytest.mark.asyncio
async def test_connect_postgres_uses_autocommit_for_standalone_statements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class _Raw:
        async def close(self) -> None:
            return None

    class _AsyncConnection:
        @staticmethod
        async def connect(url: str, **kwargs: object) -> _Raw:
            calls.append(kwargs)
            return _Raw()

    monkeypatch.setattr(db, "AsyncConnection", _AsyncConnection)
    await db.connect_postgres("postgresql://nyx:secret@localhost/nyx")
    assert len(calls) == 1 and calls[0]["autocommit"] is True


# 非 Optional 字段对应列必须 NOT NULL（对应共享类型）
NOT_NULL_COLUMNS = {
    ("memory", "aspect"),
    ("long_term_desire", "linked_values"),
    ("activity", "progress"),
    ("event_log", "content"),
    ("event_log", "correlation_id"),
    ("user_notes", "content"),
    ("user_notes", "created_at"),
    ("user_notes", "updated_at"),
    ("annotations", "user_note_id"),
    ("annotations", "content"),
    ("annotations", "created_at"),
    ("eval_prompt", "call_id"),
    ("eval_prompt", "prompt_json"),
}

# Optional 字段对应列必须可空（共享类型的 X | None）
NULLABLE_COLUMNS = {
    ("short_term_desire", "goal"),
    ("short_term_desire", "parent_long_term_id"),
    ("desire_generation_attempt", "parent_long_term_id"),
    ("activity", "ended_at"),
    ("memory", "embedding"),
    ("memory", "content_hash"),  # v6 迁移，旧行 NULL（不去重）
    ("user_notes", "book_id"),
    ("user_notes", "paragraph_id"),
    ("user_notes", "selected_text"),
    ("eval_log", "ooc_embed"),
}


async def _migrated_conn() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await db.migrate(conn)
    return conn


async def _table_names(conn: aiosqlite.Connection) -> set[str]:
    cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {r["name"] for r in await cursor.fetchall()}


async def _column_notnull(conn: aiosqlite.Connection, table: str, column: str) -> int:
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    for r in await cursor.fetchall():
        if r["name"] == column:
            return int(r["notnull"])
    raise AssertionError(f"{table}.{column} 不存在")


async def _version(conn: aiosqlite.Connection) -> int:
    cursor = await conn.execute("SELECT version FROM schema_version")
    row = await cursor.fetchone()
    assert row is not None
    return int(row["version"])


# ---- migrate：全新建库 ----


async def test_connect_cancelled_during_migration_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    connections: list[aiosqlite.Connection] = []

    async def blocked_migrate(
        conn: aiosqlite.Connection,
        *,
        migrations: list[tuple[int, list[str]]] | None = None,
    ) -> None:
        connections.append(conn)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(db, "migrate", blocked_migrate)
    task = asyncio.create_task(db.connect(":memory:"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ValueError, match="no active connection"):
            await connections[0].execute("SELECT 1")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for conn in connections:
            await conn.close()


async def test_connect_cancelled_during_checkpoint_open_closes_both_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    connections: list[aiosqlite.Connection] = []
    original = aiosqlite.connect

    def delayed_connect(*args: object, **kwargs: object) -> aiosqlite.Connection:
        conn = original(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        connections.append(conn)
        if len(connections) == 2:
            connector = conn._connector

            def open_after_release() -> sqlite3.Connection:
                loop.call_soon_threadsafe(entered.set)
                release.wait(timeout=5)
                return connector()

            conn._connector = open_after_release
        return conn

    monkeypatch.setattr(aiosqlite, "connect", delayed_connect)
    task = asyncio.create_task(db.connect(str(tmp_path / "role.db")))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        for conn in connections:
            # 0.20 uses Connection as Thread; 0.22 owns a separate worker.
            worker = cast(threading.Thread, getattr(conn, "_thread", conn))
            await asyncio.to_thread(worker.join, 1)
            assert not worker.is_alive()
            with pytest.raises(ValueError, match="no active connection"):
                await conn.execute("SELECT 1")
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for conn in connections:
            await conn.close()


async def test_migrate_creates_all_tables() -> None:
    conn = await _migrated_conn()
    try:
        names = await _table_names(conn)
    finally:
        await conn.close()
    assert BUSINESS_TABLES <= names
    assert "schema_version" in names
    assert len(names) == 54
    assert "material" not in names


async def test_migration_backfills_event_sequence_from_sqlite_rowid() -> None:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    try:
        await db.migrate(conn, migrations=[m for m in db._MIGRATIONS if m[0] < 52])
        for event_id in ("event-a", "event-b"):
            await conn.execute(
                "INSERT INTO event_log "
                "(id, timestamp, source, type, content, correlation_id) "
                "VALUES (?, 1, 'internal', 'speak', '{}', 'turn')",
                (event_id,),
            )
        await conn.commit()
        await db.migrate(conn)
        rows = await (
            await conn.execute("SELECT id, event_seq FROM event_log ORDER BY event_seq")
        ).fetchall()
    finally:
        await conn.close()
    assert [(row["id"], row["event_seq"]) for row in rows] == [
        ("event-a", 1), ("event-b", 2),
    ]


@pytest.mark.parametrize("terminal_type", ["speak", "ask"])
async def test_connect_cleans_terminal_tool_checkpoint_but_keeps_unfinished(
    tmp_path: Path, terminal_type: str,
) -> None:
    path = str(tmp_path / "role.db")
    database = await db.connect(path)
    try:
        saver = cast(BaseCheckpointSaver[str], database.checkpointer)
        for thread in ("finished", "unfinished"):
            await ToolCallStore(database).start_execution(thread, thread)
            config = await saver.aput(
                {"configurable": {"thread_id": thread, "checkpoint_ns": ""}},
                empty_checkpoint(), {"source": "input", "step": -1}, {},
            )
            await saver.aput_writes(config, [("pending", "retry_wait")], "tool-task")
        async with database.transaction() as conn:
            await conn.execute(
                "INSERT INTO event_log "
                "(id, timestamp, source, type, content, correlation_id) "
                "VALUES (?, 1, 'internal', ?, '{}', 'finished')",
                ("terminal-reply", terminal_type),
            )
    finally:
        await database.close()

    database = await db.connect(path)
    try:
        saver = cast(BaseCheckpointSaver[str], database.checkpointer)
        assert await saver.aget_tuple({
            "configurable": {"thread_id": "finished"},
        }) is None
        assert await saver.aget_tuple({
            "configurable": {"thread_id": "unfinished"},
        }) is not None
        rows = await (await database.conn.execute(
            "SELECT thread_id, count(*) AS n FROM writes GROUP BY thread_id"
        )).fetchall()
        assert {row["thread_id"]: row["n"] for row in rows} == {"unfinished": 1}
        receipts = await (await database.conn.execute(
            "SELECT correlation_id FROM tool_execution"
        )).fetchall()
        assert [row["correlation_id"] for row in receipts] == ["unfinished"]
    finally:
        await database.close()


async def test_parent_long_term_foreign_keys_set_null() -> None:
    conn = await _migrated_conn()
    try:
        for table in ("short_term_desire", "desire_generation_attempt"):
            cursor = await conn.execute(f"PRAGMA foreign_key_list({table})")
            rows = await cursor.fetchall()
            parent = next(row for row in rows if row["from"] == "parent_long_term_id")
            assert parent["table"] == "long_term_desire"
            assert parent["on_delete"] == "SET NULL"
    finally:
        await conn.close()


async def test_migrate_adds_assigned_task_fifo_index() -> None:
    conn = await _migrated_conn()
    try:
        cursor = await conn.execute("PRAGMA index_list(assigned_task)")
        indexes = {row["name"] for row in await cursor.fetchall()}
    finally:
        await conn.close()
    assert "idx_assigned_task_status_created" in indexes


async def test_migrate_adds_source_memory_state_columns() -> None:
    conn = await _migrated_conn()
    try:
        columns: dict[str, set[str]] = {}
        for table in ("books",):
            cursor = await conn.execute(f"PRAGMA table_info({table})")
            columns[table] = {row["name"] for row in await cursor.fetchall()}
    finally:
        await conn.close()
    assert "memory_state" in columns["books"]


async def test_migrate_adds_memory_freshness_settlement_anchor() -> None:
    conn = await _migrated_conn()
    try:
        cursor = await conn.execute("PRAGMA table_info(memory)")
        columns = {row["name"]: row for row in await cursor.fetchall()}
    finally:
        await conn.close()
    assert columns["freshness_updated_at"]["notnull"] == 1


async def test_memory_recall_use_schema_and_cascade() -> None:
    conn = await _migrated_conn()
    try:
        columns = await (
            await conn.execute("PRAGMA table_info(memory_recall_use)")
        ).fetchall()
        foreign_keys = await (
            await conn.execute("PRAGMA foreign_key_list(memory_recall_use)")
        ).fetchall()
    finally:
        await conn.close()
    assert {row["name"]: row["pk"] for row in columns} == {
        "user_event_id": 1,
        "memory_id": 2,
    }
    assert any(
        row["table"] == "memory"
        and row["from"] == "memory_id"
        and row["on_delete"] == "CASCADE"
        for row in foreign_keys
    )


async def test_freshness_anchor_migration_preserves_existing_freshness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = db._MIGRATIONS
    monkeypatch.setattr(db, "_MIGRATIONS", [m for m in full if m[0] <= 33])
    conn = await _migrated_conn()
    try:
        await conn.execute(
            "INSERT INTO memory "
            "(id, created_at, content, kind, topics, summary, freshness, type, "
            "recall_count, aspect, embedding, content_hash, first_created_at) "
            "VALUES ('old', 1.0, 'old', 'episode', '[]', 'old', 0.42, "
            "'short_term', 0, '[]', NULL, 'hash', 1.0)"
        )
        await conn.commit()
        monkeypatch.setattr(db, "_MIGRATIONS", full)

        await db.migrate(conn)

        row = await (
            await conn.execute(
                "SELECT freshness, freshness_updated_at FROM memory WHERE id = 'old'"
            )
        ).fetchone()
        assert row is not None
        assert row["freshness"] == pytest.approx(0.42)
        assert row["freshness_updated_at"] > 0
    finally:
        await conn.close()


async def test_migrate_adds_paragraph_format_column() -> None:
    conn = await _migrated_conn()
    try:
        cursor = await conn.execute("PRAGMA table_info(paragraphs)")
        columns = {row["name"]: row for row in await cursor.fetchall()}
    finally:
        await conn.close()
    assert columns["format_json"]["notnull"] == 1
    assert columns["format_json"]["dflt_value"] == '\'{"blocks":[],"marks":[]}\''


async def test_migrate_adds_highlight_offsets_and_bookmarks() -> None:
    conn = await _migrated_conn()
    try:
        note_rows = await (
            await conn.execute("PRAGMA table_info(user_notes)")
        ).fetchall()
        bookmark_rows = await (
            await conn.execute("PRAGMA table_info(bookmarks)")
        ).fetchall()
        note_columns = {row["name"] for row in note_rows}
        bookmark_columns = {row["name"] for row in bookmark_rows}
    finally:
        await conn.close()
    assert {"selection_start", "selection_end"} <= note_columns
    assert bookmark_columns == {"id", "book_id", "paragraph_id", "created_at"}


async def test_migrate_creates_expected_indexes() -> None:
    conn = await _migrated_conn()
    try:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        )
        names = {r["name"] for r in await cursor.fetchall()}
    finally:
        await conn.close()
    assert names == {
        "idx_research_correlation",
        "idx_memory_kind",
        "idx_memory_kind_hash",
        "idx_memory_type",
        "idx_event_log_corr",
        "idx_event_log_event_seq",
        "idx_memory_content_hash",
        "idx_books_content_hash",
        "idx_eval_log_created",
        "idx_event_delivery_ready",
        "idx_event_delivery_consumer_ready",
        "idx_long_term_desire_name_normalized",
        "idx_desire_generation_attempt_type",
        "idx_expression_attempt_status_expiry",
        "idx_expression_attempt_waiting_paused",
        "idx_expression_attempt_status_created",
        "idx_expression_attempt_correlation",
        "idx_memory_fact_subject",
        "idx_memory_fact_object",
        "idx_memory_fact_validity",
        "idx_memory_fact_predicate",
        "idx_memory_entity_alias_lookup",
        "idx_activity_results",
        "idx_assigned_task_status_created",
        "idx_reading_evidence_pending",
        "idx_reading_evidence_exact",
        "idx_conversation_segment_time",
        "idx_memory_fact_source_segment",
        "idx_tool_call_log_created",
        "idx_tool_call_log_correlation",
        "idx_tool_call_log_call",
        "idx_tool_execution_correlation",
        "idx_transport_outbox_order",
    }


async def test_activity_results_index_matches_query() -> None:
    conn = await _migrated_conn()
    try:
        rows = await (
            await conn.execute("PRAGMA index_info(idx_activity_results)")
        ).fetchall()
        ddl = await (
            await conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='index' AND name='idx_activity_results'"
            )
        ).fetchone()
    finally:
        await conn.close()
    assert [row["name"] for row in rows] == ["status", "type", "ended_at"]
    assert ddl is not None and "ended_at DESC" in ddl["sql"]


async def test_migrate_books_content_hash_index_unique() -> None:
    conn = await _migrated_conn()
    try:
        cursor = await conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name='idx_books_content_hash'"
        )
        row = await cursor.fetchone()
    finally:
        await conn.close()
    assert row is not None
    assert row["sql"].startswith("CREATE UNIQUE INDEX")


async def test_memory_edge_schema_typed_and_canonical() -> None:
    conn = await _migrated_conn()
    try:
        cols = await (await conn.execute("PRAGMA table_info(memory_edge)")).fetchall()
        pk = {r["name"]: r["pk"] for r in cols}
        notnull = {r["name"]: r["notnull"] for r in cols}
        ddl = await (
            await conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND "
                "name='memory_edge'"
            )
        ).fetchone()
    finally:
        await conn.close()
    assert pk == {
        "from_id": 1,
        "to_id": 2,
        "kind": 3,
        "weight": 0,
        "created_at": 0,
    }
    assert notnull["kind"] == 1 and notnull["created_at"] == 1
    assert ddl is not None and "CHECK (from_id < to_id)" in ddl["sql"]


async def test_event_delivery_and_effect_schema() -> None:
    conn = await _migrated_conn()
    try:
        delivery = await (
            await conn.execute("PRAGMA table_info(event_delivery)")
        ).fetchall()
        effect = await (
            await conn.execute("PRAGMA table_info(event_effect)")
        ).fetchall()
    finally:
        await conn.close()
    delivery_pk = {row["name"]: row["pk"] for row in delivery}
    effect_pk = {row["name"]: row["pk"] for row in effect}
    assert delivery_pk["event_id"] == 1
    assert delivery_pk["consumer_id"] == 2
    assert effect_pk["event_id"] == 1
    assert effect_pk["consumer_id"] == 2
    assert {row["name"] for row in delivery} == {
        "event_id",
        "consumer_id",
        "status",
        "attempts",
        "available_at",
        "started_at",
        "completed_at",
        "lease_until",
        "last_error",
    }


async def test_migrate_v8_dedupes_duplicate_content_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = db._MIGRATIONS
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    try:
        # 先迁到 v7（旧竞态窗口可能留下的库），插入两条同 content_hash 的书
        monkeypatch.setattr(db, "_MIGRATIONS", [m for m in full if m[0] <= 7])
        await db.migrate(conn)
        now = 1.0
        for bid in ("b1", "b2"):
            await conn.execute(
                "INSERT INTO books (id, title, author, filename, content_hash, "
                "total_paragraphs, created_at, updated_at) "
                "VALUES (?, ?, '', '', ?, 0, ?, ?)",
                (bid, f"书{bid}", "dup-hash", now, now),
            )
            await conn.execute(
                'INSERT INTO paragraphs (id, book_id, "index", text, is_chapter_start) '
                "VALUES (?, ?, 1, '正文', 0)",
                (f"p-{bid}", bid),
            )
        await conn.commit()

        # 再跑完整迁移（含 v8）：应自动去重 + 建唯一索引，不抛
        monkeypatch.setattr(db, "_MIGRATIONS", full)
        await db.migrate(conn)

        cursor = await conn.execute("SELECT COUNT(*) AS n FROM books")
        n = await cursor.fetchone()
        cursor = await conn.execute("SELECT COUNT(*) AS n FROM paragraphs")
        np_ = await cursor.fetchone()
        cursor = await conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name='idx_books_content_hash'"
        )
        idx = await cursor.fetchone()
    finally:
        await conn.close()
    assert n is not None and n["n"] == 1  # 重复行被清到 1
    assert np_ is not None and np_["n"] == 1  # 被删书其 paragraphs 级联清空
    assert idx is not None and idx["sql"].startswith("CREATE UNIQUE INDEX")


async def test_memory_edge_migration_canonicalizes_reverse_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = db._MIGRATIONS
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    try:
        monkeypatch.setattr(db, "_MIGRATIONS", [m for m in full if m[0] <= 13])
        await db.migrate(conn)
        for mid in ("a", "b"):
            await conn.execute(
                "INSERT INTO memory (id, created_at, content, tag, summary, freshness, "
                "type, recall_count, aspect, embedding, content_hash, "
                "first_created_at) VALUES (?, 1.0, ?, 't', 's', 1.0, "
                "'short_term', 0, '[]', NULL, ?, 1.0)",
                (mid, mid, mid),
            )
        await conn.execute(
            "INSERT INTO memory_edge (from_id, to_id, weight) VALUES ('a', 'b', 0.4)"
        )
        await conn.execute(
            "INSERT INTO memory_edge (from_id, to_id, weight) VALUES ('b', 'a', 0.9)"
        )
        await conn.commit()
        monkeypatch.setattr(db, "_MIGRATIONS", [m for m in full if m[0] <= 14])
        await db.migrate(conn)
        rows = await (
            await conn.execute(
                "SELECT from_id, to_id, kind, weight, created_at FROM memory_edge"
            )
        ).fetchall()
    finally:
        await conn.close()
    assert [
        (r["from_id"], r["to_id"], r["kind"], r["weight"], r["created_at"])
        for r in rows
    ] == [("a", "b", "semantic", 0.9, 0.0)]


async def test_memory_kind_migration_clears_legacy_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = db._MIGRATIONS
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    try:
        monkeypatch.setattr(db, "_MIGRATIONS", [m for m in full if m[0] <= 18])
        await db.migrate(conn)
        for mid in ("a", "b"):
            await conn.execute(
                "INSERT INTO memory (id, created_at, content, tag, summary, freshness, "
                "type, recall_count, aspect, embedding, content_hash, "
                "first_created_at) VALUES (?, 1.0, ?, 'legacy', 's', 1.0, "
                "'short_term', 0, '[]', NULL, ?, 1.0)",
                (mid, mid, mid),
            )
        await conn.execute(
            "INSERT INTO memory_edge (from_id, to_id, kind, weight, created_at) "
            "VALUES ('a', 'b', 'semantic', 0.9, 1.0)"
        )
        await conn.commit()
        monkeypatch.setattr(db, "_MIGRATIONS", full)
        await db.migrate(conn)
        memory_row = await (
            await conn.execute("SELECT COUNT(*) AS count FROM memory")
        ).fetchone()
        edge_row = await (
            await conn.execute("SELECT COUNT(*) AS count FROM memory_edge")
        ).fetchone()
        assert memory_row is not None
        assert edge_row is not None
        memory_count = cast(int, memory_row["count"])
        edge_count = cast(int, edge_row["count"])
        cursor = await conn.execute("PRAGMA table_info(memory)")
        columns = {row["name"] for row in await cursor.fetchall()}
    finally:
        await conn.close()
    assert memory_count == 0
    assert edge_count == 0
    assert "kind" in columns and "topics" in columns and "tag" not in columns


async def test_removed_browsing_data_is_cleaned_from_previous_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = db._MIGRATIONS
    monkeypatch.setattr(db, "_MIGRATIONS", [m for m in full if m[0] <= 22])
    conn = await _migrated_conn()
    try:
        await conn.execute(
            "INSERT INTO event_log "
            "(id, timestamp, source, type, content, correlation_id) "
            "VALUES ('old-event', 1.0, 'internal', 'browsing_mutter', '{}', 'page')"
        )
        await conn.execute(
            "INSERT INTO event_delivery (event_id, consumer_id, status) "
            "VALUES ('old-event', 'legacy.consumer', 'pending')"
        )
        await conn.execute(
            "INSERT INTO memory "
            "(id, created_at, content, kind, topics, summary, freshness, type, "
            "recall_count, aspect, embedding, content_hash, first_created_at) "
            "VALUES ('old-memory', 1.0, 'old', 'browsing', '[]', 'old', 1.0, "
            "'long_term', 0, '[]', NULL, NULL, 1.0)"
        )
        await conn.execute(
            "INSERT INTO expression_interaction_attempt "
            "(id, kind, source_id, correlation_id, text, created_at, expires_at, "
            "status) "
            "VALUES ('old-attempt', 'browsing_question', 'page', 'page', 'old', "
            "1.0, 2.0, 'waiting')"
        )
        await conn.execute("CREATE TABLE browsing_session (id TEXT PRIMARY KEY)")
        await conn.execute(
            "CREATE TABLE browsing_page ("
            "id TEXT PRIMARY KEY, session_id TEXT REFERENCES browsing_session(id))"
        )
        await conn.execute("UPDATE schema_version SET version = 22")
        await conn.commit()

        monkeypatch.setattr(db, "_MIGRATIONS", full)
        await db.migrate(conn)

        for table, column, value in (
            ("event_log", "type", "browsing_mutter"),
            ("memory", "kind", "browsing"),
            ("expression_interaction_attempt", "kind", "browsing_question"),
        ):
            cursor = await conn.execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE {column} = ?",
                (value,),
            )
            row = await cursor.fetchone()
            assert row is not None and row["count"] == 0
        cursor = await conn.execute(
            "SELECT COUNT(*) AS count FROM event_delivery WHERE event_id = 'old-event'"
        )
        row = await cursor.fetchone()
        assert row is not None and row["count"] == 0
        tables = await _table_names(conn)
        assert "browsing_session" not in tables
        assert "browsing_page" not in tables
    finally:
        await conn.close()


async def test_migrate_sets_version_to_max() -> None:
    conn = await _migrated_conn()
    try:
        version = await _version(conn)
    finally:
        await conn.close()
    assert version == max(v for v, _ in db._MIGRATIONS)


async def test_migrate_35_releases_claimed_attempt_without_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = db._MIGRATIONS
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    try:
        monkeypatch.setattr(db, "_MIGRATIONS", [m for m in full if m[0] <= 34])
        await db.migrate(conn)
        await conn.execute(
            "INSERT INTO expression_interaction_attempt "
            "(id, kind, source_id, correlation_id, text, created_at, expires_at, "
            "status, answered_at, answer_event_id, failure_reason) VALUES "
            "('legacy-claim', 'chat_ask', 'u', 'c', '在吗', 1, 2, "
            "'claimed', NULL, NULL, NULL)"
        )
        await conn.commit()
        monkeypatch.setattr(db, "_MIGRATIONS", full)
        await db.migrate(conn)
        row = await (
            await conn.execute(
                "SELECT status, answer_event_id FROM expression_interaction_attempt "
                "WHERE id = 'legacy-claim'"
            )
        ).fetchone()
    finally:
        await conn.close()
    assert row is not None and row["status"] == "waiting"
    assert row["answer_event_id"] is None


async def test_material_retirement_handles_missing_source_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = db._MIGRATIONS
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    try:
        monkeypatch.setattr(db, "_MIGRATIONS", [m for m in full if m[0] <= 32])
        await db.migrate(conn)
        await conn.execute(
            "INSERT INTO short_term_desire "
            "(id, created_at, type, strength, description, goal, retry_count, "
            "status, goal_progress, parent_long_term_id) "
            "VALUES ('d1', 1, 'exploration', 0.8, '读旧资料', NULL, 0, "
            "'suppressed', 0, NULL)"
        )
        await conn.execute(
            "INSERT INTO activity "
            "(id, type, schedule_block_id, status, progress, started_at, ended_at) "
            "VALUES ('a1', 'reading', '09:00', 'paused', "
            '\'{"source":"old.txt","desire_id":"d1"}\', 1, 2)'
        )
        await conn.commit()
        monkeypatch.setattr(db, "_MIGRATIONS", full)
        await db.migrate(conn)
        tables = await _table_names(conn)
        activity = await (
            await conn.execute("SELECT status FROM activity WHERE id = 'a1'")
        ).fetchone()
        desire = await (
            await conn.execute("SELECT status FROM short_term_desire WHERE id = 'd1'")
        ).fetchone()
    finally:
        await conn.close()
    assert "material" not in tables
    assert activity is not None and activity["status"] == "abandoned"
    assert desire is not None and desire["status"] == "pending"


# ---- 可空性对齐 ----


async def test_migrate_not_null_alignment() -> None:
    conn = await _migrated_conn()
    try:
        for table, column in NOT_NULL_COLUMNS:
            assert await _column_notnull(conn, table, column) == 1, f"{table}.{column}"
    finally:
        await conn.close()


async def test_migrate_nullable_alignment() -> None:
    conn = await _migrated_conn()
    try:
        for table, column in NULLABLE_COLUMNS:
            assert await _column_notnull(conn, table, column) == 0, f"{table}.{column}"
    finally:
        await conn.close()


# ---- 幂等 / 版本门控 / 原子回滚 ----


async def test_migrate_idempotent() -> None:
    conn = await _migrated_conn()
    names: set[str] = set()
    version = 0
    try:
        await db.migrate(conn)  # 第二遍
        names = await _table_names(conn)
        version = await _version(conn)
    finally:
        await conn.close()
    assert len(names) == 54
    assert version == max(v for v, _ in db._MIGRATIONS)


async def test_migrate_backfills_memory_entity_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = db._MIGRATIONS
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    aliases: list[str] = []
    try:
        monkeypatch.setattr(db, "_MIGRATIONS", [m for m in full if m[0] <= 25])
        await db.migrate(conn)
        await conn.execute(
            "INSERT INTO memory_entity "
            "(id, canonical_name, entity_type, aliases, created_at, updated_at) "
            "VALUES ('entity-1', '尼克斯', 'agent', '[\"Nyx\", \"尼克斯夏本\"]', 1, 1)"
        )
        await conn.commit()
        monkeypatch.setattr(db, "_MIGRATIONS", full)
        await db.migrate(conn)
        cursor = await conn.execute(
            "SELECT alias FROM memory_entity_alias "
            "WHERE entity_id = 'entity-1' ORDER BY alias"
        )
        aliases = [row["alias"] for row in await cursor.fetchall()]
    finally:
        await conn.close()
    assert aliases == ["Nyx", "尼克斯夏本"]


async def test_migrate_version_gating(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = await _migrated_conn()
    try:
        next_version = max(v for v, _ in db._MIGRATIONS) + 1
        monkeypatch.setattr(
            db,
            "_MIGRATIONS",
            db._MIGRATIONS
            + [(next_version, ["CREATE TABLE foo (id TEXT PRIMARY KEY)"])],
        )
        await db.migrate(conn)
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='foo'"
        )
        foo = await cursor.fetchone()
        version = await _version(conn)
    finally:
        await conn.close()
    assert foo is not None  # 下一版本套用
    assert version == next_version


async def test_migrate_atomic_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    try:
        monkeypatch.setattr(
            db,
            "_MIGRATIONS",
            [(1, ["CREATE TABLE ok (id TEXT PRIMARY KEY)", "这不是合法 SQL"])],
        )
        with pytest.raises(aiosqlite.Error):
            await db.migrate(conn)
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ok'"
        )
        ok = await cursor.fetchone()
        version = await _version(conn)
    finally:
        await conn.close()
    assert ok is None  # 回滚生效：ok 表不存在
    assert version == 0  # 版本不推进


# ---- connect：pragma / row_factory / lock ----


async def test_connect_returns_database(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    database = await db.connect(str(db_path))
    try:
        journal = await (await database.conn.execute("PRAGMA journal_mode")).fetchone()
        fk = await (await database.conn.execute("PRAGMA foreign_keys")).fetchone()
        x = await (await database.conn.execute("SELECT 1 AS x")).fetchone()
    finally:
        await database.close()
    assert db_path.exists()
    assert journal is not None and journal["journal_mode"] == "wal"
    assert fk is not None and int(fk["foreign_keys"]) == 1
    assert x is not None and x["x"] == 1  # row_factory 生效
    assert isinstance(database.lock, asyncio.Lock)


async def test_connect_explicit_path_priority(tmp_path: Path) -> None:
    a = tmp_path / "a.db"
    database = await db.connect(str(a))
    await database.close()
    assert a.exists()


async def test_connect_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "data" / "nyx.db"
    database = await db.connect(str(nested))
    await database.close()
    assert nested.exists()


async def test_connect_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_db = tmp_path / "env.db"
    monkeypatch.setenv("NYX_DB", str(env_db))
    database = await db.connect()
    await database.close()
    assert env_db.exists()


async def test_connect_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NYX_DB", raising=False)
    database = await db.connect()
    await database.close()
    assert (tmp_path / "data" / "nyx.db").exists()


async def test_connect_reuses_legacy_default_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NYX_DB", raising=False)
    legacy_path = tmp_path / "nyx.db"
    legacy = await aiosqlite.connect(legacy_path)
    await legacy.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
    await legacy.execute("INSERT INTO legacy_marker VALUES ('preserved')")
    await legacy.commit()
    await legacy.close()

    database = await db.connect()
    row = await (
        await database.conn.execute("SELECT value FROM legacy_marker")
    ).fetchone()
    await database.close()

    assert row is not None and row["value"] == "preserved"
    assert not (tmp_path / "data" / "nyx.db").exists()


# ---- connect：错误路径不泄漏连接 ----


class _SpyConn:
    """记录 close 是否被调用的假连接；execute 返回可 await 的空结果。"""

    def __init__(self) -> None:
        self.row_factory: object = None
        self.closed = False

    async def execute(self, sql: str) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


async def test_connect_closes_conn_on_migrate_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _SpyConn()

    async def fake_connect(path: str) -> _SpyConn:
        return spy

    async def boom(
        conn: aiosqlite.Connection,
        *,
        migrations: list[tuple[int, list[str]]] | None = None,
    ) -> None:
        raise aiosqlite.Error("迁移失败")

    monkeypatch.setattr(db.aiosqlite, "connect", fake_connect)
    monkeypatch.setattr(db, "migrate", boom)

    with pytest.raises(aiosqlite.Error):
        await db.connect("x.db")

    assert spy.closed  # 迁移失败 → 连接被 close，不泄漏


async def test_database_close_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = await db.connect(":memory:")
    close_calls = 0
    real_close = database.conn.close

    async def close() -> None:
        nonlocal close_calls
        close_calls += 1
        await real_close()

    # aiosqlite connection methods are replaceable in the test double path.
    monkeypatch.setattr(database.conn, "close", close)
    await database.close()
    await database.close()
    assert database.is_closed
    assert close_calls == 1
async def test_archive_migration_backfills_inactive_and_duplicate_turns() -> None:
    from nyx import db

    database = await db.connect(
        ":memory:", migrations=[m for m in db._MIGRATIONS if m[0] <= 52]
    )
    try:
        from nyx.memory.store import MemoryStore
        from nyx.types import ConversationSegment

        store = MemoryStore(database)
        for identity in ("z", "a"):
            await store.add_conversation_segment(
                ConversationSegment(
                    id=identity,
                    previous_id=None,
                    started_at=1.0,
                    ended_at=2.0,
                    raw_event_ids=["u:user", "u:nyx"],
                    raw_content="原文",
                    summary="原文",
                    topics=[],
                    active=False,
                )
            )
        await db.migrate(cast(aiosqlite.Connection, database.conn))
        rows = await (
            await database.conn.execute(
                "SELECT turn_id, segment_id FROM conversation_turn_archive "
                "ORDER BY turn_id"
            )
        ).fetchall()
        assert [(r["turn_id"], r["segment_id"]) for r in rows] == [
            ("u:nyx", "a"),
            ("u:user", "a"),
        ]
        await db.migrate(cast(aiosqlite.Connection, database.conn))
        assert (
            len(
                list(
                    await (
                        await database.conn.execute(
                            "SELECT * FROM conversation_turn_archive"
                        )
                    ).fetchall()
                )
            )
            == 2
        )
    finally:
        await database.close()


@pytest.mark.parametrize("raw_ids", ["{}", "[1]", '[""]', "invalid"])
async def test_archive_migration_bad_ids_roll_back(raw_ids: str) -> None:
    from nyx import db

    database = await db.connect(
        ":memory:", migrations=[m for m in db._MIGRATIONS if m[0] <= 52]
    )
    try:
        await database.conn.execute(
            "INSERT INTO conversation_segment (id, started_at, ended_at, "
            "raw_event_ids, raw_content, summary, topics) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("bad", 1.0, 1.0, raw_ids, "原文", "原文", "[]"),
        )
        await database.conn.commit()
        with pytest.raises(ValueError, match="JSON|轮次|array|数组"):
            await db.migrate(cast(aiosqlite.Connection, database.conn))
        row = await (
            await database.conn.execute("SELECT version FROM schema_version")
        ).fetchone()
        assert row is not None and row[0] == 52
    finally:
        await database.close()
