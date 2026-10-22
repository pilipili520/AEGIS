import sys,json,ctypes as C
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from pico import Scope,ps,check
with Scope() as s:
 for ch,en in [(0,1),(1,1),(2,0),(3,0)]:
  check(ps.ps3000aSetChannel(s.h,ch,en,1,8 if ch!=1 else 6,0.0))
 rows=[]
 for tb in range(4):
  dt=C.c_float();maxn=C.c_int32()
  status=ps.ps3000aGetTimebase2(s.h,tb,1200000,C.byref(dt),0,C.byref(maxn),0)
  rows.append(dict(timebase=tb,status=status,interval_ns=dt.value,max_samples=maxn.value))
 report=dict(scope=s.info,enabled_channels=['A','B'],requested_samples=1200000,timebases=rows)
 print(json.dumps(report,indent=2))
 (Path(__file__).resolve().parents[1]/'build/sampling_capabilities.json').write_text(json.dumps(report,indent=2))
