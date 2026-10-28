"""Offline context benchmark using real CLI fixtures.

Usage: python tools/benchmark_context.py /absolute/checkout 500 label
Runs 12 samples for context rendering and host-event admission.
"""
import sys,json,time,cProfile,pstats,statistics
from pathlib import Path
sys.path[:0]=[sys.argv[1],str(Path(sys.argv[1])/'tests')]
from tests.test_native_lifecycle_contract import NativeLifecycle
from scripts.cao_guidance import render_context
n=int(sys.argv[2]); case=NativeLifecycle('runTest'); case.setUp()
def measure(fn):
 samples=[]
 for _ in range(12):
  t=time.perf_counter();out=fn();samples.append(time.perf_counter()-t)
 p=cProfile.Profile();p.enable();out=fn();p.disable()
 counts={}
 for k,v in pstats.Stats(p).stats.items():
  if k[2] in {'code_snapshot','verification_inputs','project_prerequisite','capture'}:counts[k[2]]=counts.get(k[2],0)+v[1]
 return {'p50_seconds':statistics.median(samples),'p95_seconds':sorted(samples)[-1],'samples':len(samples),'calls':counts,'output_bytes':len(str(out).encode())}
try:
 folder=case.repo/'products';folder.mkdir()
 for i in range(n):(folder/f'module_{i}.py').write_text('VALUE = '+str(i)+'\n'+'# sample\n'*100)
 case.bind()
 work=case.work()
 from scripts.evidence_domains import capture
 product_paths=sorted(capture(case.repo,work)['product'])
 result={'version':sys.argv[3],'extra_files':n,'extra_file_bytes':sum(p.stat().st_size for p in folder.iterdir())}
 result['scan_scope']={'product_files':len(product_paths),'product_bytes':sum((case.repo/p).stat().st_size for p in product_paths if (case.repo/p).is_file()),'extra_file_root':'products/'}
 result['render_context']=measure(lambda:render_context(case.repo,work))
 payload={'event':'pre_tool','session_id':'benchmark','toolName':'write','input':{'path':'feature.py','content':'valid = True\n'}}
 result['host_event']=measure(lambda:case.fixture.cli('host-event','--host','pi',payload=payload))
 print(json.dumps(result),flush=True)
finally:case.doCleanups()
