import asyncio, importlib.util, json, os, tempfile, uuid
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path.cwd()))
spec=importlib.util.spec_from_file_location('p2base',Path(__file__).with_name('nyx-p2-e2e.py'))
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
from nyx.main import build_app_context
from nyx.config import Config
from nyx.memory.facade import build_source_topic
from nyx.memory.store import MemoryStore
from nyx.reading.segmenter import Segment
from nyx.enums import ActivityType, ActivityStatus, AssignedTaskType, AssignedTaskStatus
from nyx.types import Activity, AssignedTask, LLMOutput

class Fake(base.Fake):
    async def complete(self,messages,*,module,output_type,correlation_id,**kwargs):
        if output_type=='fact_extraction':
            return LLMOutput(module=module,type=output_type,model='p2-recovery',correlation_id=correlation_id,
                content=json.dumps({'facts':[{'subject': 'Source'+correlation_id,'predicate':'value','object':'stored snapshot','mode':'multi'}]}))
        return await super().complete(messages,module=module,output_type=output_type,correlation_id=correlation_id,**kwargs)

async def fails(call):
    try: await call()
    except (RuntimeError,ValueError): return
    raise AssertionError('expected injected failure')

async def main():
    os.environ['NYX_DB']=':memory:'
    fake=Fake();config=Config();config.exploration.web_enabled=True
    with patch('nyx.app_context.LlmClient.from_config',return_value=fake),patch('nyx.app_context.build_embed',return_value=base.embed):
        app=await build_app_context(config)
    memory=app.memory;store=MemoryStore(app.database);activity_store=app.activity._store;rs=app.reading._store
    results=[]
    try:
        for kind in ('epub','web','exploration'):
            original='P2Nebula '+kind+' atmosphere appears blue.'
            incoming=original+' A volcano erupted.'
            if kind=='epub':
                book,_=await rs.insert_book_with_paragraphs('P2Book','A','p2.epub',uuid.uuid4().hex,[Segment(text=incoming,is_chapter_start=True)])
                topic=build_source_topic('book',book.id);cid=book.id
                async def run(): await app.reading._integration.sediment(book.id,1,flush=True)
                async def state(): return await rs.get_memory_state(book.id)
                target=rs;method='update_memory_state';orig_save=rs.update_memory_state
                def final(args): return args[1].get('pending') is None and args[1].get('cursor',{}).get('paragraph_index')==2
            elif kind=='web':
                url='https://recovery.example/'+kind
                task=await activity_store.create_task(AssignedTask(str(uuid.uuid4()),AssignedTaskType.WEB,AssignedTaskStatus.PENDING,url,None,None,{},None,1,1))
                cid=task.id;topic=build_source_topic('web',url)
                async def run():
                    current=await activity_store.get_task(task.id)
                    await app.activity._run_web_task(current)
                async def state(): return (await activity_store.get_task(task.id)).checkpoint
                target=activity_store;method='save_task';orig_save=activity_store.save_task
                def final(args): return args[0].checkpoint.get('pending') is None and args[0].checkpoint.get('cursor',0)>0
            else:
                url='https://recovery.example/'+kind
                activity=Activity(str(uuid.uuid4()),ActivityType.FREE_EXPLORATION,'',ActivityStatus.RUNNING,
                    {'exploration':{'state':'reading_results','topic':'P2Nebula','raw_results':[{'title':'P2Page','url':url,'snippet':'old snippet'}]}},1)
                await activity_store.insert(activity)
                cid=activity.id;topic=build_source_topic('web',url)
                async def run():
                    current=await activity_store.get(activity.id)
                    await app.activity._exploration.run(current)
                async def state(): return (await activity_store.get(activity.id)).progress['exploration']
                target=activity_store;method='update';orig_save=activity_store.update
                def final(args):
                    cp=args[0].progress['exploration']
                    return cp.get('source_pending') is None and cp.get('cursor',0)>0
            chunk_id=await memory.record_reading_evidence(topic,'original',original,'preseed')
            fake.fail_digest=True
            with patch('nyx.tools.web_fetch.fetch_url',return_value=incoming) as fetched:
                await fails(run)
                first=await state();pending_key='source_pending' if kind=='exploration' else 'pending'
                assert first[pending_key]['source_text']==incoming and first.get('cursor',0) in (0,{'paragraph_index':1,'char_offset':0}),first
                assert len(await store.list_knowledge_chunks(source_topic=topic))==1
                fake.fail_digest=False
                with patch.object(memory,'remember_knowledge',side_effect=RuntimeError('knowledge failed')):
                    await fails(run)
                second=await state();assert second[pending_key]['source_chunk_id']==chunk_id,second
                digests=[json.loads(m[-1]['content'])['source_text'] for t,m in fake.calls if t=='source_digest']
                assert digests[-1]==original,digests
                n=len(digests)
                async def fail_final(*args,**kwargs):
                    if final(args): raise RuntimeError('final checkpoint failed')
                    return await orig_save(*args,**kwargs)
                with patch.object(target,method,side_effect=fail_final): await fails(run)
                third=await state();assert third[pending_key]['source_chunk_id']==chunk_id
                facts=await memory.search_facts('Source'+cid,scope='knowledge')
                assert any(f.source_chunk_id==chunk_id for f in facts),(kind,facts,cid)
                with patch('nyx.tools.web_fetch.fetch_url',return_value='CHANGED REMOTE SOURCE'):
                    await run()
                last=await state();assert last[pending_key] is None,last
                assert sum(t=='source_digest' for t,m in fake.calls)==n,(kind,n)
                assert len(await store.list_knowledge_chunks(source_topic=topic))==1
                if kind!='epub': assert fetched.call_count==1,(kind,fetched.call_count)
            results.append(kind+'_raw_extracted_final_failure_replay_dedup_snapshot_provenance')
            print('PASS '+results[-1],flush=True)
        # Legacy extracted pending, with and without raw text: never guess provenance.
        for raw in (False,True):
            book,_=await rs.insert_book_with_paragraphs('Legacy','A','old.epub',uuid.uuid4().hex,[Segment(text='legacy',is_chapter_start=True)])
            pending={'cursor':{'paragraph_index':2,'char_offset':0},'profile':{},'knowledge':[{'topic':'legacy','content':'old extracted '+str(raw),'source_topic':build_source_topic('book',book.id)}]}
            if raw: pending.update(source_text='P2Nebula legacy raw snapshot',source_name='Legacy',block_key='old')
            await rs.update_memory_state(book.id,{'cursor':{'paragraph_index':1,'char_offset':0},'profile':{},'pending':pending})
            n=sum(t=='source_digest' for t,m in fake.calls)
            with patch.object(memory,'remember_knowledge',wraps=memory.remember_knowledge) as remember:
                await app.reading._integration.sediment(book.id,1,flush=True)
                assert remember.call_args.kwargs['source_chunk_id'] is None
            assert sum(t=='source_digest' for t,m in fake.calls)==n
        results.append('legacy_extracted_pending_no_guessed_link_or_redigest')
        print('PASS '+results[-1],flush=True)
        Path(tempfile.gettempdir(),'nyx-p2-recovery-results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    finally: await app.database.close()

asyncio.run(main())
