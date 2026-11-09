import asyncio,json,os,socket,sys,time,uuid
from pathlib import Path
from unittest.mock import patch
import httpx,uvicorn
sys.path.insert(0,str(Path.cwd()))
from nyx.config import Config
from nyx.main import build_app,build_app_context
from nyx.types import LLMOutput
from nyx.enums import EventType
from nyx.runtime import root_event

class Fake:
    def __init__(self):
        self.calls=[]
        self.feedback={'entities':[],'facts':[{'scope':'profile','subject':'用户','subject_type':'person','predicate':'回复偏好','object':'希望先说明不确定性','asserted':True,'mode':'multi'},{'scope':'conversation','subject':'用户','subject_type':'person','predicate':'回复反馈','object':'认为这次回答过于肯定','asserted':True,'mode':'multi'}],'is_correction':True}
    async def complete(self,messages,*,module,output_type,correlation_id,**kw):
        self.calls.append((output_type,messages,kw,correlation_id))
        if output_type=='reason':data={'summary':'核对批注。','next_action':'answer' if '[工作摘要' in messages[-1]['content'] else 'tools'}
        elif output_type=='reply':data={'think':'认真理解批注','speak':'我会先说明不确定性。','needs_continuation':False}
        elif output_type=='fact_extraction':data=self.feedback
        elif output_type=='tool':data={}
        elif output_type=='behavior_reflection':data={'observation':'用户多次要求说明不确定性','cause_analysis':'回复缺少限定条件','effective_strategy':'先说明边界','next_strategy':'先说明不确定性再给结论','applicability':'普通对话','evidence_refs':['feedback','reply'],'skill_change_decision':'update','skill_change_reason':'多次纠正支持调整','proposed_skill_text':'保留角色口吻，先说明证据边界和不确定性，再给出具体结论。'}
        elif output_type=='reflection':data={'story':'我会留意用户反馈','becoming':'先说明不确定性','personality_delta':{},'values_delta':{},'long_term_desires':[]}
        else:data={'content':'E2E','summary':'E2E','topics':[],'importance':'normal','entities':[],'facts':[]}
        return LLMOutput(module=module,type=output_type,model='p4-script',content=json.dumps(data,ensure_ascii=False),correlation_id=correlation_id,call_id=str(uuid.uuid4()),prompt_messages=messages,prompt_tokens=11,completion_tokens=7)

async def until(fn):
    for _ in range(250):
        value=await fn()
        if value:return value
        await asyncio.sleep(.02)
    raise AssertionError('timed out')

async def main():
    os.environ['NYX_DB']=':memory:';os.environ.pop('NYX_LAUNCH_NONCE',None)
    fake=Fake();config=Config();config.expression.slow_max_rounds=1
    with patch('nyx.app_context.LlmClient.from_config',return_value=fake),patch('nyx.app_context.build_embed',return_value=None): app=await build_app_context(config)
    bus=asyncio.create_task(app.bus.run())
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(64)
    server=uvicorn.Server(uvicorn.Config(build_app(app),log_level='error',lifespan='off'))
    task=asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:await asyncio.sleep(.01)
        anchor=root_event(EventType.SPEAK,{'content':'这是旧回复，结论非常肯定。中文🙂片段。'})
        await app.behavior.skills.for_execution(anchor.correlation_id,"conversation","原始问题")
        await app.bus.publish(anchor)
        annotation={'reply_event_id':anchor.id,'quote':'结论非常肯定。中文🙂','comment':'我希望先说明不确定性，这个回复过于肯定。'}
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{sock.getsockname()[1]}',headers={'Host':'127.0.0.1:8000'},timeout=15) as client:
            async with client.stream('GET','/api/events') as stream:
                events=[]
                async def read():
                    kind=''
                    async for line in stream.aiter_lines():
                        if line.startswith('event: '):kind=line[7:]
                        elif line.startswith('data: '):events.append((kind,json.loads(line[6:])))
                reader=asyncio.create_task(read())
                try:
                    r=await client.post('/api/chat',json={'message':annotation['comment'],'discussion':False,'annotation':annotation})
                    assert r.status_code==200,r.text
                    cid=r.json()['event_id']
                    durable=await app.bus.list_events(correlation_id=cid)
                    assert next(e for e in durable if e.type is EventType.USER_MESSAGE).content.get('annotation')==annotation,'missing complete annotation in durable USER_MESSAGE'
                    async def terminal():return [e for k,e in events if k=='speak' and e['correlation_id']==cid]
                    await until(terminal)
                    prompts=[m for typ,m,_,c in fake.calls if typ=='reply' and c==cid]
                    assert prompts and all('[对上轮回复的批注]' in p[-1]['content'] and annotation['quote'] in p[-1]['content'] and annotation['comment'] in p[-1]['content'] for p in prompts),prompts
                    print('PASS 4.1 HTTP/SSE durable snapshot and same-reply prompt',flush=True)
                    if '--stage1' in sys.argv:return
                    async def feedback():return [e for k,e in events if k=='annotation_feedback' and e['reply_event_id']==anchor.id]
                    receipt=(await until(feedback))[0]
                    assert receipt['quote']==annotation['quote'] and receipt['comment']==annotation['comment']
                    again=await client.post('/api/chat',json={'message':'再次批注','annotation':annotation})
                    assert again.json()['annotation_persistence']=='recorded',again.text
                    async with app.database.lock:
                        row=await (await app.database.conn.execute('SELECT count(*) FROM annotation_feedback')).fetchone()
                    assert row[0]==1,row
                    print('PASS 4.2 durable receipt, unique row and recorded replay',flush=True)
                    if '--stage2' in sys.argv:return
                    async def facts():
                        r=await client.get('/api/memories/facts',params={'scope':'profile','limit':64})
                        return [x for x in r.json() if x['predicate']=='回复偏好']
                    assert await until(facts)
                    async def reflection():return [e for e in await app.bus.list_events(event_type=EventType.BEHAVIOR_REFLECTION)]
                    for index in range(2):
                        prior = root_event(
                            EventType.SPEAK,
                            {'content': f'这是第 {index + 2} 条待纠正回复。'},
                        )
                        await app.behavior.skills.for_execution(prior.correlation_id,"conversation","原始问题")
                        await app.bus.publish(prior)
                        followup = {
                            'reply_event_id': prior.id,
                            'quote': prior.content['content'],
                            'comment': f'我希望先说明不确定性，反馈 {index + 2}。',
                        }
                        response = await client.post(
                            '/api/chat',
                            json={
                                'message': followup['comment'],
                                'discussion': False,
                                'annotation': followup,
                            },
                        )
                        assert response.status_code == 200, response.text
                        cid = response.json()['event_id']
                        async def followup_feedback():
                            return [
                                e for e in await app.bus.list_events(
                                    event_type=EventType.ANNOTATION_FEEDBACK
                                ) if e.content.get('reply_event_id') == prior.id
                            ]
                        await until(followup_feedback)
                    assert await until(reflection)
                    async def done():return [r for r in await app.behavior.store.list_records('behavior_reflection') if r['status']=='completed' and r['target_skill']=='conversation']
                    assert await until(done)
                    assert not await app.bus.list_events(event_type=EventType.REFLECTION)
                    assert (await app.behavior.skills.load('conversation')).version==2
                    print('PASS 4.3 profile facts, correction behavior reflection and downstream skill without inner-state reflection',flush=True)
                finally:
                    reader.cancel();await asyncio.gather(reader,return_exceptions=True)
    finally:
        server.should_exit=True;await task;sock.close();bus.cancel();await asyncio.gather(bus,return_exceptions=True);await app.database.conn.close()

if __name__=='__main__':asyncio.run(main())
