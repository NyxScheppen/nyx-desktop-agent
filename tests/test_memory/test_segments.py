# pyright: reportPrivateUsage=false
import json
import sqlite3
from unittest.mock import patch

import pytest

from nyx.db import connect
from nyx.memory.facade import _parse_segment_evaluation
from nyx.memory.segments import append_turn, search_segments, should_continue
from nyx.memory.store import MemoryStore
from nyx.types import ConversationSegment


def _segment() -> ConversationSegment:
    return ConversationSegment(
        id="s1",
        previous_id=None,
        started_at=1.0,
        ended_at=1.0,
        raw_event_ids=["e1"],
        raw_content="鱼火锅很好吃",
        summary="鱼火锅很好吃",
        topics=["鱼火", "火锅"],
    )


def test_segment_connection_is_permissive_for_short_reply_and_topic_overlap() -> None:
    segment = _segment()
    assert should_continue(segment, "嗯", 10_000.0)
    assert should_continue(segment, "下次还想吃鱼火锅", 10_000.0)


def test_segment_connection_honors_explicit_topic_break() -> None:
    assert not should_continue(_segment(), "对了，汉代制度呢", 2.0)


@pytest.mark.asyncio
async def test_append_turn_keeps_raw_content_and_chain() -> None:
    database = await connect(":memory:")
    try:
        store = MemoryStore(database)
        first = await append_turn(
            store, event_id="e1", content="鱼火锅", timestamp=1.0
        )
        second = await append_turn(
            store, event_id="e2", content="嗯", timestamp=2.0
        )
        third = await append_turn(
            store, event_id="e3", content="对了，汉代制度", timestamp=3.0
        )
        assert second.id == first.id
        assert second.raw_event_ids == ["e1", "e2"]
        assert third.previous_id == first.id
        assert third.raw_content == "用户：对了，汉代制度"
    finally:
        await database.conn.close()


async def test_archive_duplicate_does_not_append_or_refresh_segment() -> None:
    database = await connect(":memory:")
    try:
        store = MemoryStore(database)
        first = await append_turn(
            store, event_id="u:user", content="鱼火锅", timestamp=10.0
        )
        await database.conn.execute(
            "UPDATE conversation_segment SET active=0 WHERE id=?", (first.id,)
        )
        await database.conn.commit()
        await append_turn(store, event_id="u:user", content="鱼火锅", timestamp=20.0)
        saved = await store.get_conversation_segment(first.id)
        assert saved is not None and saved.raw_event_ids == ["u:user"]
        assert saved.active is False and saved.ended_at == 10.0
        row = await (
            await database.conn.execute(
                "SELECT count(*) AS n FROM conversation_segment"
            )
        ).fetchone()
        assert row is not None and row["n"] == 1
    finally:
        await database.close()


@pytest.mark.parametrize("table", ["conversation_segment", "conversation_turn_archive"])
async def test_archive_write_failure_rolls_back_segment_and_index(table: str) -> None:
    database = await connect(":memory:")
    try:
        store = MemoryStore(database)
        await database.conn.execute(
            f"CREATE TRIGGER fail_archive BEFORE INSERT ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'archive write failed'); END"
        )
        await database.conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="archive write failed"):
            await append_turn(store, event_id="u:user", content="原话", timestamp=1.0)
        assert await store.latest_conversation_segment() is None
        assert await store.archived_conversation_turn("u:user") is None
        await database.conn.execute("DROP TRIGGER fail_archive")
        await database.conn.commit()
        saved = await append_turn(
            store, event_id="u:user", content="原话", timestamp=1.0
        )
        assert saved.raw_event_ids == ["u:user"]
    finally:
        await database.close()


async def test_archive_uncertain_commit_reuses_committed_index() -> None:
    database = await connect(":memory:")
    try:
        store = MemoryStore(database)
        commit = database.conn.commit

        async def committed_then_failed() -> None:
            await commit()
            raise OSError("commit acknowledgement lost")

        with patch.object(database.conn, "commit", committed_then_failed):
            first = await append_turn(
                store, event_id="u:user", content="原话", timestamp=1.0
            )
        again = await append_turn(
            store, event_id="u:user", content="原话", timestamp=2.0
        )
        assert again.id == first.id
        assert again.raw_event_ids == ["u:user"]
        assert again.ended_at == 1.0
    finally:
        await database.close()


async def test_old_source_time_keeps_reply_with_the_latest_committed_segment() -> None:
    database = await connect(":memory:")
    try:
        store = MemoryStore(database)
        await append_turn(store, event_id="a:user", content="杭州旅行", timestamp=100.0)
        second = await append_turn(
            store, event_id="b:user", content="换个话题，音乐", timestamp=1.0
        )
        reply = await append_turn(
            store, event_id="b:nyx", content="一起听音乐", timestamp=2.0
        )
        assert reply.id == second.id
        assert reply.raw_event_ids == ["b:user", "b:nyx"]
        assert (reply.started_at, reply.ended_at) == (1.0, 2.0)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_segment_raw_content_marks_speaker() -> None:
    database = await connect(":memory:")
    try:
        store = MemoryStore(database)
        await append_turn(store, event_id="turn:user", content="鱼火锅", timestamp=1.0)
        segment = await append_turn(
            store, event_id="turn:nyx", content="听起来不错", timestamp=2.0
        )
        assert segment.raw_content == "用户：鱼火锅\n尼克斯：听起来不错"
    finally:
        await database.close()


def test_segment_evaluation_rejects_nyx_and_unknown_event_evidence() -> None:
    segment = _segment()
    segment.raw_event_ids = ["turn:user", "turn:nyx"]
    result = _parse_segment_evaluation(
        json.dumps({
            "importance": "high", "long_term": True, "memory_kind": "episode",
            "summary": "重要经历", "topics": ["经历"],
            "evidence_event_ids": ["turn:user", "turn:nyx", "invented:user"],
            "evidence_segment_ids": ["prior"], "reason": "用户明确叙述",
        }),
        segment,
    )
    assert result[-2] == ["turn:user"]
    assert result[-1] == ["prior"]


@pytest.mark.asyncio
async def test_segment_vector_recall_has_similarity_gate() -> None:
    database = await connect(":memory:")
    try:
        store = MemoryStore(database)

        async def embed(text: str) -> list[float]:
            return [1.0, 0.0] if text == "鱼火锅" else [0.0, 1.0]

        await append_turn(
            store, event_id="e1", content="完全无关", timestamp=1.0, embed=embed
        )
        assert await search_segments(store, "鱼火锅", embed=embed) == []
    finally:
        await database.conn.close()


@pytest.mark.asyncio
async def test_recall_promotes_once_per_user_event_and_keeps_raw_text() -> None:
    database = await connect(":memory:")
    try:
        store = MemoryStore(database)
        segment = await append_turn(
            store, event_id="e1", content="鱼火锅", timestamp=1.0
        )
        assert await store.record_segment_recall(segment.id, "user-1", 2) is False
        assert await store.record_segment_recall(segment.id, "user-1", 2) is False
        assert await store.record_segment_recall(segment.id, "user-2", 2) is True
        saved = await store.latest_conversation_segment()
        assert saved is not None and (saved.recall_count, saved.long_term) == (2, True)
        assert saved.raw_content == "用户：鱼火锅"
    finally:
        await database.close()


@pytest.mark.parametrize(
    "greeting", ["早上好呀，尼克斯", "早上好呀，尼克斯。", "你好Nyx."]
)
async def test_greeting_does_not_recall_unrelated_episode_by_vector(
    greeting: str,
) -> None:
    database = await connect(":memory:")
    try:
        store = MemoryStore(database)

        async def embed(text: str) -> list[float]:
            return [1.0, 0.0]

        await append_turn(store, event_id="old", content="下班后特别开心", embed=embed)
        assert await search_segments(store, greeting, embed=embed) == []
    finally:
        await database.close()


async def test_segment_embedding_failure_keeps_lexical_recall() -> None:
    database = await connect(":memory:")
    try:
        store = MemoryStore(database)
        segment = await append_turn(
            store, event_id="old", content="生日那天你陪我走过低谷"
        )

        async def embed(text: str) -> list[float]:
            raise RuntimeError("embedding unavailable")

        result = await search_segments(store, "生日那天", embed=embed)
        assert [item.id for item in result] == [segment.id]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_capacity_evicts_only_short_term_recall_index() -> None:
    database = await connect(":memory:")
    try:
        store = MemoryStore(database)
        first = await append_turn(store, event_id="e1", content="鱼火锅", timestamp=1.0)
        await append_turn(store, event_id="e2", content="对了，汉代制度", timestamp=2.0)
        await store.settle_segment_freshness(100.0, 0.01)
        await store.evict_short_segments(1)
        saved = await store.get_conversation_segment(first.id)
        assert saved is not None and not saved.active
        assert saved.raw_content == "用户：鱼火锅"
        assert await search_segments(store, "鱼火锅") == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_freshness_affects_segment_recall_ranking() -> None:
    database = await connect(":memory:")
    try:
        store = MemoryStore(database)
        older = _segment()
        older.id = "a-low"
        older.freshness = 0.1
        newer = _segment()
        newer.id = "z-high"
        newer.freshness = 0.9
        await store.add_conversation_segment(older)
        await store.add_conversation_segment(newer)

        found = await search_segments(store, "鱼火锅")

        assert [item.id for item in found] == ["z-high", "a-low"]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_old_long_term_segment_remains_searchable_after_200_newer() -> None:
    database = await connect(":memory:")
    try:
        store = MemoryStore(database)
        old = _segment()
        old.id = "old-long-term"
        old.long_term = True
        await store.add_conversation_segment(old)
        for index in range(201):
            await store.add_conversation_segment(
                ConversationSegment(
                    id=f"other-{index}", previous_id=None,
                    started_at=index + 2.0, ended_at=index + 2.0,
                    raw_event_ids=[f"event-{index}"], raw_content="无关话题",
                    summary="无关话题", topics=["无关话题"], long_term=True,
                )
            )

        found = await search_segments(store, "鱼火锅")

        assert [item.id for item in found] == [old.id]
    finally:
        await database.close()
