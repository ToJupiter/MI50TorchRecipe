#!/usr/bin/env python3
"""parse_rom.py — ATOM BIOS parser used for the MI50/Pro VII cross-flash audit.

Usage:
    python3 parse_rom.py [romfile]

Default input: ../roms/card-vbios-legacy.rom (59904-byte legacy image dumped
from debugfs `amdgpu_vbios`; the full 512 KB stock reference is in
../roms/stock-provii-200818.rom).

Prints the ATOM ROM header, the master data table, and the contents of
`smc_dpm_info` (VRM/I2C mapping the SMU is configured with) plus the raw
`firmwareinfo` table (firmware revision, capability, boot clocks, board ids).
"""
import os
import struct
import sys

DEF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   '..', 'roms', 'card-vbios-legacy.rom')
path = sys.argv[1] if len(sys.argv) > 1 else DEF
d = open(path, 'rb').read()


def u8(o): return d[o]
def u16(o): return struct.unpack_from('<H', d, o)[0]
def u32(o): return struct.unpack_from('<I', d, o)[0]


print("rom:", path, "size:", len(d))

cands = []
i = 0
while True:
    i = d.find(b'ATOM', i)
    if i < 0:
        break
    cands.append(i)
    i += 1
print("ATOM strings at:", [hex(c) for c in cands])

for c in cands:
    hdr = c - 4
    if hdr < 0:
        continue
    size, fmt, content = u16(hdr), u8(hdr + 2), u8(hdr + 3)
    print(f"\nheader @0x{hdr:x}: size={size} fmt={fmt} content={content}")
    if size == 40 and fmt in (1, 2):        # atom_rom_header_v2_2
        mdt = u16(hdr + 32)
        print(f"  masterdatatable=0x{mdt:x} subsys={u16(hdr+24):04x}:{u16(hdr+26):04x}")
        if not (0 < mdt < len(d)):
            continue
        print(f"  master table @0x{mdt:x}: size={u16(mdt)} fmt={u8(mdt+2)} content={u8(mdt+3)}")
        names = ["utilitypipeline", "multimedia_info", "smc_dpm_info", "sw3",
                 "firmwareinfo", "sw5", "lcd_info", "sw7", "smu_info", "sw9",
                 "sw10", "vram_usagebyfirmware", "gpio_pin_lut", "sw13",
                 "gfx_info", "powerplayinfo", "sw16", "sw17", "sw18", "sw19",
                 "sw20", "sw21", "displayobjectinfo", "indirectioaccess",
                 "umc_info", "sw25", "sw26", "dce_info", "vram_info", "sw29",
                 "integratedsysteminfo", "asic_profiling_info",
                 "voltageobject_info", "sw33", "sw34"]
        entries = []
        for k in range(40):
            off = mdt + 4 + 2 * k
            if off + 2 > len(d):
                break
            entries.append(u16(off))
            nm = names[k] if k < len(names) else f"idx{k}"
            off_v = entries[-1]
            if off_v and off_v < len(d):
                print(f"   [{k:2d}] {nm:22s} off=0x{off_v:05x} "
                      f"size={u16(off_v):5d} ver={u8(off_v+2)}.{u8(off_v+3)}")
            else:
                print(f"   [{k:2d}] {nm:22s} off={off_v}")

        off = entries[2] if len(entries) > 2 else 0
        if off:
            print(f"\n== smc_dpm_info @0x{off:x} size={u16(off)} "
                  f"version={u8(off+2)}.{u8(off+3)}")
            o = off + 4 + 12
            print("  maxvoltagestepgfx/soc:", u16(o), u16(o + 2)); o += 4
            print("  vddgfxvrmapping:", u8(o), " vddsocvrmapping:", u8(o + 1),
                  " vddmem0vrmapping:", u8(o + 2), " vddmem1vrmapping:", u8(o + 3)); o += 4
            print("  ulv masks/ext sensor:", u8(o), u8(o + 1), u8(o + 2)); o += 4
            for nm in ("gfx", "soc", "mem0", "mem1"):
                print(f"  {nm}maxcurrent: {u16(o)} offset: "
                      f"{struct.unpack_from('<b', d, o+2)[0]}"); o += 4
            print("  acdcgpio/pol vr0hotgpio/pol:", u8(o), u8(o+1), u8(o+2), u8(o+3)); o += 4
            print("  vr1hotgpio/pol:", u8(o), u8(o + 1)); o += 4
            print("  led pins:", u8(o), u8(o + 1), u8(o + 2)); o += 4
            o += 16  # spread-spectrum groups
            # v4_4 I2C controllers are 28 bytes each (7x u32 fields)
            print("  i2c controllers[7] (en, slaveaddr, port, name, throttler, proto, speed):")
            for k in range(7):
                vals = struct.unpack_from('<7I', d, o + k * 28)
                print(f"    [{k}] " + " ".join(str(v) for v in vals))

        off = entries[4] if len(entries) > 4 else 0
        if off:
            print(f"\n== firmwareinfo @0x{off:x} size={u16(off)} "
                  f"version={u8(off+2)}.{u8(off+3)}")
            print("  firmware_revision:", hex(u32(off + 4)))
            print("  bootup_sclk (10kHz):", u32(off + 8), "->", u32(off + 8) / 100, "MHz")
            print("  bootup_mclk (10kHz):", u32(off + 12), "->", u32(off + 12) / 100, "MHz")
            print("  firmware_capability:", hex(u32(off + 16)))
            print("  bootup vddc/vddci/mvddc/vddgfx (mV):", u16(off + 28), u16(off + 30),
                  u16(off + 32), u16(off + 34))
            print("  mem_module_id:", u8(off + 36), " cooling_id:", u8(off + 37))
