import argparse,json,time,hashlib,sys
from datetime import datetime
from pathlib import Path
from hardware import ROOT,session,prepare,result,KEY,PT,verify_ready
from reference import encrypt
from pico import Scope,RANGES
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--count',type=int,default=100); ap.add_argument('--timebase',type=int,default=1)
 ap.add_argument('--samples',type=int,default=1200000); ap.add_argument('--pre',type=int,default=10000)
 ap.add_argument('--b-range',choices=RANGES,default='1V'); ap.add_argument('--out',type=Path)
 args=ap.parse_args()
 if args.count<1 or not 1<=args.pre<args.samples: raise ValueError('Invalid capture dimensions')
 out=args.out or ROOT/'captures'/datetime.now().strftime('%Y%m%d_%H%M%S')
 out.mkdir(parents=True,exist_ok=False)
 meta={'status':'running','created':datetime.now().astimezone().isoformat(),'count_requested':args.count,
       'a':'PA6 trigger, DC, +/-5V, rising edge 1.5V','b':f'power input, DC, +/-{args.b_range}',
       'timebase':args.timebase,'samples':args.samples,'pre':args.pre,'auto_trigger_ms':0,
       'key_hex':KEY.hex(),'plaintext_mode':'first RFC vector, then random bytes from OS',
       'trigger_region':'single-block encryption only; key schedule excluded',
       'core_clock_hz_nominal':16000000,'optimization':'-O0 -fno-lto',
       'firmware_sha256':hashlib.sha256((ROOT/'build/clefia128.bin').read_bytes()).hexdigest(),
       'transport':'SWD mailbox; core halted for setup/results; no SWD traffic during capture'}
 traces_a=[];traces_b=[];pts=[];cts=[];rows=[]
 try:
  with session() as sess,Scope() as scope:
   t=sess.target; verify_ready(t); scope.configure(args.timebase,args.samples,args.pre,args.b_range)
   meta.update(scope=scope.info,interval_ns=scope.dt,sample_rate_hz=1e9/scope.dt,max_adc=scope.adc,b_range_mv=RANGES[args.b_range])
   for i in range(args.count):
    pt=PT if i==0 else __import__('secrets').token_bytes(16); expected=encrypt(KEY,pt)
    seq=i+100;prepare(t,seq,KEY,pt);scope.arm();time.sleep(.003);t.resume()
    (a,b),overflow=scope.fetch()
    ct,st=result(t,seq)
    if ct!=expected: raise RuntimeError(f'Ciphertext mismatch at trace {i}')
    high=a>round(1500/5000*scope.adc)
    rises=np.flatnonzero(~high[:-1]&high[1:])+1;falls=np.flatnonzero(high[:-1]&~high[1:])+1
    if len(rises)!=1 or len(falls)!=1 or falls[0]<=rises[0]: raise RuntimeError(f'Incomplete or multiple triggers: {rises}, {falls}')
    rise,fall=int(rises[0]),int(falls[0])
    if abs(rise-args.pre)>8: raise RuntimeError(f'Trigger misaligned: {rise}')
    if overflow&3: raise RuntimeError(f'ADC overflow on A/B: {overflow}; increase range')
    row={'index':i,'rise':rise,'fall':fall,'pulse_us':(fall-rise)*scope.dt/1000,'cycles':st[7],
         'overflow':overflow,'flash_acr':st[6],'ciphertext_verified':True}
    rows.append(row);traces_a.append(a);traces_b.append(b);pts.append(np.frombuffer(pt,dtype=np.uint8));cts.append(np.frombuffer(ct,dtype=np.uint8))
    if (i+1)%10==0 or i==0: print(f'{i+1}/{args.count}: pulse={row["pulse_us"]:.3f} us B=[{b.min()*float(RANGES[args.b_range])/scope.adc:.3f},{b.max()*float(RANGES[args.b_range])/scope.adc:.3f}] mV',flush=True)
   t.resume()
   meta['flash_acr']=0
  meta['status']='complete'
 except BaseException as e:
  meta.update(status='failed',error=repr(e)); raise
 finally:
  meta['count_captured']=len(rows);meta['traces']=rows
  if rows:
   a=np.stack(traces_a);b=np.stack(traces_b)
   np.savez_compressed(out/'traces.npz',trigger_adc=a,power_adc=b,plaintext=np.stack(pts),ciphertext=np.stack(cts),
                       key=np.frombuffer(KEY,dtype=np.uint8),interval_ns=meta['interval_ns'],pre_trigger=args.pre,
                       max_adc=meta['max_adc'],a_range_mv=5000,b_range_mv=RANGES[args.b_range])
   meta['power_min_mv']=float(b.min()*float(RANGES[args.b_range])/meta['max_adc'])
   meta['power_max_mv']=float(b.max()*float(RANGES[args.b_range])/meta['max_adc'])
   meta['pulse_us_mean']=float(np.mean([r['pulse_us'] for r in rows]))
   x=(np.arange(args.samples)-args.pre)*meta['interval_ns']/1000
   fig,axs=plt.subplots(2,1,figsize=(13,7),sharex=True)
   axs[0].plot(x,a[0]*5000.0/meta['max_adc']/1000,lw=.7);axs[0].set_ylabel('A: PA6 trigger (V)')
   axs[1].plot(x,b[0]*float(RANGES[args.b_range])/meta['max_adc'],lw=.55,label='Trace 0')
   if len(rows)>1: axs[1].plot(x,b.mean(axis=0)*float(RANGES[args.b_range])/meta['max_adc'],lw=.8,label=f'Mean of {len(rows)} traces',alpha=.85)
   axs[1].set_ylabel('B: measured signal (mV)');axs[1].set_xlabel('Time relative to trigger (us)');axs[1].legend()
   for ax in axs: ax.grid(alpha=.25);ax.axvline(0,color='green',ls='--',lw=.8);ax.axvline(meta['pulse_us_mean'],color='red',ls='--',lw=.8)
   fig.suptitle(f'CLEFIA-128 | STM32F415 | O0 | cache/prefetch OFF | {len(rows)} traces | {meta["interval_ns"]:g} ns/sample')
   fig.tight_layout();fig.savefig(out/'waveform.png',dpi=160);plt.close(fig)
   # Detail plot shows acquisition resolution, without claiming leakage strength.
   n0=rows[0]['rise'];n1=min(n0+int(30_000/meta['interval_ns']),args.samples)
   fig,ax=plt.subplots(figsize=(13,3.5));ax.plot(x[n0:n1],b[0,n0:n1]*float(RANGES[args.b_range])/meta['max_adc'],lw=.7)
   ax.set(xlabel='Time relative to trigger (us)',ylabel='B signal (mV)',title='First 30 us of encryption, trace 0');ax.grid(alpha=.25)
   fig.tight_layout();fig.savefig(out/'waveform_zoom.png',dpi=160);plt.close(fig)
  (out/'metadata.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
  print('Saved:',out,flush=True)
if __name__=='__main__':main()



