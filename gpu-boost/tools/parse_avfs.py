#!/usr/bin/env python3
"""parse_avfs.py — float-decode DpmDescriptor / AVFS-related PP table fields.

Usage:
    python3 parse_avfs.py [pp_table.bin]

Default input: ../roms/pp_table.bin.

Note: the raw PP table stores these as IEEE-754 floats in u32 slots. The
driver does NOT patch these fields — whatever is here is what the SMU gets.
On this card they decode to sane values (e.g. GFXCLK SsCurve a=0.3744,
b=-0.485, c=0.8207), which is further evidence that the VBIOS tables are not
corrupted. The later hand-computed offsets in this script are approximate and
were used as a quick sanity check only; the DpmDescriptor block is exact.
"""
import os
import struct
import sys

DEF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   '..', 'roms', 'pp_table.bin')
path = sys.argv[1] if len(sys.argv) > 1 else DEF
d = open(path, 'rb').read()


def f32(o): return struct.unpack_from('<f', d, o)[0]
def u8(o): return d[o]
def u16(o): return struct.unpack_from('<H', d, o)[0]
def u32(o): return struct.unpack_from('<I', d, o)[0]


B = 474
lo = B + 12
dd = lo + 76

print("== DpmDescriptor (floats decoded)")
for i, n in enumerate(["GFXCLK", "VCLK", "DCLK", "ECLK", "SOCCLK", "UCLK",
                       "DCEFCLK", "DISPCLK", "PIXCLK", "PHYCLK", "FCLK"]):
    base = dd + i * 24
    print(f"  {n:8s} vmode={u8(base)} snap={u8(base+1)} nlevels={u8(base+2):2d}"
          f"  ConversionToAvfs m={f32(base+4)!r} b={f32(base+8)!r}"
          f"  SsCurve a={f32(base+12)!r} b={f32(base+16)!r} c={f32(base+20)!r}")

o = dd + 11 * 24
o += 2 * (16 + 8 + 8 + 8 + 8 + 4 + 8 + 8 + 8 + 8 + 8)
o += 22 + 2 + 4 + 4 + 2 + 2 + 2 + 2 + 12 + 4 + 2 + 1 + 1 + 1 + 3
o += 2 + 2 + 4 + 8 + 4 + 13 * 2 + 2 * 2 + 2 * 3 + 2 + 2 + 2
QAV = o
print("\n== AVFS/voltage tables (approximate offsets — sanity check only)")
print("  OverrideAvfsGb[GFX,SOC]:", u8(QAV - 4), u8(QAV - 3))
qa = QAV
print(f"  qAvfsGb[GFX]: a={f32(qa)!r} b={f32(qa+4)!r} c={f32(qa+8)!r}")
print(f"  qAvfsGb[SOC]: a={f32(qa+12)!r} b={f32(qa+16)!r} c={f32(qa+20)!r}")
db = qa + 24
print(f"  dBtcGbGfxCksOn : a={f32(db)!r} b={f32(db+4)!r} c={f32(db+8)!r}")
print(f"  dBtcGbGfxCksOff: a={f32(db+12)!r} b={f32(db+16)!r} c={f32(db+20)!r}")
print(f"  dBtcGbGfxAfll  : a={f32(db+24)!r} b={f32(db+28)!r} c={f32(db+32)!r}")
print(f"  dBtcGbSoc      : a={f32(db+36)!r} b={f32(db+40)!r} c={f32(db+44)!r}")
ag = db + 48
print(f"  qAgingGb[GFX]: m={f32(ag)!r} b={f32(ag+4)!r}")
print(f"  qAgingGb[SOC]: m={f32(ag+8)!r} b={f32(ag+12)!r}")
sv = ag + 16
print(f"  qStaticVoltageOffset[GFX]: a={f32(sv)!r} b={f32(sv+4)!r} c={f32(sv+8)!r}")
print(f"  qStaticVoltageOffset[SOC]: a={f32(sv+12)!r} b={f32(sv+16)!r} c={f32(sv+20)!r}")
