import asyncio, importlib.util, json, logging, os, sys, time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch
sys.path.insert(0, str(Path.cwd()))
spec=importlib.util.spec_from_file_location('acceptance',Path(__file__).with_name('nyx-p3-acceptance.py'))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
from nyx.config import Config
from nyx.main import build_app_context
from nyx.memory.facade import build_source_topic
from nyx.memory.store import MemoryStore
from nyx.research import run_research, build_deep_research_tool
logging.disable(logging.CRITICAL)

class Script(a.Fake):
    def __init__(self, stage=None, conclusion=False, malformed=False):
        super().__init__();self.stage=stage;self.conclusion=conclusion;self.malformed=malformed;self.outputs=[]
    async def complete(self,messages,**kwargs):
        typ=kwargs['output_type']
        if typ==self.stage and (not self.conclusion or 'sections' in messages[-1]['content']):
            await asyncio.Event().wait()
        out=await super().complete(messages,**kwargs)
        if self.malformed and typ=='research_plan':out.content='{broken'
        self.outputs.append(out)
        return out

@asynccontextmanager
async def context(llm):
    os.environ['NYX_DB']=':memory:'
    config=Config();config.exploration.web_enabled=True
    captured=[]
    def factory(**kwargs):
        assert kwargs['evaluator'] is not None
        tool=build_deep_research_tool(**kwargs);captured.append(tool);return tool
    with patch('nyx.app_context.LlmClient.from_config',return_value=llm),patch('nyx.app_context.build_embed',return_value=None),patch('nyx.plugins.build_deep_research_tool',side_effect=factory):app=await build_app_context(config)
    app._p3_tool=captured[0]
    task=asyncio.create_task(app.bus.run())
    try:yield app,MemoryStore(app.database)
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True);await app.database.close()

async def search(query):return [{'url':a.A},{'url':a.B}]
async def fetch(url):return {'text':a.TEXT[url],'url':url}
def args(app,llm):return dict(search=search,fetch=fetch,llm=llm,ingest=app.memory.ingest_source,evaluator=app.evaluator)

async def main():
    for stage,conclusion in [('source_digest',False),('fact_extraction',False),('source_digest',True),('fact_extraction',True)]:
        llm=Script(stage,conclusion)
        async with context(llm) as (app,store):
            started=time.monotonic();r=await run_research('P3Nebula中断',**args(app,llm),budget={'seconds':.4})
            rows=await store.list_knowledge_chunks();expected={x.id for x in rows}
            assert expected and expected=={x['chunk_id'] for x in r['ingested']},(stage,conclusion,r,rows)
            assert all(x['dedup']=='new' for x in r['ingested']) and '回执可能不完整' in r['notice'] and time.monotonic()-started<1.2,r
            llm.stage=None
            again=await run_research('P3Nebula中断',**args(app,llm));old=[x for x in again['ingested'] if x['chunk_id'] in expected]
            assert len(old)==len(expected) and all(x['dedup']=='hit' for x in old),again
    print('PASS committed receipts survive source/conclusion digest and fact cancellation; retries find hits',flush=True)

    llm=Script()
    async with context(llm) as (app,store):
        original=app.database.conn.commit;stalled=False
        async def commit():
            nonlocal stalled
            await original()
            cursor=await app.database.conn.execute('SELECT count(*) FROM reading_evidence')
            if (await cursor.fetchone())[0] and not stalled:
                stalled=True;await asyncio.Event().wait()
        with patch.object(app.database.conn,'commit',new=commit):
            started=time.monotonic();r=await run_research('P3Nebula确认窗口',**args(app,llm),budget={'seconds':.4})
        rows=await store.list_knowledge_chunks()
        assert stalled and len(rows)==1 and not r['ingested'] and '回执可能不完整' in r['notice'] and time.monotonic()-started<1.2,(rows,r)
        assert len(await store.list_knowledge_chunks(source_topic=build_source_topic('web',a.A)))==1
        again=await run_research('P3Nebula确认窗口',**args(app,llm))
        assert next(x for x in again['ingested'] if x['chunk_id']==rows[0].id)['dedup']=='hit',again
    print('PASS actual SQLite commit before cancelled confirmation: explicit incomplete notice, no fabricated receipt, retry discovers hit',flush=True)

    llm=Script()
    async with context(llm) as (app,store):
        sentinel={'chunk_id':'preexisting','source_topic':'local:old','dedup':'hit'};progress=[sentinel.copy()]
        for _ in range(2):
            await app.memory.ingest_source(a.TEXT[a.A],'local:acc','acc','cid',committed_chunks=progress)
        assert len(progress)==2 and progress[0]==sentinel and progress[1]['dedup']=='new',progress
        rolled=[]
        # Keep this probe at the raw chunk stage; no eval transaction can nest here.
        with patch.object(app.memory,'digest_source_block',new=AsyncMock(return_value=({},[]))),patch.object(app.memory,'remember_knowledge',new=AsyncMock(return_value={})):
            try:
                async with app.database.transaction():
                    await app.memory.ingest_source('rollback','local:roll','roll','cid',committed_chunks=rolled)
                    assert len(rolled)==1
                    raise RuntimeError('rollback')
            except RuntimeError:pass
        assert not await store.list_knowledge_chunks(source_topic='local:roll');rolled.clear()
        failed=[]
        with patch.object(app.database.conn,'commit',new=AsyncMock(side_effect=RuntimeError('failed commit'))):
            await app.memory.ingest_source('failed','local:fail','fail','cid',committed_chunks=failed)
        assert not failed
        await app.database.conn.rollback()
        assert not await store.list_knowledge_chunks(source_topic='local:fail')
    print('PASS progress preserves prior entries/deduplicates IDs; failed commit and outer rollback boundaries',flush=True)

    llm=Script()
    async with context(llm) as (app,store):
        # Exercise the actual composition-root tool; no evaluator passed by this driver.
        with patch('nyx.tools.web_search._search_web_sync',side_effect=lambda q:[{'url':a.A},{'url':a.B}]),patch('nyx.tools.web_fetch.fetch_url',side_effect=lambda u:a.TEXT[u]):
            result=await app._p3_tool.handler(query='P3Nebula记账')
        assert result['report'],result
        records=await app.eval_store.list_recent(100)
        stages={o.type for o in llm.outputs if o.module=='research'}
        assert stages=={'research_'+s for s in ['plan','deepen','evidence','synthesis','compress']}
        research=[r for r in records if r.module=='research'];assert {r.output_type for r in research}==stages
        for r in research:
            output=next(o for o in llm.outputs if o.call_id==r.call_id)
            assert (r.prompt_tokens,r.completion_tokens)==(11,7)
            assert await app.eval_store.get_prompt(r.id)==(True,output.prompt_messages)
        stats=await app.eval_store.total_tokens();unique={r.call_id:r for r in records}
        assert stats.total_tokens==sum(r.prompt_tokens+r.completion_tokens for r in unique.values()),(stats,len(unique))
        assert sum(r.prompt_tokens+r.completion_tokens for r in research)==18*sum(o.module=='research' for o in llm.outputs)
    print('PASS production injection, five research stages, tokens, call IDs and full prompt snapshots',flush=True)

    llm=Script(malformed=True)
    async with context(llm) as (app,store):
        r=await run_research('malformed',**args(app,llm))
        records=await app.eval_store.list_recent(100)
        assert any(x.output_type=='research_plan' for x in records) and '模型生成失败' in r['notice']
    for failure in ['store','evaluator']:
        llm=Script()
        async with context(llm) as (app,store):
            target=app.eval_store if failure=='store' else app.evaluator
            attr='insert' if failure=='store' else 'evaluate'
            with patch.object(target,attr,new=AsyncMock(side_effect=RuntimeError('eval failure'))):
                r=await run_research('P3Nebula失败隔离',**args(app,llm))
            assert r['report'] and '模型生成失败' not in r['notice'],(failure,r)
    print('PASS malformed JSON recorded before parsing; eval store/evaluator failures preserve valid research',flush=True)

    llm=Script()
    async with context(llm) as (app,store):
        reached=asyncio.Event()
        async def pause(output):reached.set();await asyncio.Event().wait()
        with patch.object(app.evaluator,'evaluate',new=pause):
            started=time.monotonic();r=await run_research('eval timeout',**args(app,llm),budget={'seconds':.03})
            assert reached.is_set() and '超时' in r['notice'] and time.monotonic()-started<.5
            reached.clear();task=asyncio.create_task(run_research('eval cancel',**args(app,llm)))
            await asyncio.wait_for(reached.wait(),1);task.cancel()
            try:await task;raise AssertionError('cancel swallowed')
            except asyncio.CancelledError:pass
    print('PASS eval awaits share research deadline and external cancellation propagates',flush=True)

asyncio.run(main())
