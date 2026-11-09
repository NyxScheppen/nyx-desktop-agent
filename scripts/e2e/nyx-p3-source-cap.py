import asyncio, importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('boundaries',Path(__file__).with_name('nyx-p3-boundaries.py'))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
async def main():
    s=a.Script();r=await s.run()
    assert len(s.searched)==1 and len(s.fetched)==6 and len(s.saved)==6,r
    assert all(len(x[0])<=6000 for x in s.saved)
    reduced=a.Script();await reduced.run(budget={'max_sources':2})
    assert len(reduced.fetched)==2
    print('PASS model-selected reads respect six-source cap and per-run reductions')
asyncio.run(main())
