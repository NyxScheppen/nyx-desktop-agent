import asyncio, importlib.util, json, os, sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
sys.path.insert(0,str(Path.cwd()))
spec=importlib.util.spec_from_file_location('acceptance',Path(__file__).with_name('nyx-p3-acceptance.py'))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
from nyx.config import Config
from nyx.main import build_app_context
from nyx.memory.store import MemoryStore
from research_support import run_research

async def main(mode):
    if mode == 'all':
        for selected in ('created_ids', 'timeout', 'eval'):
            await main(selected)
        return
    os.environ['NYX_DB']=':memory:'
    llm=a.Fake();config=Config();config.exploration.web_enabled=True
    with patch('nyx.app_context.LlmClient.from_config',return_value=llm),patch('nyx.app_context.build_embed',return_value=None): app=await build_app_context(config)
    bus=asyncio.create_task(app.bus.run());store=MemoryStore(app.database)
    try:
        if mode=='created_ids':
            async def embed(text):return [1.0,0.0]
            app.memory._embed=embed
            ids={'sentinel'}
            cid=await app.memory.record_reading_evidence('web:probe','probe','恒星通过核聚变释放能量。','0',created_ids=ids)
            assert ids=={'sentinel',cid},ids
            exact=set();hit=await app.memory.record_reading_evidence('web:probe','probe','恒星通过核聚变释放能量。','1',created_ids=exact)
            semantic=set();similar=await app.memory.record_reading_evidence('web:probe','probe','恒星通过核聚变持续释放能量。','2',created_ids=semantic)
            assert hit==similar==cid and not exact and not semantic,(hit,similar,cid,exact,semantic)
            other=set();cross=await app.memory.record_reading_evidence('web:other','probe','恒星通过核聚变释放能量。','0',created_ids=other)
            assert cross!=cid and other=={cross}
            provisional=set()
            try:
                async with app.database.transaction():
                    rolled=await store.record_reading_evidence('web:rollback','probe','回滚正文','0',created_ids=provisional)
                    assert provisional=={rolled}
                    raise RuntimeError('rollback')
            except RuntimeError: pass
            assert not await store.list_knowledge_chunks(source_topic='web:rollback')
            provisional.clear()
            failed=set()
            with patch.object(app.database.conn,'commit',new=AsyncMock(side_effect=RuntimeError('commit failed'))):
                try: await store.record_reading_evidence('web:failed','probe','提交失败','0',created_ids=failed)
                except RuntimeError:pass
            assert not failed
            await app.database.conn.rollback()
            assert not await store.list_knowledge_chunks(source_topic='web:failed')
            # Force INSERT ON CONFLICT rather than SELECT's exact-hit early return.
            await app.database.conn.execute('UPDATE reading_evidence SET content_hash=? WHERE id=?',('invalid',cid))
            await app.database.conn.commit()
            ignored=set();same=await store.record_reading_evidence('web:probe','probe','恒星通过核聚变释放能量。','0',created_ids=ignored)
            assert same==cid and not ignored
            print('PASS actual insertion, exact/semantic hit, source isolation, ignored insert, failed commit and caller rollback')
        else:
            async def search(query):return [{'url':a.A}]
            async def fetch(url):return {'text':a.TEXT[url],'url':url}
            if mode=='timeout':llm.pause='source_digest'
            result=await run_research('P3Nebula',search=search,fetch=fetch,llm=llm,memory=app.memory,evaluator=app.evaluator,budget={'seconds':.4} if mode=='timeout' else None)
            if mode=='timeout':
                chunks=await store.list_knowledge_chunks()
                print('persisted_chunks=',len(chunks),'receipts=',result['ingested'],flush=True)
                assert chunks and result.get('error') and not result['report'],result
                pending=result['checkpoint']['source_pending'];assert pending['source_chunk_id'] in {x.id for x in chunks}
            else:
                records=await app.eval_store.list_recent(100)
                stages={typ for typ,_,_ in llm.calls if typ.startswith('exploration_')}
                recorded={r.output_type for r in records if r.module=='activity'}
                print('research stages=',sorted(stages),'recorded=',sorted(recorded),flush=True)
                assert stages==recorded,(stages,recorded)
    finally:
        bus.cancel();await asyncio.gather(bus,return_exceptions=True);await app.database.close()

asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else 'all'))
