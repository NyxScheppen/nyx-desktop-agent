import asyncio
from pathlib import Path
import runpy
from unittest.mock import patch

ns = runpy.run_path(str(Path(__file__).with_name('nyx-p2-e2e.py')))

async def check():
    ns['os'].environ['NYX_DB'] = ':memory:'
    with patch('nyx.app_context.LlmClient.from_config', return_value=ns['Fake']()), patch('nyx.app_context.build_embed', return_value=ns['embed']):
        app = await ns['build_app_context'](ns['Config']())
    try:
        store = ns['MemoryStore'](app.database)
        chunk_id = await store.record_reading_evidence('local:keyword-gate', 'archive', 'quasar observations only', 'gate', embedding=[1., 0., 0.])
        low = await app.memory.search_knowledge('quasar basalt glacier orchard')
        assert low == {'facts': [], 'chunks': []}, ('low keyword coverage must not return evidence', low)
        positive = await app.memory.search_knowledge('quasar basalt')
        assert [c['chunk_id'] for c in positive['chunks']] == [chunk_id], positive
        vector = await app.memory.search_knowledge('P2Nebula')
        assert chunk_id in [c['chunk_id'] for c in vector['chunks']], vector
        print('PASS keyword .25 excluded; keyword .5 and cosine 1 accepted', flush=True)
    finally:
        await app.database.conn.close()

asyncio.run(check())
