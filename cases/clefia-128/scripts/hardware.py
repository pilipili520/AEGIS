"""Local runtime paths and the SWD mailbox transport."""
from pathlib import Path
import os, struct
ROOT = Path(__file__).resolve().parents[1]
PACK = os.environ.get('STM32_PACK')
MAIL = 0x20000000
KEY = bytes.fromhex('ffeeddccbbaa99887766554433221100')
PT = bytes(range(16))
CT = bytes.fromhex('de2bf2fd9b74aacdf1298555459494fd')
def session():
    from pyocd.core.helpers import ConnectHelper

    if not PACK:
        raise RuntimeError('Set STM32_PACK to the installed STM32F4xx CMSIS pack path')
    probes=ConnectHelper.get_all_connected_probes(blocking=False)
    if len(probes)!=1: raise RuntimeError(f'Expected one probe, found {len(probes)}')
    return ConnectHelper.session_with_chosen_probe(unique_id=probes[0].unique_id,
        target_override='stm32f415rg', pack=PACK, frequency=1000000,
        connect_mode='halt', resume_on_disconnect=True)
def state(t):
    return struct.unpack('<8I',bytes(t.read_memory_block8(MAIL,32)))
def verify_ready(t):
    s=state(t)
    if s[0]!=0x434c4631 or s[1]!=1 or s[5]!=1 or s[6]&0x700:
        raise RuntimeError(f'Invalid firmware state {s}')
    if t.read32(0x40023c00)&0x700: raise RuntimeError('Cache/prefetch still enabled')
    return s

def prepare(t,seq,key,pt):
    t.halt(); verify_ready(t)
    t.write_memory_block8(MAIL+32,key+pt)
    t.write32(MAIL+12,seq); t.write32(MAIL+8,1); t.flush()

def result(t,seq):
    t.halt(); s=verify_ready(t)
    if s[4]!=seq: raise RuntimeError(f'Sequence mismatch: {s[4]} != {seq}')
    return bytes(t.read_memory_block8(MAIL+64,16)),s
