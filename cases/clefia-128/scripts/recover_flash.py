import json,time,hashlib
from hardware import ROOT,session,verify_ready,prepare,result,KEY,PT,CT
from pyocd.flash.file_programmer import FileProgrammer
from pyocd.core.target import Target
with session() as s:
 t=s.target
 t.reset_and_halt(reset_type=Target.ResetType.SYSRESETREQ)
 t.write_core_register('primask',1); t.write32(0xe000e010,0)
 for i in range(8):
  t.write32(0xe000e180+4*i,0xffffffff); t.write32(0xe000e280+4*i,0xffffffff)
 t.write32(0xe000ed04,0x0a000000); t.flush()
 print('Reset and interrupts cleared',flush=True)
 FileProgrammer(s,chip_erase='sector').program(str(ROOT/'build/clefia128.elf'))
 expected=(ROOT/'build/clefia128.bin').read_bytes()
 assert bytes(t.read_memory_block8(0x08000000,len(expected)))==expected
 t.reset(reset_type=Target.ResetType.SYSRESETREQ); time.sleep(.5); t.halt(); boot=verify_ready(t)
 prepare(t,1,KEY,PT); t.resume(); time.sleep(.1); ct,st=result(t,1); assert ct==CT,ct.hex()
 meta={'chip_id':hex(t.read32(0xe0042000)),'flash_bytes':t.read16(0x1fff7a22)*1024,'boot':list(boot),'state':list(st),'ciphertext':ct.hex(),'firmware_sha256':hashlib.sha256(expected).hexdigest(),'flash_acr':hex(t.read32(0x40023c00)),'rcc_cfgr':hex(t.read32(0x40023808)),'firmware_verified':True,'kat_pass':True,'backup':'backup/20261015_191224'}
 (ROOT/'build/board_validation.json').write_text(json.dumps(meta,indent=2)); print(json.dumps(meta,indent=2)); t.resume()

