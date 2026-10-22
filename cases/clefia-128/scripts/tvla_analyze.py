"""Analyze committed TVLA batches and produce first-order Welch results."""
import argparse,json,hashlib
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from tvla_stats import Moments,welch
from reference import encrypt

def summary(t,start,stop,dt,pre):
    v=t[start:stop];defined=~np.isnan(v)
    if not defined.any():return {'defined_samples':0}
    absolute=np.abs(v);absolute[np.isnan(absolute)]=-1
    local=int(np.argmax(absolute));idx=start+local
    exceeds=np.flatnonzero(absolute>4.5)
    return {'start_sample':start,'stop_sample_exclusive':stop,'defined_samples':int(defined.sum()),
       'nan_samples':int(np.isnan(v).sum()),'infinite_samples':int(np.isinf(v).sum()),
       'max_abs_t':float(absolute[local]),'signed_t_at_peak':float(t[idx]),'peak_sample':idx,
       'peak_time_us':(idx-pre)*dt/1000,'samples_abs_t_gt_4_5':int(len(exceeds)),
       'first_crossing_time_us':float((start+exceeds[0]-pre)*dt/1000) if len(exceeds) else None}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('folder',type=Path)
    ap.add_argument('--verify-raw',action='store_true',help='Recompute exact sums from all saved raw B waveforms')
    args=ap.parse_args();out=args.folder
    config=json.loads((out/'config.json').read_text());progress=json.loads((out/'progress.json').read_text())
    if progress['status']!='capture_complete':raise RuntimeError('Full acquisition has not completed')
    m=Moments.load(out/progress['checkpoint']);expected=config['per_group']
    if m.n.sum(0).tolist()!=[expected,expected]:raise RuntimeError('Incorrect group sizes')
    rises=[];falls=[];cycles=[];labels=[];retries=[];verified=0;prev=0
    recompute=Moments(config['samples']) if args.verify_raw else None
    with np.load(out/'schedule.npz') as d:scheduled_labels=d['labels'];scheduled_plaintext=d['plaintext']
    for bi,entry in enumerate(progress['batches']):
        p=out/entry['file']
        if entry['begin']!=prev:raise RuntimeError('Non-contiguous batches')
        prev=entry['stop']
        if hashlib.sha256(p.read_bytes()).hexdigest()!=entry['sha256']:raise RuntimeError('Batch checksum mismatch')
        with np.load(p) as d:
            lab=d['labels'];pt=d['plaintext'];ct=d['ciphertext'];lo=entry['begin'];hi=entry['stop']
            np.testing.assert_array_equal(lab,scheduled_labels[lo:hi]);np.testing.assert_array_equal(pt,scheduled_plaintext[lo:hi])
            if not all(encrypt(bytes.fromhex(config['key_hex']),a.tobytes())==b.tobytes() for a,b in zip(pt,ct)):
                raise RuntimeError('Saved ciphertext mismatch')
            rises.extend(d['rise'].tolist());falls.extend(d['fall'].tolist());cycles.extend(d['cycles'].tolist())
            labels.extend(lab.tolist());retries.extend(d['attempt'].tolist());verified+=len(lab)
            if recompute is not None:
                b=d['power_adc'];a=d['trigger_adc']
                if a.shape!=b.shape!=(hi-lo,config['samples']):raise RuntimeError('Raw sample shape mismatch')
                for j,g in enumerate(lab):
                    recompute.add(int(lo+j>=expected),int(g),b[j])
                    high=a[j]>round(1500/5000*config['max_adc'])
                    ri=np.flatnonzero(~high[:-1]&high[1:])+1;fa=np.flatnonzero(high[:-1]&~high[1:])+1
                    if len(ri)!=1 or len(fa)!=1 or ri[0]!=rises[lo+j] or fa[0]!=falls[lo+j]:raise RuntimeError('Saved trigger mismatch')
        if (bi+1)%10==0:print(f'Checked {verified}/{2*expected} saved traces',flush=True)
    if prev!=2*expected:raise RuntimeError('Missing traces')
    if recompute is not None:
        for name in ['n','s','ss']:np.testing.assert_array_equal(getattr(recompute,name),getattr(m,name))
    dt=config['interval_ns'];pre=config['pre_trigger'];size=config['samples'];n=m.n.sum(0)
    full,mean,var=welch(n,m.s.sum(0),m.ss.sum(0))
    half0,_,_=welch(m.n[0],m.s[0],m.ss[0]);half1,_,_=welch(m.n[1],m.s[1],m.ss[1])
    # Predeclared 5us guards keep GPIO transition edges out of the interior statistic.
    guard=round(5000/dt);active_start=max(rises)+guard;active_stop=min(falls)-guard
    windows={'full_record':(0,size),'encryption_interior_5us_guard':(active_start,active_stop),
             'pretrigger_control':(0,min(rises)-guard),'posttrigger_control':(max(falls)+guard,size)}
    metrics={name:summary(full,a,b,dt,pre) for name,(a,b) in windows.items()}
    same=(np.abs(half0)>4.5)&(np.abs(half1)>4.5)&(np.sign(half0)==np.sign(half1))
    pulse=(np.array(falls)-np.array(rises))*dt/1000;lab=np.array(labels)
    report={'test':'first-order Welch, fixed vs random plaintext, fixed key','groups':{'fixed':int(n[0]),'random':int(n[1])},
        'sample_rate_hz':config['sample_rate_hz'],'samples_per_trace':size,'threshold':4.5,
        'preprocessing':config['preprocessing'],'statistic':'(mean_fixed-mean_random)/sqrt(sample_var_fixed/n_fixed+sample_var_random/n_random)',
        'variance_ddof':1,'metrics':metrics,'half_group_counts':m.n.tolist(),
        'half0_interior':summary(half0,active_start,active_stop,dt,pre),'half1_interior':summary(half1,active_start,active_stop,dt,pre),
        'both_halves_same_sign_crossing_samples_interior':int(same[active_start:active_stop].sum()),
        'ciphertexts_verified':verified,'raw_moments_and_triggers_recomputed':bool(args.verify_raw),
        'rejected_attempts':progress['rejections'],'accepted_traces_requiring_retry':int((np.array(retries)>1).sum()),
        'trigger_rise_sample_min':int(min(rises)),'trigger_rise_sample_max':int(max(rises)),
        'pulse_us_min':float(pulse.min()),'pulse_us_max':float(pulse.max()),
        'pulse_us_mean_fixed':float(pulse[lab==0].mean()),'pulse_us_mean_random':float(pulse[lab==1].mean()),
        'dwt_cycles':sorted(set(cycles)),
        'interpretation':'Crossings indicate evidence of differing measured distributions under this acquisition; not a key-recovery result. Half tests use chronological halves of the same campaign, not independent full reruns. A non-crossing result would not prove absence of leakage.',
        'method_source':config['method_source']}
    np.savez_compressed(out/'tvla_results.npz',t=full,t_half0=half0,t_half1=half1,
        mean_adc=mean,variance_adc=var,n=n,interval_ns=dt,pre_trigger=pre,
        interior_start=active_start,interior_stop=active_stop)
    (out/'tvla_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    time_us=(np.arange(size)-pre)*dt/1000;scale=config['b_range_mv']/config['max_adc']
    fig,axs=plt.subplots(3,1,figsize=(14,10),sharex=True)
    axs[0].plot(time_us,mean[0]*scale,lw=.7,label=f'Fixed, n={n[0]}');axs[0].plot(time_us,mean[1]*scale,lw=.7,label=f'Random, n={n[1]}')
    axs[0].set_ylabel('Mean B signal (mV)');axs[0].legend()
    axs[1].plot(time_us,full,lw=.5,color='tab:purple',label='All traces');axs[1].set_ylabel('Welch t, first order')
    axs[2].plot(time_us,half0,lw=.5,alpha=.8,label='First chronological half');axs[2].plot(time_us,half1,lw=.5,alpha=.7,label='Second chronological half')
    axs[2].set_ylabel('Welch t, half checks');axs[2].set_xlabel('Time relative to trigger (us)');axs[2].legend()
    for ax in axs:
        ax.grid(alpha=.2);ax.axvline((active_start-pre)*dt/1000,color='grey',ls=':',lw=.8);ax.axvline((active_stop-pre)*dt/1000,color='grey',ls=':',lw=.8)
    for ax in axs[1:]:ax.axhline(4.5,color='red',ls='--',lw=.8);ax.axhline(-4.5,color='red',ls='--',lw=.8)
    peak=metrics['encryption_interior_5us_guard']['max_abs_t']
    fig.suptitle(f'CLEFIA-128 first-order TVLA | {n[0]} fixed vs {n[1]} random | 500 MS/s\nO0, cache/prefetch OFF | encryption interior max |t| = {peak:.3f}')
    fig.tight_layout();fig.savefig(out/'tvla.png',dpi=160);plt.close(fig)
    text=f'''# CLEFIA-128 一阶 TVLA：5000 vs 5000\n\n固定明文与随机明文各 {n[0]} 条，固定密钥，随机交错采集。采样率 500 MS/s，每条 {size:,} 点，O0，Flash 缓存与预取关闭。\n\n- 加密内部窗口（两端各排除 5 us 的 GPIO 边沿）：max |t| = {peak:.6f}。\n- 内部窗口 |t| > 4.5 的样本点：{metrics['encryption_interior_5us_guard']['samples_abs_t_gt_4_5']:,}。\n- 全记录 max |t| = {metrics['full_record']['max_abs_t']:.6f}。\n- 前后半数据在相同采样点、同符号且都越过阈值的内部点：{report['both_halves_same_sign_crossing_samples_interior']:,}。\n- 密文校验 {verified}/{verified}；拒收重采 {progress['rejections']} 次。\n- 原始数据重新累计及触发复核：{args.verify_raw}。\n\n原始 A/B 波形按批保存在 batches/，分组、明密文和触发位置随批保存。统计没有滤波、时间伸缩或按 t 值选点。pretrigger_control / posttrigger_control 的结果见 tvla_report.json；t 数组、组均值、无偏样本方差见 tvla_results.npz。\n\n|t| > 4.5 是本次测试的泄漏检测阈值；多采样点和测量系统效应需要结合前后半重复性及控制窗口判断。本次并非独立的两次 5000 vs 5000 测试，也未执行密钥恢复。未越阈值不能证明没有泄漏。\n\n方法参考：[TVLA Derived Test Requirements](https://www.rambus.com/wp-content/uploads/2015/08/TVLA-DTR-with-AES.pdf)。\n'''
    (out/'RESULTS.md').write_text(text,encoding='utf-8')
    print(json.dumps(report,indent=2),flush=True)
if __name__=='__main__':main()
