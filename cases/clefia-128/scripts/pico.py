"""PicoScope ps3000a block acquisition; A=PA6, B=power signal."""
import os,ctypes as C,time
DLL=os.environ.get('PICO_SDK_LIB',r'C:\Program Files\Pico Technology\SDK\lib')
os.environ['PATH']=DLL+os.pathsep+os.environ.get('PATH','')
_dll_cookie=os.add_dll_directory(DLL)
from picosdk.ps3000a import ps3000a as ps
from picosdk.functions import assert_pico_ok as check
import numpy as np
RANGES={'10mV':10,'20mV':20,'50mV':50,'100mV':100,'200mV':200,'500mV':500,'1V':1000,'2V':2000,'5V':5000,'10V':10000,'20V':20000}
class Scope:
 def __init__(self): self.h=C.c_int16(); self.open=False
 def __enter__(self):
  status=ps.ps3000aOpenUnit(C.byref(self.h),None)
  if status in (282,286): check(ps.ps3000aChangePowerSource(self.h,status))
  else: check(status)
  self.open=True
  v=C.c_int16(); check(ps.ps3000aMaximumValue(self.h,C.byref(v))); self.adc=v.value
  self.info={}
  for label,code in [('driver',0),('model',3),('serial',4)]:
   b=C.create_string_buffer(256); required=C.c_int16()
   check(ps.ps3000aGetUnitInfo(self.h,b,len(b),C.byref(required),code)); self.info[label]=b.value.decode()
  print('PicoScope',self.info,flush=True)
  return self
 def __exit__(self,*exc):
  if self.open:
   try: check(ps.ps3000aStop(self.h))
   finally: check(ps.ps3000aCloseUnit(self.h)); self.open=False
 def configure(self,tb,n,pre,b_range):
  self.tb,self.n,self.pre,self.b_range=tb,n,pre,b_range
  for channel,enabled,rng in [(0,1,'5V'),(1,1,b_range),(2,0,'5V'),(3,0,'5V')]:
   check(ps.ps3000aSetChannel(self.h,channel,enabled,1,ps.PS3000A_RANGE['PS3000A_'+rng.upper()],0.0))
  dt=C.c_float(); maximum=C.c_int32()
  check(ps.ps3000aGetTimebase2(self.h,tb,n,C.byref(dt),0,C.byref(maximum),0))
  if n>maximum.value: raise ValueError('Capture exceeds device memory')
  self.dt=dt.value
  check(ps.ps3000aSetSimpleTrigger(self.h,1,0,round(1500/5000*self.adc),2,0,0))
  self.buffers=[(C.c_int16*n)(),(C.c_int16*n)()]
  for ch,b in enumerate(self.buffers): check(ps.ps3000aSetDataBuffer(self.h,ch,b,n,0,0))
 def arm(self):
  busy=C.c_int32()
  check(ps.ps3000aRunBlock(self.h,self.pre,self.n-self.pre,self.tb,0,C.byref(busy),0,None,None))
 def fetch(self,timeout=5):
  deadline=time.monotonic()+timeout; ready=C.c_int16()
  while not ready.value:
   check(ps.ps3000aIsReady(self.h,C.byref(ready)))
   if time.monotonic()>deadline: raise TimeoutError('No complete PA6 trigger capture within timeout')
   time.sleep(.001)
  count=C.c_uint32(self.n); overflow=C.c_int16()
  check(ps.ps3000aGetValues(self.h,0,C.byref(count),1,0,0,C.byref(overflow)))
  check(ps.ps3000aStop(self.h))
  if count.value!=self.n: raise RuntimeError(f'Short capture {count.value}/{self.n}')
  return [np.ctypeslib.as_array(b).copy() for b in self.buffers],overflow.value
