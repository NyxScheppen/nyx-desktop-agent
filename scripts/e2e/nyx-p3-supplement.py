"""Real composition root, durable recovery and accounting for shared research."""
import asyncio, importlib.util, json, os, sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch
sys.path.insert(0,str(Path.cwd()))
spec=importlib.util.spec_from_file_location('acceptance',Path(__file__).with_name('nyx-p3-acceptance.py'))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
from nyx.config import Config
from nyx.main import build_app_context
from nyx.memory.store import MemoryStore
from nyx.enums import EventType, MemoryKind

class Script(a.Fake):
    def __init__(self,stage=None):
        super().__init__();self.stage=stage;self.reached=asyncio.Event()
    async def complete(self,messages,**kwargs):
        if kwargs['output_type']==self.stage:
            self.reached.set();await asyncio.Event().wait()
        return await super().complete(messages,**kwargs)

@asynccontextmanager
async def context(llm):
    os.environ['NYX_DB']=':memory:'
    config=Config();config.exploration.web_enabled=True
    with patch('nyx.app_context.LlmClient.from_config',return_value=llm),patch('nyx.app_context.build_embed',return_value=None):app=await build_app_context(config)
    try:yield app,MemoryStore(app.database)
    finally:await app.database.close()

async def main():
    for stage in ('source_digest','fact_extraction'):
        llm=Script(stage)
        async with context(llm) as (app,store):
            assert app.research and app.behavior
            with patch('nyx.tools.web_search._search_web_sync',side_effect=lambda q:[{'url':a.B if '校准争议' in q else a.A}]),patch('nyx.tools.web_fetch.fetch_url',side_effect=lambda url:a.TEXT[url]):
                task=asyncio.create_task(app.research.run('resume','P3Nebula','dialogue','resume'))
                await asyncio.wait_for(llm.reached.wait(),10);task.cancel()
                try:await task;raise AssertionError('cancel swallowed')
                except asyncio.CancelledError:pass
                rows=await store.list_knowledge_chunks();assert len(rows)==1,rows
                assert not await app.behavior.store.list_records('behavior_reflection')
                llm.stage=None
                result=await app.research.resume('resume');assert result['status']=='completed' and result['report'],result
                assert len(await store.list_knowledge_chunks())==2
                memories=await store.list_memories(kind=MemoryKind.KNOWLEDGE);assert len(memories)==2
                replay=await app.research.resume('resume');assert replay==result
                event=(await app.bus.list_events(event_type=EventType.BEHAVIOR_REFLECTION))[0]
                await app.behavior.apply_event(event,app.memory)
                await app.behavior.apply_event(event,app.memory)
                records=await app.eval_store.list_recent(100)
                stages={t for t,_,_ in llm.calls if t.startswith('exploration_') or t=='behavior_reflection'}
                assert stages<={r.output_type for r in records}
                for record in records:
                    assert record.call_id and (record.prompt_tokens,record.completion_tokens)==(11,7)
                    found,prompt=await app.eval_store.get_prompt(record.id);assert found and prompt
                assert len(await app.behavior.skills.history('discussion'))==1
                assert len(await store.list_memories(kind=MemoryKind.LESSON))==1
    print('PASS assembled graph pauses/resumes digest+facts, reuses receipts, reflects once and accounts every model call',flush=True)
    llm=Script();llm.mode='bad'
    async with context(llm) as (app,store):
        assert app.research
        result=await app.research.run('invalid','invalid','dialogue','invalid')
        assert result['status']=='failed' and not await store.list_knowledge_chunks()
        assert any(r.output_type=='exploration_decide' for r in await app.eval_store.list_recent(100))
    llm=Script()
    async with context(llm) as (app,store):
        assert app.research
        with patch('nyx.tools.web_search._search_web_sync',side_effect=lambda q:[{'url':a.B if '校准争议' in q else a.A}]),patch('nyx.tools.web_fetch.fetch_url',side_effect=lambda url:a.TEXT[url]),patch.object(app.eval_store,'insert',new=AsyncMock(side_effect=RuntimeError('eval failure'))):
            result=await app.research.run('eval','P3Nebula','dialogue','eval')
        assert result['status']=='completed' and result['report'],result
    print('PASS invalid raw output is logged; accounting failure cannot corrupt valid research',flush=True)

if __name__=='__main__':asyncio.run(main())
