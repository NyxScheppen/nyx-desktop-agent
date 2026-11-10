"""Conversation RAG through the real host, memory tool and reply assembly.

Default models are controlled fixtures. --real-model additionally uses the local
ONNX encoder and configured LLM for final replies, with synthetic evidence only.
All databases and host state live in an automatically cleaned temporary folder.
"""

import argparse
import asyncio
import hashlib
import json
import os
import runpy
import sqlite3
import sys
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from nyx import db as db_module
from nyx.config import load_config
from nyx.db import connect
from nyx.expression.prompt import _tool_outputs_block
from nyx.llm.client import LlmClient
from nyx.memory.conversation_chunks import index_version, split_conversation
from nyx.memory.conversation_indexing import maintain_indexes
from nyx.memory.conversation_retrieval import ConversationRetrieval
from nyx.memory.embedding import build_embed
from nyx.memory.facts import USER_TIMEZONE, FactCandidate, MemoryFactStore
from nyx.memory.segments import append_turn, search_segments
from nyx.memory.store import MemoryStore

support = runpy.run_path(str(Path(__file__).with_name("nyx-p5-conversation-e2e.py")))


class Model(support["Model"]):
    def __init__(self, real=None):
        super().__init__()
        self.real = real
        self.answers = []

    async def complete(self, messages, **kwargs):
        if self.real is not None and kwargs["output_type"] == "reply":
            self.calls.append(("reply", kwargs["correlation_id"], messages))
            output = await self.real.complete(messages, **kwargs)
            self.answers.append(output.content)
            return output
        return await super().complete(messages, **kwargs)


async def controlled_embed(text):
    if "火星签证" in text:
        return [0.0, 0.0, 1.0]
    if any(term in text for term in ("白鹭", "蓝风铃", "接头", "碰面", "口令")):
        return [1.0, 0.0, 0.0]
    return [0.0, 1.0, 0.0]


async def persistence(folder):
    path = folder / "index.db"
    database = await connect(str(path))
    try:
        store = MemoryStore(database)
        retrieval = ConversationRetrieval(store)
        old = await append_turn(store, event_id="original:user", content="旧地址是银杏街九号。", embed=controlled_embed)

        async def search(query):
            return await search_segments(store, query, retrieval=retrieval)

        assert (await search("银杏街"))[0].id == old.id
        assert await search_segments(store, "无词面查询", embed=controlled_embed, retrieval=retrieval)
        try:
            async with database.transaction():
                changed = replace(old, raw_content="新地址是琥珀路七号。")
                chunks = [replace(chunk, embedding=[0.0, 0.0, 1.0])
                          for chunk in split_conversation(changed.id, changed.raw_content)]
                await store.update_conversation_segment(changed, chunks=chunks)
                assert await search("琥珀路")
                assert not await search("银杏街")
                assert not await search_segments(store, "无词面查询", embed=controlled_embed, retrieval=retrieval)
                raise RuntimeError("rollback fixture")
        except RuntimeError as error:
            assert str(error) == "rollback fixture"
        assert await search("银杏街")
        assert not await search("琥珀路")
        assert await search_segments(store, "无词面查询", embed=controlled_embed, retrieval=retrieval)
        print("PASS: warm vector/index views do not leak rolled-back source changes")

        await database.conn.execute("CREATE TRIGGER fail_posting BEFORE INSERT ON conversation_chunk_term BEGIN SELECT RAISE(ABORT, 'posting failed'); END")
        await database.conn.commit()
        try:
            await append_turn(store, event_id="failed:user", content="换个话题，失败的索引写入。")
            raise AssertionError("index failure was swallowed")
        except sqlite3.IntegrityError:
            pass
        assert await store.archived_conversation_turn("failed:user") is None
        assert (await store.get_conversation_segment(old.id)).raw_content == old.raw_content
        await database.conn.execute("DROP TRIGGER fail_posting")
        await database.conn.commit()

        async def cancelled(_text):
            raise asyncio.CancelledError()

        try:
            await append_turn(store, event_id="cancelled:user", content="换个话题，取消准备。", embed=cancelled)
            raise AssertionError("cancellation was swallowed")
        except asyncio.CancelledError:
            pass
        assert await store.archived_conversation_turn("cancelled:user") is None
        print("PASS: failed postings and cancellation preserve archive/index atomicity")

        # Replaying an accepted turn must not duplicate chunks or postings.
        before = await store.list_conversation_chunks(old.id)
        await append_turn(store, event_id="original:user", content="重放不会改写原文。")
        assert await store.list_conversation_chunks(old.id) == before
    finally:
        await database.close()

    database = await connect(str(path))
    other = await connect(str(folder / "other-character.db"))
    try:
        store = MemoryStore(database)
        retrieval = ConversationRetrieval(store)
        assert await search_segments(store, "银杏街", retrieval=retrieval)
        assert not await search_segments(MemoryStore(other), "银杏街")
        async with database.transaction():
            await database.conn.execute("DELETE FROM conversation_segment WHERE id=?", (old.id,))
        assert not await search_segments(store, "银杏街", retrieval=retrieval)
        assert await store.list_conversation_chunks(old.id) == []
        print("PASS: restart, character isolation and deletion of warm indexed evidence")
    finally:
        await other.close()
        await database.close()


async def lifecycle(folder, postgres=False):
    path = folder / "phase-two.db"
    database = await connect(str(path), migrations=None if postgres else [
        migration for migration in db_module._MIGRATIONS if migration[0] <= 56
    ])
    try:
        if postgres:
            # Exercise fresh compilation, then restore the exact v56 table shape
            # before seeding history and testing the incremental v57 migration.
            for name in ("insert", "revise", "delete"):
                await database.conn.execute(
                    f"DROP TRIGGER conversation_source_{name} ON conversation_segment"
                )
            for table in ("conversation_retrieval_hit", "conversation_retrieval_trace",
                          "conversation_index_job"):
                await database.conn.execute(f"DROP TABLE {table}")
            await database.conn.execute(
                "ALTER TABLE conversation_chunk DROP COLUMN embedding_version"
            )
            await database.conn.execute("UPDATE schema_version SET version=56")
        for identity, active in (("history-active", 1), ("history-cold", 0)):
            await database.conn.execute(
                "INSERT INTO conversation_segment "
                "(id, started_at, ended_at, raw_event_ids, raw_content, active) "
                "VALUES (?, 1, 2, '[]', ?, ?)",
                (identity, "用户：旧地址是银杏街九号。\n尼克斯：我记下了。\n用户：接头暗号是蓝风铃。", active),
            )
        await database.conn.commit()
    finally:
        await database.close()

    async def encoder(_text):
        return [1.0, 0.0]

    setattr(encoder, "index_version", "model-a")
    database = await connect(str(path))
    try:
        store = MemoryStore(database)
        await maintain_indexes(store, encoder, max_chunks=1)
        status = await store.conversation_index_status(index_version(encoder))
        assert status["pending"] == 2, status
        chunks = await store.list_conversation_chunks("history-active")
        assert sum(chunk.embedding is not None for chunk in chunks) == 1
    finally:
        await database.close()

    database = await connect(str(path))
    try:
        store = MemoryStore(database)
        await maintain_indexes(store, encoder)
        assert (await store.conversation_index_status(index_version(encoder)))["ready"] == 2
        retrieval = ConversationRetrieval(store)
        assert len(await search_segments(store, "saturn", embed=encoder,
                                        retrieval=retrieval, include_archived=True)) == 2
        assert len(await search_segments(store, "saturn", embed=encoder,
                                        retrieval=retrieval)) == 1
        print("PASS: legacy active/cold backfill resumes after partial progress and restart")

        setattr(encoder, "index_version", "model-b")
        assert not await search_segments(store, "saturn", embed=encoder, retrieval=retrieval)
        assert await search_segments(store, "银杏街", embed=encoder, retrieval=retrieval)

        async def cancelled_swap(_text):
            raise asyncio.CancelledError()

        setattr(cancelled_swap, "index_version", "model-b")
        try:
            await maintain_indexes(store, cancelled_swap)
            raise AssertionError("model swap cancellation was swallowed")
        except asyncio.CancelledError:
            pass
        assert (await store.conversation_index_status(index_version(encoder)))["pending"] == 2
        await maintain_indexes(store, encoder)
        assert await search_segments(store, "saturn", embed=encoder, retrieval=retrieval)
        print("PASS: model identity excludes same-dimension old vectors; lexical search stays available")

        async def failed(_text):
            raise OSError("synthetic provider failure")

        setattr(failed, "index_version", "model-c")
        await maintain_indexes(store, failed)
        status = await store.conversation_index_status(index_version(failed))
        assert status["failed"] == 2 and status["pending"] == 2, status
        await database.close()
        database = await connect(str(path))
        store = MemoryStore(database)
        retrieval = ConversationRetrieval(store)
        assert (await store.conversation_index_status(index_version(failed)))["failed"] == 2
        assert not await store.pending_conversation_indexes(index_version(failed), 0)
        async with database.transaction():
            await database.conn.execute("UPDATE conversation_index_job SET retry_at=0")

        async def cancelled(_text):
            raise asyncio.CancelledError()

        setattr(cancelled, "index_version", "model-c")
        try:
            await maintain_indexes(store, cancelled)
            raise AssertionError("cancelled indexing was swallowed")
        except asyncio.CancelledError:
            pass
        setattr(encoder, "index_version", "model-c")
        await maintain_indexes(store, encoder)
        assert (await store.conversation_index_status(index_version(encoder)))["ready"] == 2
        print("PASS: durable retry/backoff and cancellation resume without discarding progress")

        # Pause real embedding preparation while a correction/deletion/rebuild wins.
        for action in ("revise", "delete", "rebuild"):
            await store.rebuild_conversation_indexes()
            entered, release = asyncio.Event(), asyncio.Event()

            async def blocked(_text):
                entered.set()
                await release.wait()
                return [0.0, 1.0]

            setattr(blocked, "index_version", "model-c")
            work = asyncio.create_task(maintain_indexes(store, blocked, max_chunks=1))
            await asyncio.wait_for(entered.wait(), 5)
            source = await store.get_conversation_segment("history-active")
            assert source is not None
            if action == "revise":
                digest = hashlib.sha256(source.raw_content.encode()).hexdigest()
                changed = await store.revise_conversation_segment(
                    source.id, "用户：新地址是琥珀路七号。", digest,
                )
                assert changed is not None
            elif action == "delete":
                assert await store.delete_conversation_segment(source.id)
            else:
                await store.rebuild_conversation_indexes()
            release.set()
            await work
            for identity in ("history-active", "history-cold"):
                assert not any(chunk.embedding == [0.0, 1.0]
                               for chunk in await store.list_conversation_chunks(identity))
            if action == "delete":
                # Keep the next loop's ordering deterministic.
                await store.add_conversation_segment(replace(source, raw_content="用户：新地址是琥珀路七号。"))
            await maintain_indexes(store, encoder)
        print("PASS: revision, deletion and rebuild during inference cannot publish stale vectors")

        fact_store = MemoryFactStore(database)
        at = lambda day: datetime(2026, 5, day, tzinfo=USER_TIMEZONE).timestamp()
        await fact_store.apply([FactCandidate("用户", "居住地", "北京", at(1), None)],
                               None, "profile", source_segment_id="history-active")
        await fact_store.apply([FactCandidate("用户", "居住地", "上海", at(20), None)],
                               None, "profile", source_segment_id="history-cold")
        await fact_store.apply([FactCandidate("用户", "居住地", "杭州", at(10), None)],
                               None, "profile", source_segment_id="history-active")
        assert (await fact_store.search("我2026-05-05的居住地"))[0].object_value == "北京"
        assert (await fact_store.search("我2026年5月15日的居住地"))[0].object_value == "杭州"
        assert (await fact_store.search("我的居住地", now=at(25)))[0].object_value == "上海"
        await search_segments(store, "琥珀路", embed=encoder, retrieval=retrieval)
        source = await store.get_conversation_segment("history-active")
        await store.revise_conversation_segment(source.id,
            source.raw_content + "\n用户：地址更正，暂不透露。",
            hashlib.sha256(source.raw_content.encode()).hexdigest())
        assert not await fact_store.search("我2026-05-15的居住地")
        assert (await fact_store.search("我的居住地", now=at(25)))[0].object_value == "上海"
        await store.delete_conversation_segment("history-cold")
        assert not await fact_store.search("我的居住地", now=at(25))
        for trace in await store.conversation_traces(100):
            assert all(hit["segment_id"] != "history-cold" for hit in trace["hits"])
        print("PASS: late historical facts preserve current validity; source corrections/deletes retract facts and traces")

        # Force the resident reader to fall behind the bounded change journal.
        async with database.transaction():
            await database.conn.executemany(
                "INSERT INTO conversation_chunk_change(chunk_id) VALUES (?)",
                [("compaction-fixture",)] * 10005,
            )
            await database.conn.execute(
                "UPDATE conversation_retrieval_trace SET created_at=0"
            )
        await store.maintain_conversation_index_logs()
        assert not await store.conversation_traces()
        await search_segments(store, "地址", embed=encoder, retrieval=retrieval)
        actual = await store.list_conversation_chunks("history-active")
        assert set(retrieval._indexes.chunks) == {chunk.id for chunk in actual}
        print("PASS: trace expiry and compacted journal rebuild the resident view without stale chunks")
    finally:
        await database.close()


async def host_path(folder, real_model, postgres=False):
    config = load_config()
    llm = LlmClient.from_config(replace(config.llm, temperature=0.0)) if real_model else None
    model = Model(llm)
    embed = build_embed(config.embedding.model) if real_model else controlled_embed
    async with support["fixture"](folder, model, fast=True, run_bus=False, postgres=postgres) as (app, client, _port):
        app.memory._embed = embed
        memory, store = app.memory, app.memory._store
        story = ("我今天整理书架。" * 300
                 + "我们约好周五晚上七点在白鹭书店碰头，接头暗号是雨后的蓝风铃。"
                 + "今天的日常很平静。" * 300)
        await memory.append_conversation_turn("long:user", story)
        source = await store.archived_conversation_turn("long:user")
        assert "白鹭书店" not in source.summary
        assert "白鹭书店" not in source.raw_content[:2000]
        result = await memory.search_conversation_segments("白鹭书店 暗号")
        assert result and result[0].id == source.id
        evidence = result[0]
        assert "白鹭书店" in evidence.retrieval_excerpt
        assert evidence.retrieval_chunk_ids
        assert evidence.retrieval_sources == ["bm25", "vector"]
        assert (await store.get_conversation_segment(source.id)).raw_content == source.raw_content
        for a, b in evidence.retrieval_ranges:
            assert source.raw_content[a:b] in evidence.retrieval_excerpt
        print("PASS: detail beyond head/tail summaries, BM25/vector fusion and exact source ranges")

        paraphrase = "那次约好碰面的地点和接头口令是什么？"
        semantic = await memory.search_conversation_segments(paraphrase)
        assert semantic and "白鹭书店" in semantic[0].retrieval_excerpt
        await app.expression.reply(paraphrase, "rag-reply")
        prompt = next(messages[-1]["content"] for kind, cid, messages in model.calls if kind == "reply" and cid == "rag-reply")
        assert "白鹭书店" in prompt and "蓝风铃" in prompt, prompt
        assert "segment_id=" in prompt and "原文字符范围=" in prompt
        used = await store.get_conversation_segment(source.id)
        assert used.recall_count == 1
        traces = await store.conversation_traces(20)
        adopted = [hit for trace in traces for hit in trace["hits"]
                   if hit["injected_event_id"] == "rag-reply"]
        assert adopted and all(hit["selected"] for hit in adopted), traces
        assert any(hit["selected"] and hit["injected_event_id"] is None
                   for trace in traces for hit in trace["hits"]), traces
        assert all("raw_content" not in trace and "query" not in trace for trace in traces)
        if real_model:
            answer = json.loads(model.answers[-1])["speak"]
            assert "白鹭书店" in answer and "蓝风铃" in answer, answer
            print("PASS: real ONNX paraphrase recall -> actual reply prompt -> real LLM grounded answer")
        else:
            print("PASS: paraphrase evidence reaches ordinary reply assembly and counts once")

        async with app.database.transaction():
            await app.database.conn.execute("UPDATE conversation_segment SET active=0 WHERE id=?", (source.id,))
        assert not await memory.search_conversation_segments("白鹭书店 暗号")
        tools = app.expression._tools
        result = await tools.call("memory_recall", {"query": "白鹭书店 暗号", "top_k": 8})
        assert result["segments"] and result["segments"][0]["segment_id"] == source.id
        entry = json.dumps(result["segments"][0], ensure_ascii=False)
        rendered = _tool_outputs_block(["memory_recall: " + json.dumps(result, ensure_ascii=False)])
        assert entry in rendered and "白鹭书店" in rendered
        assert (await store.get_conversation_segment(source.id)).recall_count == 1
        assert await memory.search_conversation_segments("还记得白鹭书店的暗号吗")
        print("PASS: active/cold boundary, memory_recall injection budget, retrieval alone does not count")

        if real_model:
            await app.expression.reply("我之前说过的火星签证编号是什么？如果没有记录，请直接说明不知道。", "rag-no-evidence")
            answer = json.loads(model.answers[-1])["speak"]
            assert any(word in answer for word in ("没有", "不知道", "没提", "不记得", "没找到", "找不到", "不清楚")), answer
            print("PASS: real LLM reports missing evidence for an absent identifier")

        prefix = "/api/characters/nyx/memories"
        response = await client.get(prefix + "/rag/status")
        assert response.status_code == 200, response.text
        assert (await client.get(prefix + "/rag/traces")).status_code == 200
        assert (await client.post(prefix + "/rag/rebuild")).status_code == 202
        await memory.maintain_conversation_indexes(max_chunks=500)
        # Host routing must not grant another character this role's evidence.
        assert (await client.get("/api/characters/missing/memories/rag/traces")).status_code == 404
        response = await client.get(prefix + "/conversations")
        entry = next(item for item in response.json() if item["id"] == source.id)
        revision = {"raw_content": "用户：已更正，新的暗号是清晨的银杏叶。",
                    "expected_content_hash": entry["content_hash"]}
        assert (await client.patch(prefix + "/conversations/" + source.id, json=revision)).status_code == 200
        assert (await client.patch(prefix + "/conversations/" + source.id, json=revision)).status_code == 409
        assert (await client.delete(prefix + "/conversations/" + source.id)).status_code == 204
        assert not await store.list_conversation_chunks(source.id)
        assert all(segment.id != source.id for segment in
                   await memory.search_conversation_segments("白鹭书店 暗号"))
        print("PASS: traced selection/injection and role-scoped status/rebuild/CAS revision/delete HTTP paths")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--postgres", action="store_true",
                        help="Use only an isolated disposable NYX_DATABASE_URL (writes test data)")
    args = parser.parse_args()
    if args.real_model:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    postgres_url = os.environ.get("NYX_DATABASE_URL", "")
    if args.postgres and not postgres_url:
        raise ValueError("--postgres requires an isolated NYX_DATABASE_URL")
    os.environ["NYX_DATABASE_URL"] = ""
    with tempfile.TemporaryDirectory(prefix="nyx-memory-rag-") as temporary:
        folder = Path(temporary)
        await persistence(folder)
        if args.postgres:
            os.environ["NYX_DATABASE_URL"] = postgres_url
        await lifecycle(folder, args.postgres)
        await host_path(folder / "host", args.real_model, args.postgres)
    print("PASS: conversation RAG acceptance (" + ("PostgreSQL" if args.postgres else "SQLite") + "; " + ("real encoder/reply LLM" if args.real_model else "controlled models") + ")")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
