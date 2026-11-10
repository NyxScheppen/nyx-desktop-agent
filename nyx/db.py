import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from psycopg import AsyncConnection
from psycopg import Error as PostgresError
from psycopg.rows import dict_row

from nyx.types import InnerTopic


def _qmark_to_pyformat(sql: str) -> str:
    """Convert SQLite qmark parameters while leaving quoted text untouched."""
    output: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(sql):
        char = sql[index]
        if quote is not None:
            output.append(char)
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    output.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            output.append(char)
        elif char == "?":
            output.append("%s")
        else:
            output.append(char)
        index += 1
    return "".join(output)


def _sqlite_sql_to_postgres(sql: str) -> str:
    statement = _qmark_to_pyformat(sql)
    statement = statement.replace(
        "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY",
    )
    statement = re.sub(r"\bLIMIT\s+-1\b", "LIMIT ALL", statement)
    statement = re.sub(r"\bMAX\(\s*(0\.0)\s*,", r"GREATEST(\1,", statement)
    statement = re.sub(r"\bMIN\(\s*(1\.0)\s*,", r"LEAST(\1,", statement)
    statement = statement.replace(
        "json_each(memory.topics)",
        "jsonb_array_elements_text(memory.topics::jsonb) AS memory_topics",
    ).replace("json_each.value", "memory_topics.value")
    statement = statement.replace(
        "json_each(s.raw_event_ids) AS j",
        "jsonb_array_elements_text(s.raw_event_ids::jsonb) AS j(value)",
    )
    statement = re.sub(
        r"json_extract\(progress,\s*'\$\.task_id'\)",
        "(progress::jsonb ->> 'task_id')",
        statement,
        flags=re.IGNORECASE,
    )
    if re.match(r"\s*INSERT\s+OR\s+IGNORE\s+INTO\b", statement, re.IGNORECASE):
        statement = re.sub(
            r"INSERT\s+OR\s+IGNORE\s+INTO\b",
            "INSERT INTO",
            statement,
            count=1,
            flags=re.IGNORECASE,
        ).rstrip().removesuffix(";") + " ON CONFLICT DO NOTHING"
    return statement


def _conversation_index_triggers() -> list[str]:
    return [
        "CREATE OR REPLACE FUNCTION nyx_record_conversation_chunk_change() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN INSERT INTO "
        "conversation_chunk_change(chunk_id) VALUES "
        "(CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END); "
        "RETURN NULL; END $$",
        *(f"CREATE TRIGGER conversation_chunk_{action.lower()} AFTER {action} "
          "ON conversation_chunk FOR EACH ROW EXECUTE FUNCTION "
          "nyx_record_conversation_chunk_change()"
          for action in ("INSERT", "UPDATE", "DELETE")),
    ]


def _conversation_lifecycle_triggers(*, postgres: bool = False) -> list[str]:
    """Keep source invalidation atomic, including direct SQL maintenance."""
    cleanup = (
        "DELETE FROM memory_fact WHERE source_segment_id=OLD.id; "
        "DELETE FROM conversation_retrieval_trace WHERE id IN "
        "(SELECT trace_id FROM conversation_retrieval_hit WHERE segment_id=OLD.id); "
    )
    # Appending a real turn preserves earlier evidence and temporal fact history.
    # Replacing source text retracts it. Both still invalidate the indexing job.
    newline = "chr(10)" if postgres else "char(10)"
    correction = (
        "NOT (substr(NEW.raw_content,1,length(OLD.raw_content))=OLD.raw_content "
        f"AND substr(NEW.raw_content,length(OLD.raw_content)+1,1)={newline})"
    )
    retract = (
        f"DELETE FROM memory_fact WHERE source_segment_id=OLD.id AND {correction}; "
        "DELETE FROM conversation_retrieval_trace WHERE id IN "
        "(SELECT trace_id FROM conversation_retrieval_hit "
        f"WHERE segment_id=OLD.id) AND {correction}; "
    )
    actions = [
        ("insert", "AFTER INSERT", "", "INSERT INTO conversation_index_job "
         "(segment_id) VALUES (NEW.id); "),
        ("revise", "AFTER UPDATE OF raw_content", "NEW.raw_content<>OLD.raw_content",
         retract + "UPDATE conversation_index_job SET revision=revision+1, "
         "attempts=0, retry_at=0, last_error='' WHERE segment_id=NEW.id; "
         "UPDATE conversation_segment SET evaluated_at=NULL, facts_extracted_at=NULL "
         "WHERE id=NEW.id; "),
        ("delete", "BEFORE DELETE", "", cleanup),
    ]
    result: list[str] = []
    for name, event, condition, body in actions:
        when = f" WHEN ({condition})" if condition else ""
        if postgres:
            function = f"nyx_conversation_{name}"
            result.extend([
                f"CREATE OR REPLACE FUNCTION {function}() RETURNS trigger "
                f"LANGUAGE plpgsql AS $$ BEGIN {body}RETURN "
                f"{'OLD' if name == 'delete' else 'NEW'}; END $$",
                f"CREATE TRIGGER conversation_source_{name} {event} ON "
                f"conversation_segment FOR EACH ROW{when} "
                f"EXECUTE FUNCTION {function}()",
            ])
        else:
            result.append(
                f"CREATE TRIGGER conversation_source_{name} {event} ON "
                f"conversation_segment{when} BEGIN {body}END"
            )
    return result


def postgres_migrations(
    migrations: list[tuple[int, list[str]]] | None = None,
) -> list[tuple[int, list[str]]]:
    """Compile the current schema for an empty PostgreSQL database."""
    source = _MIGRATIONS if migrations is None else migrations
    character_schema = migrations is None or migrations is _MIGRATIONS
    sqlite = sqlite3.connect(":memory:")
    if character_schema:
        sqlite.create_function("nyx_raw_sha256", 1, _raw_sha256, deterministic=True)

    def task_source_snapshot(checkpoint: str) -> str:
        source: str = json.loads(checkpoint)["source_text"]
        return source[:200_000]

    sqlite.create_function(
        "nyx_task_source_snapshot", 1, task_source_snapshot, deterministic=True
    )
    try:
        for _, statements in source:
            for statement in statements:
                sqlite.execute(statement)
        rows = sqlite.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
            "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, "
            "name"
        ).fetchall()
        table_rows = {str(row[1]): row for row in rows if row[0] == "table"}
        table_names = set(table_rows)
        dependencies = {
            table: {
                str(row[2])
                for row in sqlite.execute(
                    f'PRAGMA foreign_key_list("{table.replace(chr(34), chr(34) * 2)}")'
                ).fetchall()
                if str(row[2]) in table_names and str(row[2]) != table
            }
            for table in table_names
        }
        ordered_tables: list[str] = []
        remaining_tables = set(table_names)
        while remaining_tables:
            ready = sorted(
                table
                for table in remaining_tables
                if not dependencies[table] & remaining_tables
            )
            if not ready:
                raise ValueError("foreign key cycle in SQLite schema")
            ordered_tables.extend(ready)
            remaining_tables.difference_update(ready)
        rows = [
            *(table_rows[table] for table in ordered_tables),
            *(row for row in rows if row[0] != "table"),
        ]
    finally:
        sqlite.close()

    postgres: list[str] = []
    for kind, _, statement in rows:
        if kind == "trigger":
            continue
        if kind == "table" and statement.startswith(
            ("CREATE TABLE checkpoints ", "CREATE TABLE writes ")
        ):
            continue
        assert statement is not None
        statement = re.sub(
            r"\bREAL\b", "DOUBLE PRECISION", statement, flags=re.IGNORECASE
        )
        statement = re.sub(r"\bBLOB\b", "BYTEA", statement, flags=re.IGNORECASE)
        postgres.append(
            statement.replace(
                "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"
            )
        )
    if character_schema:
        postgres.extend(_conversation_index_triggers())
        postgres.extend(_conversation_lifecycle_triggers(postgres=True))
        postgres.extend(
            [
                "CREATE OR REPLACE FUNCTION nyx_record_knowledge_chunk_change() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN INSERT INTO "
                "knowledge_chunk_change(chunk_id) VALUES "
                "(CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END); "
                "RETURN NULL; END $$",
                "CREATE TRIGGER knowledge_chunk_insert AFTER INSERT ON "
                "reading_evidence FOR EACH ROW EXECUTE FUNCTION "
                "nyx_record_knowledge_chunk_change()",
                "CREATE TRIGGER knowledge_chunk_delete AFTER DELETE ON "
                "reading_evidence FOR EACH ROW EXECUTE FUNCTION "
                "nyx_record_knowledge_chunk_change()",
                "CREATE TRIGGER knowledge_chunk_update AFTER UPDATE OF source_topic, "
                "source_name, content, location, embedding, created_at ON "
                "reading_evidence FOR EACH ROW EXECUTE FUNCTION "
                "nyx_record_knowledge_chunk_change()",
            ]
        )
    return [(max(version for version, _ in source), postgres)]


class PostgresConnection:
    """Small DB-API surface shared by stores during PostgreSQL migration."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    async def execute(
        self,
        sql: str,
        parameters: tuple[Any, ...] | list[Any] | Mapping[str, Any] | None = None,
    ) -> Any:
        statement = _sqlite_sql_to_postgres(sql)
        if parameters is None:
            return await self._raw.execute(statement)
        return await self._raw.execute(statement, parameters)

    async def executemany(
        self, sql: str, parameters: list[tuple[Any, ...]]
    ) -> Any:
        cursor = self._raw.cursor()
        await cursor.executemany(_sqlite_sql_to_postgres(sql), parameters)
        return cursor

    async def commit(self) -> None:
        await self._raw.commit()

    async def rollback(self) -> None:
        await self._raw.rollback()

    async def close(self) -> None:
        await self._raw.close()


Connection = aiosqlite.Connection | PostgresConnection


async def _validate_archive_turns(conn: Connection) -> None:
    cursor = await conn.execute("SELECT id, raw_event_ids FROM conversation_segment")
    seen: set[str] = set()
    duplicates = 0
    for row in await cursor.fetchall():
        try:
            ids: Any = json.loads(row["raw_event_ids"])
        except json.JSONDecodeError as error:
            raise ValueError("归档轮次 ID 的 JSON 非法") from error
        if not isinstance(ids, list) or any(
            not isinstance(item, str) or not item for item in cast(list[Any], ids)
        ):
            raise ValueError("归档轮次 ID 必须是非空字符串 JSON 数组")
        for identity in cast(list[str], ids):
            duplicates += identity in seen
            seen.add(identity)
    if duplicates:
        logging.getLogger(__name__).warning("旧归档重复轮次数量=%d", duplicates)


def _legacy_inner_topics(
    identity: str,
    *,
    story: list[str],
    self_view: Mapping[str, str],
    becoming: list[str],
) -> list[InnerTopic]:
    """Move existing pre-v55 prose into topics without interpreting trait scores."""
    from nyx.inner_life.document import MAX_TOPIC_CHARS, validate_topics

    topics = [InnerTopic("identity", "self-understanding", "自我理解", identity)]
    for key, title, texts in (
        (
            "legacy-self-view",
            "已有自画像",
            [f"{k}：{v}" for k, v in self_view.items()],
        ),
        ("legacy-story", "已有个人叙事", story),
        ("legacy-becoming", "已有认知变化", becoming),
    ):
        # Pack short entries and split long ones without losing old growth.
        chunks: list[str] = []
        for text in texts:
            if not text.strip():
                continue
            if chunks and len(chunks[-1]) + len(text) + 2 <= MAX_TOPIC_CHARS:
                chunks[-1] += "\n\n" + text
            else:
                chunks.extend(
                    text[start : start + MAX_TOPIC_CHARS]
                    for start in range(0, len(text), MAX_TOPIC_CHARS)
                )
        for index, chunk in enumerate(chunks):
            topics.append(InnerTopic("narrative", f"{key}-{index}", title, chunk))
    validate_topics(topics)
    return topics


async def _migrate_inner_document(conn: Connection) -> None:
    from dataclasses import asdict

    narrative = await (
        await conn.execute("SELECT * FROM self_narrative WHERE id='self'")
    ).fetchone()
    if narrative is None:
        return
    topics = _legacy_inner_topics(
        narrative["identity"],
        story=json.loads(narrative["story"]),
        self_view=json.loads(narrative["self_view"]),
        becoming=json.loads(narrative["becoming"]),
    )
    await conn.execute(
        "INSERT INTO inner_document (id, topics, revision, updated_at) "
        "VALUES ('self', ?, 1, ?)",
        (
            json.dumps([asdict(t) for t in topics], ensure_ascii=False),
            narrative["updated_at"],
        ),
    )


async def connect_postgres(url: str) -> PostgresConnection:
    """Open one PostgreSQL connection with dictionary rows."""
    raw: Any = await AsyncConnection.connect(  # pyright: ignore[reportUnknownMemberType]
        url, autocommit=True, row_factory=cast(Any, dict_row)
    )
    return PostgresConnection(raw)


async def migrate_postgres(
    conn: PostgresConnection,
    *,
    migrations: list[tuple[int, list[str]]] | None = None,
) -> None:
    """Initialize the current schema in the connection's selected PostgreSQL schema."""
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    cursor = await conn.execute("SELECT version FROM schema_version")
    row = await cursor.fetchone()
    if row is None:
        await conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        current = 0
    else:
        current = int(row["version"])
    await conn.commit()

    compiled = postgres_migrations(migrations)
    incremental = migrations is None and current >= 52
    if incremental:
        compiled = [
            (version, statements)
            for version, statements in _MIGRATIONS
            if version > current
        ]
    for version, statements in compiled:
        if version <= current:
            continue
        await conn.execute("BEGIN")
        try:
            if migrations is None and current == 52 and version == 53:
                await _validate_archive_turns(conn)
            for statement in statements:
                if (incremental and version in (56, 57)
                        and statement.startswith("CREATE TRIGGER")):
                    continue
                if version == 55 and statement == "DROP TABLE personality":
                    await _migrate_inner_document(conn)
                await conn.execute(statement)
            if incremental and version == 56:
                for statement in _conversation_index_triggers():
                    await conn.execute(statement)
            if incremental and version == 57:
                for statement in _conversation_lifecycle_triggers(postgres=True):
                    await conn.execute(statement)
            await conn.execute("UPDATE schema_version SET version = ?", (version,))
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise


def _raw_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


DEFAULT_DB_PATH = "data/nyx.db"
_LEGACY_DB_PATH = "nyx.db"
DB_LOCK_TIMEOUT = 3.0
DB_OPERATION_TIMEOUT = 3.0
DB_FAILURE_THRESHOLD = 5
DB_COOLDOWN_SECONDS = 5.0


class DatabaseLockTimeout(TimeoutError):
    """No transaction has started; retrying lock acquisition is safe."""


class DatabaseOperationTimeout(TimeoutError):
    """A database operation timed out; verify durable receipts before replay."""


DATABASE_ERRORS = (
    sqlite3.Error,
    PostgresError,
    DatabaseLockTimeout,
    DatabaseOperationTimeout,
)


class DatabaseLock(asyncio.Lock):
    """Report long shared-connection holders without logging SQL or data."""

    def __init__(self) -> None:
        super().__init__()
        self.owner = "unknown"
        self.acquired_at = 0.0

    async def acquire(self) -> Literal[True]:
        started = monotonic()
        await super().acquire()
        task = asyncio.current_task()
        self.owner = (
            f"{task.get_name()}:{getattr(task.get_coro(), '__qualname__', 'unknown')}"
            if task else "unknown"
        )
        self.acquired_at = monotonic()
        if self.acquired_at - started >= 1:
            logging.getLogger(__name__).warning(
                "DB lock waited %.2fs; owner=%s", self.acquired_at - started, self.owner
            )
        return True

    def release(self) -> None:
        held = monotonic() - self.acquired_at
        if self.locked() and held >= 1:
            logging.getLogger(__name__).warning(
                "DB lock held %.2fs; owner=%s", held, self.owner
            )
        super().release()
        self.owner = "unknown"


def resolve_postgres_url() -> str | None:
    """Return the configured PostgreSQL URL without exposing it in validation errors."""
    value = os.environ.get("NYX_DATABASE_URL")
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in ("postgres", "postgresql")
            and parsed.hostname is not None
            and parsed.path not in ("", "/")
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("NYX_DATABASE_URL must be a PostgreSQL URL")
    return value


@dataclass
class Database:
    """SQLite 连接 + 共享锁的捆绑；connect() 创建并返回，store 共用这一个。

    conn 全项目共享；lock 串行化并发访问（同一连接不能并发 execute/commit）。
    """

    conn: Connection
    lock: asyncio.Lock
    lock_timeout: float = DB_LOCK_TIMEOUT
    operation_timeout: float = DB_OPERATION_TIMEOUT
    failure_threshold: int = DB_FAILURE_THRESHOLD
    cooldown_seconds: float = DB_COOLDOWN_SECONDS
    checkpointer: BaseCheckpointSaver[str] | None = None
    _closed: bool = False
    _failure_state: str = "closed"
    _consecutive_failures: int = 0
    _opened_at: float = 0.0
    _transaction_state: ContextVar[bool] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._transaction_state = ContextVar(
            f"nyx_db_transaction_{id(self)}", default=False
        )

    async def close(self) -> None:
        """Close the shared connection once; repeated shutdown is a no-op."""
        if self._closed:
            return
        if isinstance(self.checkpointer, (AsyncSqliteSaver, AsyncPostgresSaver)):
            await self.checkpointer.conn.close()
        await self.conn.close()
        self._closed = True

    @property
    def is_closed(self) -> bool:
        """Return whether this database wrapper has been closed."""
        return self._closed

    @property
    def failure_state(self) -> str:
        """Return whether admission is closed, open, or half-open."""
        if self._failure_state == "open":
            if monotonic() - self._opened_at >= self.cooldown_seconds:
                self._failure_state = "half_open"
        return self._failure_state

    def record_success(self) -> None:
        """Reset the database circuit after a successful operation."""
        self._consecutive_failures = 0
        self._failure_state = "closed"
        self._opened_at = 0.0

    def record_failure(self) -> None:
        """Open the circuit after enough consecutive database failures."""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._failure_state = "open"
            self._opened_at = monotonic()

    @property
    def in_transaction(self) -> bool:
        """Return whether the current task owns a transaction on this DB."""
        return self._transaction_state.get()

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[Connection, None]:
        """Run one bounded local transaction under the shared DB lock."""
        if self.in_transaction:
            raise RuntimeError("不允许嵌套 Database.transaction()")
        try:
            async with asyncio.timeout(self.lock_timeout):
                await self.lock.acquire()
        except TimeoutError as error:
            owner = (
                self.lock.owner if isinstance(self.lock, DatabaseLock) else "unknown"
            )
            logging.getLogger(__name__).warning(
                "DB transaction lock timeout after %.2fs; holder=%s",
                self.lock_timeout, owner,
            )
            raise DatabaseLockTimeout(
                f"DB lock acquisition exceeded {self.lock_timeout:.2f}s "
                f"(holder={owner})"
            ) from error
        marker = self._transaction_state.set(True)
        try:
            try:
                # BEGIN may have reached the driver when cancellation arrives.
                # Its cleanup must share the same rollback boundary as the body.
                try:
                    await asyncio.wait_for(
                        self.conn.execute("BEGIN"), timeout=self.operation_timeout
                    )
                except TimeoutError as error:
                    raise DatabaseOperationTimeout("DB begin timed out") from error
                yield self.conn
                try:
                    await asyncio.wait_for(
                        self.conn.commit(), timeout=self.operation_timeout
                    )
                except TimeoutError as error:
                    raise DatabaseOperationTimeout("DB commit timed out") from error
            except BaseException:
                try:
                    await asyncio.wait_for(
                        self.conn.rollback(), timeout=self.operation_timeout
                    )
                except TimeoutError as error:
                    raise DatabaseOperationTimeout("DB rollback timed out") from error
                raise
        finally:
            self._transaction_state.reset(marker)
            self.lock.release()


# 迁移列表：每项 (version, [单条 SQL])。升序；已应用（≤ schema_version）的跳过。
_MIGRATIONS: list[tuple[int, list[str]]] = [
    (
        1,
        [
            """CREATE TABLE personality (
                id TEXT PRIMARY KEY,            -- 固定 'self'
                openness REAL NOT NULL,         -- 1-10
                conscientiousness REAL NOT NULL,
                extraversion REAL NOT NULL,
                agreeableness REAL NOT NULL,
                neuroticism REAL NOT NULL
            )""",
            """CREATE TABLE value_system (         -- 三观
                id TEXT PRIMARY KEY,            -- 固定 'self'
                attitude_to_human REAL NOT NULL,      -- 1-10
                ai_identity_acceptance REAL NOT NULL, -- 1-10
                altruism REAL NOT NULL,               -- 1-10
                optimism REAL NOT NULL                 -- 1-10
            )""",
            """CREATE TABLE energy (
                id TEXT PRIMARY KEY,            -- 固定 'self'
                value REAL NOT NULL,            -- 0-100（映射 CurrentState.energy）
                state TEXT NOT NULL             -- EnergyState（映射 energy_state）
            )""",
            """CREATE TABLE self_narrative (
                id TEXT PRIMARY KEY,            -- 固定 'self'
                identity TEXT NOT NULL,
                story TEXT NOT NULL,            -- JSON 数组
                self_view TEXT NOT NULL,        -- JSON 对象
                becoming TEXT NOT NULL,         -- JSON 数组
                updated_at REAL NOT NULL
            )""",
            """CREATE TABLE memory (
                id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                content TEXT NOT NULL,
                tag TEXT NOT NULL,
                summary TEXT NOT NULL,
                freshness REAL NOT NULL,
                type TEXT NOT NULL,             -- MemoryType
                recall_count INTEGER NOT NULL DEFAULT 0,
                aspect TEXT NOT NULL,           -- user 画像，JSON 数组（空 = "[]"）
                embedding TEXT               -- 向量 JSON（list[float]）；未嵌入为 NULL
            )""",
            "CREATE INDEX idx_memory_tag ON memory(tag)",
            "CREATE INDEX idx_memory_type ON memory(type)",
            """CREATE TABLE memory_edge (
                from_id TEXT NOT NULL REFERENCES memory(id),
                to_id TEXT NOT NULL REFERENCES memory(id),
                weight REAL NOT NULL DEFAULT 1.0,
                PRIMARY KEY (from_id, to_id)
            )""",
            """CREATE TABLE short_term_desire (
                id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                type TEXT NOT NULL,             -- DesireType
                strength REAL NOT NULL,
                description TEXT NOT NULL,
                goal TEXT,                      -- JSON: Goal（Optional，可空）
                retry_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL            -- DesireStatus
            )""",
            """CREATE TABLE desire_value (
                type TEXT PRIMARY KEY,          -- DesireType
                value REAL NOT NULL,
                expression_weight REAL NOT NULL,
                suppression_threshold REAL NOT NULL,
                updated_at REAL NOT NULL   -- value 上次变化时间戳（elapsed 衰减来源）
            )""",
            """CREATE TABLE long_term_desire (
                id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                type TEXT NOT NULL,             -- DesireType
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                strength REAL NOT NULL,
                progress REAL NOT NULL,
                subtopics TEXT NOT NULL,        -- JSON 数组
                linked_values TEXT NOT NULL     -- JSON 数组（空 = "[]"）
            )""",
            """CREATE TABLE activity (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,             -- ActivityType
                schedule_block_id TEXT NOT NULL,
                status TEXT NOT NULL,           -- ActivityStatus
                progress TEXT NOT NULL,         -- JSON
                started_at REAL NOT NULL,
                ended_at REAL                   -- float | None，可空
            )""",
            """CREATE TABLE event_log (
                id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                source TEXT NOT NULL,           -- Source
                type TEXT NOT NULL,             -- EventType
                content TEXT NOT NULL,          -- JSON
                correlation_id TEXT NOT NULL
            )""",
            "CREATE INDEX idx_event_log_corr ON event_log(correlation_id)",
        ],
    ),
    (
        2,
        [
            """CREATE TABLE material (
                path TEXT PRIMARY KEY,          -- 读物绝对路径
                filename TEXT NOT NULL,
                total_chars INTEGER NOT NULL,   -- 总字数（字符）
                read_chars INTEGER NOT NULL DEFAULT 0,  -- 已读字数（分块进度）
                created_at REAL NOT NULL,       -- 上传时间（「最近那本」排序键）
                updated_at REAL NOT NULL        -- 进度上次推进时间
            )""",
        ],
    ),
    (
        3,
        [
            # 读书聚合：每块 note 片段（JSON 数组，读完一本后聚合用）
            "ALTER TABLE material ADD COLUMN note_fragments TEXT NOT NULL DEFAULT '[]'",
            # goal 精确计数：已完成单位数（count 次才满足）
            "ALTER TABLE short_term_desire ADD COLUMN goal_progress INTEGER "
            "NOT NULL DEFAULT 0",
        ],
    ),
    (
        6,
        [
            # 记忆去重：content 精确哈希（store 派生，非 Memory 字段；旧行 NULL 不去重）
            "ALTER TABLE memory ADD COLUMN content_hash TEXT",
            "CREATE INDEX idx_memory_content_hash ON memory(content_hash)",
        ],
    ),
    (
        7,
        [
            # 陪读：EPUB 书（books）+ 段落（paragraphs）
            """CREATE TABLE books (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                author TEXT NOT NULL DEFAULT '',
                filename TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL,
                total_paragraphs INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""",
            "CREATE INDEX idx_books_content_hash ON books(content_hash)",
            """CREATE TABLE paragraphs (
                id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                "index" INTEGER NOT NULL,   -- index 是 SQLite 关键字，需引号
                text TEXT NOT NULL,
                is_chapter_start INTEGER NOT NULL DEFAULT 0,
                UNIQUE(book_id, "index")
            )""",
        ],
    ),
    (
        8,
        [
            # content_hash 去重升级为唯一索引（并发导入不产重复书；原 v7 为普通索引）。
            # 升级前先清掉旧竞态窗口可能留下的重复行（保留最早插入的一本），否则
            # CREATE UNIQUE INDEX 撞 IntegrityError 会让 migrate 整体回滚、应用起不来。
            "DELETE FROM books WHERE rowid NOT IN ("
            "SELECT MIN(rowid) FROM books GROUP BY content_hash)",
            "DROP INDEX IF EXISTS idx_books_content_hash",
            "CREATE UNIQUE INDEX idx_books_content_hash ON books(content_hash)",
        ],
    ),
    (
        9,
        [
            # 陪读进度：1:1 书（book_id PK）。
            # user/nyx_position 从 1 起（与 paragraphs."index" 对齐）；
            # reading_speed 10-200；read_count 只由阅读系统
            # 整本读完 ++，默认 0。
            """CREATE TABLE reading_progress (
                book_id TEXT PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
                user_position INTEGER NOT NULL DEFAULT 1,
                nyx_position INTEGER NOT NULL DEFAULT 1,
                reading_speed INTEGER NOT NULL DEFAULT 50,
                read_count INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            )""",
        ],
    ),
    (
        10,
        [
            # 陪读笔记：用户手写笔记 + Nyx 批注。
            # 用户笔记与 Nyx 笔记严格分离；book/paragraph 删除时 SET NULL 兜底
            # （笔记文字仍可读）；批注随笔记 CASCADE 删除。
            """CREATE TABLE user_notes (
                id TEXT PRIMARY KEY,
                book_id TEXT REFERENCES books(id) ON DELETE SET NULL,
                paragraph_id TEXT REFERENCES paragraphs(id) ON DELETE SET NULL,
                content TEXT NOT NULL,
                selected_text TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""",
            """CREATE TABLE annotations (
                id TEXT PRIMARY KEY,
                user_note_id TEXT NOT NULL REFERENCES user_notes(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            )""",
        ],
    ),
    (
        11,
        [
            # 审美维度：四轴 1-10（10=第一极）。
            """CREATE TABLE aesthetic (
                id TEXT PRIMARY KEY,            -- 固定 'self'
                ornate REAL NOT NULL,           -- 华丽
                lyrical REAL NOT NULL,          -- 抒情
                classical REAL NOT NULL,        -- 古典
                somber REAL NOT NULL            -- 沉重
            )""",
        ],
    ),
    (
        12,
        [
            # 记忆「首次创建」锚点：created_at/first_created_at 均为创建时间，
            # strengthen 不刷新；count_new 以 first_created_at 判定真新增。
            "ALTER TABLE memory ADD COLUMN first_created_at REAL",
            # 回填旧行：迁移前无 first_created_at，用当前 created_at 近似
            # （历史行的真首次时间已不可考，这是能取到的最好近似）。
            "UPDATE memory SET first_created_at = created_at "
            "WHERE first_created_at IS NULL",
        ],
    ),
    (
        13,
        [
            # eval 可观测：LLM 调用 + token 记账。
            # 不存 content 原文（数据最小化）。
            # call_id 一次 complete() 唯一，think/speak 共享（总 token 去重锚点）。
            """CREATE TABLE eval_log (
                id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                call_id TEXT NOT NULL,
                module TEXT NOT NULL,
                output_type TEXT NOT NULL,
                model TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                ooc_keyword REAL NOT NULL,
                ooc_embed REAL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0
            )""",
            "CREATE INDEX idx_eval_log_created ON eval_log(created_at)",
        ],
    ),
    (
        14,
        [
            "ALTER TABLE memory_edge RENAME TO memory_edge_old",
            """CREATE TABLE memory_edge (
                from_id TEXT NOT NULL REFERENCES memory(id) ON DELETE CASCADE,
                to_id TEXT NOT NULL REFERENCES memory(id) ON DELETE CASCADE,
                kind TEXT NOT NULL DEFAULT 'semantic',
                weight REAL NOT NULL DEFAULT 1.0,
                created_at REAL NOT NULL DEFAULT 0.0,
                CHECK (from_id < to_id),
                PRIMARY KEY (from_id, to_id, kind)
            )""",
            """INSERT INTO memory_edge (from_id, to_id, kind, weight, created_at)
            SELECT
                CASE WHEN from_id < to_id THEN from_id ELSE to_id END,
                CASE WHEN from_id < to_id THEN to_id ELSE from_id END,
                'semantic',
                MAX(weight),
                0.0
            FROM memory_edge_old
            WHERE from_id != to_id
            GROUP BY
                CASE WHEN from_id < to_id THEN from_id ELSE to_id END,
                CASE WHEN from_id < to_id THEN to_id ELSE from_id END""",
            "DROP TABLE memory_edge_old",
        ],
    ),
    (
        15,
        [
            """CREATE TABLE event_delivery (
                event_id TEXT NOT NULL REFERENCES event_log(id) ON DELETE CASCADE,
                consumer_id TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at REAL NOT NULL DEFAULT 0.0,
                started_at REAL,
                completed_at REAL,
                lease_until REAL,
                last_error TEXT,
                PRIMARY KEY (event_id, consumer_id)
            )""",
            """CREATE INDEX idx_event_delivery_ready
            ON event_delivery(status, available_at)""",
            """CREATE INDEX idx_event_delivery_consumer_ready
            ON event_delivery(consumer_id, status, available_at)""",
            """CREATE TABLE event_effect (
                event_id TEXT NOT NULL REFERENCES event_log(id) ON DELETE CASCADE,
                consumer_id TEXT NOT NULL,
                applied_at REAL NOT NULL,
                PRIMARY KEY (event_id, consumer_id)
            )""",
        ],
    ),
    (
        16,
        [
            "ALTER TABLE long_term_desire ADD COLUMN name_normalized TEXT "
            "NOT NULL DEFAULT ''",
            "UPDATE long_term_desire SET name_normalized = lower(trim(name)) "
            "WHERE name_normalized = ''",
            "DELETE FROM long_term_desire WHERE rowid NOT IN ("
            "SELECT MIN(rowid) FROM long_term_desire "
            "GROUP BY name_normalized)",
            "CREATE UNIQUE INDEX idx_long_term_desire_name_normalized "
            "ON long_term_desire(name_normalized)",
            """CREATE TABLE desire_generation_attempt (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                created_at REAL NOT NULL,
                peak_value REAL NOT NULL,
                seed TEXT,
                output_content TEXT NOT NULL
            )""",
            "CREATE INDEX idx_desire_generation_attempt_type "
            "ON desire_generation_attempt(type)",
        ],
    ),
    (
        17,
        [
            """CREATE TABLE expression_interaction_attempt (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                status TEXT NOT NULL,
                answered_at REAL,
                answer_event_id TEXT,
                failure_reason TEXT
            )""",
            "CREATE INDEX idx_expression_attempt_status_expiry "
            "ON expression_interaction_attempt(status, expires_at)",
            "CREATE INDEX idx_expression_attempt_status_created "
            "ON expression_interaction_attempt(status, created_at)",
            "CREATE INDEX idx_expression_attempt_correlation "
            "ON expression_interaction_attempt(correlation_id)",
        ],
    ),
    (
        18,
        [
            # 阅读进度 CAS 版本：每次成功写入递增，防旧快照覆盖新位置。
            (
                "ALTER TABLE reading_progress ADD COLUMN revision "
                "INTEGER NOT NULL DEFAULT 0"
            ),
        ],
    ),
    (
        19,
        [
            # 记忆标签正式替换为受控 kind；旧记忆及其关系/召回状态按产品决策清空。
            "DELETE FROM memory_edge",
            "DELETE FROM memory",
            "DROP INDEX IF EXISTS idx_memory_tag",
            "ALTER TABLE memory RENAME COLUMN tag TO kind",
            "ALTER TABLE memory ADD COLUMN topics TEXT NOT NULL DEFAULT '[]'",
            "CREATE INDEX idx_memory_kind ON memory(kind)",
            "CREATE INDEX idx_memory_kind_hash ON memory(kind, content_hash)",
        ],
    ),
    (
        20,
        [
            """CREATE TABLE desire_eval_applied (
                event_id TEXT PRIMARY KEY,
                applied_at REAL NOT NULL
            )""",
        ],
    ),
    (
        21,
        [
            # 完整 prompt 按真实 LLM call 去重；think/speak 通过 call_id 共享。
            """CREATE TABLE eval_prompt (
                call_id TEXT NOT NULL PRIMARY KEY,
                prompt_json TEXT NOT NULL
            )""",
        ],
    ),
    (
        23,
        [
            # Browsing was removed after schema 22. Delete its durable rows before
            # the corresponding enum values disappear from the runtime.
            "DELETE FROM event_delivery WHERE event_id IN ("
            "SELECT id FROM event_log WHERE type IN ("
            "'browsing_mutter', 'browsing_question', 'browsing_association'))",
            "DELETE FROM event_effect WHERE event_id IN ("
            "SELECT id FROM event_log WHERE type IN ("
            "'browsing_mutter', 'browsing_question', 'browsing_association'))",
            "DELETE FROM event_log WHERE type IN ("
            "'browsing_mutter', 'browsing_question', 'browsing_association')",
            "DELETE FROM memory_edge WHERE from_id IN ("
            "SELECT id FROM memory WHERE kind = 'browsing') OR to_id IN ("
            "SELECT id FROM memory WHERE kind = 'browsing')",
            "DELETE FROM memory WHERE kind = 'browsing'",
            "DELETE FROM expression_interaction_attempt "
            "WHERE kind = 'browsing_question'",
            "DROP TABLE IF EXISTS browsing_page",
            "DROP TABLE IF EXISTS browsing_session",
        ],
    ),
    (
        24,
        [
            """CREATE TABLE IF NOT EXISTS memory_entity (
                id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL UNIQUE,
                entity_type TEXT NOT NULL DEFAULT 'concept',
                aliases TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS memory_fact (
                id TEXT PRIMARY KEY,
                subject_entity_id TEXT NOT NULL REFERENCES memory_entity(id),
                predicate TEXT NOT NULL,
                object_entity_id TEXT REFERENCES memory_entity(id),
                object_value TEXT NOT NULL,
                source_memory_id TEXT REFERENCES memory(id) ON DELETE SET NULL,
                valid_from REAL NOT NULL,
                valid_until REAL,
                created_at REAL NOT NULL,
                CHECK (valid_until IS NULL OR valid_until > valid_from)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_memory_fact_subject "
            "ON memory_fact(subject_entity_id)",
            "CREATE INDEX IF NOT EXISTS idx_memory_fact_object "
            "ON memory_fact(object_entity_id)",
            "CREATE INDEX IF NOT EXISTS idx_memory_fact_validity "
            "ON memory_fact(valid_from, valid_until)",
            "CREATE INDEX IF NOT EXISTS idx_memory_fact_predicate "
            "ON memory_fact(predicate, valid_from)",
        ],
    ),
    (
        25,
        [
            "CREATE TABLE memory_entity_new ("
            "id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL, "
            "entity_type TEXT NOT NULL DEFAULT 'concept', "
            "aliases TEXT NOT NULL DEFAULT '[]', "
            "created_at REAL NOT NULL, updated_at REAL NOT NULL, "
            "UNIQUE(canonical_name, entity_type))",
            "INSERT INTO memory_entity_new "
            "SELECT id, canonical_name, entity_type, aliases, created_at, updated_at "
            "FROM memory_entity",
            "CREATE TABLE memory_fact_new ("
            "id TEXT PRIMARY KEY, "
            "subject_entity_id TEXT NOT NULL REFERENCES memory_entity_new(id), "
            "predicate TEXT NOT NULL, "
            "object_entity_id TEXT REFERENCES memory_entity_new(id), "
            "object_value TEXT NOT NULL, "
            "source_memory_id TEXT REFERENCES memory(id) ON DELETE SET NULL, "
            "valid_from REAL NOT NULL, valid_until REAL, created_at REAL NOT NULL, "
            "polarity INTEGER NOT NULL DEFAULT 1, "
            "CHECK (valid_until IS NULL OR valid_until > valid_from))",
            "INSERT INTO memory_fact_new "
            "SELECT id, subject_entity_id, predicate, object_entity_id, object_value, "
            "source_memory_id, valid_from, valid_until, created_at, 1 FROM memory_fact",
            "DROP TABLE memory_fact",
            "DROP TABLE memory_entity",
            "ALTER TABLE memory_entity_new RENAME TO memory_entity",
            "ALTER TABLE memory_fact_new RENAME TO memory_fact",
            "CREATE INDEX idx_memory_fact_subject ON memory_fact(subject_entity_id)",
            "CREATE INDEX idx_memory_fact_object ON memory_fact(object_entity_id)",
            "CREATE INDEX idx_memory_fact_validity "
            "ON memory_fact(valid_from, valid_until)",
            "CREATE INDEX idx_memory_fact_predicate "
            "ON memory_fact(predicate, valid_from)",
        ],
    ),
    (
        26,
        [
            """CREATE TABLE IF NOT EXISTS memory_entity_alias (
                entity_id TEXT NOT NULL REFERENCES memory_entity(id) ON DELETE CASCADE,
                alias TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                PRIMARY KEY (entity_id, alias),
                UNIQUE (alias, entity_type)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_memory_entity_alias_lookup "
            "ON memory_entity_alias(alias, entity_type)",
            "INSERT OR IGNORE INTO memory_entity_alias "
            "(entity_id, alias, entity_type) "
            "SELECT e.id, trim(j.value), e.entity_type "
            "FROM memory_entity e, json_each(e.aliases) j "
            "WHERE trim(j.value) <> ''",
        ],
    ),
    (
        27,
        [
            "CREATE INDEX IF NOT EXISTS idx_activity_results "
            "ON activity(status, type, ended_at DESC)",
        ],
    ),
    (
        28,
        [
            "ALTER TABLE books ADD COLUMN memory_state TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE material ADD COLUMN memory_state TEXT NOT NULL DEFAULT '{}'",
        ],
    ),
    (
        29,
        [
            "ALTER TABLE paragraphs ADD COLUMN format_json TEXT NOT NULL "
            'DEFAULT \'{"blocks":[],"marks":[]}\'',
        ],
    ),
    (
        30,
        [
            "ALTER TABLE user_notes ADD COLUMN selection_start INTEGER",
            "ALTER TABLE user_notes ADD COLUMN selection_end INTEGER",
            """CREATE TABLE bookmarks (
                id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                paragraph_id TEXT NOT NULL REFERENCES paragraphs(id) ON DELETE CASCADE,
                created_at REAL NOT NULL,
                UNIQUE(book_id, paragraph_id)
            )""",
        ],
    ),
    (
        31,
        [
            """CREATE TABLE assigned_task (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                url TEXT,
                book_id TEXT REFERENCES books(id) ON DELETE SET NULL,
                target_paragraph INTEGER,
                checkpoint TEXT NOT NULL DEFAULT '{}',
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""",
            "CREATE INDEX idx_assigned_task_status_created "
            "ON assigned_task(status, created_at)",
        ],
    ),
    (
        32,
        [
            "ALTER TABLE short_term_desire ADD COLUMN parent_long_term_id TEXT "
            "REFERENCES long_term_desire(id) ON DELETE SET NULL",
            "ALTER TABLE desire_generation_attempt ADD COLUMN "
            "parent_long_term_id TEXT REFERENCES long_term_desire(id) "
            "ON DELETE SET NULL",
        ],
    ),
    (
        33,
        [
            "UPDATE short_term_desire SET status = 'pending' "
            "WHERE status IN ('active', 'suppressed') AND id IN ("
            "SELECT json_extract(a.progress, '$.desire_id') FROM activity a "
            "WHERE a.type = 'reading' "
            "AND a.status IN ('pending', 'running', 'paused') "
            "AND json_type(a.progress, '$.source') = 'text' "
            "AND json_extract(a.progress, '$.book_id') IS NULL "
            "AND json_extract(a.progress, '$.task_id') IS NULL)",
            "UPDATE activity SET status = 'abandoned', "
            "ended_at = COALESCE(ended_at, CAST(strftime('%s', 'now') AS REAL)) "
            "WHERE type = 'reading' "
            "AND status IN ('pending', 'running', 'paused') "
            "AND json_type(progress, '$.source') = 'text' "
            "AND json_extract(progress, '$.book_id') IS NULL "
            "AND json_extract(progress, '$.task_id') IS NULL",
            "DROP TABLE material",
        ],
    ),
    (
        34,
        [
            "ALTER TABLE memory ADD COLUMN freshness_updated_at "
            "REAL NOT NULL DEFAULT 0",
            "UPDATE memory SET freshness_updated_at = "
            "CAST(strftime('%s', 'now') AS REAL)",
        ],
    ),
    (
        35,
        [
            """CREATE TABLE memory_recall_use (
                user_event_id TEXT NOT NULL,
                memory_id TEXT NOT NULL REFERENCES memory(id) ON DELETE CASCADE,
                PRIMARY KEY (user_event_id, memory_id)
            )""",
            "UPDATE expression_interaction_attempt SET status = 'waiting' "
            "WHERE status = 'claimed' AND answer_event_id IS NULL",
        ],
    ),
    (
        36,
        [
            """CREATE TABLE reading_evidence (
                id TEXT PRIMARY KEY,
                source_topic TEXT NOT NULL,
                source_name TEXT NOT NULL,
                content TEXT NOT NULL CHECK(length(content) BETWEEN 1 AND 6000),
                created_at REAL NOT NULL,
                consumed_at REAL,
                book_id TEXT REFERENCES books(id) ON DELETE CASCADE
            )""",
            "CREATE INDEX idx_reading_evidence_pending "
            "ON reading_evidence(created_at, id) WHERE consumed_at IS NULL",
        ],
    ),
    (
        37,
        [
            "ALTER TABLE expression_interaction_attempt ADD COLUMN paused_at REAL",
            "CREATE INDEX idx_expression_attempt_waiting_paused "
            "ON expression_interaction_attempt(status, paused_at, expires_at)",
        ],
    ),
    (
        38,
        [
            "CREATE TABLE character_initialization (id TEXT PRIMARY KEY)",
            "CREATE TABLE presence_checkpoint (id TEXT PRIMARY KEY, "
            "sequence INTEGER NOT NULL, snapshot TEXT NOT NULL)",
        ],
    ),
    (
        39,
        [
            """CREATE TABLE conversation_segment (
                id TEXT PRIMARY KEY,
                previous_id TEXT REFERENCES conversation_segment(id) ON DELETE SET NULL,
                started_at REAL NOT NULL,
                ended_at REAL NOT NULL,
                raw_event_ids TEXT NOT NULL,
                raw_content TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                topics TEXT NOT NULL DEFAULT '[]',
                embedding TEXT,
                importance TEXT NOT NULL DEFAULT 'normal',
                long_term INTEGER NOT NULL DEFAULT 0
            )""",
            "CREATE INDEX idx_conversation_segment_time "
            "ON conversation_segment(ended_at, id)",
        ],
    ),
    (
        40,
        [
            "ALTER TABLE memory_fact ADD COLUMN scope TEXT NOT NULL DEFAULT "
            "'conversation'"
        ],
    ),
    (
        41,
        [
            "ALTER TABLE conversation_segment ADD COLUMN evaluated_at REAL",
            "ALTER TABLE conversation_segment ADD COLUMN evaluation_reason "
            "TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE conversation_segment ADD COLUMN evidence_event_ids "
            "TEXT NOT NULL DEFAULT '[]'",
            "CREATE TABLE conversation_segment_evidence ("
            "segment_id TEXT NOT NULL REFERENCES conversation_segment(id) "
            "ON DELETE CASCADE, "
            "evidence_segment_id TEXT NOT NULL REFERENCES conversation_segment(id) "
            "ON DELETE CASCADE, "
            "relation TEXT NOT NULL DEFAULT 'supports', "
            "created_at REAL NOT NULL, "
            "PRIMARY KEY(segment_id, evidence_segment_id, relation))",
        ],
    ),
    (
        42,
        [
            "ALTER TABLE conversation_segment ADD COLUMN freshness "
            "REAL NOT NULL DEFAULT 1.0",
            "ALTER TABLE conversation_segment ADD COLUMN freshness_updated_at "
            "REAL NOT NULL DEFAULT 0.0",
            "UPDATE conversation_segment SET freshness_updated_at = "
            "CAST(strftime('%s', 'now') AS REAL)",
            "ALTER TABLE conversation_segment ADD COLUMN recall_count "
            "INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE conversation_segment ADD COLUMN active "
            "INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE conversation_segment ADD COLUMN facts_extracted_at REAL",
            "ALTER TABLE conversation_segment ADD COLUMN fact_scope "
            "TEXT NOT NULL DEFAULT 'conversation'",
            "CREATE TABLE conversation_segment_recall_use ("
            "user_event_id TEXT NOT NULL, segment_id TEXT NOT NULL "
            "REFERENCES conversation_segment(id) ON DELETE CASCADE, "
            "PRIMARY KEY(user_event_id, segment_id))",
            "ALTER TABLE memory_fact ADD COLUMN source_segment_id TEXT "
            "REFERENCES conversation_segment(id) ON DELETE SET NULL",
            "CREATE INDEX idx_memory_fact_source_segment "
            "ON memory_fact(source_segment_id)",
        ],
    ),
    (
        43,
        [
            "ALTER TABLE reading_evidence ADD COLUMN location TEXT",
            "ALTER TABLE reading_evidence ADD COLUMN embedding TEXT",
            "ALTER TABLE memory_fact ADD COLUMN source_chunk_id TEXT "
            "REFERENCES reading_evidence(id) ON DELETE SET NULL",
        ],
    ),
    (
        44,
        [
            "ALTER TABLE reading_evidence ADD COLUMN content_hash TEXT NOT NULL "
            "DEFAULT ''",
            "UPDATE reading_evidence SET content_hash = nyx_raw_sha256(content)",
            "CREATE INDEX idx_reading_evidence_exact ON reading_evidence "
            "(source_topic, content_hash, created_at, id)",
            "CREATE TABLE knowledge_chunk_change ("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, chunk_id TEXT NOT NULL)",
            "CREATE TRIGGER knowledge_chunk_insert AFTER INSERT ON reading_evidence "
            "BEGIN INSERT INTO knowledge_chunk_change(chunk_id) VALUES (NEW.id); END",
            "CREATE TRIGGER knowledge_chunk_delete AFTER DELETE ON reading_evidence "
            "BEGIN INSERT INTO knowledge_chunk_change(chunk_id) VALUES (OLD.id); END",
            "CREATE TRIGGER knowledge_chunk_update AFTER UPDATE OF "
            "source_topic, source_name, content, location, embedding, created_at "
            "ON reading_evidence BEGIN INSERT INTO knowledge_chunk_change(chunk_id) "
            "VALUES (NEW.id); END",
        ],
    ),
    (
        45,
        [
            "CREATE TABLE assigned_task_source ("
            "task_id TEXT PRIMARY KEY NOT NULL "
            "REFERENCES assigned_task(id) ON DELETE CASCADE, "
            "content TEXT NOT NULL CHECK(length(content) <= 200000))",
            "INSERT INTO assigned_task_source(task_id, content) "
            "SELECT id, nyx_task_source_snapshot(checkpoint) "
            "FROM assigned_task WHERE type = 'web' "
            "AND json_type(checkpoint, '$.source_text') = 'text'",
            "UPDATE assigned_task SET checkpoint = "
            "json_remove(checkpoint, '$.source_text') "
            "WHERE id IN (SELECT task_id FROM assigned_task_source)",
        ],
    ),
    (
        46,
        [
            "CREATE TABLE annotation_feedback ("
            "event_id TEXT PRIMARY KEY, reply_event_id TEXT NOT NULL, "
            "quote TEXT NOT NULL, comment TEXT NOT NULL, "
            "user_event_id TEXT NOT NULL, created_at REAL NOT NULL, "
            "UNIQUE(reply_event_id, comment))",
        ],
    ),
    (
        47,
        [
            "CREATE TABLE tool_call_log ("
            "id TEXT PRIMARY KEY, created_at REAL NOT NULL, correlation_id TEXT, "
            "call_id TEXT, tool_name TEXT NOT NULL, args_json TEXT NOT NULL, "
            "status TEXT NOT NULL, duration_ms REAL NOT NULL, result_json TEXT, "
            "error TEXT, adopted INTEGER NOT NULL DEFAULT 0)",
            "CREATE INDEX idx_tool_call_log_created ON tool_call_log(created_at)",
            "CREATE INDEX idx_tool_call_log_correlation "
            "ON tool_call_log(correlation_id)",
            "CREATE INDEX idx_tool_call_log_call ON tool_call_log(call_id)",
        ],
    ),
    (
        48,
        [
            "CREATE TABLE lesson_signal ("
            "topic TEXT PRIMARY KEY, kind TEXT NOT NULL, "
            "count INTEGER NOT NULL DEFAULT 0, last_at REAL NOT NULL)",
        ],
    ),
    (
        49,
        [
            "CREATE TABLE checkpoints (thread_id TEXT NOT NULL, "
            "checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL, "
            "parent_checkpoint_id TEXT, type TEXT, checkpoint BLOB, metadata BLOB, "
            "PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id))",
            "CREATE TABLE writes (thread_id TEXT NOT NULL, "
            "checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL, "
            "task_id TEXT NOT NULL, idx INTEGER NOT NULL, channel TEXT NOT NULL, "
            "type TEXT, value BLOB, "
            "PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx))",
        ],
    ),
    (
        50,
        [
            "CREATE TABLE tool_execution (id TEXT PRIMARY KEY, "
            "correlation_id TEXT NOT NULL, status TEXT NOT NULL, "
            "result_json TEXT, error TEXT)",
            "CREATE INDEX idx_tool_execution_correlation "
            "ON tool_execution(correlation_id)",
        ],
    ),
    (
        51,
        [
            "CREATE TABLE transport_checkpoint (transport TEXT PRIMARY KEY, "
            "scan_rowid INTEGER NOT NULL, sent_rowid INTEGER NOT NULL)",
            "CREATE TABLE transport_outbox (transport TEXT NOT NULL, "
            "event_id TEXT NOT NULL REFERENCES event_log(id) ON DELETE CASCADE, "
            "event_rowid INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
            "attempts INTEGER NOT NULL DEFAULT 0, "
            "available_at REAL NOT NULL DEFAULT 0, lease_until REAL, "
            "last_error TEXT, PRIMARY KEY(transport, event_id))",
            "CREATE INDEX idx_transport_outbox_order "
            "ON transport_outbox(transport, event_rowid) WHERE status != 'sent'",
        ],
    ),
    (
        52,
        [
            "ALTER TABLE event_log ADD COLUMN event_seq INTEGER",
            "UPDATE event_log SET event_seq = rowid WHERE event_seq IS NULL",
            "CREATE UNIQUE INDEX idx_event_log_event_seq ON event_log(event_seq)",
        ],
    ),
    (
        53,
        [
            "CREATE TABLE conversation_turn_archive ("
            "turn_id TEXT PRIMARY KEY NOT NULL CHECK (length(turn_id)>0), "
            "segment_id TEXT NOT NULL REFERENCES conversation_segment(id) "
            "ON DELETE CASCADE)",
            "INSERT INTO conversation_turn_archive (turn_id, segment_id) "
            "SELECT j.value, MIN(s.id) FROM conversation_segment s, "
            "json_each(s.raw_event_ids) AS j GROUP BY j.value",
        ],
    ),
    (
        54,
        [
            "CREATE TABLE skill_execution (id TEXT PRIMARY KEY, data TEXT NOT NULL)",
            "CREATE TABLE research_knowledge_receipt (id TEXT PRIMARY KEY, "
            "result TEXT NOT NULL)",
            "CREATE TABLE skill_revision (name TEXT NOT NULL, "
            "version INTEGER NOT NULL, "
            "body TEXT NOT NULL, tool_names TEXT NOT NULL, reflection_id TEXT UNIQUE, "
            "created_at DOUBLE PRECISION NOT NULL, PRIMARY KEY(name, version))",
            "CREATE TABLE behavior_reflection (id TEXT PRIMARY KEY, "
            "status TEXT NOT NULL, data TEXT NOT NULL, "
            "created_at DOUBLE PRECISION NOT NULL, "
            "updated_at DOUBLE PRECISION NOT NULL)",
            "CREATE TABLE research_run (id TEXT PRIMARY KEY, origin TEXT NOT NULL, "
            "correlation_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL, "
            "created_at DOUBLE PRECISION NOT NULL, "
            "updated_at DOUBLE PRECISION NOT NULL)",
            "CREATE INDEX idx_research_correlation ON research_run(correlation_id)",
        ],
    ),
    (
        55,
        [
            "CREATE TABLE inner_document (id TEXT PRIMARY KEY, topics TEXT NOT NULL, "
            "revision INTEGER NOT NULL, updated_at DOUBLE PRECISION NOT NULL)",
            "DROP TABLE personality",
            "DROP TABLE value_system",
            "DROP TABLE aesthetic",
            "DROP TABLE self_narrative",
            "ALTER TABLE long_term_desire DROP COLUMN linked_values",
        ],
    ),
    (
        56,
        [
            "CREATE TABLE conversation_chunk (id TEXT PRIMARY KEY, "
            "segment_id TEXT NOT NULL REFERENCES conversation_segment(id) "
            "ON DELETE CASCADE, start_offset INTEGER NOT NULL, "
            "end_offset INTEGER NOT NULL, content TEXT NOT NULL, embedding TEXT, "
            "token_count INTEGER NOT NULL, "
            "CHECK(start_offset>=0 AND end_offset>start_offset))",
            "CREATE INDEX idx_conversation_chunk_segment "
            "ON conversation_chunk(segment_id, start_offset)",
            "CREATE TABLE conversation_chunk_term (term TEXT NOT NULL, "
            "chunk_id TEXT NOT NULL REFERENCES conversation_chunk(id) "
            "ON DELETE CASCADE, frequency INTEGER NOT NULL, "
            "PRIMARY KEY(term, chunk_id))",
            "CREATE INDEX idx_conversation_chunk_term_chunk "
            "ON conversation_chunk_term(chunk_id)",
            "CREATE TABLE conversation_chunk_change "
            "(seq INTEGER PRIMARY KEY AUTOINCREMENT, chunk_id TEXT NOT NULL)",
            *(f"CREATE TRIGGER conversation_chunk_{action.lower()} "
              f"AFTER {action} ON conversation_chunk BEGIN "
              "INSERT INTO conversation_chunk_change(chunk_id) "
              f"VALUES ({'OLD' if action == 'DELETE' else 'NEW'}.id); END"
              for action in ("INSERT", "UPDATE", "DELETE")),
        ],
    ),
    (
        57,
        [
            "ALTER TABLE conversation_chunk ADD COLUMN embedding_version TEXT "
            "NOT NULL DEFAULT ''",
            "CREATE TABLE conversation_index_job (segment_id TEXT PRIMARY KEY "
            "REFERENCES conversation_segment(id) ON DELETE CASCADE, "
            "revision INTEGER NOT NULL DEFAULT 1, completed_revision INTEGER "
            "NOT NULL DEFAULT 0, index_version TEXT NOT NULL DEFAULT '', "
            "attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0, "
            "last_error TEXT NOT NULL DEFAULT '')",
            "INSERT INTO conversation_index_job(segment_id) "
            "SELECT id FROM conversation_segment",
            "CREATE INDEX idx_conversation_index_retry ON "
            "conversation_index_job(retry_at, segment_id)",
            "CREATE TABLE conversation_retrieval_trace (id TEXT PRIMARY KEY, "
            "created_at REAL NOT NULL, query_hash TEXT NOT NULL, "
            "index_version TEXT NOT NULL, include_archived INTEGER NOT NULL, "
            "elapsed_ms REAL NOT NULL)",
            "CREATE INDEX idx_conversation_trace_time ON "
            "conversation_retrieval_trace(created_at, id)",
            "CREATE TABLE conversation_retrieval_hit (trace_id TEXT NOT NULL "
            "REFERENCES conversation_retrieval_trace(id) ON DELETE CASCADE, "
            "chunk_id TEXT NOT NULL REFERENCES conversation_chunk(id) "
            "ON DELETE CASCADE, segment_id TEXT NOT NULL REFERENCES "
            "conversation_segment(id) ON DELETE CASCADE, "
            "bm25 REAL, cosine REAL, fusion REAL NOT NULL, score REAL NOT NULL, "
            "selected INTEGER NOT NULL, source_ranges TEXT NOT NULL, "
            "injected_event_id TEXT, PRIMARY KEY(trace_id, chunk_id))",
            "CREATE INDEX idx_conversation_hit_segment ON "
            "conversation_retrieval_hit(segment_id, trace_id)",
            *_conversation_lifecycle_triggers(),
        ],
    ),
]


def resolve_db_path(path: str | None = None) -> str:
    """Resolve legacy defaults without moving existing saves."""
    return (
        path
        or os.environ.get("NYX_DB")
        or (_LEGACY_DB_PATH if Path(_LEGACY_DB_PATH).is_file() else DEFAULT_DB_PATH)
    )


async def connect(
    path: str | None = None,
    *,
    migrations: list[tuple[int, list[str]]] | None = None,
    schema: str = "public",
) -> Database:
    """打开（或创建）SQLite：设 pragma + row_factory，跑迁移，返回 conn+lock 捆绑。

    path 优先级：显式参数 > NYX_DB env > 旧默认 nyx.db（若存在）
    > 新默认 data/nyx.db。
    """
    postgres_url = resolve_postgres_url()
    if postgres_url is not None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            raise ValueError("PostgreSQL schema 名称非法")
        conn = await connect_postgres(postgres_url)
        checkpoint_raw: Any | None = None
        try:
            quoted_schema = '"' + schema.replace('"', '""') + '"'
            await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {quoted_schema}")
            await conn.execute(f"SET search_path TO {quoted_schema}")
            await migrate_postgres(conn, migrations=migrations)
            checkpointer: BaseCheckpointSaver[str] | None = None
            if migrations is None:
                checkpoint_raw = await AsyncConnection.connect(
                    postgres_url,
                    autocommit=True,
                    prepare_threshold=0,
                    row_factory=cast(Any, dict_row),
                )
                await checkpoint_raw.execute(f"SET search_path TO {quoted_schema}")
                checkpointer = AsyncPostgresSaver(conn=checkpoint_raw)
                await checkpointer.setup()
        except BaseException:
            if checkpoint_raw is not None:
                await checkpoint_raw.close()
            await conn.close()
            raise
        return Database(
            conn=conn,
            lock=DatabaseLock(),
            checkpointer=checkpointer,
        )

    resolved = resolve_db_path(path)
    if resolved != ":memory:":
        Path(resolved).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(resolved)
    checkpoint_conn: aiosqlite.Connection | None = None
    checkpointer: BaseCheckpointSaver[str] | None = None
    try:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")  # FK 完整性（SQLite 默认关）
        await conn.execute("PRAGMA journal_mode = WAL")  # 崩溃安全 + 读写不互斥
        await conn.execute(f"PRAGMA busy_timeout = {int(DB_OPERATION_TIMEOUT * 1000)}")
        await migrate(conn, migrations=migrations)
        if migrations is None and resolved == ":memory:":
            checkpointer = InMemorySaver()
        elif migrations is None:
            # The saver commits independently; it must not share a transaction
            # with business stores on the primary connection.
            checkpoint_conn = aiosqlite.connect(resolved, timeout=DB_OPERATION_TIMEOUT)
            # aiosqlite 0.20 can leak its worker when opening is cancelled.
            # Keep acquisition alive until the outer cleanup can close it.
            opening = asyncio.ensure_future(checkpoint_conn)
            cancelled: asyncio.CancelledError | None = None
            while True:
                try:
                    await asyncio.shield(opening)
                    break
                except asyncio.CancelledError as exc:
                    cancelled = exc
            if cancelled is not None:
                raise cancelled
            checkpointer = AsyncSqliteSaver(checkpoint_conn)
            # A crash may occur after the reply commits but before cleanup.
            for table in ("writes", "checkpoints", "tool_execution"):
                key = "correlation_id" if table == "tool_execution" else "thread_id"
                await conn.execute(
                    f"DELETE FROM {table} WHERE {key} IN "
                    "(SELECT correlation_id FROM event_log "
                    "WHERE type IN ('speak', 'ask'))"
                )
            await conn.commit()
    except BaseException:
        if checkpoint_conn is not None:
            await checkpoint_conn.close()
        await conn.close()  # 迁移失败或启动取消：关闭未交付给宿主的连接。
        raise
    return Database(conn=conn, lock=DatabaseLock(), checkpointer=checkpointer)


async def migrate(
    conn: aiosqlite.Connection,
    *,
    migrations: list[tuple[int, list[str]]] | None = None,
) -> None:
    """版本化迁移：schema_version 单行记录当前版本，逐版本套用未应用的迁移。

    每版本一个事务（BEGIN/COMMIT/ROLLBACK）：失败整体回滚、版本不推进，
    重启后干净重试——避免非原子迁移部分建表后重跑撞「表已存在」。
    """
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    cursor = await conn.execute("SELECT version FROM schema_version")
    row = await cursor.fetchone()
    if row is None:
        await conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        await conn.commit()
        current = 0
    else:
        current = int(row[0])
    for version, statements in _MIGRATIONS if migrations is None else migrations:
        if version <= current:
            continue
        if version == 44:
            await conn.create_function(  # pyright: ignore[reportUnknownMemberType]
                "nyx_raw_sha256",
                1,
                _raw_sha256,
                deterministic=True,
            )
        if version == 45:
            # SQLite text functions stop at NUL; match Python checkpoint decoding.
            def task_source_snapshot(checkpoint: str) -> str:
                source: str = json.loads(checkpoint)["source_text"]
                return source[:200_000]

            await conn.create_function(  # pyright: ignore[reportUnknownMemberType]
                "nyx_task_source_snapshot",
                1,
                task_source_snapshot,
                deterministic=True,
            )
        await conn.execute("BEGIN")
        try:
            if version == 53:
                await _validate_archive_turns(conn)
            for stmt in statements:
                if version == 55 and stmt == "DROP TABLE personality":
                    await _migrate_inner_document(conn)
                await conn.execute(stmt)
            await conn.execute("UPDATE schema_version SET version = ?", (version,))
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
