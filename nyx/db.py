import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import monotonic

import aiosqlite

DEFAULT_DB_PATH = "nyx.db"
DB_LOCK_TIMEOUT = 3.0
DB_OPERATION_TIMEOUT = 3.0
DB_FAILURE_THRESHOLD = 5
DB_COOLDOWN_SECONDS = 5.0


@dataclass
class Database:
    """SQLite 连接 + 共享锁的捆绑；connect() 创建并返回，store 共用这一个。

    conn 全项目共享；lock 串行化并发访问（同一连接不能并发 execute/commit）。
    """

    conn: aiosqlite.Connection
    lock: asyncio.Lock
    lock_timeout: float = DB_LOCK_TIMEOUT
    operation_timeout: float = DB_OPERATION_TIMEOUT
    failure_threshold: int = DB_FAILURE_THRESHOLD
    cooldown_seconds: float = DB_COOLDOWN_SECONDS
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
    async def transaction(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """Run one bounded local transaction under the shared DB lock."""
        if self.in_transaction:
            raise RuntimeError("不允许嵌套 Database.transaction()")
        await asyncio.wait_for(self.lock.acquire(), timeout=self.lock_timeout)
        marker = self._transaction_state.set(True)
        try:
            await asyncio.wait_for(
                self.conn.execute("BEGIN"), timeout=self.operation_timeout
            )
            try:
                yield self.conn
                await asyncio.wait_for(
                    self.conn.commit(), timeout=self.operation_timeout
                )
            except BaseException:
                await asyncio.wait_for(
                    self.conn.rollback(), timeout=self.operation_timeout
                )
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
            # 陪读：EPUB 书（books）+ 段落（paragraphs），12-reading-system
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
            # 陪读进度：1:1 书（book_id PK），12-reading-system。
            # user/nyx_position 从 1 起（与 paragraphs."index" 对齐）；
            # reading_speed 10-200；read_count 只由 12-reading-system
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
            # 陪读笔记：用户手写笔记 + Nyx 批注，12-reading-system。
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
            # 审美维度：08-inner-life，四轴 1-10（10=第一极）。
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
            # eval 可观测（10-eval）：LLM 调用 + token 记账。
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
]


async def connect(path: str | None = None) -> Database:
    """打开（或创建）SQLite：设 pragma + row_factory，跑迁移，返回 conn+lock 捆绑。

    path 优先级：显式参数 > NYX_DB env > 默认 "nyx.db"（同 NYX_CONFIG 约定）。
    """
    resolved = path or os.environ.get("NYX_DB") or DEFAULT_DB_PATH
    conn = await aiosqlite.connect(resolved)
    try:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")   # FK 完整性（SQLite 默认关）
        await conn.execute("PRAGMA journal_mode = WAL")  # 崩溃安全 + 读写不互斥
        await conn.execute(
            f"PRAGMA busy_timeout = {int(DB_OPERATION_TIMEOUT * 1000)}"
        )
        await migrate(conn)
    except Exception:
        await conn.close()   # 迁移失败：关连接避免泄漏，原异常上抛
        raise
    return Database(conn=conn, lock=asyncio.Lock())


async def migrate(conn: aiosqlite.Connection) -> None:
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
    for version, statements in _MIGRATIONS:
        if version <= current:
            continue
        await conn.execute("BEGIN")
        try:
            for stmt in statements:
                await conn.execute(stmt)
            await conn.execute("UPDATE schema_version SET version = ?", (version,))
            await conn.commit()
        except aiosqlite.Error:
            await conn.rollback()
            raise
