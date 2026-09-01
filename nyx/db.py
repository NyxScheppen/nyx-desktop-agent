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
            # 陪读：EPUB 书（books）+ 段落（paragraphs），19-reading-content
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
            # 陪读进度：1:1 书（book_id PK），20-reading-progress。
            # user/nyx_position 从 1 起（与 paragraphs."index" 对齐）；
            # reading_speed 10-200；read_count 只由 22 整本读完 ++，默认 0。
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
            # 陪读笔记：用户手写笔记 + Nyx 批注，22-reading-notes。
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
            # 审美维度：23-aesthetic-dimension，四轴 1-10（10=第一极）。
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
            # 记忆「首次创建」锚点：strengthen 会刷新 created_at（decay 锚点），
            # 但 first_created_at 定格在 INSERT、永不更新——审美「新读章数」
            # 用它判「是否新增」，纯重读不污染（23 口径注修正）。
            "ALTER TABLE memory ADD COLUMN first_created_at REAL",
            # 回填旧行：迁移前无 first_created_at，用当前 created_at 近似
            # （历史行的真首次时间已不可考，这是能取到的最好近似）。
            "UPDATE memory SET first_created_at = created_at "
            "WHERE first_created_at IS NULL",
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
