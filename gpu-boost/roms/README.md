# ROM / table artifacts

| File | Size | md5 | What it is |
|---|---|---|---|
| `stock-provii-200818.rom` | 524288 | `64d1c521a9fd0ae594e4ca9d9e14f8c7` | Genuine stock AMD Radeon Pro VII 16 GB VBIOS, `113-D1640600-104`, ver `016.004.000.064.015319`, build 2020-08-18. Reference image. |
| `card-vbios-legacy.rom` | 59904 | `526c3dfaeb2f9befeb9542a86a643c88` | This card's VBIOS image, dumped from debugfs (`0x75 × 512` = legacy image, contains all ATOM tables). |
| `pp_table.bin` | 1730 | — | Live copy of the card's SMU PowerPlay table (`/sys/class/drm/card1/device/pp_table`). Parse with `../tools/parse_pptable.py`. |

## The 3-byte difference (card vs stock)

```
$ cmp -l <(head -c 59904 stock-provii-200818.rom) card-vbios-legacy.rom
   34 224 222      # 0x21  ROM header checksum byte        0x94 -> 0x92  (-2)
55397   4   5      # 0xD864 firmwareinfo.firmware_capability 0x04 -> 0x05 (+1)
55417   0   1      # 0xD878 firmwareinfo.mem_module_id       0x00 -> 0x01 (+1)
```

Sum of changes = 0 → deliberate, checksum-correct edit (most likely the HBM
module ID for this board). No power/voltage/frequency table was touched.

## How to regenerate

```bash
# SMU PowerPlay table (world-readable, no root)
cp /sys/class/drm/card1/device/pp_table pp_table.bin

# VBIOS image (root; regular /sys/.../rom reads 0 bytes on VFCT systems)
sudo cat /sys/kernel/debug/dri/0000:03:00.0/amdgpu_vbios > card-vbios-legacy.rom

# Full 512 KB flash dump (only if amdvbflash is installed; best before any
# reflash attempt — the debugfs image lacks the UEFI/padding part):
sudo amdvbflash -s 0 card-full-512k.rom
```

The stock reference image was obtained from the TechPowerUp VGA BIOS
collection (entry 229229, `AMD.RadeonProVII.16384.200818.rom`) through the
Internet Archive (`https://web.archive.org/web/2024id_/<url>`). The direct TPU
download is protected by an anti-bot challenge; the Wayback copy matches the
published md5 exactly.
