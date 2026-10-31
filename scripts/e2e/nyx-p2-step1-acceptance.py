import asyncio
import hashlib
import heapq
import importlib.util
import json
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path.cwd()))
from nyx import db as db_module
from nyx.config import MemoryConfig
from nyx.enums import MemoryKind, MemoryType
from nyx.memory.ann import AnnIndex
from nyx.memory.facade import MemoryFacade
from nyx.memory.store import MemoryStore
from nyx.types import Memory, MemoryFact


async def east(text):
    return [1.0, 0.0]


async def north(text):
    return [0.0, 1.0]


def facade(store, embed=north):
    result = MemoryFacade(store, None, None, None, None, MemoryConfig(), embed=embed)
    result.search_facts = AsyncMock(return_value=[])
    return result


def ids(result):
    return {item['chunk_id'] for item in result['chunks']}


async def scalar(db, sql, args=()):
    return (await (await db.conn.execute(sql, args)).fetchone())[0]


async def insert(store, content, source='local:a', name='资料', vector=(1, 0), **kwargs):
    return await store.record_reading_evidence(
        source, name, content, content, embedding=list(vector) if vector is not None else None,
        **kwargs,
    )


async def performance(n):
    db = await db_module.connect(':memory:')
    try:
        store = MemoryStore(db)
        await db.conn.executemany(
            'INSERT INTO reading_evidence(id,source_topic,source_name,content,created_at,embedding,content_hash) VALUES(?,?,?,?,?,?,?)',
            [(f'{i:08d}', 'local:one', '科学', f'宇宙在膨胀，编号{i}', i, '[1,0]',
              hashlib.sha256(f'宇宙在膨胀，编号{i}'.encode()).hexdigest()) for i in range(n)],
        )
        await db.conn.commit()
        api = facade(store, east)
        deltas = []
        original_delta = store.knowledge_chunk_changes
        async def delta(after):
            result = await original_delta(after)
            deltas.append((after, len(result[1]), len(result[2])))
            return result
        store.knowledge_chunk_changes = delta
        assert (await api.search_knowledge('宇宙'))['chunks']
        assert deltas == [(None, n, 0)]
        deltas.clear()
        visits = 0
        original_merge = heapq.merge
        def merge(*args, **kwargs):
            nonlocal visits
            for item in original_merge(*args, **kwargs):
                visits += 1
                yield item
        with patch.object(AnnIndex, 'build', wraps=AnnIndex.build) as builds:
            with patch('heapq.merge', merge):
                started = time.perf_counter()
                for _ in range(3):
                    assert (await api.search_knowledge('宇宙'))['chunks']
                seconds = time.perf_counter() - started
            assert deltas == [(n, 0, 0)] * 3, deltas
            assert builds.call_count == 0
            assert visits <= 3 * 1100, visits
            deltas.clear()
            before = await scalar(db, 'SELECT COUNT(*) FROM reading_evidence')
            for i in range(30):
                await api.record_reading_evidence('local:one', '科学', f'新观测编号{n+i}', str(i))
            await api.search_knowledge('宇宙')
            assert await scalar(db, 'SELECT COUNT(*) FROM reading_evidence') == before + 30
            assert sum(rows for _, rows, _ in deltas) == 30, deltas
            assert builds.call_count == 0
        print(json.dumps({'group':'bounded_work','chunks':n,'warm_queries':3,
                          'warm_rows':0,'warm_ann_builds':0,'candidate_visits':visits,
                          'seconds':round(seconds,4),'inserted':30,'delta_rows':30}))
    finally:
        await db.close()


async def lifecycle():
    db = await db_module.connect(':memory:')
    other = await db_module.connect(':memory:')
    try:
        store = MemoryStore(db)
        api = facade(store)
        a = await insert(store, 'alpha beta gamma', name='英文')
        b = await insert(store, '宇宙膨胀科学', name='中文')
        null = await insert(store, 'alpha beta 无向量', vector=None)
        assert ids(await api.search_knowledge('alpha beta')) == {a}
        assert ids(await api.search_knowledge('alpha beta gamma delta')) == {a}
        assert ids(await api.search_knowledge('alpha delta epsilon zeta')) == set()
        assert ids(await api.search_knowledge('宇宙膨胀')) == {b}
        assert ids(await api.search_knowledge('完全无关')) == set()
        assert ids(await api.search_knowledge(' ')) == set()
        api.search_facts.return_value = [MemoryFact('f','主体','说明','结论',0,None,None,0,
                                                   scope='knowledge',source_chunk_id=null)]
        assert ids(await api.search_knowledge('alpha beta')) == {a, null}
        api.search_facts.return_value = []
        version = api._chunk_version
        async with db.transaction():
            await store.consume_reading_evidence([a])
        assert ids(await api.search_knowledge('alpha beta')) == {a}
        assert api._chunk_version == version
        # Mutation fields and source migration refresh all derived indexes.
        async with db.transaction():
            await db.conn.execute('UPDATE reading_evidence SET source_topic=?,source_name=?,content=?,content_hash=?,location=? WHERE id=?',
                                  ('local:new','新版','omega epsilon',hashlib.sha256(b'omega epsilon').hexdigest(),'新定位',a))
        changed = await api.search_knowledge('omega epsilon')
        assert ids(changed) == {a}
        assert changed['chunks'][0]['source_topic'] == 'local:new'
        assert changed['chunks'][0]['location'] == '新定位'
        assert ids(await api.search_knowledge('alpha beta')) == set()
        assert await api.record_reading_evidence('local:new','新版','omega epsilon','retry') == a
        # Ordinary Store insert is visible without facade-owned invalidation.
        c = await insert(store, 'delta epsilon')
        assert ids(await api.search_knowledge('delta')) == {c}
        # Resident state survives rollback, including SQLite sequence reuse.
        resident_version = api._chunk_version
        class Rollback(Exception):
            pass
        try:
            async with db.transaction():
                tmp = await insert(store, 'rollbackword')
                await db.conn.execute('DELETE FROM reading_evidence WHERE id=?',(c,))
                assert ids(await api.search_knowledge('rollbackword')) == {tmp}
                assert ids(await api.search_knowledge('delta')) == set()
                assert api._chunk_version == resident_version
                raise Rollback
        except Rollback:
            pass
        assert ids(await api.search_knowledge('rollbackword')) == set()
        assert ids(await api.search_knowledge('delta')) == {c}
        kept = await insert(store, 'keptword')
        assert ids(await api.search_knowledge('keptword')) == {kept}
        assert ids(await api.search_knowledge('rollbackword')) == set()
        # Commit after transient cold view gets discovered by resident sync.
        async with db.transaction():
            committed = await insert(store,'transactionword')
            assert ids(await api.search_knowledge('transactionword')) == {committed}
        assert ids(await api.search_knowledge('transactionword')) == {committed}
        # Synchronization failure invalidates resident state then recovers cold.
        with patch.object(store, 'knowledge_chunk_changes', side_effect=RuntimeError('injected')):
            try:
                await api.search_knowledge('keptword')
            except RuntimeError:
                pass
            else:
                raise AssertionError('synchronization failure swallowed')
        assert api._chunk_indexes is None and api._chunk_version is None
        assert ids(await api.search_knowledge('keptword')) == {kept}
        assert ids(await facade(store).search_knowledge('keptword')) == {kept}
        assert ids(await facade(MemoryStore(other)).search_knowledge('keptword')) == set()
        async with db.transaction():
            await db.conn.execute('DELETE FROM reading_evidence WHERE id=?',(kept,))
        assert ids(await api.search_knowledge('keptword')) == set()
        print('PASS lifecycle: keywords/thresholds/null-link/consume/update/store-write/rollback/commit/failure/facade-isolation/delete')
    finally:
        await db.close()
        await other.close()


async def projection_lifecycle():
    old = Memory('old', 1.0, '', MemoryKind.KNOWLEDGE, '', 1.0,
                 MemoryType.LONG_TERM, embedding=[1.0, 0.0])
    new = Memory('new', 2.0, '', MemoryKind.KNOWLEDGE, '', 1.0,
                 MemoryType.LONG_TERM, embedding=[1.0, 0.0, 0.0])
    index = AnnIndex.build([old])
    index.remove(old.id)
    index.add(new)
    assert [candidate.memory_id for candidate in index.query(new.embedding, 5)] == ['new']

    db = await db_module.connect(':memory:')
    try:
        store = MemoryStore(db)
        api = facade(store, east)
        first = await insert(store, 'source one', source='local:one', vector=(1, 0))
        second = await insert(store, 'source two', source='local:two', vector=(1, 0))
        await api.search_knowledge('source')
        assert set(api._chunk_indexes[2]) == {'local:one', 'local:two'}
        first_planes = api._chunk_indexes[2]['local:one']._planes_by_table
        second_planes = api._chunk_indexes[2]['local:two']._planes_by_table
        assert first_planes is second_planes
        async with db.transaction():
            await db.conn.execute('DELETE FROM reading_evidence WHERE id=?', (first,))
        await api.search_knowledge('source')
        assert 'local:one' not in api._chunk_indexes[2]
        assert 'local:two' in api._chunk_indexes[2]
        async with db.transaction():
            await db.conn.execute('DELETE FROM reading_evidence WHERE id=?', (second,))
        await api.search_knowledge('source')
        assert not api._chunk_indexes[2]
        third = await insert(store, 'different dimension', source='local:three', vector=(1, 0, 0))
        await api.search_knowledge('different')
        assert [candidate.memory_id for candidate in api._chunk_indexes[1].query([1, 0, 0], 5)] == [third]
        print('PASS projection lifecycle: shared planes, source eviction, empty-index dimension reset')
    finally:
        await db.close()


async def dedup_and_budget():
    db = await db_module.connect(':memory:')
    try:
        store = MemoryStore(db)
        api = facade(store, east)
        # More than the global candidate cap in other sources must not hide this source.
        for i in range(205):
            await insert(store, f'other content {i}', source='local:other')
        original = await insert(store, '星系向外运动', source='local:target')
        assert await api.record_reading_evidence('local:target','资料','星系正在向外运动','semantic') == original
        other = await api.record_reading_evidence('local:third','资料','星系向外运动','separate')
        assert other != original
        for left, right in [('数量是1','数量是2'), ('今天发生','明天发生'), ('存在引力','不存在引力')]:
            a = await api.record_reading_evidence('local:guards','资料',left,left)
            b = await api.record_reading_evidence('local:guards','资料',right,right)
            assert a != b, (left,right)
        # Exact lookup uses raw bytes, not whitespace normalization; disable semantic dedup.
        exact = facade(store, embed=None)
        a = await exact.record_reading_evidence('local:raw','资料','line  space','a')
        b = await exact.record_reading_evidence('local:raw','资料','line space','b')
        assert a != b
        assert await exact.record_reading_evidence('local:raw','资料','line  space','replay') == a
        for i in range(6):
            await insert(store, 'budget '+str(i)+'x'*5900, source='local:budget',name='来源'*120,location='位置'*120)
        result = await api.search_knowledge('budget',top_k=99)
        assert len(json.dumps(result['chunks'],ensure_ascii=False)) <= 12000
        assert all(len(item['content'])<=2000 for item in result['chunks'])
        assert len(result['chunks']) <= 5
        print('PASS dedup/budget: source-cap-isolation/exact-raw/replay/semantic/numeric-time-negation/serialized-json')
    finally:
        await db.close()


async def migration_restart_cascade():
    with tempfile.TemporaryDirectory(prefix='nyx-p2-step1-') as folder:
        path = str(Path(folder)/'db.sqlite')
        db = await db_module.connect(path,migrations=[x for x in db_module._MIGRATIONS if x[0]<=43])
        text = '  old\n原文  '
        try:
            await db.conn.execute('INSERT INTO reading_evidence(id,source_topic,source_name,content,created_at) VALUES(?,?,?,?,?)',
                                  ('old','local:legacy','旧数据',text,1))
            await db.conn.commit()
        finally:
            await db.close()
        db = await db_module.connect(path)
        try:
            store = MemoryStore(db)
            assert await scalar(db,'SELECT version FROM schema_version') == db_module._MIGRATIONS[-1][0]
            assert await scalar(db,'SELECT content_hash FROM reading_evidence WHERE id=?',('old',)) == hashlib.sha256(text.encode()).hexdigest()
            assert await store.find_knowledge_chunk('local:legacy',text) == 'old'
            plan = await (await db.conn.execute('EXPLAIN QUERY PLAN SELECT id FROM reading_evidence WHERE source_topic=? AND content_hash=? AND content=? ORDER BY created_at,id LIMIT 1',
                                              ('local:legacy',hashlib.sha256(text.encode()).hexdigest(),text))).fetchall()
            details = ' '.join(row[3] for row in plan)
            assert 'idx_reading_evidence_exact' in details and 'TEMP B-TREE' not in details, details
            await db.conn.execute('INSERT INTO books(id,title,content_hash,created_at,updated_at) VALUES(?,?,?,?,?)',('book','书','hash',1,1))
            await db.conn.commit()
            book_chunk = await insert(store,'cascade text',source='book:book',book_id='book')
            await db.conn.execute("INSERT INTO memory_entity(id,canonical_name,entity_type,aliases,created_at,updated_at) VALUES('s','主体','concept','[]',0,0)")
            await db.conn.execute("INSERT INTO memory_fact(id,subject_entity_id,predicate,object_value,valid_from,created_at,source_chunk_id) VALUES('fact','s','p','o',0,0,?)",(book_chunk,))
            await db.conn.commit()
            api = facade(store)
            assert ids(await api.search_knowledge('cascade text')) == {book_chunk}
            await db.conn.execute('DELETE FROM books WHERE id=?',('book',))
            await db.conn.commit()
            assert ids(await api.search_knowledge('cascade text')) == set()
            assert await scalar(db,"SELECT source_chunk_id FROM memory_fact WHERE id='fact'") is None
            saved = await insert(store,'restartword')
        finally:
            await db.close()
        db = await db_module.connect(path)
        peer = await db_module.connect(path)
        try:
            api = facade(MemoryStore(db))
            assert ids(await api.search_knowledge('restartword')) == {saved}
            extra = await insert(MemoryStore(peer),'externalword')
            assert ids(await api.search_knowledge('externalword')) == {extra}
            await peer.conn.execute('DELETE FROM reading_evidence WHERE id=?',(extra,))
            await peer.conn.commit()
            assert ids(await api.search_knowledge('externalword')) == set()
        finally:
            await db.close()
            await peer.close()
    print(f'PASS migration/restart: schema43-to-{db_module._MIGRATIONS[-1][0]}/raw-hash/indexed-query/cascade/SET-NULL/reopen/cross-connection')


async def lock_order():
    db = await db_module.connect(':memory:')
    try:
        store = MemoryStore(db)
        api = facade(store)
        ready = asyncio.Event()
        go = asyncio.Event()
        async def owner():
            async with db.transaction():
                await insert(store,'lock token')
                ready.set()
                await go.wait()
                assert (await api.search_knowledge('lock token'))['chunks']
        task = asyncio.create_task(owner())
        await ready.wait()
        blocked = asyncio.create_task(api.search_knowledge('lock token'))
        # Let the query acquire chunk lock and block on the transaction's DB lock.
        for _ in range(50):
            if api._chunk_lock.locked():
                break
            await asyncio.sleep(0)
        assert api._chunk_lock.locked()
        go.set()
        await asyncio.wait_for(asyncio.gather(task,blocked),2)
        print('PASS lock-order: transaction-query and concurrent resident sync complete without deadlock')
    finally:
        await db.close()


def ann_compatibility():
    old_source = subprocess.run(['git','show','HEAD:nyx/memory/ann.py'],check=True,capture_output=True,text=True).stdout
    with tempfile.TemporaryDirectory(prefix='nyx-ann-reference-') as folder:
        path = Path(folder)/'reference_ann.py'
        path.write_text(old_source,encoding='utf-8')
        spec = importlib.util.spec_from_file_location('nyx_ann_reference',path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        rng = random.Random(5)
        memories = [Memory(f'{i:04}',rng.random(),'',MemoryKind.KNOWLEDGE,'',1,MemoryType.LONG_TERM,
                           embedding=[rng.uniform(-1,1) for _ in range(4)]) for i in range(450)]
        reference = module.AnnIndex.build(memories,planes=5,tables=3)
        current = AnnIndex.build(memories,planes=5,tables=3)
        for _ in range(30):
            vector = [rng.uniform(-1,1) for _ in range(4)]
            allowed = None if rng.random()>.5 else {m.id for m in memories[::3]}
            for count in (1,5,200):
                a = reference.query(vector,count,allowed_ids=allowed)
                b = current.query(vector,count,allowed_ids=allowed)
                assert [(c.memory_id,c.cosine) for c in a] == [(c.memory_id,c.cosine) for c in b]
        for memory in memories[::2]:
            current.remove(memory.id)
            current.remove(memory.id)
        for memory in memories[::2]:
            current.add(memory)
        vector = [1,0,0,0]
        assert [(c.memory_id,c.cosine) for c in current.query(vector,200)] == [(c.memory_id,c.cosine) for c in reference.query(vector,200)]
    print('PASS shared-ANN: 90 reference comparisons plus incremental removal/reinsert')


async def main():
    ann_compatibility()
    await performance(1000)
    await performance(10000)
    await lifecycle()
    await projection_lifecycle()
    await dedup_and_budget()
    await migration_restart_cascade()
    await lock_order()
    print('ALL STEP-1 ACCEPTANCE GROUPS PASSED')


asyncio.run(main())
