from hardware import session
with session() as s:
 t=s.target
 print('map',t.memory_map)
 for name in ['pc','sp','lr','xpsr','control']: print(name,hex(t.read_core_register(name)))
 for a in [0xe000ed28,0xe000ed2c,0xe000ed38,0x40023c00,0x40023c0c,0x40023c10,0x40023c14,0x40023800,0x40023808]: print(hex(a),hex(t.read32(a)))
 for r in t.memory_map:
  if r.is_flash: print('flash',r.name,{k:v for k,v in r.flash.flash_algo.items() if k!='instructions'})

