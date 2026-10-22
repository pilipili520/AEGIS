"""Byte-oriented CLEFIA-128 reference using constants in firmware/tables.h."""
from pathlib import Path
import re
TABLES = (Path(__file__).resolve().parents[1] / 'firmware' / 'tables.h').read_text(encoding='utf-8')
def constants(name, count):
    match = re.search(r'\b' + name + r'\[' + str(count) + r'\]\s*=\s*\{([^}]+)\}', TABLES, re.S)
    if not match:
        raise ValueError(f'Missing {name} constants in firmware/tables.h')
    values = [int(value, 16) for value in re.findall(r'0x([0-9a-fA-F]+)', match.group(1))]
    if len(values) != count:
        raise ValueError(f'Expected {count} {name} constants, found {len(values)}')
    return values
S = [bytes(constants('s0', 256)), bytes(constants('s1', 256))]
CON = [value.to_bytes(4, 'big') for value in constants('con128', 60)]
M=[[[1,2,4,6],[2,1,6,4],[4,6,1,2],[6,4,2,1]],[[1,8,2,10],[8,1,10,2],[2,10,1,8],[10,2,8,1]]]
def xor(a,b): return bytes(x^y for x,y in zip(a,b))
def mul(a,b):
    y=0
    for _ in range(8):
        if b&1: y^=a
        a=((a<<1)^(0x11d if a&128 else 0))&255; b>>=1
    return y
def f(x,k,which):
    t=[S[(i+which)%2][v] for i,v in enumerate(xor(x,k))]
    out=[]
    for row in M[which]:
        v=0
        for a,b in zip(t,row): v^=mul(a,b)
        out.append(v)
    return bytes(out)
def gfn(data,keys):
    w=[data[i:i+4] for i in range(0,16,4)]
    for r in range(len(keys)//2):
        w[1]=xor(w[1],f(w[0],keys[2*r],0)); w[3]=xor(w[3],f(w[2],keys[2*r+1],1))
        if r<len(keys)//2-1: w=w[1:]+w[:1]
    return b''.join(w)
def schedule(key):
    l=gfn(key,CON[:24]); rk=[]
    for i in range(9):
        t=xor(l,b''.join(CON[24+4*i:28+4*i]))
        if i%2: t=xor(t,key)
        rk.extend(t[j:j+4] for j in range(0,16,4))
        bits=''.join(f'{x:08b}' for x in l)
        l=int(bits[7:64]+bits[121:128]+bits[0:7]+bits[64:121],2).to_bytes(16,'big')
    return rk
def encrypt(key,pt):
    x=pt[:4]+xor(pt[4:8],key[:4])+pt[8:12]+xor(pt[12:],key[4:8])
    x=gfn(x,schedule(key))
    return x[:4]+xor(x[4:8],key[8:12])+x[8:12]+xor(x[12:],key[12:])
if __name__=='__main__':
    key=bytes.fromhex('ffeeddccbbaa99887766554433221100')
    assert encrypt(key,bytes(range(16))).hex()=='de2bf2fd9b74aacdf1298555459494fd'
    expected=['f3e6cef9','8df75e38','41c06256','640ac51b']
    assert [v.hex() for v in schedule(key)[:4]]==expected
    print('RFC 6114 ciphertext and first four round keys: PASS')
