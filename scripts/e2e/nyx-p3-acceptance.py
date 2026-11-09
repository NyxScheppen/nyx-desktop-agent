import asyncio, json, os, socket, sys, time, uuid
from pathlib import Path
from collections import defaultdict
from unittest.mock import patch
import httpx, uvicorn
sys.path.insert(0,str(Path.cwd()))
from nyx.config import Config
from nyx.main import build_app, build_app_context
from nyx.memory.store import MemoryStore
from nyx.memory.facade import build_source_topic
from nyx.research import run_research
from nyx.types import LLMOutput
from nyx.expression.prompt import _tool_outputs_block

A='https://research.example/a'; B='https://research.example/b'
TEXT={A:'P3Nebula 观测显示尺度以 12 单位增长。该数据存在校准不确定性。',B:'P3Nebula 校准后仅增长 8 单位。两组研究方法不同，仍有分歧。'}
class Fake:
    def __init__(self):
        self.calls=[]; self.rounds=defaultdict(int); self.mode='normal'; self.pause=None
    async def complete(self,messages,*,module,output_type,correlation_id,**kw):
        self.calls.append((output_type,messages,kw))
        data=json.loads(messages[-1]['content']) if output_type.startswith('exploration_') or output_type=='source_digest' else {}
        if output_type==self.pause: await asyncio.Event().wait()
        if output_type=='reason': content={'summary':'核对研究结果。','next_action':'answer' if '[工作摘要' in messages[-1]['content'] else 'tools'}
        elif self.mode=='bad' and output_type.startswith('exploration_'): content=[]
        elif output_type=='exploration_decide':
            unread=[i for i,item in enumerate(data['results']) if (item.get('url') or item.get('path')) not in data['processed_sources']]
            searches=sum(a['name']=='web_search' for a in data['actions'])
            if unread: content={'action':'read','index':unread[0]}
            elif not searches: content={'action':'search','query':'P3Nebula 观测证据'}
            elif not any(a['name']=='follow_up' for a in data['actions']): content={'action':'follow_up','question':'P3Nebula 校准争议','reason':'核对测量分歧'}
            elif searches<2: content={'action':'search','query':'P3Nebula 校准争议'}
            else: content={'action':'finish','reason':'两组证据已检查'}
        elif output_type=='exploration_source_check': content={'relevant':True,'usable':True,'reason':'回答观测与校准问题'}
        elif output_type=='exploration_claim_support': content={'supported':[True]*len(data['report'])}
        elif output_type=='exploration_finalize':
            report=[{'section':'P3Nebula 分歧','claim':TEXT[u],'citations':[{'quote':TEXT[u].split('。')[0]+'。','source_url':u}]} for u in (A,B) if any(x['location']==u for x in data['sources'])]
            content={'summary':'两组测量存在校准分歧。','core_discovery':'校准方法影响结果，不能直接选边。','report':report}
        elif output_type=='behavior_reflection': content={'observation':'已核对两个研究来源。','cause_analysis':'单次研究不能推广。','effective_strategy':'补搜校准方法。','next_strategy':'检索时核对方法差异。','applicability':'研究检索','evidence_refs':['actions'],'skill_change_decision':'no_change','skill_change_reason':'保留现有策略','proposed_skill_text':''}
        elif output_type=='source_digest':
            text=data['source_text'];content={'summary':text[:100],'themes':['P3Nebula'],'content_category':'nonfiction','points':[{'topic':'P3Nebula','content':text}]}
        elif output_type=='fact_extraction':
            content={'entities':[],'facts':[{'subject':'P3Nebula','predicate':'研究来源','object':str(messages)[-80:],'mode':'multi'}]}
        elif output_type=='tool':
            self.rounds[correlation_id]+=1
            return LLMOutput(module=module,type=output_type,model='p3-script',content='{}',correlation_id=correlation_id,tool_calls=[{'name':'deep_research','args':{'query':'P3Nebula 的研究结论'}}] if self.rounds[correlation_id]==1 else [],call_id=str(uuid.uuid4()),prompt_messages=messages,prompt_tokens=11,completion_tokens=7)
        elif output_type=='reply':
            material=messages[0]['content']; assert 'deep_research:' in material and '校准' in material and A in material and B in material,material
            line=next(x for x in material.splitlines() if x.startswith('- deep_research: ')); report=json.loads(line.split(': ',1)[1]);assert report['report']
            content={'think':'根据有引文的报告回答','speak':f'两组研究仍有分歧，来源：{A} 和 {B}。','needs_continuation':False}
        else: content={'content':'E2E','summary':'E2E','topics':[],'importance':'normal','entities':[],'facts':[]}
        return LLMOutput(module=module,type=output_type,model='p3-script',content=json.dumps(content,ensure_ascii=False),correlation_id=correlation_id,call_id=str(uuid.uuid4()),prompt_messages=messages,prompt_tokens=11,completion_tokens=7)

async def main():
    os.environ['NYX_DB']=':memory:';os.environ.pop('NYX_LAUNCH_NONCE',None)
    llm=Fake();config=Config();config.exploration.web_enabled=True;config.expression.slow_max_rounds=1
    searches=[];fetches=[]
    async def search(query):
        searches.append(query);url=B if '校准争议' in query else A
        return [{'url':url},{'url':url}]
    async def fetch(url): fetches.append(url);return {'text':TEXT[url],'url':url}
    with patch('nyx.app_context.LlmClient.from_config',return_value=llm),patch('nyx.app_context.build_embed',return_value=None): app=await build_app_context(config)
    bus=asyncio.create_task(app.bus.run())
    store=MemoryStore(app.database)
    args=dict(search=search,fetch=fetch,llm=llm,ingest=app.memory.ingest_source,evaluator=app.evaluator)
    try:
        result=await run_research('P3Nebula 的研究结论',**args)
        assert result['report'] and len(searches)==2 and fetches==[A,B],result
        chunks=await store.list_knowledge_chunks();assert len(chunks)==2,chunks
        facts=await app.memory.recent_facts(100,scope='knowledge')
        assert {c.id for c in chunks}<={f.source_chunk_id for f in facts},facts
        replay=await run_research('P3Nebula 的研究结论',**args)
        assert replay['report'] and len(await store.list_knowledge_chunks())==2
        for section in result['report']:
            for cite in section['citations']: assert cite['quote'] in TEXT[cite['source_url']]
        print('PASS shared adaptive graph, citations, knowledge/fact provenance and replay',flush=True)
        disabled=await run_research('x',**{**args,'search':None});assert '联网没开' in str(disabled)
        async def empty(query):return []
        no=await run_research('x',**{**args,'search':empty});assert not no['report'] and '未找到' in no['summary']
        async def fail(url):return {'error':'failure'}
        no=await run_research('x',**{**args,'fetch':fail});assert no['error']=='failure' and not no['report']
        llm.mode='bad';no=await run_research('x',**args);assert no['error'] and not no['report'];llm.mode='normal'
        llm.pause='exploration_finalize'
        partial=await run_research('timeout',**args,budget={'seconds':.2})
        assert partial['error'] and len(await store.list_knowledge_chunks())==2
        task=asyncio.create_task(run_research('cancel',**args));await asyncio.sleep(.1);task.cancel()
        try:await task;raise AssertionError('cancel swallowed')
        except asyncio.CancelledError:pass
        llm.pause=None
        print('PASS disabled/empty/fetch failure/invalid output, deadline and cancellation',flush=True)
        block=_tool_outputs_block(['other: '+'x'*1900]*4+['deep_research: '+json.dumps(result,ensure_ascii=False)])
        for line in block.splitlines():
            if line.startswith('- deep_research:'):json.loads(line.split(': ',1)[1])
        assert len(block)<=8000
        # Real HTTP/SSE through composition-root handlers.
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(64)
        server=uvicorn.Server(uvicorn.Config(build_app(app),log_level='error',lifespan='off'))
        server_task=asyncio.create_task(server.serve(sockets=[sock]))
        try:
            for _ in range(100):
                if server.started:break
                await asyncio.sleep(.01)
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{sock.getsockname()[1]}',headers={'Host':'127.0.0.1:8000'},timeout=30) as client:
                async with client.stream('GET','/api/events') as stream:
                    events=asyncio.Queue()
                    async def consume():
                        kind=''
                        async for line in stream.aiter_lines():
                            if line.startswith('event: '):kind=line[7:]
                            elif line.startswith('data: '):await events.put((kind,json.loads(line[6:])))
                    reader=asyncio.create_task(consume())
                    with patch('nyx.tools.web_search._search_web_sync',side_effect=lambda q:[{'url': B if '校准争议' in q else A}]),patch('nyx.tools.web_fetch.fetch_url',side_effect=lambda u:TEXT[u]):
                        response=await client.post('/api/chat',json={'message':'P3Nebula 的研究结论','discussion':True});assert response.status_code==200,response.text
                        cid=response.json()['event_id']
                        async def speak():
                            while True:
                                kind,event=await events.get()
                                if kind=='speak' and event['correlation_id']==cid:return event
                        event=await asyncio.wait_for(speak(),20);assert A in event['content'] and B in event['content'],event
                    facts_response=await client.get('/api/memories/facts',params={'scope':'knowledge','limit':64})
                    assert facts_response.status_code==200,facts_response.text
                    fact_chunks={x['source_chunk_id'] for x in facts_response.json()}
                    persisted=await store.list_knowledge_chunks()
                    assert {c.id for c in persisted if c.source_name in (A,B)}<=fact_chunks,(persisted,facts_response.json())
                    reader.cancel();await asyncio.gather(reader,return_exceptions=True)
            print('PASS HTTP/SSE research-to-cited-reply composition-root E2E',flush=True)
        finally:
            server.should_exit=True;await server_task;sock.close()
    finally:
        bus.cancel();await asyncio.gather(bus,return_exceptions=True);await app.database.conn.close()

if __name__ == '__main__':
    asyncio.run(main())

