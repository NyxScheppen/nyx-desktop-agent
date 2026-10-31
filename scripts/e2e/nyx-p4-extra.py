import asyncio,json,os,sys,time,uuid
from pathlib import Path
from unittest.mock import patch
from importlib.util import spec_from_file_location,module_from_spec
import httpx
sys.path.insert(0,str(Path.cwd()))
sp=spec_from_file_location('bounds',Path(__file__).with_name('nyx-p4-boundaries.py'));b=module_from_spec(sp);sp.loader.exec_module(b)
from nyx.enums import EventType,InteractionKind,InteractionStatus
from nyx.types import InteractionAttempt
from nyx.runtime import root_event
from nyx.expression.store import ExpressionInteractionStore
from nyx.events import bus as bus_module

async def channels_waiting():
 app,fake=await b.new()
 try:
  annotation={'reply_event_id':'old','quote':'旧回复原文🙂','comment':'更喜欢这样的回复'}
  orig=fake.complete
  async def complete(*args,**kw):
   out=await orig(*args,**kw)
   if kw['output_type']=='reply':
    obj=json.loads(out.content);obj['needs_continuation']=True;out.content=json.dumps(obj,ensure_ascii=False)
   return out
  with patch.object(fake,'complete',side_effect=complete):
   for label,threshold,discussion in [('fast',1.0,False),('slow',0.0,False),('discussion',1.0,True)]:
    fake.calls.clear();app.expression._config.slow_threshold=threshold
    await app.expression.reply('你好',label,discussion=discussion,annotation=annotation)
    replies=[x for x in fake.calls if x[0]=='reply'];tools=[x for x in fake.calls if x[0]=='tool']
    assert len(replies)==(1 if label=='fast' else 3),(label,[x[0] for x in fake.calls])
    assert bool(tools)==(label!='fast'),(label,[x[0] for x in fake.calls])
    if label!='fast':
     names={t['function']['name'] for t in tools[0][2]['tools']};assert ('knowledge_search' in names)==(label=='discussion'),names
    for typ,messages,kw,cid in replies+tools:
     assert '[对上轮回复的批注]' in messages[-1]['content'] and all(annotation[k] in messages[-1]['content'] for k in ['quote','comment']),(label,typ,messages)
  app.expression._config.slow_threshold=1.0
  store=ExpressionInteractionStore(app.database)
  now=time.time();attempt=InteractionAttempt(str(uuid.uuid4()),InteractionKind.CHAT_ASK,'ask','ask-cid','今天好吗？',now,now+600)
  await store.create(attempt)
  await app.expression.reply('这句话让我不舒服','annotation-waiting',annotation=annotation,discussion=False)
  assert (await store.get(attempt.id)).status is InteractionStatus.WAITING
  await app.expression.reply('今天还好','ordinary-answer',reply_to=attempt.id,discussion=False)
  result=await store.get(attempt.id)
  assert result.status is InteractionStatus.ANSWERED and result.answer_event_id=='ordinary-answer',result
  print('PASS FAST/SLOW/discussion tool+final+continuation prompts; annotation does not force channel; real WAITING preserved then ordinary answer',flush=True)
 finally:await app.database.close()

async def accounting():
 app,fake=await b.new()
 try:
  orig=fake.complete
  for bad in [False,True]:
   f=await b.feedback(app)
   async def complete(*args,**kw):
    out=await orig(*args,**kw)
    if bad:out.content='{bad json'
    return out
   with patch.object(fake,'complete',side_effect=complete):await app.memory.apply_annotation_feedback(f,b.MED)
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.build_app(app)),base_url='http://127.0.0.1:8000') as c:
   recent=(await c.get('/api/eval/recent',params={'limit':100})).json()
   records=[x for x in recent if x['output_type']=='fact_extraction'];assert len(records)==2,records
   for r in records:
    assert r['module']=='memory' and r['prompt_tokens']==11 and r['completion_tokens']==7,r
    prompt=(await c.get('/api/eval/'+r['id']+'/prompt')).json()
    assert prompt and any('comment' in m['content'] and 'quote' in m['content'] for m in prompt),prompt
   totals=(await c.get('/api/eval/total_tokens')).json();assert totals=={'total_tokens':36,'prompt_tokens':22,'completion_tokens':14},totals
  print('PASS actual eval HTTP recent/tokens/final prompt for valid and malformed JSON',flush=True)
 finally:await app.database.close()

async def reflection_boundary():
 app,fake=await b.new()
 try:
  for _ in range(2):
   prior=await b.feedback(app)
   await app.memory.apply_annotation_feedback(prior,b.MED)
  f=await b.feedback(app)
  await app.memory.apply_annotation_feedback(f,b.MED)
  ref=next(
   event for event in await app.bus.list_events(event_type=EventType.REFLECTION)
   if event.content.get('evidence',[{}])[0].get('source_id')==f.id
  )
  orig=app.inner_life._reflection.prepare
  with patch.object(app.inner_life._reflection,'prepare',side_effect=RuntimeError('reflection unavailable')):
   try:await app.inner_life.apply_event(ref,'inner_life.reflection')
   except RuntimeError:pass
   else:raise AssertionError('expected reflection failure')
  assert await app.memory.has_annotation_feedback(f.content['reply_event_id'],f.content['comment'])
  assert await app.bus.has_effect(f.id,b.MED) and not await app.bus.has_effect(ref.id,'inner_life.reflection')
  await app.expression.reply('你好','reply-independent',discussion=False)
  assert await app.bus.list_events(event_type=EventType.SPEAK,correlation_id='reply-independent')
  await app.inner_life.apply_event(ref,'inner_life.reflection')
  assert await app.bus.has_effect(ref.id,'inner_life.reflection')
  assert await app.bus.list_events(event_type=EventType.REFLECTION_DONE,correlation_id=ref.correlation_id)
  old=root_event(EventType.REFLECTION,{'reason':'annotation_correction','evidence':[{'source_id':f.id,'summary':'旧反馈'}]});old.timestamp=0.0
  await app.bus.publish(old);calls=len(fake.calls)
  await app.inner_life.apply_event(old,'inner_life.reflection')
  assert await app.bus.has_effect(old.id,'inner_life.reflection') and len(fake.calls)==calls
  assert not await app.bus.list_events(event_type=EventType.REFLECTION_DONE,correlation_id=old.correlation_id)
  print('PASS reflection failure isolation, reply, retry/DONE and expired correction no-op',flush=True)
 finally:await app.database.close()

async def dead_letter():
 app,fake=await b.new();worker=None
 try:
  speak=await b.publish(app,EventType.SPEAK,{'content':'旧回复原文'})
  annotation={'reply_event_id':speak.id,'quote':'旧回复','comment':'我有反馈'}
  with patch.object(app.memory._store,'record_annotation_feedback',side_effect=RuntimeError('recording fails')),patch.object(bus_module,'_DELIVERY_RETRY_DELAYS',(.01,)*5):
   worker=asyncio.create_task(app.bus.run())
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.build_app(app)),base_url='http://127.0.0.1:8000') as c:
    r=await c.post('/api/chat',json={'message':'你好','annotation':annotation,'discussion':False});assert r.status_code==200 and r.json()['annotation_persistence']=='pending',r.text
    uid=r.json()['event_id']
    async def dead():
     rows=await b.rows(app,'SELECT status,attempts FROM event_delivery WHERE event_id=? AND consumer_id=?',(uid,b.REC))
     return rows and rows[0]['status']=='dead_letter' and rows[0]['attempts']==5
    await b.p4.until(dead)
    assert await app.bus.list_events(event_type=EventType.SPEAK,correlation_id=uid)
    assert not await app.memory.has_annotation_feedback(speak.id,annotation['comment'])
   worker.cancel();await asyncio.gather(worker,return_exceptions=True);worker=None
  print('PASS independent recording fails 5 attempts/dead_letter; chat still replies and receipt stays pending',flush=True)
 finally:
  if worker:worker.cancel();await asyncio.gather(worker,return_exceptions=True)
  await app.database.close()

async def main():
 for fn in [channels_waiting,accounting,reflection_boundary,dead_letter]:await fn()
if __name__=='__main__':asyncio.run(main())

