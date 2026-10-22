import json,time,hashlib
from datetime import datetime
from hardware import ROOT,session,state,verify_ready,prepare,result,KEY,PT,CT
from pyocd.flash.file_programmer import FileProgrammer
from pyocd.core.target import Target

def main():
    with session() as s:
        t=s.target
        ident=t.read32(0xe0042000); size=t.read16(0x1fff7a22)*1024
        if ident&0xfff!=0x413 or size!=1024*1024: raise RuntimeError(f'Unexpected chip {ident:x}/{size}')
        stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
        out=ROOT/'backup'/stamp; out.mkdir(parents=True)
        flash=bytearray()
        print(f'Backing up {size} bytes of original flash...',flush=True)
        for addr in range(0x08000000,0x08000000+size,4096):
            flash.extend(t.read_memory_block8(addr,4096))
            if len(flash)%262144==0: print(f'Backup {len(flash)}/{size}',flush=True)
        (out/'original_flash.bin').write_bytes(flash)
        meta={'chip_id':hex(ident),'flash_bytes':size,'sha256':hashlib.sha256(flash).hexdigest(),
              'option_control':hex(t.read32(0x40023c14)), 'probe':s.probe.unique_id}
        (out/'metadata.json').write_text(json.dumps(meta,indent=2))
        print('Backup complete. Programming only required flash sector(s).',flush=True)
        t.reset_and_halt(reset_type=Target.ResetType.SYSRESETREQ)
        t.write_core_register('primask',1); t.write32(0xe000e010,0)
        for i in range(8):
            t.write32(0xe000e180+4*i,0xffffffff); t.write32(0xe000e280+4*i,0xffffffff)
        t.write32(0xe000ed04,0x0a000000); t.flush()
        FileProgrammer(s,chip_erase='sector').program(str(ROOT/'build/clefia128.elf'))
        expected=(ROOT/'build/clefia128.bin').read_bytes()
        actual=bytes(t.read_memory_block8(0x08000000,len(expected)))
        if actual!=expected: raise RuntimeError('Flash byte verification failed')
        t.reset(reset_type=Target.ResetType.SYSRESETREQ); time.sleep(0.5); t.halt(); boot=verify_ready(t)
        prepare(t,1,KEY,PT); t.resume(); time.sleep(0.1)
        ct,st=result(t,1)
        if ct!=CT: raise RuntimeError(f'KAT failed: {ct.hex()}')
        meta.update(boot=list(boot),state=list(st),ciphertext=ct.hex(),
                    firmware_sha256=hashlib.sha256(expected).hexdigest(),
                    flash_acr=hex(t.read32(0x40023c00)),rcc_cfgr=hex(t.read32(0x40023808)),
                    firmware_verified=True,kat_pass=True,backup=str(out))
        (ROOT/'build/board_validation.json').write_text(json.dumps(meta,indent=2))
        print(json.dumps(meta,indent=2),flush=True)
        t.resume()
if __name__=='__main__': main()

