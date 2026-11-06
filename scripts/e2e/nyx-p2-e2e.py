import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import uuid
from collections import defaultdict
from unittest.mock import patch

import httpx
import uvicorn

sys.path.insert(0, str(Path.cwd()))
from nyx.config import Config
from nyx.db import connect, _MIGRATIONS, migrate
from nyx.main import build_app, build_app_context
from nyx.memory.store import MemoryStore
from nyx.memory.facts import MemoryFactStore, FactCandidate
from nyx.types import LLMOutput

ROWS = []
def passed(name):
    ROWS.append(name)
    print('PASS ' + name, flush=True)

class Fake:
    def __init__(self):
        self.calls = []
        self.rounds = defaultdict(int)
        self.fail_digest = False
        self.delay = False
    async def complete(self, messages, *, module, output_type, correlation_id, **kwargs):
        self.calls.append((output_type, messages))
        calls = []
        if output_type == 'reason':
            content = {'summary': '核对检索结果。', 'next_action': 'answer' if '[工作摘要' in messages[-1]['content'] else 'tools'}
        elif output_type == 'source_digest':
            if self.fail_digest:
                raise ValueError('injected digest failure')
            text = json.loads(messages[-1]['content'])['source_text']
            content = {'summary': text[:100], 'themes': ['P2Nebula'], 'content_category': 'nonfiction',
                       'points': [{'topic': 'P2Nebula', 'content': text[:300]}]}
        elif output_type == 'fact_extraction':
            content = {'entities': [], 'facts': [{'subject': 'P2Nebula', 'predicate': '性质',
                'object': '正在膨胀', 'mode': 'multi'}]}
        elif output_type == 'tool':
            self.rounds[correlation_id] += 1
            if self.rounds[correlation_id] == 1:
                calls = [{'name': 'knowledge_search', 'args': {'query': 'P2Nebula'}}]
            content = {}
        elif output_type == 'reply':
            source = str(messages)
            assert 'P2Nebula' in source and 'source_chunk_id' in source and 'chunk_id' in source, source
            content = {'think': '根据原文回答', 'speak': 'P2Nebula 正在膨胀，来源：手动文本。', 'needs_continuation': False}
        else:
            content = {'content':'E2E','summary':'E2E','topics':[],'importance':'normal', 'entities':[], 'facts':[]}
        return LLMOutput(module=module,type=output_type,model='scripted-p2',
            content=json.dumps(content,ensure_ascii=False),correlation_id=correlation_id,
            tool_calls=calls,call_id=str(uuid.uuid4()),prompt_messages=messages)

async def embed(text):
    if 'P2Nebula' in text:
        return [1.0, 0.0, 0.0]
    if 'unrelated' in text:
        return [0.0, 1.0, 0.0]
    return [0.0, 0.0, 1.0]

async def migration():
    with tempfile.TemporaryDirectory(prefix='nyx-p2-') as folder:
        db = await connect(str(Path(folder)/'old.db'), migrations=_MIGRATIONS[:-1])
        try:
            await db.conn.execute("INSERT INTO reading_evidence (id,source_topic,source_name,content,created_at) VALUES ('old-chunk','web:old','old','old text',1)")
            await db.conn.commit()
            before = await (await db.conn.execute('SELECT * FROM reading_evidence')).fetchall()
            await migrate(db.conn)
            after = await (await db.conn.execute('SELECT * FROM reading_evidence')).fetchone()
            assert after['content'] == before[0]['content'] and after['embedding'] is None and after['location'] is None
            facts = MemoryFactStore(db)
            await facts.apply([FactCandidate(subject='migration',predicate='value',object_value='kept',valid_from=1,valid_until=None)],None,scope='knowledge',source_chunk_id='old-chunk')
            async with db.transaction():
                await db.conn.execute("DELETE FROM reading_evidence WHERE id='old-chunk'")
            saved = await facts.recent(scope='knowledge')
            assert len(saved)==1 and saved[0].source_chunk_id is None
            passed('migration42_to43_preserves_source_and_fk_set_null')
        finally:
            await db.conn.close()

async def main():
    await migration()
    os.environ['NYX_DB']=':memory:'
    os.environ.pop('NYX_LAUNCH_NONCE',None)
    fake = Fake()
    config = Config()
    config.exploration.web_enabled=True
    config.expression.slow_max_rounds=1
    with patch('nyx.app_context.LlmClient.from_config',return_value=fake),patch('nyx.app_context.build_embed',return_value=embed):
        app=await build_app_context(config)
    memory=app.memory
    store=MemoryStore(app.database)
    bus_task=asyncio.create_task(app.bus.run())
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(64)
    server=uvicorn.Server(uvicorn.Config(build_app(app),log_level='error',lifespan='off'))
    server_task=asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{sock.getsockname()[1]}',headers={'Host':'127.0.0.1:8000'},timeout=30) as client:
            for _ in range(100):
                if server.started:break
                await asyncio.sleep(.01)
            source='P2Nebula 的空间尺度正在膨胀，原始观测记录。'
            first=await client.post('/api/knowledge/sources',json={'text':source})
            assert first.status_code==200,first.text
            body=first.json();assert body['created_memories']==1 and body['created_facts']==1,body
            chunk_id=body['chunks'][0]['chunk_id']
            facts=await memory.search_facts('P2Nebula',scope='knowledge')
            assert facts[0].source_chunk_id==chunk_id
            repeat=(await client.post('/api/knowledge/sources',json={'text':source})).json()
            assert repeat['created_memories']==repeat['created_facts']==0 and repeat['chunks'][0]['chunk_id']==chunk_id,repeat
            passed('http_text_actual_counts_provenance_idempotent_retry')
            async with client.stream('GET','/api/events') as stream:
                events=asyncio.Queue()
                async def consume():
                    kind=''
                    async for line in stream.aiter_lines():
                        if line.startswith('event: '):kind=line[7:]
                        elif line.startswith('data: '):await events.put((kind,json.loads(line[6:])))
                reader=asyncio.create_task(consume())
                response=await client.post('/api/chat',json={'message':'P2Nebula 为什么膨胀','discussion':True})
                cid=response.json()['event_id']
                async def speak():
                    while True:
                        kind,event=await events.get()
                        if kind=='speak' and event['correlation_id']==cid:return event
                result=await asyncio.wait_for(speak(),15)
                assert '手动文本' in result['content']
                assert any(chunk_id in str(messages) for typ,messages in fake.calls if typ=='reply')
                reader.cancel()
                await asyncio.gather(reader,return_exceptions=True)
            passed('http_manual_ingestion_tool_search_sse_source_reply')
            for payload in ({},{'text':'  '},{'url':'  '},{'text':5}):
                assert (await client.post('/api/knowledge/sources',json=payload)).status_code==422
            config.exploration.web_enabled=False
            assert (await client.post('/api/knowledge/sources',json={'url':'https://example.com'})).status_code==409
            partial=(await client.post('/api/knowledge/sources',json={'text':source,'url':'https://example.com'})).json()
            assert partial['errors'] and partial['chunks'] and partial['created_memories']==0
            config.exploration.web_enabled=True
            malformed=await client.post('/api/knowledge/sources',json={'url':'http://['})
            assert malformed.status_code==400,malformed.text
            malformed_partial=await client.post('/api/knowledge/sources',json={'text':source,'url':'http://['})
            assert malformed_partial.status_code==200 and malformed_partial.json()['chunks'] and malformed_partial.json()['errors'],malformed_partial.text
            for bad_url in ('https://a..example.com', 'https://' + 'a'*64 + '.example.com'):
                invalid = await client.post('/api/knowledge/sources',json={'text':source,'url':bad_url})
                assert invalid.status_code==200 and invalid.json()['chunks'] and invalid.json()['errors'],invalid.text
            assert (await client.post('/api/knowledge/sources',json={'url':'http://127.0.0.1/private'})).status_code==400
            with patch('nyx.tools.web_fetch.fetch_url',return_value=''):
                assert (await client.post('/api/knowledge/sources',json={'url':'https://empty.example'})).status_code==400
            with patch('nyx.tools.web_fetch.fetch_url',return_value='P2Nebula webpage full original 25 years'):
                n=sum(t=='source_digest' for t,m in fake.calls)
                both=await client.post('/api/knowledge/sources',json={'text':source,'url':'https://page.example/article'})
                assert both.status_code==200 and len(both.json()['chunks'])==2,both.text
                assert sum(t=='source_digest' for t,m in fake.calls)==n+2
                n=sum(t=='source_digest' for t,m in fake.calls)
                fetched=await app.activity._tools.call('web_fetch',{'url':'https://tool.example/article'})
                assert fetched['ingestion']['chunks'] and sum(t=='source_digest' for t,m in fake.calls)==n+1,fetched
            passed('http_blank_disabled_unsafe_empty_both_and_fetch_ingests_once')
            fake.fail_digest=True
            failed=await client.post('/api/knowledge/sources',json={'text':'P2Nebula failed source 99 meters'})
            assert failed.status_code==200 and failed.json()['errors'] and failed.json()['chunks'],failed.text
            assert failed.json()['created_memories']==failed.json()['created_facts']==0
            fake.fail_digest=False
            retried=(await client.post('/api/knowledge/sources',json={'text':'P2Nebula failed source 99 meters'})).json()
            assert retried['chunks'][0]['chunk_id']==failed.json()['chunks'][0]['chunk_id'] and retried['created_memories']==1,retried
            passed('http_digest_failure_retains_raw_chunk_then_retry')
            async with app.database.transaction():
                await memory.consume_reading_evidence([chunk_id])
            found=await memory.search_knowledge('P2Nebula',5)
            assert chunk_id in [c['chunk_id'] for c in found['chunks']]
            assert all(len(c['content'])<=2000 for c in found['chunks'])
            assert (await memory.search_knowledge('unrelated_unknown_abcdef'))=={'facts':[],'chunks':[]}
            null_id=await store.record_reading_evidence('local:null','null','NULL vector rareword', '1')
            assert null_id not in [c['chunk_id'] for c in (await memory.search_knowledge('rareword'))['chunks']]
            fs=MemoryFactStore(app.database)
            await fs.apply([FactCandidate(subject='rareword',predicate='value',object_value='linked',valid_from=time.time(),valid_until=None)],None,scope='knowledge',source_chunk_id=null_id)
            assert null_id in [c['chunk_id'] for c in (await memory.search_knowledge('rareword'))['chunks']]
            passed('consumed_search_null_vector_fact_link_and_no_latest_fallback')
            a=await memory.record_reading_evidence('web:dedup','same','P2Nebula expands at 10 meters','a')
            b=await memory.record_reading_evidence('web:dedup','same','P2Nebula is expanding at 10 meters','b')
            c=await memory.record_reading_evidence('web:other','same','P2Nebula expands at 10 meters','c')
            d=await memory.record_reading_evidence('web:dedup','same','P2Nebula expands at 20 meters','d')
            e=await memory.record_reading_evidence('web:dedup','same','P2Nebula does not expand at 10 meters','e')
            assert a==b and c!=a and d!=a and e!=a,(a,b,c,d,e)
            replay=await asyncio.gather(*[memory.record_reading_evidence('web:concurrent','race','P2Nebula race 7','x') for _ in range(4)])
            assert len(set(replay))==1
            passed('source_local_exact_semantic_concurrent_dedup_number_polarity_guard')
            for i in range(6):
                await store.record_reading_evidence('web:large'+str(i),'P2Nebula'+str(i)+'x'*2100,'P2Nebula '+str(i)+'q'*5990,str(i),location='https://large.example/'+'x'*1800,embedding=[1.,0.,0.])
            bounded=await memory.search_knowledge('P2Nebula')
            assert len(json.dumps(bounded['chunks'],ensure_ascii=False))<=12000,len(json.dumps(bounded['chunks'],ensure_ascii=False))
            passed('serialized_chunk_block_12000_budget')
    finally:
        server.should_exit=True
        await server_task
        await app.bus.stop()
        await bus_task
        await app.database.conn.close()
    Path(tempfile.gettempdir(),'nyx-p2-e2e-results.json').write_text(json.dumps(ROWS,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'{len(ROWS)} P2 scenarios passed',flush=True)

if __name__=='__main__':asyncio.run(main())
