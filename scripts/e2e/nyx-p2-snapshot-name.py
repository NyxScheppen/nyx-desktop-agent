import asyncio
import json
from pathlib import Path
import runpy
from unittest.mock import patch

ns = runpy.run_path(str(Path(__file__).with_name('nyx-p2-e2e.py')))

async def check():
    ns['os'].environ['NYX_DB'] = ':memory:'
    fake = ns['Fake']()
    with patch('nyx.app_context.LlmClient.from_config', return_value=fake), patch('nyx.app_context.build_embed', return_value=ns['embed']):
        app = await ns['build_app_context'](ns['Config']())
    try:
        raw = 'Immutable source observations.'
        chunk_id = await app.memory.record_reading_evidence('web:snapshot-name', 'original archive', raw, 'old')
        result = await app.memory.ingest_source(raw, 'web:snapshot-name', 'changed label', 'retry')
        payload = json.loads(next(messages[-1]['content'] for typ, messages in fake.calls if typ == 'source_digest'))
        assert payload['source']['name'] == 'original archive', payload
        assert payload['source_text'] == raw and result['chunks'][0]['chunk_id'] == chunk_id, result
        saved = await app.memory.list_memories()
        assert saved and all('changed label' not in m.content for m in saved), saved
        print('PASS ingest replay retains saved source name, text, and chunk provenance', flush=True)
    finally:
        await app.database.conn.close()

asyncio.run(check())
