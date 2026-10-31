import asyncio
import json
import logging
import re
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path.cwd()))
from nyx import db
from nyx.config import MemoryConfig
from nyx.events.bus import EventBus
from nyx.memory.facade import MemoryFacade
from nyx.memory.facts import MemoryFactStore
from nyx.memory.retrieval import MemoryRetrieval
from nyx.memory.store import MemoryStore
from nyx.types import LLMOutput

logging.disable(logging.CRITICAL)


class ScriptedLlm:
    def __init__(self, points=5):
        self.points = points
        self.calls = []
        self.fail_digest = None
        self.fail_facts = False

    async def complete(self, messages, *, module, output_type, correlation_id, **kwargs):
        user = messages[-1]['content']
        self.calls.append((output_type, user))
        payload = {}
        if output_type == 'source_digest':
            source = json.loads(user)
            marker = source['source_text'].split()[0]
            if marker == self.fail_digest:
                raise RuntimeError('injected digest failure')
            payload = {
                'summary': marker, 'themes': [], 'content_category': 'reference',
                'points': [
                    {'topic': f'{marker}-{i}', 'content': f'{marker} concept {i} uses distinct value {i}'}
                    for i in range(self.points)
                ],
            }
        elif output_type == 'fact_extraction':
            if self.fail_facts:
                raise RuntimeError('injected extraction failure')
            sections = re.split(r'记忆索引：\d+\n', user)[1:] if '记忆索引：' in user else [user]
            facts = []
            for i, section in enumerate(sections):
                summary = re.search(r'摘要：([^\n]+)', section).group(1)
                facts.append({
                    'memory_index': i, 'subject': summary, 'subject_type': 'concept',
                    'predicate': '使用', 'object': f'value-{summary}', 'object_type': 'concept',
                    'mode': 'multi',
                })
            payload = {'entities': [], 'facts': facts}
        return LLMOutput(module=module, type=output_type, model='scripted',
                         content=json.dumps(payload, ensure_ascii=False), correlation_id=correlation_id)

    def fact_calls(self):
        return sum(kind == 'fact_extraction' for kind, _ in self.calls)


class Evaluator:
    async def evaluate(self, output):
        pass


async def stack(points=5):
    database = await db.connect(':memory:')
    store = MemoryStore(database)
    llm = ScriptedLlm(points)
    facts = MemoryFactStore(database)
    facade = MemoryFacade(store, MemoryRetrieval(store, None), EventBus(database),
                          llm, Evaluator(), MemoryConfig(), None, facts)
    return database, store, facts, facade, llm


async def row_count(database, table):
    return (await (await database.conn.execute(f'SELECT COUNT(*) FROM {table}')).fetchone())[0]


async def ingest(facade, text='alpha original evidence', name='Original source', source='local:batch'):
    return await facade.ingest_source(text, source, name, 'step2', location='local-evidence')


async def happy():
    database, store, facts, facade, llm = await stack()
    try:
        result = await ingest(facade)
        assert (result['created_memories'], result['created_facts']) == (5, 5), result
        assert llm.fact_calls() == 1, f'expected 1 fact extraction, got {llm.fact_calls()}'
        chunk = result['chunks'][0]
        memories = await facade.list_memories()
        fact_rows = await facts.recent(32, scope='knowledge')
        by_id = {m.id: m for m in memories}
        assert len(fact_rows) == 5 and all(f.source_chunk_id == chunk['chunk_id'] for f in fact_rows)
        assert all(by_id[f.source_memory_id].summary == f.subject for f in fact_rows)
        assert all('local:batch' in m.topics for m in memories)
        assert llm.calls[-1][0] != 'source_digest'
        extraction = next(user for kind, user in llm.calls if kind == 'fact_extraction')
        assert extraction.count('来源名称：Original source') == 5
        result = await ingest(facade, name='Changed name')
        assert (result['created_memories'], result['created_facts']) == (0, 0), result
        assert result['chunks'][0]['source_name'] == 'Original source'
        assert json.loads([u for k, u in llm.calls if k == 'source_digest'][-1])['source']['name'] == 'Original source'
        print('PASS five points: 1 fact request, counts 5/5, provenance and replay')
    finally:
        await database.close()


async def boundaries():
    for count in (0, 1):
        database, store, facts, facade, llm = await stack(count)
        try:
            result = await ingest(facade)
            assert (result['created_memories'], result['created_facts']) == (count, count)
            assert llm.fact_calls() == count
            empty = await ingest(facade, text='')
            assert empty == {'created_memories': 0, 'created_facts': 0, 'chunks': []}
        finally:
            await database.close()
    database, store, facts, facade, llm = await stack()
    try:
        text = 'alpha ' + 'A' * 5994 + 'beta ' + 'B' * 5995
        with patch('nyx.memory.facade.time.time', return_value=1000.0):
            result = await ingest(facade, text)
        assert (result['created_memories'], result['created_facts'], len(result['chunks'])) == (10, 10, 2)
        assert llm.fact_calls() == 2
        rows = await facts.recent(32, scope='knowledge')
        assert all(f.source_chunk_id == result['chunks'][0 if f.subject.startswith('alpha') else 1]['chunk_id'] for f in rows)
        with patch('nyx.memory.facade.time.time', return_value=1000.0):
            result = await ingest(facade, text, source='web:other')
        assert (result['created_memories'], result['created_facts']) == (10, 0)
        assert len({f.source_chunk_id for f in await facts.recent(32, scope='knowledge')}) == 2
        print('PASS empty/single/multiple blocks, chunk isolation and cross-source fact provenance')
    finally:
        await database.close()


async def failures():
    database, store, facts, facade, llm = await stack()
    try:
        llm.fail_digest = 'beta'
        text = 'alpha ' + 'A' * 5994 + 'beta original evidence'
        result = await ingest(facade, text)
        assert (result['created_memories'], result['created_facts'], len(result['chunks'])) == (5, 5, 2)
        assert result.get('errors')
        llm.fail_digest = None
        result = await ingest(facade, text)
        assert (result['created_memories'], result['created_facts']) == (5, 5) and not result.get('errors')
    finally:
        await database.close()
    for failure in ('extract', 'apply'):
        database, store, facts, facade, llm = await stack()
        try:
            if failure == 'extract':
                llm.fail_facts = True
                result = await ingest(facade)
                llm.fail_facts = False
                expected = 0
            else:
                apply = facts.apply
                calls = 0
                async def fail_one(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 3:
                        raise RuntimeError('injected fact apply failure')
                    return await apply(*args, **kwargs)
                with patch.object(facts, 'apply', side_effect=fail_one):
                    result = await ingest(facade)
                expected = 4
            assert (result['created_memories'], result['created_facts']) == (5, expected), result
            assert await row_count(database, 'memory') == 5
            replay = await ingest(facade)
            assert (replay['created_memories'], replay['created_facts']) == (0, 0)
            assert await row_count(database, 'memory_fact') == expected
        finally:
            await database.close()
    print('PASS digest partial failure/retry, extraction fallback and fact-apply best effort')


async def interrupted_batch():
    database, store, facts, facade, llm = await stack()
    try:
        add = store.add
        calls = 0
        async def fail_third(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError('injected memory insert failure')
            return await add(*args, **kwargs)
        with patch.object(store, 'add', side_effect=fail_third):
            result = await ingest(facade)
        assert await row_count(database, 'memory') == await row_count(database, 'memory_fact') == 2
        assert result.get('errors')
        assert (result['created_memories'], result['created_facts']) == (2, 2), result
        replay = await ingest(facade)
        assert (replay['created_memories'], replay['created_facts']) == (3, 3), replay
        assert await row_count(database, 'memory') == await row_count(database, 'memory_fact') == 5
        print('PASS interrupted batch: committed count retained; retry creates remaining 3/3')
    finally:
        await database.close()


async def accumulator():
    database, store, facts, facade, llm = await stack()
    try:
        progress = {'created_memories': 11, 'created_facts': 7}
        chunk_id = await facade.record_reading_evidence('local:batch', 'Source', 'saved snapshot', '0')
        items = [{'topic': str(i), 'content': f'distinct item {i}'} for i in range(5)]
        result = await facade.remember_knowledge(items, 'direct', source_chunk_id=chunk_id, created_counts=progress)
        assert result == {'created_memories': 5, 'created_facts': 5}
        assert progress == {'created_memories': 16, 'created_facts': 12}
        for replay in (items, [], [{'topic': 'blank', 'content': '  '}]):
            result = await facade.remember_knowledge(replay, 'direct', source_chunk_id=chunk_id, created_counts=progress)
            assert result == {'created_memories': 0, 'created_facts': 0}
            assert progress == {'created_memories': 16, 'created_facts': 12}
        facade._fact_store = None
        progress = {}
        result = await facade.remember_knowledge([{'content': 'no fact store item'}], 'no-facts', created_counts=progress)
        assert result == progress == {'created_memories': 1, 'created_facts': 0}
        print('PASS optional accumulator: additive progress, per-call return, duplicates, blanks and absent fact store')
    finally:
        await database.close()


async def main():
    selected = sys.argv[1] if len(sys.argv) > 1 else 'all'
    for name, run in [('happy', happy), ('boundaries', boundaries), ('failures', failures), ('interrupted', interrupted_batch), ('accumulator', accumulator)]:
        if selected in ('all', name):
            await run()


asyncio.run(main())
