import asyncio, importlib.util, json, sys, time, logging
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from nyx.research import run_research
from nyx.types import LLMOutput
logging.disable(logging.CRITICAL)

class Script:
    def __init__(self, pause=None, fail=None, invalid=None, huge=False):
        self.pause=pause;self.fail=fail;self.invalid=invalid;self.huge=huge
        self.calls=[];self.saved=[];self.searched=[];self.fetched=[];self.sources={}
    async def complete(self,messages,*,module,output_type,correlation_id,**kwargs):
        self.calls.append(output_type)
        if output_type==self.pause: await asyncio.Event().wait()
        if output_type==self.fail: raise RuntimeError('injected LLM failure')
        data=json.loads(messages[-1]['content'])
        if output_type=='research_plan': value={'sub_queries':['q1','q2'] if 'research_deepen' in (self.pause,self.fail) else ['q1','q2','q3','q4','q5']}
        elif output_type=='research_deepen': value={'need_deeper':True,'new_sub_queries':['q6','q7']}
        elif output_type=='research_evidence': value={'evidence':[{'claim':data['text'][:40],'quote':data['text'][:40],'source_url':data['source_url']}] * 10}
        else:
            records=[{'section':'分歧','claim':'存在分歧。' * (1200 if self.huge else 1),'citations':[{'quote':text[:180] if not self.huge else text,'source_url':url}]} for url,text in self.sources.items()]
            value={'sections' if output_type=='research_synthesis' else 'report':records}
        if output_type==self.invalid: value={'evidence':[], 'sections':[], 'report':[]}
        return LLMOutput(module=module,type=output_type,model='script',correlation_id=correlation_id,content=json.dumps(value,ensure_ascii=False))
    async def search(self,query):
        self.searched.append(query)
        base=int(query[1:])*10
        return [{'url':f'https://example.com/{base+i}#fragment'} for i in range(1 if 'research_deepen' in (self.pause,self.fail) else 6)]
    async def fetch(self,url):
        self.fetched.append(url)
        if self.pause=='fetch': await asyncio.Event().wait()
        if self.fail=='fetch': raise RuntimeError('injected fetch')
        text=('观测 "\\\n数据😀。' * 1000) if self.huge else '原文证据来自'+url
        self.sources[url]=text[:6000]
        return {'text':text,'url':url}
    async def ingest(self,text,topic,name,cid,*,location,committed_chunks=None):
        if self.pause=='ingest': await asyncio.Event().wait()
        if self.fail=='ingest': raise RuntimeError('injected ingest')
        self.saved.append((text,topic,name,location))
        return {'chunks':[{'chunk_id':f'{len(self.saved):064x}','source_topic':topic,'dedup':'new'}]}
    async def run(self,**kwargs):
        return await run_research('边界问题',search=self.search,fetch=self.fetch,llm=self,ingest=self.ingest,**kwargs)

async def main():
    s=Script();r=await s.run()
    assert len(s.searched)==2 and len(s.fetched)==6 and len(set(s.fetched))==6
    assert 'research_deepen' not in s.calls
    assert all(len(x[0])<=6000 for x in s.saved)
    assert len(json.dumps(r,ensure_ascii=False))<=1960 and r['report']
    assert s.calls.index('research_compress') < len(s.calls)
    print('PASS hard limits, six unique sources, full receipt+report JSON budget',flush=True)
    for budget in ({'max_sources':1},{'max_sub_queries':1},{'max_depth':1},{'max_sources':100,'max_sub_queries':100,'max_depth':100}):
        s=Script();r=await s.run(budget=budget)
        assert len(s.fetched)<=min(6,budget.get('max_sources',6))
        assert len(s.searched)<=min(4,budget.get('max_sub_queries',4))
        assert len(json.dumps(r,ensure_ascii=False))<=1960
    print('PASS per-call limits only tighten hard caps',flush=True)
    for stage in ('research_plan','fetch','ingest','research_deepen','research_evidence','research_synthesis','research_compress'):
        s=Script(pause=stage);started=time.monotonic();r=await s.run(budget={'seconds':.03})
        assert time.monotonic()-started<.4 and '超时' in r['notice'],(stage,r)
        assert len(json.dumps(r,ensure_ascii=False))<=1960
        before=len(s.saved);await asyncio.sleep(.001);assert len(s.saved)==before
        if stage in ('research_deepen','research_evidence','research_synthesis','research_compress'): assert r['report'],(stage,r)
    print('PASS deadline at every awaited stage; completed partial data retained',flush=True)
    for stage in ('research_plan','fetch','ingest','research_deepen','research_evidence','research_synthesis','research_compress'):
        s=Script(fail=stage);r=await s.run()
        assert len(json.dumps(r,ensure_ascii=False))<=1960 and '失败' in r['notice'],(stage,r)
    for stage in ('research_evidence','research_synthesis','research_compress'):
        s=Script(invalid=stage);r=await s.run();assert r['report'] and ('未完成' in r['notice'] or '尚无完整' in r['notice']),(stage,r)
    print('PASS LLM/network/ingestion failures and invalid outputs degrade explicitly',flush=True)
    s=Script(huge=True);r=await s.run()
    assert all(len(x[0])<=6000 for x in s.saved)
    assert len(json.dumps(r,ensure_ascii=False))<=1960 and r['report'],r
    for section in r['report']:
        for c in section['citations']: assert c['quote'] in s.sources[c['source_url']]
    print('PASS long unicode/escaped sources and oversized reports retain whole valid citations',flush=True)

asyncio.run(main())
