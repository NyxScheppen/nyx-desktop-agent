# pyright: reportPrivateUsage=false
import asyncio
from pathlib import Path
from typing import cast

import aiosqlite
import pytest

from nyx import db

# 27 张业务表（不含 schema_version）
BUSINESS_TABLES = {
    "personality",
    "value_system",
    "energy",
    "self_narrative",
    "aesthetic",
    "memory",
    "memory_edge",
    "short_term_desire",
    "desire_value",
    "long_term_desire",
    "activity",
    "event_log",
    "material",
    "books",
    "paragraphs",
    "reading_progress",
    "user_notes",
    "annotations",
    "eval_log",
    "eval_prompt",
    "event_delivery",
    "event_effect",
    "desire_generation_attempt",
    "expression_interaction_attempt",
    "desire_eval_applied",
}

# 非 Optional 字段对应列必须 NOT NULL（01-types 契约）
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

# Optional 字段对应列必须可空（01-types 的 X | None）
NULLABLE_COLUMNS = {
    ("short_term_desire", "goal"),
    ("activity", "ended_at"),
    ("memory", "embedding"),
    ("memory", "content_hash"),   # v6 迁移，旧行 NULL（不去重）
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

async def test_migrate_creates_all_tables() -> None:
    conn = await _migrated_conn()
    try:
        names = await _table_names(conn)
    finally:
        await conn.close()
    assert BUSINESS_TABLES <= names
    assert "schema_version" in names
    assert len(names) == 26


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
        "idx_memory_kind",
        "idx_memory_kind_hash",
        "idx_memory_type",
        "idx_event_log_corr",
        "idx_memory_content_hash",
        "idx_books_content_hash",
        "idx_eval_log_created",
        "idx_event_delivery_ready",
        "idx_event_delivery_consumer_ready",
        "idx_long_term_desire_name_normalized",
        "idx_desire_generation_attempt_type",
        "idx_expression_attempt_status_expiry",
        "idx_expression_attempt_status_created",
        "idx_expression_attempt_correlation",
    }


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
        ddl = await (await conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_edge'"
        )).fetchone()
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
            "INSERT INTO memory_edge (from_id, to_id, weight) "
            "VALUES ('a', 'b', 0.4)"
        )
        await conn.execute(
            "INSERT INTO memory_edge (from_id, to_id, weight) "
            "VALUES ('b', 'a', 0.9)"
        )
        await conn.commit()
        monkeypatch.setattr(db, "_MIGRATIONS", [m for m in full if m[0] <= 14])
        await db.migrate(conn)
        rows = await (await conn.execute(
            "SELECT from_id, to_id, kind, weight, created_at FROM memory_edge"
        )).fetchall()
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
        memory_row = await (await conn.execute(
            "SELECT COUNT(*) AS count FROM memory"
        )).fetchone()
        edge_row = await (await conn.execute(
            "SELECT COUNT(*) AS count FROM memory_edge"
        )).fetchone()
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
        await conn.execute(
            "CREATE TABLE browsing_session (id TEXT PRIMARY KEY)"
        )
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
    assert len(names) == 26
    assert version == max(v for v, _ in db._MIGRATIONS)


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
        await database.conn.close()
    assert db_path.exists()
    assert journal is not None and journal["journal_mode"] == "wal"
    assert fk is not None and int(fk["foreign_keys"]) == 1
    assert x is not None and x["x"] == 1  # row_factory 生效
    assert isinstance(database.lock, asyncio.Lock)


async def test_connect_explicit_path_priority(tmp_path: Path) -> None:
    a = tmp_path / "a.db"
    database = await db.connect(str(a))
    await database.conn.close()
    assert a.exists()


async def test_connect_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_db = tmp_path / "env.db"
    monkeypatch.setenv("NYX_DB", str(env_db))
    database = await db.connect()
    await database.conn.close()
    assert env_db.exists()


def test_default_db_path_constant() -> None:
    assert db.DEFAULT_DB_PATH == "nyx.db"


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

    async def boom(conn: aiosqlite.Connection) -> None:
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
