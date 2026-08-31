# pyright: reportPrivateUsage=false
import asyncio
from pathlib import Path

import aiosqlite
import pytest

from nyx import db

# 14 张业务表（不含 schema_version）
BUSINESS_TABLES = {
    "personality",
    "value_system",
    "energy",
    "self_narrative",
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
}

# 5 个非 Optional 字段对应列必须 NOT NULL（01-types 契约）
NOT_NULL_COLUMNS = {
    ("memory", "aspect"),
    ("long_term_desire", "linked_values"),
    ("activity", "progress"),
    ("event_log", "content"),
    ("event_log", "correlation_id"),
}

# Optional 字段对应列必须可空（01-types 的 X | None）
NULLABLE_COLUMNS = {
    ("short_term_desire", "goal"),
    ("activity", "ended_at"),
    ("memory", "embedding"),
    ("memory", "content_hash"),   # v6 迁移，旧行 NULL（不去重）
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
    assert len(names) == 15


async def test_migrate_creates_five_indexes() -> None:
    conn = await _migrated_conn()
    try:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        )
        names = {r["name"] for r in await cursor.fetchall()}
    finally:
        await conn.close()
    assert names == {
        "idx_memory_tag",
        "idx_memory_type",
        "idx_event_log_corr",
        "idx_memory_content_hash",
        "idx_books_content_hash",
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
    try:
        await db.migrate(conn)  # 第二遍
        names = await _table_names(conn)
        version = await _version(conn)
    finally:
        await conn.close()
    assert len(names) == 15
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
