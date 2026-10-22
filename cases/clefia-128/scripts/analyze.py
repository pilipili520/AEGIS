"""Validate saved ADC data and regenerate plots without integer overflow."""
import sys,json,hashlib
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from reference import encrypt

def analyze(folder):
 folder=Path(folder);meta=json.loads((folder/'metadata.json').read_text())
 with np.load(folder/'traces.npz') as d:
  a=d['trigger_adc'];b=d['power_adc'];pts=d['plaintext'];cts=d['ciphertext'];key=d['key'].tobytes()
  dt=float(d['interval_ns']); pre=int(d['pre_trigger']); maximum=int(d['max_adc']);br=float(d['b_range_mv'])
  assert a.shape==b.shape==(meta['count_captured'],meta['samples'])
  assert len(pts)==len(cts)==len(a)
  assert all(encrypt(key,p.tobytes())==c.tobytes() for p,c in zip(pts,cts))
  assert all(r['overflow']==0 and r['flash_acr']==0 for r in meta['traces'])
  pulse=np.array([r['pulse_us'] for r in meta['traces']]);rise=np.array([r['rise'] for r in meta['traces']])
  qa={'shape':list(b.shape),'ciphertexts_verified':len(a),'adc_overflow_count':0,
      'trigger_rise_sample_min':int(rise.min()),'trigger_rise_sample_max':int(rise.max()),
      'pulse_us_min':float(pulse.min()),'pulse_us_mean':float(pulse.mean()),'pulse_us_max':float(pulse.max()),
      'dwt_cycle_values':sorted(set(r['cycles'] for r in meta['traces'])),
      'power_min_mv':float(b.min())*br/maximum,'power_max_mv':float(b.max())*br/maximum,
      'traces_sha256':hashlib.sha256((folder/'traces.npz').read_bytes()).hexdigest()}
  meta.update(power_min_mv=qa['power_min_mv'],power_max_mv=qa['power_max_mv'])
  (folder/'metadata.json').write_text(json.dumps(meta,indent=2))
  (folder/'quality.json').write_text(json.dumps(qa,indent=2))
  x=(np.arange(b.shape[1])-pre)*dt/1000
  fig,axs=plt.subplots(2,1,figsize=(13,7),sharex=True)
  axs[0].plot(x,a[0].astype(float)*5/maximum,lw=.7);axs[0].set_ylabel('A: PA6 trigger (V)')
  axs[1].plot(x,b[0].astype(float)*br/maximum,lw=.55,label='Trace 0')
  if len(a)>1: axs[1].plot(x,b.mean(axis=0)*br/maximum,lw=.8,label=f'Mean of {len(a)} traces')
  axs[1].legend();axs[1].set_ylabel('B: measured signal (mV)');axs[1].set_xlabel('Time relative to trigger (us)')
  for ax in axs:
   ax.grid(alpha=.25);ax.axvline(0,color='green',ls='--',lw=.8);ax.axvline(pulse.mean(),color='red',ls='--',lw=.8)
  fig.suptitle(f'CLEFIA-128 | STM32F415 | O0 | cache/prefetch OFF | {len(a)} traces | {dt:g} ns/sample')
  fig.tight_layout();fig.savefig(folder/'waveform.png',dpi=160);plt.close(fig)
  lo=int(rise[0]);hi=min(lo+int(30000/dt),b.shape[1])
  fig,ax=plt.subplots(figsize=(13,3.5));ax.plot(x[lo:hi],b[0,lo:hi].astype(float)*br/maximum,lw=.7)
  ax.set(xlabel='Time relative to trigger (us)',ylabel='B signal (mV)',title='First 30 us of encryption, trace 0');ax.grid(alpha=.25)
  fig.tight_layout();fig.savefig(folder/'waveform_zoom.png',dpi=160);plt.close(fig)
  print(json.dumps(qa,indent=2))
if __name__=='__main__':
 for p in sys.argv[1:]:analyze(p)
