"""Shared graph budgets, admission, hard failures and cancellation boundaries."""
import asyncio, importlib.util, json, sys, time
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
spec=importlib.util.spec_from_file_location('acceptance',Path(__file__).with_name('nyx-p3-acceptance.py'))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
from research_support import run_research
from nyx.types import LLMOutput

class Script(a.Fake):
    def __init__(self,pause=None,fail=None,invalid=None,huge=False,reject=False):
        super().__init__();self.pause=pause;self.fail=fail;self.invalid=invalid
        self.huge=huge;self.reject=reject;self.saved=[];self.searched=[];self.fetched=[];self.sources={}
    async def complete(self,messages,*,module,output_type,correlation_id,**kwargs):
        if output_type==self.pause:await asyncio.Event().wait()
        if output_type==self.fail:raise RuntimeError('injected '+output_type)
        data=json.loads(messages[-1]['content'])
        if output_type=='exploration_decide':
            unread=[i for i,x in enumerate(data['results']) if x['url'] not in data['processed_sources']]
            if unread and data['remaining']['sources']>0:value={'action':'read','index':unread[0]}
            elif not self.searched and data['remaining']['searches']>0:value={'action':'search','query':'证据查询'}
            else:value={'action':'finish','reason':'达到预算或已检查来源'}
        elif output_type=='exploration_source_check':value={'relevant':not self.reject,'usable':True,'reason':'检查主题'}
        elif output_type=='exploration_finalize':
            value={'summary':'已检查资料','core_discovery':'获得可核对的证据','report':[{'section':'证据','claim':text[:40],'citations':[{'quote':text[:40],'source_url':url}]} for url,text in self.sources.items()]}
        elif output_type=='source_digest':
            if self.pause=='ingest':await asyncio.Event().wait()
            if self.fail=='ingest':raise RuntimeError('injected ingest')
            text=data['source_text'];self.saved.append((text,data.get('source_name',''),''))
            value={'summary':text[:40],'content_category':'knowledge',
                   'points':[{'topic':'来源知识','content':text[:80]}]}
        elif output_type=='exploration_claim_support':value={'supported':[True]*len(data['report'])}
        else:value={}
        if output_type==self.invalid:return LLMOutput(module,output_type,'script','{broken',correlation_id)
        return LLMOutput(module,output_type,'script',json.dumps(value,ensure_ascii=False),correlation_id)
    async def search(self,query):
        self.searched.append(query)
        return [{'url':f'https://example.test/{i}'} for i in range(8)]
    async def fetch(self,url):
        self.fetched.append(url)
        if self.pause=='fetch':await asyncio.Event().wait()
        if self.fail=='fetch':return {'error':'injected fetch'}
        text=('原文 "\\ 数据😀。'*1000) if self.huge else '原文证据 '+url
        self.sources[url]=text[:6000]
        return {'text':text}
    async def run(self,**kwargs):
        return await run_research('研究边界问题',search=self.search,fetch=self.fetch,llm=self,**kwargs)

async def main():
    s=Script();r=await s.run();assert len(s.fetched)==6 and len(set(s.fetched))==6 and r['report'],r
    for budget in ({'max_sources':1},{'max_sub_queries':1},{'max_steps':2},{'max_sources':100}):
        s=Script();r=await s.run(budget=budget)
        assert len(s.fetched)<=min(6,budget.get('max_sources',6))
        assert len(s.searched)<=min(4,budget.get('max_sub_queries',4))
    print('PASS hard caps and reduced per-call budgets',flush=True)
    for stage in ('exploration_decide','fetch','ingest','exploration_source_check','exploration_finalize'):
        s=Script(pause=stage);started=time.monotonic();r=await s.run(budget={'seconds':3})
        assert time.monotonic()-started<5 and r.get('error') and not r['report'],(stage,r)
        before=len(s.saved);await asyncio.sleep(.01);assert len(s.saved)==before
        s=Script(fail=stage);r=await s.run();assert r.get('error') and not r['report'],(stage,r)
    print('PASS awaited stage deadlines/failures propagate without fabricated fallback',flush=True)
    for stage in ('exploration_decide','exploration_source_check','exploration_finalize'):
        r=await Script(invalid=stage).run();assert r.get('error') and not r['report'],(stage,r)
    s=Script(reject=True);r=await s.run();assert not s.saved and not r['report']
    s=Script(huge=True);r=await s.run();assert r['report'] and all(len(x[0])<=6000 for x in s.saved)
    for item in r['report']:
        for c in item['citations']:assert c['quote'] in s.sources[c['source_url']]
    print('PASS invalid output, rejected-source isolation and truncated exact citations',flush=True)

if __name__=='__main__':asyncio.run(main())
