import asyncio,copy,json,os,sys,tempfile,uuid,logging
from pathlib import Path
from unittest.mock import patch,AsyncMock
import httpx
sys.path.insert(0,str(Path.cwd()));sys.path.insert(0,str(Path(__file__).parent))
from importlib.util import spec_from_file_location,module_from_spec
sp=spec_from_file_location('p4',Path(__file__).with_name('nyx-p4-acceptance.py'));p4=module_from_spec(sp);sp.loader.exec_module(p4)
from nyx import db
from nyx.config import Config
from nyx.main import build_app_context,build_app
from nyx.runtime import root_event,internal_event
from nyx.enums import EventType
from nyx.types import LLMOutput
logging.disable(logging.CRITICAL)
REC='memory.annotation_recording';MED='memory.annotation_feedback'
async def new(path=':memory:'):
 os.environ['NYX_DB']=path;fake=p4.Fake()
 with patch('nyx.app_context.LlmClient.from_config',return_value=fake),patch('nyx.app_context.build_embed',return_value=None):app=await build_app_context(Config())
 return app,fake
async def rows(app,sql,args=()):
 async with app.database.lock:return await (await app.database.conn.execute(sql,args)).fetchall()
async def count(app,table):return (await rows(app,'SELECT count(*) FROM '+table))[0][0]
async def publish(app,kind,content):
 e=root_event(kind,content);await app.bus.publish(e);return e
async def user(app,comment=None,quote='quote',anchor='anchor'):
 return await publish(app,EventType.USER_MESSAGE,{'message':'feedback','annotation':{'reply_event_id':anchor,'quote':quote,'comment':comment or str(uuid.uuid4())}})
async def feedback(app):
 u=await user(app);await app.memory.record_annotation_feedback(u,REC)
 events=await app.bus.list_events(event_type=EventType.ANNOTATION_FEEDBACK)
 return next(e for e in events if e.content.get('user_event_id')==u.id)
async def migration():
 d=await db.connect(':memory:',migrations=[x for x in db._MIGRATIONS if x[0]<=45])
 try:
  await d.conn.execute("INSERT INTO user_notes (id,content,created_at,updated_at) VALUES ('legacy-note','原有笔记🙂',1,1)")
  await d.conn.execute("INSERT INTO annotations (id,user_note_id,content,created_at) VALUES ('legacy-annotation','legacy-note','原有阅读批注🙂',1)")
  await d.conn.commit()
  before=[tuple(x) for x in await (await d.conn.execute('SELECT * FROM annotations')).fetchall()]
  assert len(before)==1
  migration_46=next(migration for migration in db._MIGRATIONS if migration[0]==46)
  bad=[(46,migration_46[1]+['INSERT INTO nonexistent_p4 VALUES (1)'])]
  try:await db.migrate(d.conn,migrations=bad)
  except Exception:pass
  else:raise AssertionError('expected migration error')
  assert (await (await d.conn.execute('SELECT max(version) FROM schema_version')).fetchone())[0]==45
  assert await (await d.conn.execute("SELECT name FROM sqlite_master WHERE name='annotation_feedback'")).fetchone() is None
  await db.migrate(d.conn)
  assert (await (await d.conn.execute('SELECT max(version) FROM schema_version')).fetchone())[0]==52
  assert before==[tuple(x) for x in await (await d.conn.execute('SELECT * FROM annotations')).fetchall()]
 finally:await d.close()
 print('PASS schema45->52, migration rollback, existing reading table preserved',flush=True)
async def inputs():
 app,fake=await new();other,_=await new()
 try:
  speak=await publish(app,EventType.SPEAK,{'content':'先前 中文🙂 引文 后文'+'🙂'*2000})
  wrong=await publish(app,EventType.ASK,{'content':'问题'})
  foreign=await publish(other,EventType.SPEAK,{'content':'外角色'})
  ann={'reply_event_id':speak.id,'quote':'中文🙂','comment':' 原样反馈🙂 '}
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app(app)),base_url='http://127.0.0.1:8000') as c:
   async def send(a,**extra):return await c.post('/api/chat',json={'message':'反馈','annotation':a,**extra})
   for field in ann:
    for value in [None,True,42,[],{},'', '  ', 'x'*(129 if field=='reply_event_id' else 2001)]:
     r=await send({**ann,field:value});assert r.status_code==422,(field,value,r.text)
    missing=dict(ann);missing.pop(field);assert (await send(missing)).status_code==422
   for ident in ['missing',foreign.id]:assert (await send({**ann,'reply_event_id':ident})).status_code==404
   for a in [{**ann,'reply_event_id':wrong.id,'quote':'问题'},{**ann,'quote':'先前后文'},{**ann,'quote':'被改写'}]:assert (await send(a)).status_code==422
   assert (await send(ann,reply_to='waiting')).status_code==422
   for a in [ann,{**ann,'quote':'🙂'*2000,'comment':'🙂'*2000},None]:
    r=await send(a);assert r.status_code==200,r.text
   r=await send(ann);event=await app.bus.get_event(r.json()['event_id']);assert event.content['annotation']==ann
   with patch.object(app.bus,'get_event',side_effect=RuntimeError('lookup')):assert (await send(ann)).status_code==503
   with patch.object(app.memory,'has_annotation_feedback',side_effect=RuntimeError('receipt')):assert (await send(ann)).json()['annotation_persistence']=='pending'
  print('PASS strict API field/type/Unicode limits, anchors, contiguous quote, cross-database isolation, 503/pending',flush=True)
 finally:await app.database.close();await other.database.close()
async def recording():
 app,fake=await new()
 try:
  a=await user(app,' raw ');b=await user(app,' raw ',quote='different valid snapshot');c=await user(app,'raw')
  await asyncio.gather(*(app.memory.record_annotation_feedback(x,REC) for x in [a,b,c]))
  assert await count(app,'annotation_feedback')==2
  raw=await rows(app,'SELECT quote FROM annotation_feedback WHERE comment=?',(' raw ',));assert raw[0][0]=='quote'
  assert len(await app.bus.list_events(event_type=EventType.ANNOTATION_FEEDBACK))==2
  plain=await publish(app,EventType.USER_MESSAGE,{'message':'plain'});await app.memory.record_annotation_feedback(plain,REC)
  for stage in ['row','append','effect','commit']:
   u=await user(app);before=[await count(app,x) for x in ['annotation_feedback','event_log','event_effect']];pc=app.bus.persisted_count
   obj,name= {'row':(app.memory._store,'record_annotation_feedback'),'append':(app.bus,'append_in_transaction'),'effect':(app.bus,'try_mark_effect_in_transaction'),'commit':(app.database.conn,'commit')}[stage]
   original=getattr(obj,name)
   async def fail(*args,**kw):
    if stage!='commit':await original(*args,**kw)
    raise RuntimeError(stage)
   with patch.object(obj,name,side_effect=fail):
    try:await app.memory.record_annotation_feedback(u,REC)
    except RuntimeError:pass
    else:raise AssertionError('fault not raised '+stage)
   assert [await count(app,x) for x in ['annotation_feedback','event_log','event_effect']]==before,stage
   assert app.bus.persisted_count==pc,stage
   await app.memory.record_annotation_feedback(u,REC);assert await app.bus.has_effect(u.id,REC)
  print('PASS raw-key concurrent dedup, first snapshot, no-op, row/append/effect/commit rollback and retry/count',flush=True)
 finally:await app.database.close()
async def mediation():
 app,fake=await new()
 try:
  base=copy.deepcopy(fake.feedback)
  for case in ['valid','false','empty','invalidbool','badjson','llmfail','evalfail','filter','cancel']:
   f=await feedback(app);data=copy.deepcopy(base);data['is_correction']=case not in ['false','filter']
   data['correction_topic']=case
   if case=='empty':data['facts']=[]
   if case=='invalidbool':data['is_correction']=1
   if case=='filter':
    data['facts']=[{**data['facts'][0],**v} for v in [{'scope':'knowledge'},{'subject':'宇宙'},{'asserted':False},{'asserted':'true'},{'scope':'conversation','predicate':'宇宙真相'}]]
   fake.feedback=data
   orig=fake.complete
   async def complete(*args,**kwargs):
    if case=='llmfail':raise RuntimeError('LLM')
    if case=='cancel':raise asyncio.CancelledError()
    out=await orig(*args,**kwargs)
    if case=='badjson':out.content='{bad'
    return out
   before=await count(app,'memory_fact');refs=await count(app,'event_log');knowledge=[await count(app,x) for x in ['memory','reading_evidence']]
   with patch.object(fake,'complete',side_effect=complete),patch.object(app.memory._evaluator,'evaluate',side_effect=RuntimeError('eval') if case=='evalfail' else None,wraps=None if case=='evalfail' else app.memory._evaluator.evaluate) as ev:
    try:await app.memory.apply_annotation_feedback(f,MED)
    except asyncio.CancelledError:assert case=='cancel'
    else:assert case!='cancel'
    if case not in ['cancel','llmfail']:assert ev.call_count==1
   assert [await count(app,x) for x in ['memory','reading_evidence']]==knowledge
   if case=='cancel':assert not await app.bus.has_effect(f.id,MED);continue
   assert await app.bus.has_effect(f.id,MED)
   reflections=[e for e in await app.bus.list_events(event_type=EventType.REFLECTION) if e.content.get('evidence',[{}])[0].get('source_id')==f.id]
   assert not reflections,case
   if case in ['invalidbool','badjson','llmfail','filter']:assert await count(app,'memory_fact')==before,case
   calls=len(fake.calls);await app.memory.apply_annotation_feedback(f,MED);assert len(fake.calls)==calls
  fake.feedback=base
  for stage in ['fact','append','effect','commit']:
   for _ in range(2):
    prior=await feedback(app)
    await app.memory.apply_annotation_feedback(prior,MED)
   f=await feedback(app);before=[await count(app,x) for x in ['memory_fact','event_log','event_effect']];pc=app.bus.persisted_count
   obj,name={'fact':(app.memory._fact_store,'apply'),'append':(app.bus,'append_in_transaction'),'effect':(app.bus,'try_mark_effect_in_transaction'),'commit':(app.database.conn,'commit')}[stage];original=getattr(obj,name)
   async def fail(*args,**kw):
    if stage!='commit':await original(*args,**kw)
    raise RuntimeError(stage)
   with patch.object(obj,name,side_effect=fail):
    raised=False
    try:await app.memory.apply_annotation_feedback(f,MED)
    except RuntimeError:raised=True
   if stage=='append':
    assert not raised
    assert [await count(app,x) for x in ['memory_fact','event_log','event_effect']]==[before[0]+2,before[1],before[2]+1],stage
    assert app.bus.persisted_count==pc
    continue
   assert raised,stage
   assert [await count(app,x) for x in ['memory_fact','event_log','event_effect']]==before,stage
   assert app.bus.persisted_count==pc,stage
   await app.memory.apply_annotation_feedback(f,MED);assert await app.bus.has_effect(f.id,MED)
  print('PASS mediation true/false/empty/invalid JSON/bool/LLM/eval/cancel, filtering, no knowledge, replay and four rollback windows',flush=True)
 finally:await app.database.close()
async def recovery():
 with tempfile.TemporaryDirectory(prefix='nyx-p4-') as td:
  path=str(Path(td)/'role.db');app,fake=await new(path)
  for _ in range(2):
   prior=await feedback(app)
   await app.memory.apply_annotation_feedback(prior,MED)
  u=await user(app)
  # Complete expression first, leave independent recording delivery unprocessed.
  from nyx.runtime import on_user_message
  await on_user_message(app,u)
  assert await app.bus.list_events(event_type=EventType.SPEAK,correlation_id=u.correlation_id)
  await app.database.close();app,fake=await new(path)
  # Recording commits but the wake/SSE announce is deliberately lost.
  with patch.object(app.bus,'announce_committed',new=AsyncMock()):await app.memory.record_annotation_feedback(u,REC)
  f=next(
   e for e in await app.bus.list_events(event_type=EventType.ANNOTATION_FEEDBACK)
   if e.content.get('user_event_id')==u.id
  )
  assert not await app.bus.has_effect(f.id,MED)
  worker=asyncio.create_task(app.bus.run())
  try:
   await p4.until(lambda:app.bus.has_effect(f.id,MED))
   async def done():return await app.bus.list_events(event_type=EventType.REFLECTION_DONE,correlation_id=u.correlation_id)
   assert await p4.until(done)
   assert await count(app,'annotation_feedback')==3
  finally:worker.cancel();await asyncio.gather(worker,return_exceptions=True);await app.database.close()
 print('PASS expression-terminal restart, independent recording, lost announce durable scan, mediation and reflection recovery',flush=True)
async def main():
 for fn in [migration,inputs,recording,mediation,recovery]:await fn()
if __name__=='__main__':asyncio.run(main())
