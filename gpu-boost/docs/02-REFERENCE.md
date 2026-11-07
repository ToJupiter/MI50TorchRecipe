# 02 — Reference, wrong turns and FAQ

Companion to [`BOOST_RECOVERY.md`](../../BOOST_RECOVERY.md) (ordered fix) and
[`01-ROOT-CAUSE.md`](01-ROOT-CAUSE.md) (why it works).

## 1. PPSMC result codes (`vega20_ppsmc.h`)

| Value | Name | Meaning |
| --- | --- | --- |
| `0x01` | `PPSMC_Result_OK` | message processed successfully |
| `0xFF` | `PPSMC_Result_Failed` | **processed, generic failure** (prerequisite / state) |
| `0xFE` | `PPSMC_Result_UnknownCmd` | command not implemented |
| `0xFD` | `PPSMC_Result_CmdRejectedPrereq` | prerequisite missing |
| `0xFC` | `PPSMC_Result_CmdRejectedBusy` | SMU busy |

The recurring `Failed to send message 0x28, response 0xff` therefore means
"SMU alive, `SetHardMinByFreq(GFXCLK)` could not be applied" — **not**
"unsupported" and not a hang. `vega20_send_msg_to_smc_with_parameter()`
clears `MP1_SMN_C2PMSG_90`, writes the message to `C2PMSG_66`, waits, and
compares the reply to `PPSMC_Result_OK`.

## 2. Useful Vega20 SMU messages

| ID | Name | ID | Name |
| --- | --- | --- | --- |
| `0x06` | `EnableAllSmuFeatures` | `0x07` | `DisableAllSmuFeatures` |
| `0x08` | `EnableSmuFeaturesLow` | `0x0A` | `DisableSmuFeaturesLow` |
| `0x0E` | `SetWorkloadMask` | `0x18` | `RunBtc` |
| `0x22` | `SoftReset` (unused by driver) | `0x25` | `EnterBaco` |
| `0x26` | `SetSoftMinByFreq` | `0x27` | `SetSoftMaxByFreq` |
| `0x28` | `SetHardMinByFreq` | `0x29` | `SetHardMaxByFreq` |
| `0x2D` | `GetDpmClockFreq` | `0x40` | `PrepareMp1ForUnload` |
| `0x51` | `AllowGfxOff` | `0x52` | `DisallowGfxOff` |
| `0x57` | `RunAfllBtc` | `0x58` | `ExitBaco` |
| `0x59` | `PrepareMp1ForReset` | `0x5A` | `PrepareMp1ForShutdown` |
| `0x63` | `DFCstateControl` (data-fabric C-state; driver-internal) | | |

## 3. `amdgpu.reset_method` enum (`amdgpu.h`)

```
-1 auto    0 legacy    1 mode0    2 MODE1    3 MODE2    4 LINK
 5 BACO    6 PCI       7 ON_INIT
```

- **`4` is `LINK`, not BACO** — the source of the old
  `Specified reset method:4 isn't supported, using AUTO instead` warning.
- `amdgpu_device_set_mp1_state()`: MODE1/LINK → `PP_MP1_STATE_SHUTDOWN`
  (`PrepareMp1ForShutdown 0x5A`), MODE2 → `PP_MP1_STATE_RESET`
  (`PrepareMp1ForReset 0x59`), **BACO/default → `PP_MP1_STATE_NONE` (no MP1
  message)**.
- `soc15_asic_reset_method()` picks BACO for Vega20 when BACO is supported,
  otherwise MODE1 — which is why `auto` chose BACO.
- Runtime-writable via `/sys/module/amdgpu/parameters/reset_method` (root).
  Always restore `-1` afterwards.

## 4. `gpu_metrics_v1_0` field offsets (for the diagnose script)

`struct metrics_table_header` (4 bytes) + 4 bytes padding, then:

| Offset | Field | Offset | Field |
| --- | --- | --- | --- |
| `8` | `system_clock_counter` (u64) | `16..26` | 6 temperatures (u16) |
| `28..32` | activity (u16 ×3) | `34` | `average_socket_power` (u16) |
| `36` | `energy_accumulator` (u32) | `40..52` | 7 average clocks (u16) |
| `54` | **`current_gfxclk`** (u16) | `56` | `current_socclk` |
| `58` | `current_uclk` | `60..66` | vclk/dclk (u16 ×4) |
| `68` | `throttle_status` (u32) | `72` | `current_fan_speed` |
| `74` | `pcie_link_width` (u8) | `75` | `pcie_link_speed` (0.1 GT/s) |

`structure_size = 80`. The driver memsets the table to `0xFF` before filling
it, so unset fields read as `0xFFFF` / `0xFFFFFFFF`.

## 5. `pp_features` bits that matter

| Bit | Feature | Typical healthy value |
| --- | --- | --- |
| `0x02` | `GFXCLK_DPM` | Y |
| `0x04` | `UCLK_DPM` | Y |
| `0x40` | `ULV` (ultra-low voltage) | Y — clearing it is part of the optional pin |
| `0x400` | `GFXCLK_DS` (deep sleep) | N |
| `0x2000/0x4000/0x8000` | `PPT` / `TDC` / `THERMAL` | Y |
| `0x04000000` | `GFXOFF` | N |
| `0x08000000` | `CG` | Y |

A `pp_features` write computes `features_to_disable = enabled & ~new_mask`
and `features_to_enable = ~enabled & new_mask`, then sends
`Disable/EnableSmuFeaturesLow/High`. Note that a `pp_table` re-upload runs
`EnableAllSmuFeatures` and can **restore** bits you just cleared (e.g. ULV), so
apply feature-mask changes *after* the re-upload.

## 6. Record your own fingerprints (do this once, keep them private)

These are **card-specific**, not personal data, but they are useful to record
so you can prove nothing changed. Run these on your own machine and keep the
results in a private note — do **not** paste your hostname, username, home
path, UUID, serial or IP into a public repo.

```bash
md5sum /sys/class/drm/card*/device/pp_table      # PowerPlay table
md5sum /lib/firmware/updates/amdgpu/vega20_smc.bin
md5sum /lib/firmware/amdgpu/vega20_smc.bin.zst   # compare (decompress first)
cat /sys/class/drm/card*/device/vbios_version
cat /sys/module/amdgpu/srcversion
cat /sys/module/amdgpu/parameters/ppfeaturemask
```

No tool in this repo depends on a hardcoded checksum. `stock` is determined by
the module's own strings (`soft min gfxclk` present, `hard min gfxclk` absent).

## 7. Wrong turns — do not repeat

1. **Do not reflash the VBIOS.** Byte-identical stock PP table and VBIOS; no
   dual-BIOS switch on a crossflashed MI50; brick risk for zero benefit.
2. **Do not use the hard-DPM patch.** The SMU returns `PPSMC_Result_Failed`
   for `SetHardMinByFreq(GFXCLK)` and DPM bring-up aborts on every boot.
3. **Do not force `reset_method=4`.** It is `LINK`; auto already chooses BACO.
4. **Do not assume BACO cleared anything.** It sends no MP1 message.
5. **Do not build the AMD DKMS against a kernel with the 4-argument
   `pci_resize_resource()` API** — it fails with
   `too few arguments to function 'pci_resize_resource'`. Use a kernel with
   the 3-argument API, or the in-tree driver.
6. **Do not judge the cap from idle clocks.** Healthy cards idle at 859 MHz.
7. **Do not trust `ppfeaturemask` as a config bug.** `0xfff7bfff` is the AMD
   driver's own default (`amdgpu_drv.c`), not a stray modprobe file.
8. **Do not chase feature masks / ULV / COMPUTE profile / BAPM / ASPM /
   `pp_power_profile_mode` as the cause.** All were no-ops for this latch.
9. **Do not use `grep -q` on a live pipe under `set -o pipefail`.** `grep -q`
   closes the pipe early, the producer dies of SIGPIPE and the pipeline reports
   failure even when the string *was* found. Dump to a temp file and grep that.
10. **Do not `rm -rf /var/lib/dkms/.../build` then `dkms install` without
    `dkms add`.** DKMS 3.x creates the build tree at `dkms build` time; the
    install will fail and `set -e` aborts before `update-initramfs`.
11. **Do not forget the initramfs.** Early boot loads `amdgpu` from
    `/boot/initrd.img-*`; verify the embedded module with `unmkinitramfs`.
12. **Do not leave `perf=high` by accident** (unless you intend the pin), and
    **do not set `perf=auto` immediately after pinning** — it drops to 859.

## 8. FAQ

**Q: Is it my PSU / 8-pin cables?**
A: No evidence for that. `throttle_status=0`, all telemetry valid, the card
holds 1700 MHz / 149 W when DPM is armed. A power-delivery fault would not be
fixed by a `pp_table` write. (If you ever want to be thorough: measure the
8-pin rails with a clamp meter under load.)

**Q: Why did the AC cycle work once and then stop?**
A: Different latch depth. The first latch was cleared by any full power loss;
the post-suspend latch persisted through a short drain. Use **≥5 minutes with
the PSU off AND unplugged**.

**Q: Why did the PP re-upload work in October but "stop" in November?**
A: In October the driver was stock, so `amd_powerplay_reset()` completed. From
2026-11-04 the hard-patched module (L2) aborted DPM bring-up on every boot.

**Q: Will it come back?**
A: It can, after a failed suspend or long idle. That is why STEP 0 masks the
suspend targets and installs a boot guard, and why `mi50-fix.sh` exists.

**Q: Can I avoid the physical power cycle entirely?**
A: Sometimes. `mi50-pin-clock.sh --verify` pins 1700 MHz in software, but it
holds maximum clocks at all times and is a workaround, not a cure.

**Q: Does Windows show the same cap?**
A: It did during the latched period — consistent with a card/SMU firmware
state, not an OS driver bug. After the cold drain + PP re-arm, Linux boosts.

## 9. Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `MI50_CARD` | auto-detect by `1002:66a1` | force `cardN` |
| `MI50_KERNEL` | `uname -r` | target kernel for DKMS / initramfs |
| `MI50_DKMS_VER` | `dkms status` / `/usr/src/amdgpu-*` | DKMS module/version |
| `MI50_REPO` | grandparent of `tools/` | repo root (path printing) |
| `MI50_GB` | `tools/..` | the `gpu-boost/` directory (roms, docs) |
| `MI50_BAD_SRCVERSION` | unset | a known-bad module srcversion to compare against |
| `MI50_VERIFY_ONLY` | `0` | skip rebuild, just verify + regenerate initramfs |
| `MI50_SKIP_INITRAMFS` | `0` | skip extracting the initrd in `mi50-diagnose.sh` |
| `HSA_OVERRIDE_GFX_VERSION` | `9.0.0` | ROCm 7.x shim so OpenCL enumerates gfx906 |

## 10. Prerequisites

- Linux with `amdgpu`, sysfs DRM nodes, and the user in `render`/`video`.
- `gcc` + OpenCL headers (`-lOpenCL`) to build `gpu_burn`.
- `python3`, `zstd`, `strings`, `unmkinitramfs`, `dkms`, `update-initramfs`.
- Membership in the `adm` group for the `journalctl -k` section of
  `mi50-diagnose.sh` (optional — the rest works without it).
- `sudo` for the sysfs writes and DKMS/initramfs operations.
