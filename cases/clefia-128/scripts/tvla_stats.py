"""First-order Welch TVLA from exact integer sums; no trace filtering."""
import numpy as np
class Moments:
    def __init__(self, samples):
        self.n=np.zeros((2,2),dtype=np.int64) # chronological half, group (0=fixed)
        self.s=np.zeros((2,2,samples),dtype=np.int64)
        self.ss=np.zeros_like(self.s)
    def add(self, half, group, data):
        v=np.asarray(data,dtype=np.int64)
        self.n[half,group]+=1
        self.s[half,group]+=v
        self.ss[half,group]+=v*v
    def save(self,path):
        np.savez(path,n=self.n,s=self.s,ss=self.ss)
    @classmethod
    def load(cls,path):
        with np.load(path) as a:
            m=cls(a['s'].shape[-1]);m.n=a['n'];m.s=a['s'];m.ss=a['ss']
        return m

def welch(n,s,ss):
    n=np.asarray(n,dtype=np.int64)
    if np.any(n<2): raise ValueError('At least two observations per group required')
    nf=n[:,None].astype(np.float64)
    mean=s.astype(np.float64)/nf
    var=(ss.astype(np.float64)-s.astype(np.float64)*mean)/(nf-1)
    # Roundoff may make a constant sample infinitesimally negative.
    if np.any(var < -1e-5): raise ValueError('Invalid negative sample variance')
    var=np.maximum(var,0)
    se=np.sqrt(var[0]/n[0]+var[1]/n[1])
    with np.errstate(divide='ignore',invalid='ignore'):
        t=(mean[0]-mean[1])/se
    return t,mean,var

def test():
    rng=np.random.default_rng(54321)
    a=rng.integers(-30000,30000,(31,47),dtype=np.int16)
    b=rng.integers(-20000,20000,(39,47),dtype=np.int16)
    a[:,0]=3;b[:,0]=3;a[:,1]=1;b[:,1]=2
    m=Moments(47)
    for g,x in enumerate([a,b]):
        for row in x:m.add(0,g,row)
    t,mean,var=welch(m.n[0],m.s[0],m.ss[0])
    af=a.astype(float);bf=b.astype(float)
    with np.errstate(divide='ignore',invalid='ignore'):
        expected=(af.mean(0)-bf.mean(0))/np.sqrt(af.var(0,ddof=1)/len(a)+bf.var(0,ddof=1)/len(b))
    np.testing.assert_allclose(t,expected,rtol=1e-12,atol=1e-12,equal_nan=True)
    assert np.isnan(t[0]) and np.isneginf(t[1])
    try:welch([1,2],m.s[0],m.ss[0])
    except ValueError:pass
    else:raise AssertionError('Small group not rejected')
    print('Welch TVLA tests PASS: unequal group sizes, large int16 values, constant samples')
if __name__=='__main__':test()
