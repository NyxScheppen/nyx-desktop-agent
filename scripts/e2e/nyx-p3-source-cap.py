import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from nyx.research import run_research
from nyx.types import LLMOutput

async def main():
    searched=[]; fetched=[]; stages=[]
    class Script:
        async def complete(self, messages, *, module, output_type, correlation_id, **kwargs):
            stages.append(output_type)
            value={'sub_queries':['one','two','three','four']} if output_type=='research_plan' else {}
            return LLMOutput(module=module,type=output_type,model='probe',correlation_id=correlation_id,content=json.dumps(value))
    async def search(query):
        searched.append(query)
        return [{'url':f'https://example.com/{query}/{i}'} for i in range(3)]
    async def fetch(url):
        fetched.append(url)
        return {'text':'原文证据','url':url}
    async def ingest(*args,**kwargs): return {'chunks':[]}
    result=await run_research('cap',search=search,fetch=fetch,llm=Script(),ingest=ingest)
    assert len(searched)==2,(searched,fetched,stages)
    assert len(fetched)==6 and 'research_deepen' not in stages,stages
    assert '预算' in result['notice'],result
    print('PASS six-source cap stops further search/deepening and keeps completed evidence')

asyncio.run(main())
