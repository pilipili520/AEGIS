"""500 MS/s fixed-vs-random TVLA capture with resumable, lossless batches."""
import argparse,json,time,hashlib,secrets,zipfile,os,shutil
from datetime import datetime
from pathlib import Path
import numpy as np
from hardware import ROOT,session,prepare,result,verify_ready,KEY,PT
from reference import encrypt
from pico import Scope,RANGES,ps,check
from tvla_stats import Moments

def json_write(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2),encoding='utf-8');tmp.replace(path)

def save_batch(path,arrays):
    tmp=path.with_suffix('.tmp')
    with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as z:
        for name,data in arrays.items():
            with z.open(name+'.npy','w',force_zip64=True) as f:np.lib.format.write_array(f,np.asarray(data),allow_pickle=False)
    digest=hashlib.sha256(tmp.read_bytes()).hexdigest();tmp.replace(path)
    return digest

def capture(t,scope,seq,pt):
    prepare(t,seq,KEY,pt);scope.arm();time.sleep(.003);t.resume()
    (a,b),overflow=scope.fetch(timeout=8)
    ct,state=result(t,seq)
    return a,b,overflow,ct,state

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--per-group',type=int,default=5000);ap.add_argument('--batch',type=int,default=100)
    ap.add_argument('--out',type=Path,required=True);ap.add_argument('--resume',action='store_true')
    args=ap.parse_args();out=args.out
    samples,pre,tb=1200000,10000,1
    if args.per_group<4 or args.per_group%2 or args.batch%2 or args.batch<2:raise ValueError('Even group and batch sizes required')
    if shutil.disk_usage(out.parent).free<12*1024**3:raise RuntimeError('Need at least 12 GiB free for compressed acquisition')
    firmware_hash=hashlib.sha256((ROOT/'build/clefia128.bin').read_bytes()).hexdigest()
    if args.resume:
        config=json.loads((out/'config.json').read_text());progress=json.loads((out/'progress.json').read_text())
        if config['per_group']!=args.per_group or config['batch_size']!=args.batch or config['firmware_sha256']!=firmware_hash:
            raise ValueError('Resume configuration/firmware mismatch')
        with np.load(out/'schedule.npz') as d:labels=d['labels'];plaintext=d['plaintext']
        m=Moments.load(out/progress['checkpoint']);start=progress['committed'];rejections=progress['rejections']
    else:
        out.mkdir(parents=True,exist_ok=False);(out/'batches').mkdir();(out/'rejected').mkdir()
        # Independently shuffle each pair: exactly one fixed and one random per pair.
        labels=np.empty(2*args.per_group,dtype=np.uint8)
        for pair in range(args.per_group):
            first=secrets.randbelow(2);labels[2*pair:2*pair+2]=[first,1-first]
        plaintext=np.empty((len(labels),16),dtype=np.uint8)
        for i,g in enumerate(labels):plaintext[i]=np.frombuffer(PT if g==0 else secrets.token_bytes(16),dtype=np.uint8)
        np.savez(out/'schedule.npz',labels=labels,plaintext=plaintext)
        config={'created':datetime.now().astimezone().isoformat(),'per_group':args.per_group,'batch_size':args.batch,
          'test':'first-order fixed-vs-random plaintext, fixed key','threshold':4.5,'key_hex':KEY.hex(),'fixed_plaintext_hex':PT.hex(),
          'group_labels':{'0':'fixed','1':'random'},'schedule':'random order within each fixed/random pair',
          'half_split':'first half of acquisition pairs vs second half; each has equal group counts',
          'samples':samples,'pre_trigger':pre,'timebase':tb,'expected_interval_ns':2.0,'sample_rate_hz':500000000,
          'a_range_mv':5000,'b_range_mv':1000,'coupling':'DC','trigger':'A/PA6 rising 1.5V; no auto trigger',
          'optimization':'-O0 -fno-lto','flash_acr_expected':0,'core_clock_hz_nominal':16000000,
          'trigger_region':'single-block encryption; key schedule excluded','firmware_sha256':firmware_hash,
          'preprocessing':'none: no filtering, baseline subtraction, interpolation, resampling or elastic alignment',
          'transport':'SWD setup/readout outside capture window',
          'rejection_policy':'retry same scheduled input only for ADC overflow or invalid trigger; abort after 10 attempts/input or 100 rejected total; ciphertext/state errors are fatal',
          'raw_storage':'lossless zip-level-1 batches containing full A/B int16 ADC, plaintext, ciphertext, labels and trigger metrics',
          'method_source':'https://www.rambus.com/wp-content/uploads/2015/08/TVLA-DTR-with-AES.pdf'}
        json_write(out/'config.json',config)
        m=Moments(samples);start=0;rejections=0
        progress={'status':'starting','committed':0,'rejections':0,'batches':[]}
    begun=time.monotonic();base=start;total=len(labels);log=(out/'events.jsonl').open('a',encoding='utf-8',buffering=1)
    try:
        with session() as sess,Scope() as scope:
            t=sess.target;verify_ready(t)
            # Compare the actual board flash with the captured-build fingerprint.
            binary=(ROOT/'build/clefia128.bin').read_bytes()
            if bytes(t.read_memory_block8(0x08000000,len(binary)))!=binary:raise RuntimeError('Board firmware differs from build')
            scope.configure(tb,samples,pre,'1V')
            if scope.dt!=2.0:raise RuntimeError('Expected true 500MS/s acquisition')
            config.update(scope=scope.info,max_adc=scope.adc,interval_ns=scope.dt)
            json_write(out/'config.json',config)
            # Uncounted startup warmup and electrical sanity check.
            for w in range(10):
                a,b,ov,ct,st=capture(t,scope,100000+w,PT)
                if ct!=encrypt(KEY,PT):raise RuntimeError('Warmup KAT failed')
            for begin in range(start,total,args.batch):
                stop=min(begin+args.batch,total);n=stop-begin
                aa=np.empty((n,samples),dtype=np.int16);bb=np.empty_like(aa)
                cc=np.empty((n,16),dtype=np.uint8);rr=np.empty(n,dtype=np.int32);ff=np.empty_like(rr)
                cycles=np.empty(n,dtype=np.uint32);accepted_attempt=np.empty(n,dtype=np.uint8)
                for j,i in enumerate(range(begin,stop)):
                    pt=plaintext[i].tobytes();expected=encrypt(KEY,pt)
                    for attempt in range(1,11):
                        a,b,ov,ct,st=capture(t,scope,200000+i*10+attempt,pt)
                        if ct!=expected:raise RuntimeError(f'Ciphertext mismatch at {i}')
                        high=a>round(1500/5000*scope.adc)
                        rise=np.flatnonzero(~high[:-1]&high[1:])+1;fall=np.flatnonzero(high[:-1]&~high[1:])+1
                        good=(len(rise)==1 and len(fall)==1 and fall[0]>rise[0] and abs(int(rise[0])-pre)<=8)
                        if not (ov&3) and good:break
                        rejections+=1
                        reason={'index':i,'group':int(labels[i]),'attempt':attempt,'overflow':ov,'rises':rise.tolist(),'falls':fall.tolist()}
                        save_batch(out/'rejected'/f'{i:05d}_{attempt:02d}.npz',{'trigger_adc':a,'power_adc':b})
                        log.write(json.dumps({'rejected':reason})+'\n')
                        if rejections>=100 or attempt==10:raise RuntimeError('Rejection limit reached')
                    aa[j]=a;bb[j]=b;cc[j]=np.frombuffer(ct,dtype=np.uint8);rr[j]=rise[0];ff[j]=fall[0];cycles[j]=st[7];accepted_attempt[j]=attempt
                    half=int(i>=args.per_group);m.add(half,int(labels[i]),b)
                    if (i+1)%50==0:
                        elapsed=time.monotonic()-begun;rate=(i+1-base)/elapsed
                        print(f'{i+1}/{total} acquired; groups={m.n.sum(0).tolist()}; rejected={rejections}; {rate:.2f} traces/s; ETA={(total-i-1)/rate/60:.1f} min',flush=True)
                name=f'batches/batch_{begin:05d}_{stop:05d}.npz'
                digest=save_batch(out/name,{'trigger_adc':aa,'power_adc':bb,'plaintext':plaintext[begin:stop],
                    'ciphertext':cc,'labels':labels[begin:stop],'rise':rr,'fall':ff,'cycles':cycles,'attempt':accepted_attempt})
                # Two checkpoint slots: old committed checkpoint survives until the new progress file is atomic.
                checkpoint=f'checkpoint_{(stop//args.batch)%2}.npz';tmp=out/(checkpoint+'.tmp.npz')
                m.save(tmp);tmp.replace(out/checkpoint)
                progress.update(status='capturing',committed=stop,groups=m.n.sum(0).tolist(),rejections=rejections,checkpoint=checkpoint,
                    updated=datetime.now().astimezone().isoformat(),elapsed_session_s=time.monotonic()-begun)
                progress['batches'].append({'file':name,'begin':begin,'stop':stop,'sha256':digest})
                json_write(out/'progress.json',progress)
                log.write(json.dumps({'committed':stop,'sha256':digest,'file':name})+'\n')
            t.resume()
        assert m.n.sum(0).tolist()==[args.per_group,args.per_group]
        progress.update(status='capture_complete',updated=datetime.now().astimezone().isoformat())
        json_write(out/'progress.json',progress)
        print('CAPTURE COMPLETE',out,flush=True)
    except BaseException as e:
        progress.update(status='failed',error=repr(e),rejections=rejections)
        json_write(out/'progress.json',progress);raise
    finally:log.close()
if __name__=='__main__':main()
