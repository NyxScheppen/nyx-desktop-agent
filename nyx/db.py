import asyncio
import os
from dataclasses import dataclass

import aiosqlite

DEFAULT_DB_PATH = "nyx.db"


@dataclass
class Database:
    """SQLite 连接 + 共享锁的捆绑；connect() 创建并返回，store 共用这一个。

    conn 全项目共享；lock 串行化并发访问（同一连接不能并发 execute/commit）。
    """

    conn: aiosqlite.Connection
    lock: asyncio.Lock

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
            """CREATE TABLE eval_report (
                id TEXT PRIMARY KEY,
                output_id TEXT NOT NULL,
                module TEXT NOT NULL,
                type TEXT NOT NULL,
                scores TEXT NOT NULL,           -- JSON
                token_usage TEXT NOT NULL,      -- JSON {input, output}
                correlation_id TEXT NOT NULL,
                created_at REAL NOT NULL
            )""",
            """CREATE TABLE token_usage (
                id TEXT PRIMARY KEY,
                correlation_id TEXT,            -- str | None，可空
                module TEXT NOT NULL,
                purpose TEXT NOT NULL,         -- reply / scene_memory / desire / ...
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                created_at REAL NOT NULL
            )""",
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
