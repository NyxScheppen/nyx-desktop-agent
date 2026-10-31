import asyncio, json, sys, time
from pathlib import Path
from unittest.mock import AsyncMock, patch
sys.path.insert(0, str(Path.cwd()))
from nyx.config import MemoryConfig
from nyx.db import connect
from nyx.memory.store import MemoryStore
from nyx.memory.facade import MemoryFacade
from nyx.memory.ann import AnnIndex

async def embed(text):
    return [1.0, 0.0]

def facade(store):
    result = MemoryFacade(store, None, None, None, None, MemoryConfig(), embed=embed)
    result.search_facts = AsyncMock(return_value=[])
    return result

async def main(n):
    db = await connect(':memory:')
    try:
        store = MemoryStore(db)
        await db.conn.executemany(
            'INSERT INTO reading_evidence(id,source_topic,source_name,content,created_at,embedding) VALUES(?,?,?,?,?,?)',
            [(f'{i:08d}', 'local:one', '宇宙科学', f'宇宙在膨胀，编号{i}', i, '[1,0]') for i in range(n)])
        await db.conn.commit()
        api = facade(store)
        loaded = []
        original = store.list_knowledge_chunks
        async def counted(**kwargs):
            rows = await original(**kwargs)
            loaded.append(len(rows))
            return rows
        store.list_knowledge_chunks = counted
        with patch.object(AnnIndex, 'build', wraps=AnnIndex.build) as builds:
            assert (await api.search_knowledge('宇宙'))['chunks']
            loaded.clear()
            builds.reset_mock()
            start = time.perf_counter()
            for _ in range(3):
                assert (await api.search_knowledge('宇宙'))['chunks']
            print(json.dumps({'n':n,'warm_loaded':loaded,'warm_builds':builds.call_count,'seconds':round(time.perf_counter()-start,4)}))
            assert sum(loaded) == 0, 'warm queries reloaded the full evidence corpus'
            assert builds.call_count == 0, 'warm queries rebuilt ANN'
            loaded.clear()
            for i in range(30):
                await api.record_reading_evidence('local:one', '宇宙科学', f'新观测编号{n+i}', str(i))
            await api.search_knowledge('宇宙')
            assert sum(loaded) <= 30, 'incremental writes reloaded previous chunks'
            assert builds.call_count <= 1, 'incremental writes rebuilt ANN'
            print('PASS warm retrieval and incremental insertion')
    finally:
        await db.close()

asyncio.run(main(int(sys.argv[1]) if len(sys.argv)>1 else 1000))
