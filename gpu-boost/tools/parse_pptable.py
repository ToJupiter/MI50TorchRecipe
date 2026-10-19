#!/usr/bin/env python3
"""parse_pptable.py — decode a Vega20 SMU PowerPlay table.

Usage:
    python3 parse_pptable.py [pp_table.bin]

Default input: ../roms/pp_table.bin (the card's live table, dumped from
/sys/class/drm/card1/device/pp_table). Structure layout follows the AMD
driver's amd/pm/powerplay/inc/smu11_driver_if.h (PPTable_t) and
vega20_pptable.h (ATOM_Vega20_POWERPLAYTABLE header, smcPPTable at offset 474).
"""
import os
import struct
import sys

DEF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   '..', 'roms', 'pp_table.bin')
path = sys.argv[1] if len(sys.argv) > 1 else DEF
d = open(path, 'rb').read()


def u8(o): return d[o]
def u16(o): return struct.unpack_from('<H', d, o)[0]
def u32(o): return struct.unpack_from('<I', d, o)[0]
def i16(o): return struct.unpack_from('<h', d, o)[0]


print("file:", path, "size:", len(d))
print("== ATOM_Vega20_POWERPLAYTABLE header")
print("struct size   :", u16(0))
print("format rev    :", u8(2), "content rev:", u8(3))
print("table rev     :", u8(4), "table size:", u16(5))
print("golden ppid   :", hex(u32(7)), "golden rev:", u32(11))
print("format id     :", hex(u16(15)))
print("platform caps :", hex(u32(17)))
print("thermal ctrl  :", u8(21))
print("small limit1/2:", u16(22), u16(24), " boost limit:", u16(26),
      " OD turbo:", u16(28), " OD power save:", u16(30))
print("sw shutdown T :", u16(32))

# PowerSavingClockTable @34: rev(1) + count(4) + max[16]*4 + min[16]*4
o = 34
print("== PowerSavingClockTable")
print("rev:", u8(o), "count:", u32(o + 1))
names = "GFX VCLK DCLK ECLK SOC UCLK FCLK DCEF DISP PIX PHY".split()
for i, n in enumerate(names):
    print(f"  {n:5s} max={u32(o+5+i*4):5d} min={u32(o+5+64+i*4):5d}")

# OverDrive8 record: rev(1) count(4) cap[32] count(4) max[32]*4 min[32]*4
o = 34 + 133
print("== OverDrive8 @", o)
print("rev:", u8(o), "feature count:", u32(o + 1))
print("feature caps:", d[o + 5:o + 5 + 32].hex())
print("setting count:", u32(o + 37))
smax = struct.unpack_from('<32I', d, o + 41)
smin = struct.unpack_from('<32I', d, o + 41 + 128)
odnames = ["GFXCLKFMAX", "GFXCLKFMIN", "VDDGFXCURVEFREQ_P1",
           "VDDGFXCURVEVOLTAGEOFFSET_P1", "VDDGFXCURVEFREQ_P2",
           "VDDGFXCURVEVOLTAGEOFFSET_P2", "VDDGFXCURVEFREQ_P3",
           "VDDGFXCURVEVOLTAGEOFFSET_P3", "UCLKFMAX", "POWERPERCENTAGE",
           "FANRPMMIN", "FANRPMACOUSTICLIMIT", "FANTARGETTEMPERATURE",
           "OPERATINGTEMPMAX"]
for i, n in enumerate(odnames):
    if i < 32 and (smax[i] or smin[i]):
        print(f"  {n:30s} min={smin[i]:6d} max={smax[i]:6d}")

B = 474  # smcPPTable base in ATOM_Vega20_POWERPLAYTABLE
print("== smcPPTable @", B)
print("Version            :", hex(u32(B)))
print("FeaturesToRun      :", hex(u32(B + 4)), hex(u32(B + 8)))
lo = B + 12
print("SocketPowerLimitAc0:", u16(lo + 0), " tau:", u16(lo + 2))
print("SocketPowerLimitAc1:", u16(lo + 4), " tau:", u16(lo + 6))
print("SocketPowerLimitAc2:", u16(lo + 8), " tau:", u16(lo + 10))
print("SocketPowerLimitAc3:", u16(lo + 12), " tau:", u16(lo + 14))
print("SocketPowerLimitDc :", u16(lo + 16), " tau:", u16(lo + 18))
print("TdcLimitSoc        :", u16(lo + 20), " tau:", u16(lo + 22))
print("TdcLimitGfx        :", u16(lo + 24), " tau:", u16(lo + 26))
print("Tedge/Thotspot/Thbm:", u16(lo + 28), u16(lo + 30), u16(lo + 32))
print("Tvr_gfx/Tvr_mem    :", u16(lo + 34), u16(lo + 36))
print("Tliquid1/2/Tplx    :", u16(lo + 38), u16(lo + 40), u16(lo + 42))
print("FitLimit           :", u32(lo + 44))
print("PpmPowerLimit      :", u16(lo + 48), " PpmTempThreshold:", u16(lo + 50))
print("Tvr_SocLimit       :", u16(lo + 54))
print("UlvVoltOffset Soc/Gfx:", u16(lo + 56), u16(lo + 58))
print("MinVoltage Gfx/Soc :", u16(lo + 64), u16(lo + 66), "(units 1/4 mV)")
print("MaxVoltage Gfx/Soc :", u16(lo + 68), u16(lo + 70))
print("LoadLine Gfx/Soc   :", u16(lo + 72), u16(lo + 74))

dd = lo + 76  # DpmDescriptor[11]
for i, n in enumerate(["GFXCLK", "VCLK", "DCLK", "ECLK", "SOCCLK", "UCLK",
                       "DCEFCLK", "DISPCLK", "PIXCLK", "PHYCLK", "FCLK"]):
    base = dd + i * 24
    print(f"  DpmDesc {n:8s} vmode={u8(base)} snap={u8(base+1)} nlevels={u8(base+2)} "
          f"lin(m={u32(base+4)},b={u32(base+8)}) "
          f"ss(a={u32(base+12)},b={u32(base+16)},c={u32(base+20)})  (floats)")

ft = dd + 11 * 24
tables = [("Gfx", 16), ("Vclk", 8), ("Dclk", 8), ("Eclk", 8), ("Socclk", 8),
          ("Uclk", 4), ("Fclk", 8), ("Dcefclk", 8), ("Dispclk", 8),
          ("Pixclk", 8), ("Phyclk", 8)]
o = ft
print("== Freq tables")
for name, n in tables:
    print(f"  {name:8s}: {list(struct.unpack_from(f'<{n}H', d, o))}")
    o += n * 2
print("  DcModeMaxFreq:", list(struct.unpack_from('<11H', d, o)))
o += 22 + 2
print("  Mp0clkFreq:", list(struct.unpack_from('<2H', d, o)))
o += 8
print("  GfxclkFidle:", u16(o), " GfxclkSlewRate:", u16(o + 2),
      " CksEnableFreq:", u16(o + 4))
o += 6 + 2 + 12 + 4
print("  GfxclkDsMaxFreq:", u16(o), " GfxclkSource:", u8(o + 2))
o += 4 + 4
print("  PcieGenSpeed:", list(d[o:o + 2]), " PcieLaneCount:", list(d[o + 2:o + 4]),
      " LclkFreq:", list(struct.unpack_from('<2H', d, o + 4)))
o += 8
print("  EnableTdpm:", u16(o), " TdpmHighHyst:", u16(o + 2),
      " TdpmLowHyst:", u16(o + 4), " GfxclkFreqHighTempLimit:", u16(o + 6))
o += 8
print("  FanStopTemp:", u16(o), " FanStartTemp:", u16(o + 2))
o += 4
print("  FanGain Edge/Hotspot/Liquid:", u16(o), u16(o + 2), u16(o + 4))
print("  FanGain VrGfx/VrSoc/Plx/Hbm:", u16(o + 6), u16(o + 8), u16(o + 10), u16(o + 12))
print("  FanPwmMin:", u16(o + 14), " FanAcousticLimitRpm:", u16(o + 16),
      " FanThrottlingRpm:", u16(o + 18))
print("  FanMaximumRpm:", u16(o + 20), " FanTargetTemperature:", u16(o + 22),
      " FanTargetGfxclk:", u16(o + 24))
print("  FanZeroRpmEnable:", u8(o + 26), " FanTachEdgePerRev:", u8(o + 27))
o += 28
print("  FuzzyFan deltas:", i16(o), i16(o + 2), i16(o + 4))
print()
print("(See parse_avfs.py for float decoding of the AVFS/voltage curve fields.)")
