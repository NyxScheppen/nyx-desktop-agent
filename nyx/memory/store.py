import hashlib
import json
import math
import re
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

import aiosqlite

from nyx.db import Database
from nyx.enums import MemoryEdgeKind, MemoryKind, MemoryType
from nyx.memory.conversation_chunks import (
    ConversationChunk,
    split_conversation,
    term_frequencies,
)
from nyx.types import ConversationSegment, Event, Memory, MemoryEdge, ReadingEvidence

_MEMORY_COLS = (
    "id, created_at, content, kind, topics, summary, freshness, "
    "type, recall_count, aspect, embedding"
)
# INSERT 用：store 派生列不进入公开 Memory。
_MEMORY_INSERT_COLS = (
    _MEMORY_COLS + ", content_hash, first_created_at, freshness_updated_at"
)
_SOURCE_TOPIC_PREFIXES = ("book:", "material:", "web:", "local:")


@dataclass
class KeywordSearchHit:
    memory_id: str
    summary_tokens: list[str]
    content_tokens: list[str]


@dataclass(frozen=True)
class ConversationIndexJob:
    segment: ConversationSegment
    revision: int
    attempts: int


@dataclass(frozen=True)
class ConversationRetrievalHit:
    chunk: ConversationChunk
    bm25: float | None
    cosine: float | None
    fusion: float
    score: float
    selected: bool
    ranges: list[tuple[int, int]]


def _conversation_chunk_from_row(row: aiosqlite.Row) -> ConversationChunk:
    return ConversationChunk(
        str(row["id"]), str(row["segment_id"]),
        int(row["start_offset"]), int(row["end_offset"]), str(row["content"]),
        list(json.loads(row["embedding"])) if row["embedding"] else None,
        str(row["embedding_version"]),
    )


def _segment_from_row(row: aiosqlite.Row) -> ConversationSegment:
    embedding = row["embedding"]
    return ConversationSegment(
        id=row["id"],
        previous_id=row["previous_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        raw_event_ids=list(json.loads(row["raw_event_ids"])),
        raw_content=row["raw_content"],
        summary=row["summary"],
        topics=list(json.loads(row["topics"])),
        embedding=list(json.loads(embedding)) if embedding else None,
        importance=row["importance"],
        long_term=bool(row["long_term"]),
        evaluated_at=(
            float(row["evaluated_at"])
            if row["evaluated_at"] is not None
            else None
        ),
        evaluation_reason=str(row["evaluation_reason"] or ""),
        evidence_event_ids=list(json.loads(row["evidence_event_ids"] or "[]")),
        freshness=float(row["freshness"]),
        freshness_updated_at=float(row["freshness_updated_at"]),
        recall_count=int(row["recall_count"]),
        active=bool(row["active"]),
        facts_extracted_at=(
            float(row["facts_extracted_at"])
            if row["facts_extracted_at"] is not None else None
        ),
        fact_scope=str(row["fact_scope"]),
    )


def hash_content(content: str) -> str:
    """Canonical content → SHA-256 hex exact-dedup key."""
    canonical = re.sub(r"\s+", " ", content.strip())
    canonical = canonical.replace("，", ",").replace("。", ".")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _chunk_from_row(row: aiosqlite.Row) -> ReadingEvidence:
    return ReadingEvidence(
        id=row["id"], source_topic=row["source_topic"],
        source_name=row["source_name"], content=row["content"],
        created_at=row["created_at"], location=row["location"],
        embedding=json.loads(row["embedding"]) if row["embedding"] else None,
    )


class MemoryStore:
    """memory / memory_edge 两表的 SQLite 存取 + 行↔dataclass 序列化。

    db 由组合根注入（同所有 store 共享一个 conn+lock）。每个方法一个
    `async with db.lock` 的 SQL 块，锁作用域 = 单方法内、不跨方法嵌套
    （asyncio.Lock 不可重入，嵌套死锁）。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def db(self) -> Database:
        """Return the shared database for local transaction orchestration."""
        return self._db

    @asynccontextmanager
    async def _transaction(self) -> AsyncGenerator[None, None]:
        """Derived index writes may join a caller without publishing partial work."""
        if not self._db.in_transaction:
            async with self._db.transaction():
                yield
            return
        savepoint = f"conversation_{uuid4().hex}"
        await self._db.conn.execute(f"SAVEPOINT {savepoint}")
        try:
            yield
        except BaseException:
            await self._db.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            raise
        finally:
            await self._db.conn.execute(f"RELEASE SAVEPOINT {savepoint}")

    async def bump_lesson_signal(self, topic: str, kind: str) -> int:
        """Count the current correction without creating a memory yet."""
        async with self._operation() as should_commit:
            cursor = await self._db.conn.execute(
                "INSERT INTO lesson_signal(topic, kind, count, last_at) "
                "VALUES (?, ?, 1, ?) ON CONFLICT(topic) DO UPDATE SET "
                "count = count + 1, last_at = excluded.last_at RETURNING count",
                (topic, kind, time.time()),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if should_commit:
                await self._db.conn.commit()
        assert row is not None
        return int(row["count"])

    async def clear_lesson_signal(self, topic: str) -> None:
        """Reset only after the promoted lesson and reflection are accepted."""
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "DELETE FROM lesson_signal WHERE topic = ?", (topic,)
            )
            if should_commit:
                await self._db.conn.commit()

    async def find_annotation_feedback(
        self, reply_event_id: str, comment: str
    ) -> str | None:
        """Find the canonical receipt using the exact original feedback key."""
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT event_id FROM annotation_feedback "
                "WHERE reply_event_id = ? AND comment = ?",
                (reply_event_id, comment),
            )
            row = await cursor.fetchone()
        return str(row["event_id"]) if row is not None else None

    async def record_annotation_feedback(self, event: Event) -> bool:
        """Join the caller's transaction so row, event and effect commit together."""
        if not self._db.in_transaction:
            raise RuntimeError("批注记录必须加入外层事务")
        cursor = await self._db.conn.execute(
            "INSERT OR IGNORE INTO annotation_feedback "
            "(event_id, reply_event_id, quote, comment, user_event_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event.id, event.content["reply_event_id"], event.content["quote"],
             event.content["comment"], event.content["user_event_id"], event.timestamp),
        )
        return cursor.rowcount == 1

    async def latest_conversation_segment(self) -> ConversationSegment | None:
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT s.* FROM conversation_segment s WHERE NOT EXISTS "
                "(SELECT 1 FROM conversation_segment next WHERE next.previous_id=s.id) "
                "ORDER BY s.ended_at DESC, s.id DESC LIMIT 1"
            )
            row = await cursor.fetchone()
        return _segment_from_row(row) if row is not None else None

    async def archived_conversation_turn(
        self, turn_id: str
    ) -> ConversationSegment | None:
        """Find the permanent archive identity, including inactive segments."""
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT s.* FROM conversation_turn_archive a "
                "JOIN conversation_segment s ON s.id=a.segment_id WHERE a.turn_id=?",
                (turn_id,),
            )
            row = await cursor.fetchone()
        return _segment_from_row(row) if row is not None else None

    async def get_conversation_segment(
        self, segment_id: str
    ) -> ConversationSegment | None:
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT * FROM conversation_segment WHERE id=?", (segment_id,)
            )
            row = await cursor.fetchone()
        return _segment_from_row(row) if row is not None else None

    async def list_conversation_segments(
        self, limit: int | None = 200, *, active_only: bool = False
    ) -> list[ConversationSegment]:
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT * FROM conversation_segment "
                + ("WHERE active = 1 " if active_only else "")
                + "ORDER BY ended_at DESC, id DESC "
                + ("LIMIT ?" if limit is not None else ""),
                (max(1, min(limit, 200)),) if limit is not None else (),
            )
            rows = await cursor.fetchall()
        return [_segment_from_row(row) for row in rows]

    async def browse_conversations(
        self,
        query: str = "",
        *,
        limit: int = 100,
        offset: int = 0,
        long_term: bool | None = None,
    ) -> list[ConversationSegment]:
        """按原文/摘要浏览，包含已退出召回的历史段，不使用向量召回门槛。"""
        clauses = ["1=1"]
        params: list[str | int] = []
        if query.strip():
            pattern = (
                "%"
                + query.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_")
                + "%"
            )
            clauses.append(
                "(raw_content LIKE ? ESCAPE '!' OR summary LIKE ? ESCAPE '!')"
            )
            params.extend([pattern, pattern])
        if long_term is not None:
            clauses.append("long_term = ?")
            params.append(int(long_term))
        params.extend([max(1, min(limit, 200)), max(0, offset)])
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT * FROM conversation_segment WHERE "
                + " AND ".join(clauses)
                + " ORDER BY ended_at DESC, id DESC LIMIT ? OFFSET ?",
                tuple(params),
            )
            rows = await cursor.fetchall()
        return [_segment_from_row(row) for row in rows]

    async def list_pending_conversation_segments(
        self, before: float, limit: int = 20, *, include_facts: bool = True
    ) -> list[ConversationSegment]:
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT * FROM conversation_segment WHERE ended_at<=? "
                + (
                    "AND (evaluated_at IS NULL OR facts_extracted_at IS NULL) "
                    if include_facts else "AND evaluated_at IS NULL "
                )
                + "ORDER BY ended_at ASC, id ASC LIMIT ?",
                (before, limit),
            )
            rows = await cursor.fetchall()
        return [_segment_from_row(row) for row in rows]

    async def add_conversation_segment(
        self, segment: ConversationSegment, *,
        chunks: list[ConversationChunk] | None = None,
    ) -> None:
        if not self._db.in_transaction:
            async with self._db.transaction():
                await self.add_conversation_segment(segment, chunks=chunks)
            return
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "INSERT INTO conversation_segment "
                "(id, previous_id, started_at, ended_at, raw_event_ids, raw_content, "
                "summary, topics, embedding, importance, long_term, evaluated_at, "
                "evaluation_reason, evidence_event_ids, freshness, "
                "freshness_updated_at, recall_count, active, facts_extracted_at, "
                "fact_scope) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    segment.id,
                    segment.previous_id,
                    segment.started_at,
                    segment.ended_at,
                    json.dumps(segment.raw_event_ids, ensure_ascii=False),
                    segment.raw_content,
                    segment.summary,
                    json.dumps(segment.topics, ensure_ascii=False),
                    (
                        json.dumps(segment.embedding)
                        if segment.embedding is not None
                        else None
                    ),
                    segment.importance,
                    int(segment.long_term),
                    segment.evaluated_at,
                    segment.evaluation_reason,
                    json.dumps(segment.evidence_event_ids, ensure_ascii=False),
                    segment.freshness,
                    segment.freshness_updated_at or segment.ended_at,
                    segment.recall_count,
                    int(segment.active),
                    segment.facts_extracted_at,
                    segment.fact_scope,
                ),
            )
            await self._sync_conversation_chunks(segment, chunks)
            if should_commit:
                await self._db.conn.commit()

    async def update_conversation_segment(
        self, segment: ConversationSegment, *,
        chunks: list[ConversationChunk] | None = None,
    ) -> None:
        if not self._db.in_transaction:
            async with self._db.transaction():
                await self.update_conversation_segment(segment, chunks=chunks)
            return
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "UPDATE conversation_segment SET started_at=?, ended_at=?, "
                "raw_event_ids=?, "
                "raw_content=?, summary=?, topics=?, embedding=?, "
                "importance=?, long_term=?, evaluated_at=?, evaluation_reason=?, "
                "evidence_event_ids=?, freshness=?, freshness_updated_at=?, "
                "recall_count=?, active=?, facts_extracted_at=?, fact_scope=? "
                "WHERE id=?",
                (
                    segment.started_at,
                    segment.ended_at,
                    json.dumps(segment.raw_event_ids, ensure_ascii=False),
                    segment.raw_content,
                    segment.summary,
                    json.dumps(segment.topics, ensure_ascii=False),
                    (
                        json.dumps(segment.embedding)
                        if segment.embedding is not None
                        else None
                    ),
                    segment.importance,
                    int(segment.long_term),
                    segment.evaluated_at,
                    segment.evaluation_reason,
                    json.dumps(segment.evidence_event_ids, ensure_ascii=False),
                    segment.freshness,
                    segment.freshness_updated_at,
                    segment.recall_count,
                    int(segment.active),
                    segment.facts_extracted_at,
                    segment.fact_scope,
                    segment.id,
                ),
            )
            await self._sync_conversation_chunks(segment, chunks)
            if should_commit:
                await self._db.conn.commit()

    async def _sync_conversation_chunks(
        self, segment: ConversationSegment, chunks: list[ConversationChunk] | None,
    ) -> None:
        """Archive and derived postings always share the caller's transaction."""
        proposed = chunks if chunks is not None else split_conversation(
            segment.id, segment.raw_content,
        )
        cursor = await self._db.conn.execute(
            "SELECT * FROM conversation_chunk WHERE segment_id=?", (segment.id,),
        )
        current = {chunk.id: chunk for chunk in (
            _conversation_chunk_from_row(row) for row in await cursor.fetchall()
        )}
        desired = {chunk.id for chunk in proposed}
        for identity in current.keys() - desired:
            await self._db.conn.execute(
                "DELETE FROM conversation_chunk WHERE id=?", (identity,),
            )
        for chunk in proposed:
            old = current.get(chunk.id)
            if old is not None:
                if chunk.embedding is not None and (
                    chunk.embedding != old.embedding
                    or chunk.embedding_version != old.embedding_version
                ):
                    await self._db.conn.execute(
                        "UPDATE conversation_chunk SET embedding=?, "
                        "embedding_version=? WHERE id=?",
                        (json.dumps(chunk.embedding), chunk.embedding_version,
                         chunk.id),
                    )
                continue
            frequencies = term_frequencies(chunk.content)
            await self._db.conn.execute(
                "INSERT INTO conversation_chunk (id, segment_id, start_offset, "
                "end_offset, content, embedding, token_count, embedding_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (chunk.id, chunk.segment_id, chunk.start, chunk.end, chunk.content,
                 json.dumps(chunk.embedding) if chunk.embedding is not None else None,
                 sum(frequencies.values()), chunk.embedding_version),
            )
            if frequencies:
                await self._db.conn.executemany(
                    "INSERT INTO conversation_chunk_term "
                    "(term, chunk_id, frequency) VALUES (?, ?, ?)",
                    [(term, chunk.id, count) for term, count in frequencies.items()],
                )

    async def list_conversation_chunks(
        self, segment_id: str,
    ) -> list[ConversationChunk]:
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT * FROM conversation_chunk WHERE segment_id=? "
                "ORDER BY start_offset", (segment_id,),
            )
            return [
                _conversation_chunk_from_row(row) for row in await cursor.fetchall()
            ]

    async def conversation_chunk_changes(
        self, after: int | None,
    ) -> tuple[int, list[ConversationChunk], list[str]]:
        """Read committed changes, or an isolated transaction-local snapshot."""
        async with self._operation() as own:
            if own:
                await self._db.conn.execute("BEGIN")
            cursor = await self._db.conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS seq, MIN(seq) AS oldest "
                "FROM conversation_chunk_change"
            )
            row = await cursor.fetchone()
            version = int(row["seq"]) if row is not None else 0
            reset = after is None or (
                row is not None and row["oldest"] is not None
                and after < int(row["oldest"]) - 1
            )
            if reset:
                cursor = await self._db.conn.execute("SELECT * FROM conversation_chunk")
            else:
                cursor = await self._db.conn.execute(
                    "SELECT delta.chunk_id AS changed_id, c.* FROM "
                    "(SELECT DISTINCT chunk_id FROM conversation_chunk_change "
                    "WHERE seq>? AND seq<=?) delta LEFT JOIN conversation_chunk c "
                    "ON c.id=delta.chunk_id", (after, version),
                )
            rows = await cursor.fetchall()
            if own:
                await self._db.conn.commit()
        return (
            version,
            [_conversation_chunk_from_row(row)
             for row in rows if row["id"] is not None],
            (["*"] if reset and after is not None else
             [str(row["changed_id"]) for row in rows if row["id"] is None]),
        )

    async def pending_conversation_indexes(
        self, version: str, now: float, limit: int = 4,
    ) -> list[ConversationIndexJob]:
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT s.*, j.revision, j.attempts FROM conversation_index_job j "
                "JOIN conversation_segment s ON s.id=j.segment_id WHERE "
                "(j.revision<>j.completed_revision OR j.index_version<>?) "
                "AND (j.retry_at<=? OR j.index_version<>?) "
                "ORDER BY j.retry_at, s.ended_at, s.id LIMIT ?",
                (version, now, version, limit),
            )
            return [ConversationIndexJob(_segment_from_row(row), int(row["revision"]),
                                         int(row["attempts"]))
                    for row in await cursor.fetchall()]

    async def prepare_conversation_index(
        self, job: ConversationIndexJob, version: str,
    ) -> bool:
        async with self._transaction():
            if not await self._conversation_job_current(job):
                return False
            await self._sync_conversation_chunks(job.segment, None)
            await self._db.conn.execute(
                "UPDATE conversation_index_job SET completed_revision=CASE "
                "WHEN index_version<>? THEN 0 ELSE completed_revision END, "
                "index_version=? "
                "WHERE segment_id=? AND revision=?",
                (version, version, job.segment.id, job.revision),
            )
            return True

    async def _conversation_job_current(
        self, job: ConversationIndexJob, version: str | None = None,
    ) -> bool:
        cursor = await self._db.conn.execute(
            "SELECT j.revision, j.index_version, s.raw_content "
            "FROM conversation_index_job j "
            "JOIN conversation_segment s ON s.id=j.segment_id WHERE j.segment_id=?",
            (job.segment.id,),
        )
        row = await cursor.fetchone()
        return (row is not None and int(row["revision"]) == job.revision
                and row["raw_content"] == job.segment.raw_content
                and (version is None or row["index_version"] == version))

    async def publish_conversation_vector(
        self, job: ConversationIndexJob, chunk: ConversationChunk, version: str,
    ) -> bool:
        async with self._transaction():
            if not await self._conversation_job_current(job, version):
                return False
            await self._db.conn.execute(
                "UPDATE conversation_chunk SET embedding=?, embedding_version=? "
                "WHERE id=? AND segment_id=? AND content=?",
                (json.dumps(chunk.embedding), chunk.embedding_version,
                 chunk.id, job.segment.id, chunk.content),
            )
            return True

    async def finish_conversation_index(
        self, job: ConversationIndexJob, version: str, *,
        complete: bool, error: str = "",
    ) -> None:
        async with self._transaction():
            if not await self._conversation_job_current(
                job, None if error else version
            ):
                return
            attempts = job.attempts + 1 if error else 0
            await self._db.conn.execute(
                "UPDATE conversation_index_job SET completed_revision=?, attempts=?, "
                "retry_at=?, last_error=?, index_version=? "
                "WHERE segment_id=? AND revision=?",
                (job.revision if complete else 0, attempts,
                 time.time() + min(3600, 15 * 2 ** min(attempts, 8)) if error else 0,
                 error[:120], version, job.segment.id, job.revision),
            )

    async def rebuild_conversation_indexes(self) -> None:
        """Invalidate in-flight work; source text and lexical evidence stay usable."""
        async with self._transaction():
            await self._db.conn.execute(
                "UPDATE conversation_index_job SET revision=revision+1, "
                "completed_revision=0, attempts=0, retry_at=0, last_error=''"
            )
            await self._db.conn.execute(
                "UPDATE conversation_chunk SET embedding=NULL, embedding_version=''"
            )

    async def conversation_index_status(self, version: str) -> dict[str, object]:
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN "
                "revision=completed_revision AND index_version=? THEN 1 ELSE 0 END),0) "
                "AS ready, COALESCE(SUM(CASE WHEN attempts>0 THEN 1 ELSE 0 END),0) "
                "AS failed FROM conversation_index_job", (version,),
            )
            row = await cursor.fetchone()
            assert row is not None
            cursor = await self._db.conn.execute(
                "SELECT segment_id, revision, completed_revision, index_version, "
                "attempts, retry_at, last_error FROM conversation_index_job "
                "WHERE revision<>completed_revision OR index_version<>? "
                "ORDER BY retry_at, segment_id LIMIT 20", (version,),
            )
            return {"index_version": version, "total": int(row["total"]),
                    "ready": int(row["ready"]),
                    "pending": int(row["total"]) - int(row["ready"]),
                    "failed": int(row["failed"]),
                    "jobs": [dict(item) for item in await cursor.fetchall()]}

    async def delete_conversation_segment(self, segment_id: str) -> bool:
        async with self._transaction():
            current = await self.get_conversation_segment(segment_id)
            if current is None:
                return False
            await self._db.conn.execute(
                "DELETE FROM conversation_segment WHERE id=?", (segment_id,),
            )
            return True

    async def revise_conversation_segment(
        self, segment_id: str, raw_content: str, expected_hash: str,
    ) -> ConversationSegment | None:
        """Compare the archived source before applying a user correction."""
        async with self._transaction():
            current = await self.get_conversation_segment(segment_id)
            if current is None:
                return None
            current_hash = hashlib.sha256(current.raw_content.encode()).hexdigest()
            if current_hash != expected_hash:
                raise ValueError("对话原文已变化，请刷新后重试")
            if raw_content == current.raw_content:
                return current
            # An explicit archive correction retracts facts even when the edited
            # text happens to have the original as a prefix (unlike a live append).
            await self._db.conn.execute(
                "DELETE FROM memory_fact WHERE source_segment_id=?", (segment_id,),
            )
            await self._db.conn.execute(
                "DELETE FROM conversation_retrieval_trace WHERE id IN "
                "(SELECT trace_id FROM conversation_retrieval_hit WHERE segment_id=?)",
                (segment_id,),
            )
            current.raw_content = raw_content
            current.summary = raw_content[-2000:]
            current.topics = []
            current.embedding = None
            current.evaluated_at = None
            current.facts_extracted_at = None
            current.evaluation_reason = ""
            current.evidence_event_ids = []
            current.long_term = False
            await self.clear_segment_evidence(segment_id)
            await self.update_conversation_segment(current)
            return current

    async def save_conversation_trace(
        self, trace_id: str, query: str, version: str, include_archived: bool,
        elapsed_ms: float, hits: list[ConversationRetrievalHit],
    ) -> None:
        """Store bounded scores/provenance, never duplicate private query or text."""
        async with self._transaction():
            await self._db.conn.execute(
                "INSERT INTO conversation_retrieval_trace VALUES (?, ?, ?, ?, ?, ?)",
                (trace_id, time.time(), hash_content(query), version,
                 int(include_archived), elapsed_ms),
            )
            for hit in hits[:160]:
                await self._db.conn.execute(
                    "INSERT INTO conversation_retrieval_hit "
                    "(trace_id, chunk_id, segment_id, bm25, cosine, fusion, score, "
                    "selected, source_ranges) "
                    "SELECT ?, id, segment_id, ?, ?, ?, ?, ?, ? "
                    "FROM conversation_chunk WHERE id=? AND content=?",
                    (trace_id, hit.bm25, hit.cosine, hit.fusion, hit.score,
                     int(hit.selected), json.dumps(hit.ranges),
                     hit.chunk.id, hit.chunk.content),
                )
            await self._prune_conversation_traces()

    async def _prune_conversation_traces(self) -> None:
        await self._db.conn.execute(
            "DELETE FROM conversation_retrieval_trace WHERE created_at<? OR id IN "
            "(SELECT id FROM conversation_retrieval_trace ORDER BY created_at DESC, "
            "id DESC LIMIT -1 OFFSET 2000)", (time.time() - 7 * 86400,),
        )

    async def maintain_conversation_index_logs(self) -> None:
        async with self._transaction():
            await self._prune_conversation_traces()
            await self._db.conn.execute(
                "DELETE FROM conversation_chunk_change WHERE seq < "
                "(SELECT COALESCE(MAX(seq), 0)-10000 FROM conversation_chunk_change)"
            )

    async def mark_conversation_trace_injected(
        self, trace_id: str, segment_id: str, event_id: str,
    ) -> None:
        async with self._transaction():
            await self._db.conn.execute(
                "UPDATE conversation_retrieval_hit SET injected_event_id=? "
                "WHERE trace_id=? AND segment_id=? AND selected=1",
                (event_id, trace_id, segment_id),
            )

    async def conversation_traces(self, limit: int = 20) -> list[dict[str, object]]:
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT * FROM conversation_retrieval_trace "
                "ORDER BY created_at DESC, id DESC LIMIT ?", (max(1, min(100, limit)),),
            )
            result: list[dict[str, object]] = []
            for row in await cursor.fetchall():
                cursor = await self._db.conn.execute(
                    "SELECT * FROM conversation_retrieval_hit WHERE trace_id=? "
                    "ORDER BY score DESC, chunk_id", (row["id"],),
                )
                result.append({**dict(row), "hits": [
                    dict(hit) for hit in await cursor.fetchall()
                ]})
            return result

    async def eligible_conversation_chunks(self, include_archived: bool) -> set[str]:
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT c.id FROM conversation_chunk c JOIN conversation_segment s "
                "ON s.id=c.segment_id"
                + ("" if include_archived else " WHERE s.active=1")
            )
            return {str(row["id"]) for row in await cursor.fetchall()}

    async def search_conversation_chunks(
        self, terms: list[str], *, include_archived: bool, limit: int = 80,
    ) -> list[tuple[str, float, int]]:
        """BM25 over indexed postings; materialize only bounded lexical candidates."""
        if not terms:
            return []
        placeholders = ",".join("?" for _ in terms)
        eligible = ("SELECT c.id, c.token_count FROM conversation_chunk c "
                    "JOIN conversation_segment s ON s.id=c.segment_id"
                    + ("" if include_archived else " WHERE s.active=1"))
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"WITH eligible AS ({eligible}) "
                "SELECT COUNT(*) AS n, COALESCE(AVG(token_count), 1.0) AS avg_len "
                "FROM eligible"
            )
            stats = await cursor.fetchone()
            if stats is None or not stats["n"]:
                return []
            count, avg_len = int(stats["n"]), max(1.0, float(stats["avg_len"]))
            cursor = await self._db.conn.execute(
                f"WITH eligible AS ({eligible}) "
                "SELECT t.term, COUNT(*) AS n FROM conversation_chunk_term t "
                "JOIN eligible e ON e.id=t.chunk_id "
                f"WHERE t.term IN ({placeholders}) GROUP BY t.term", terms,
            )
            weights = {str(row["term"]): math.log1p(
                (count - int(row["n"]) + 0.5) / (int(row["n"]) + 0.5)
            ) for row in await cursor.fetchall()}
            if not weights:
                return []
            cases = " ".join("WHEN ? THEN ?" for _ in weights)
            params: list[str | float | int] = []
            for term, weight in weights.items():
                params.extend((term, weight))
            params.extend((avg_len, *terms, limit))
            cursor = await self._db.conn.execute(
                f"WITH eligible AS ({eligible}) "
                "SELECT t.chunk_id, COUNT(*) AS matched, SUM("
                f"(CASE t.term {cases} ELSE 0.0 END) * t.frequency * 2.2 / "
                "(t.frequency + 1.2 * (0.25 + 0.75 * e.token_count / ?))) AS score "
                "FROM conversation_chunk_term t JOIN eligible e ON e.id=t.chunk_id "
                f"WHERE t.term IN ({placeholders}) GROUP BY t.chunk_id "
                "ORDER BY score DESC, t.chunk_id LIMIT ?", params,
            )
            return [(str(row["chunk_id"]), float(row["score"]), int(row["matched"]))
                    for row in await cursor.fetchall()]

    async def unindexed_conversation_segments(
        self, include_archived: bool,
    ) -> list[ConversationSegment]:
        """Pre-index archives remain available until the separate backfill phase."""
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT s.* FROM conversation_segment s WHERE "
                + ("" if include_archived else "s.active=1 AND ")
                + "(NOT EXISTS (SELECT 1 FROM conversation_chunk c "
                "WHERE c.segment_id=s.id) OR (s.embedding IS NOT NULL AND "
                "NOT EXISTS (SELECT 1 FROM conversation_chunk c "
                "WHERE c.segment_id=s.id AND c.embedding IS NOT NULL)))"
            )
            return [_segment_from_row(row) for row in await cursor.fetchall()]

    async def add_segment_evidence(
        self, segment_id: str, evidence_segment_id: str
    ) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "INSERT OR IGNORE INTO conversation_segment_evidence "
                "(segment_id, evidence_segment_id, relation, created_at) "
                "VALUES (?, ?, 'supports', ?)",
                (segment_id, evidence_segment_id, time.time()),
            )
            if should_commit:
                await self._db.conn.commit()

    async def clear_segment_evidence(self, segment_id: str) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "DELETE FROM conversation_segment_evidence WHERE segment_id=?",
                (segment_id,),
            )
            if should_commit:
                await self._db.conn.commit()

    async def record_segment_recall(
        self, segment_id: str, user_event_id: str, promote_threshold: int
    ) -> bool:
        async with self._operation() as should_commit:
            marker = await self._db.conn.execute(
                "INSERT OR IGNORE INTO conversation_segment_recall_use "
                "(user_event_id, segment_id) SELECT ?, id FROM conversation_segment "
                "WHERE id=?",
                (user_event_id, segment_id),
            )
            if marker.rowcount != 1:
                if should_commit:
                    await self._db.conn.commit()
                return False
            await self._db.conn.execute(
                "UPDATE conversation_segment SET recall_count=recall_count+1 "
                "WHERE id=?", (segment_id,)
            )
            cursor = await self._db.conn.execute(
                "UPDATE conversation_segment SET long_term=1, active=1 WHERE id=? "
                "AND long_term=0 AND recall_count>=?",
                (segment_id, promote_threshold),
            )
            if should_commit:
                await self._db.conn.commit()
            return cursor.rowcount == 1

    async def settle_segment_freshness(self, now: float, rate: float) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "UPDATE conversation_segment SET freshness=MAX(0.0, freshness-?*"
                "((?-freshness_updated_at)/86400.0)), freshness_updated_at=? "
                "WHERE active=1 AND long_term=0 AND freshness_updated_at<?",
                (rate, now, now, now),
            )
            if should_commit:
                await self._db.conn.commit()

    async def evict_short_segments(self, capacity: int) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "UPDATE conversation_segment SET active=0 WHERE id IN ("
                "SELECT id FROM conversation_segment WHERE active=1 AND long_term=0 "
                "ORDER BY freshness DESC, ended_at DESC, id ASC "
                "LIMIT -1 OFFSET ?) ",
                (capacity,),
            )
            if should_commit:
                await self._db.conn.commit()

    @asynccontextmanager
    async def _operation(self) -> AsyncGenerator[bool, None]:
        """Yield whether this method owns the commit for its SQL block."""
        if self._db.in_transaction:
            yield False
            return
        async with self._db.lock:
            try:
                yield True
            except BaseException:
                await self._db.conn.rollback()
                raise

    async def add(self, memory: Memory) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                f"INSERT INTO memory ({_MEMORY_INSERT_COLS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*_memory_row(memory), hash_content(memory.content),
                 memory.created_at, memory.created_at),
            )
            if should_commit:
                await self._db.conn.commit()

    async def get(self, memory_id: str) -> Memory | None:
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_MEMORY_COLS} FROM memory WHERE id = ?", (memory_id,),
            )
            row = await cursor.fetchone()
        return _row_to_memory(row) if row is not None else None

    async def find_by_content(
        self,
        content: str,
        kind: MemoryKind,
        required_topic: str | None = None,
    ) -> Memory | None:
        """在同一 kind 内按 content 精确哈希查重。"""
        if required_topic is not None:
            topic_clause = (
                " AND EXISTS (SELECT 1 FROM json_each(memory.topics) "
                "WHERE json_each.value = ?)"
            )
        else:
            source_checks = " OR ".join(
                "json_each.value LIKE ?" for _ in _SOURCE_TOPIC_PREFIXES
            )
            topic_clause = (
                " AND NOT EXISTS (SELECT 1 FROM json_each(memory.topics) WHERE "
                f"{source_checks})"
            )
        params: tuple[str, ...] = (kind.value, hash_content(content))
        if required_topic is not None:
            params = (*params, required_topic)
        else:
            params = (*params, *(f"{prefix}%" for prefix in _SOURCE_TOPIC_PREFIXES))
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_MEMORY_COLS} FROM memory "
                f"WHERE kind = ? AND content_hash = ?{topic_clause}",
                params,
            )
            row = await cursor.fetchone()
        return _row_to_memory(row) if row is not None else None

    async def list_memories(
        self,
        kind: MemoryKind | None = None,
        type: MemoryType | None = None,
        limit: int | None = None,
        *, include_embedding: bool = True,
    ) -> list[Memory]:
        clauses: list[str] = []
        params: list[str] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        if type is not None:
            clauses.append("type = ?")
            params.append(type.value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        columns = _MEMORY_COLS if include_embedding else _MEMORY_COLS.replace(
            ", embedding", ", NULL AS embedding"
        )
        sql = (
            f"SELECT {columns} FROM memory{where} "
            "ORDER BY freshness DESC, created_at DESC"
        )
        if limit is not None:
            sql += f" LIMIT {limit}"
        async with self._operation():
            cursor = await self._db.conn.execute(sql, params)
            rows = await cursor.fetchall()
        return [_row_to_memory(r) for r in rows]


    async def delete_many(self, ids: list[str]) -> None:
        """批量删除：循环删 edge/row，单锁单 commit（淘汰溢出，避免 N 次 commit）。"""
        async with self._operation() as should_commit:
            for memory_id in ids:
                await self._db.conn.execute(
                    "DELETE FROM memory_edge WHERE from_id = ? OR to_id = ?",
                    (memory_id, memory_id),
                )
                await self._db.conn.execute(
                    "DELETE FROM memory WHERE id = ?", (memory_id,)
                )
            if should_commit:
                await self._db.conn.commit()

    async def record_recall(
        self, memory_id: str, user_event_id: str, promote_threshold: int
    ) -> bool:
        """原子记录事件级召回；首次使用才计数并按阈值升级。

        返回是否升级（供 facade 发 memory_promoted）。阈值由 facade 传入——
        策略仍在 facade，store 只提供「幂等 marker + 加一 + 条件升型」原语。
        """
        async with self._operation() as should_commit:
            marker = await self._db.conn.execute(
                "INSERT OR IGNORE INTO memory_recall_use "
                "(user_event_id, memory_id) "
                "SELECT ?, id FROM memory WHERE id = ?",
                (user_event_id, memory_id),
            )
            if marker.rowcount != 1:
                if should_commit:
                    await self._db.conn.commit()
                return False
            await self._db.conn.execute(
                "UPDATE memory SET recall_count = recall_count + 1 WHERE id = ?",
                (memory_id,),
            )
            cursor = await self._db.conn.execute(
                "UPDATE memory SET type = ? WHERE id = ? AND type = ? "
                "AND recall_count >= ?",
                (
                    MemoryType.LONG_TERM.value,
                    memory_id,
                    MemoryType.SHORT_TERM.value,
                    promote_threshold,
                ),
            )
            promoted = cursor.rowcount == 1
            if should_commit:
                await self._db.conn.commit()
        return promoted

    async def count_new(
        self,
        kind: MemoryKind | None,
        since: float,
        *,
        include_lesson_ids: set[str] | None = None,
    ) -> int:
        """计数「首次创建晚于 since 的 kind 记忆」，不物化整行/embedding。

        用 first_created_at（INSERT 时定格、strengthen 不刷新）。
        kind=None 不含反思记录；经验仅计入调用方确认的用户纠正记录。
        """
        async with self._operation():
            if kind is None:
                cursor = await self._db.conn.execute(
                    "SELECT COUNT(*) AS count FROM memory WHERE first_created_at > ? "
                    "AND kind NOT IN ('reflection', 'lesson')",
                    (since,),
                )
            else:
                cursor = await self._db.conn.execute(
                    "SELECT COUNT(*) AS count FROM memory "
                    "WHERE kind = ? AND first_created_at > ?",
                    (kind.value, since),
                )
            row = await cursor.fetchone()
            count = int(row["count"]) if row is not None else 0
            if kind is None and include_lesson_ids:
                cursor = await self._db.conn.execute(
                    "SELECT id FROM memory WHERE kind = 'lesson' "
                    "AND first_created_at > ?",
                    (since,),
                )
                count += sum(
                    row["id"] in include_lesson_ids for row in await cursor.fetchall()
                )
        return count

    async def strengthen(self, memory_id: str, now: float) -> None:
        """重复写入合并强化：freshness 重置，不冒充慢通道召回。

        created_at 是创建时间，不随强化刷新；freshness 从强化时刻重新衰减。
        """
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "UPDATE memory SET freshness = 1.0, freshness_updated_at = ? "
                "WHERE id = ?",
                (now, memory_id),
            )
            if should_commit:
                await self._db.conn.commit()

    async def record_reading_evidence(
        self,
        source_topic: str,
        source_name: str,
        content: str,
        block_key: str,
        *,
        book_id: str | None = None,
        location: str | None = None,
        embedding: list[float] | None = None,
        created_ids: set[str] | None = None,
    ) -> str | None:
        """Persist exact source exposure once, including pending replay."""
        if not content.strip():
            return None
        if not source_topic or len(content) > 6000:
            raise ValueError("阅读证据需要来源且正文不能超过 6000 字符")
        key = json.dumps([source_topic, block_key, content], ensure_ascii=False)
        evidence_id = hashlib.sha256(key.encode("utf-8")).hexdigest()
        async with self._operation() as should_commit:
            cursor = await self._db.conn.execute(
                "SELECT id FROM reading_evidence WHERE source_topic=? "
                "AND content_hash=? AND content=? ORDER BY created_at, id LIMIT 1",
                (source_topic, hashlib.sha256(content.encode("utf-8")).hexdigest(),
                 content),
            )
            existing = await cursor.fetchone()
            if existing is not None:
                return str(existing["id"])
            inserted = await self._db.conn.execute(
                "INSERT INTO reading_evidence "
                "(id, source_topic, source_name, content, created_at, book_id, "
                "location, embedding, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                (evidence_id, source_topic, source_name, content, time.time(), book_id,
                 location, json.dumps(embedding) if embedding is not None else None,
                 hashlib.sha256(content.encode("utf-8")).hexdigest()),
            )
            if should_commit:
                await self._db.conn.commit()
            if inserted.rowcount == 1 and created_ids is not None:
                created_ids.add(evidence_id)
        return evidence_id

    async def find_knowledge_chunk(
        self, source_topic: str, content: str,
    ) -> str | None:
        """Find the earliest exact raw-text match through its source/hash index."""
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT id FROM reading_evidence WHERE source_topic=? "
                "AND content_hash=? AND content=? ORDER BY created_at, id LIMIT 1",
                (source_topic, hashlib.sha256(content.encode("utf-8")).hexdigest(),
                 content),
            )
            row = await cursor.fetchone()
        return str(row["id"]) if row is not None else None

    async def knowledge_chunk_changes(
        self, after: int | None,
    ) -> tuple[int, list[ReadingEvidence], list[str]]:
        """Read a cold snapshot or changed current rows and deleted IDs atomically."""
        async with self._operation() as should_commit:
            if should_commit:
                await self._db.conn.execute("BEGIN")
            cursor = await self._db.conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS seq FROM knowledge_chunk_change"
            )
            row = await cursor.fetchone()
            version = int(row["seq"]) if row is not None else 0
            if after is None:
                cursor = await self._db.conn.execute(
                    "SELECT * FROM reading_evidence ORDER BY created_at, id"
                )
            else:
                cursor = await self._db.conn.execute(
                    "SELECT delta.chunk_id AS changed_id, evidence.* FROM "
                    "(SELECT DISTINCT chunk_id FROM knowledge_chunk_change "
                    "WHERE seq > ? AND seq <= ?) AS delta "
                    "LEFT JOIN reading_evidence AS evidence "
                    "ON evidence.id=delta.chunk_id",
                    (after, version),
                )
            rows = await cursor.fetchall()
            if should_commit:
                await self._db.conn.commit()
        return (
            version,
            [_chunk_from_row(row) for row in rows if row["id"] is not None],
            [str(row["changed_id"]) for row in rows if row["id"] is None],
        )

    async def list_knowledge_chunks(
        self, *, source_topic: str | None = None, ids: list[str] | None = None,
    ) -> list[ReadingEvidence]:
        if ids == []:
            return []
        clauses: list[str] = []
        params: list[str] = []
        if source_topic is not None:
            clauses.append("source_topic=?")
            params.append(source_topic)
        if ids is not None:
            clauses.append("id IN (" + ",".join("?" for _ in ids) + ")")
            params.extend(ids)
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT * FROM reading_evidence"
                + (" WHERE " + " AND ".join(clauses) if clauses else "")
                + " ORDER BY created_at, id", params,
            )
            rows = await cursor.fetchall()
        return [_chunk_from_row(row) for row in rows]

    async def pending_reading_evidence(self, limit: int = 3) -> list[ReadingEvidence]:
        """Return bounded oldest unconsumed source snapshots."""
        if not 1 <= limit <= 3:
            raise ValueError("阅读证据 limit 必须在 [1, 3]")
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT id, source_topic, source_name, content, created_at, "
                "location, embedding "
                "FROM reading_evidence WHERE consumed_at IS NULL "
                "ORDER BY created_at, id LIMIT ?", (limit,),
            )
            rows = await cursor.fetchall()
        return [ReadingEvidence(
            id=row["id"], source_topic=row["source_topic"],
            source_name=row["source_name"], content=row["content"],
            created_at=row["created_at"],
            location=row["location"],
            embedding=json.loads(row["embedding"]) if row["embedding"] else None,
        ) for row in rows]

    async def consume_reading_evidence(self, ids: list[str]) -> None:
        """Consume a prepared snapshot inside the caller's reflection transaction."""
        if not self._db.in_transaction:
            raise RuntimeError("阅读证据消费必须处于反思事务内")
        unique_ids = list(dict.fromkeys(ids))
        if not unique_ids:
            return
        placeholders = ",".join("?" for _ in unique_ids)
        cursor = await self._db.conn.execute(
            "UPDATE reading_evidence SET consumed_at = ? "
            f"WHERE consumed_at IS NULL AND id IN ({placeholders})",
            (time.time(), *unique_ids),
        )
        if cursor.rowcount != len(unique_ids):
            raise RuntimeError("阅读证据已删除或已消费，需要重新准备反思")

    async def settle_freshness(self, now: float, rate: float) -> None:
        """Apply only elapsed freshness decay since the prior settlement."""
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "UPDATE memory SET freshness = MAX(0.0, freshness - ? * "
                "((? - freshness_updated_at) / 86400.0)), "
                "freshness_updated_at = ? WHERE freshness_updated_at < ?",
                (rate, now, now, now),
            )
            if should_commit:
                await self._db.conn.commit()

    async def search_keywords(
        self,
        tokens: list[str],
        limit: int,
        required_topic: str | None = None,
        kind: MemoryKind | None = None,
        allowed_ids: set[str] | None = None,
    ) -> dict[str, KeywordSearchHit]:
        if not tokens or limit <= 0:
            return {}

        hits: dict[str, KeywordSearchHit] = {}
        freshness: dict[str, float] = {}
        created_at: dict[str, float] = {}
        async with self._operation():
            for token in tokens:
                pattern = f"%{_escape_like(token)}%"
                topic_clause = (
                    " AND EXISTS (SELECT 1 FROM json_each(memory.topics) "
                    "WHERE json_each.value = ?)"
                    if required_topic is not None
                    else ""
                )
                params: tuple[str, ...] = (pattern, pattern, pattern, pattern)
                if required_topic is not None:
                    params = (*params, required_topic)
                kind_clause = " AND kind = ?" if kind is not None else ""
                if kind is not None:
                    params = (*params, kind.value)
                cursor = await self._db.conn.execute(
                    "SELECT id, freshness, created_at, "
                    "summary LIKE ? ESCAPE '\\' AS summary_hit, "
                    "content LIKE ? ESCAPE '\\' AS content_hit "
                    "FROM memory "
                    "WHERE (summary LIKE ? ESCAPE '\\' "
                    "OR content LIKE ? ESCAPE '\\')"
                    f"{topic_clause}{kind_clause}",
                    params,
                )
                rows = await cursor.fetchall()
                for row in rows:
                    memory_id = row["id"]
                    if allowed_ids is not None and memory_id not in allowed_ids:
                        continue
                    hit = hits.setdefault(
                        memory_id,
                        KeywordSearchHit(memory_id, [], []),
                    )
                    freshness[memory_id] = row["freshness"]
                    created_at[memory_id] = row["created_at"]
                    if row["summary_hit"] and token not in hit.summary_tokens:
                        hit.summary_tokens.append(token)
                    if row["content_hit"] and token not in hit.content_tokens:
                        hit.content_tokens.append(token)

        sorted_hits = sorted(
            hits.values(),
            key=lambda hit: (
                -len(set(hit.summary_tokens) | set(hit.content_tokens)),
                -len(hit.summary_tokens),
                -len(hit.content_tokens),
                -freshness[hit.memory_id],
                -created_at[hit.memory_id],
                hit.memory_id,
            ),
        )
        return {hit.memory_id: hit for hit in sorted_hits[:limit]}

    async def list_edges(
        self,
        kind: MemoryEdgeKind | None = None,
    ) -> list[MemoryEdge]:
        params: tuple[str, ...] = ()
        where = ""
        if kind is not None:
            where = "WHERE kind = ? "
            params = (kind.value,)
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT from_id, to_id, kind, weight, created_at FROM memory_edge "
                f"{where}ORDER BY from_id ASC, to_id ASC, kind ASC",
                params,
            )
            rows = await cursor.fetchall()
        return [_row_to_edge(r) for r in rows]

    async def upsert_edge(
        self,
        from_id: str,
        to_id: str,
        kind: MemoryEdgeKind,
        weight: float,
        created_at: float,
    ) -> None:
        left, right = _canonical_edge_ids(from_id, to_id)
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "INSERT INTO memory_edge (from_id, to_id, kind, weight, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(from_id, to_id, kind) DO UPDATE SET "
                "weight = excluded.weight, created_at = excluded.created_at",
                (left, right, kind.value, weight, created_at),
            )
            if should_commit:
                await self._db.conn.commit()

    async def delete_edges(
        self,
        keys: list[tuple[str, str, MemoryEdgeKind]],
    ) -> None:
        async with self._operation() as should_commit:
            for from_id, to_id, kind in keys:
                await self._db.conn.execute(
                    "DELETE FROM memory_edge "
                    "WHERE from_id = ? AND to_id = ? AND kind = ?",
                    (from_id, to_id, kind.value),
                )
            if should_commit:
                await self._db.conn.commit()

    async def list_edge_degrees(
        self,
        memory_ids: list[str],
    ) -> dict[str, list[MemoryEdge]]:
        result: dict[str, list[MemoryEdge]] = {
            memory_id: [] for memory_id in memory_ids
        }
        async with self._operation():
            for memory_id in memory_ids:
                cursor = await self._db.conn.execute(
                    "SELECT from_id, to_id, kind, weight, created_at "
                    "FROM memory_edge "
                    "WHERE from_id = ? OR to_id = ? "
                    "ORDER BY from_id ASC, to_id ASC, kind ASC",
                    (memory_id, memory_id),
                )
                rows = await cursor.fetchall()
                result[memory_id] = [_row_to_edge(r) for r in rows]
        return result


def _memory_row(
    m: Memory,
) -> tuple[str, float, str, str, str, str, float, str, int, str, str | None]:
    return (
        m.id, m.created_at, m.content, m.kind.value, json.dumps(m.topics), m.summary,
        m.freshness, m.type.value, m.recall_count, json.dumps(m.aspect),
        _embedding_json(m.embedding),
    )


def _embedding_json(v: list[float] | None) -> str | None:
    """embedding 列可空：None → SQL NULL（非 "null" 字符串）。

    list → JSON 数组字符串。
    """
    return json.dumps(v) if v is not None else None


def _escape_like(query: str) -> str:
    """转义 LIKE 通配符（%/_/\\），让 query 按字面匹配。"""
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _canonical_edge_ids(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _row_to_memory(row: aiosqlite.Row) -> Memory:
    return Memory(
        id=row["id"],
        created_at=row["created_at"],
        content=row["content"],
        kind=MemoryKind(row["kind"]),
        topics=json.loads(row["topics"]),
        summary=row["summary"],
        freshness=row["freshness"],
        type=MemoryType(row["type"]),
        recall_count=row["recall_count"],
        aspect=json.loads(row["aspect"]),
        embedding=(
            json.loads(row["embedding"]) if row["embedding"] is not None else None
        ),
    )


def _row_to_edge(row: aiosqlite.Row) -> MemoryEdge:
    return MemoryEdge(
        from_id=row["from_id"],
        to_id=row["to_id"],
        kind=MemoryEdgeKind(row["kind"]),
        weight=row["weight"],
        created_at=row["created_at"],
    )
