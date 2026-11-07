# 01 — Root cause (condensed)

This is the compressed version of the whole investigation. Read
[`BOOST_RECOVERY.md`](../../BOOST_RECOVERY.md) for the ordered fix; this file
explains *why* it works.

## 1. The symptom is a parked DPM engine, not a throttled or broken GPU

Under full FP32 load the card stays at its **lowest** DPM level on every clock
domain at once — GFXCLK 859, SOCCLK 309, UCLK 350, FCLK 550 MHz — drawing
43–49 W of a 190 W cap and delivering ~5.8 TFLOPS. A healthy card reaches
1700 MHz / 132–151 W / ~10.9–11.8 TFLOPS.

Evidence that this is *not* throttling, power delivery or dead silicon:

- `gpu_metrics` `throttle_status = 0`; temperatures ~50 °C (no thermal limit).
- `pp_features` shows `GFXCLK_DPM=Y`, `UCLK_DPM=Y`, `SOCCLK_DPM=Y`, `PPT/TDC/THERMAL=Y`.
- The PP table is byte-for-byte stock; VBIOS from VFCT; SMU/PSP/TA firmware
  blobs are consistent between `/lib/firmware/amdgpu` and `/lib/firmware/updates/amdgpu`.
- All rings, KFD/OpenCL, 16 GB HBM2 and telemetry work; compute throughput is
  normal *for 859 MHz*.
- The SMU answers messages, including `GetDpmClockFreq (0x2D)`.
- The card **can** be driven to 1700 MHz with soft min/max pinned to one level.

## 2. Three layers

| Layer | When | Cause | Cleared by |
| --- | --- | --- | --- |
| **L0 trigger** | failed S3 suspend | `UNLOAD_TA(0x2) 0x117`, `DFCstateControl (0x63) → 0xffffffff`, `[DisableAllSMUFeatures]` / `[PowerOffAsic] Failed to disable DPM`, `GPU Recovery Failed: -62`. A Vega20 power-management firmware bug leaves the SMU (MP1) in a half-powered-down state | never letting S3 happen |
| **L1 latch** | after L0 | The SMU firmware's DPM / low-power state is parked. Warm reboot and BACO do not reload SMU firmware, so it persists | **true cold AC drain** (full power loss) or, less reliably, a MODE1/MODE2 full ASIC reset |
| **L2 obstacle** | a hard-DPM driver experiment | GFXCLK limit upload changed from `SetSoftMinByFreq (0x26)` to `SetHardMinByFreq (0x28)`. The SMU replies `PPSMC_Result_Failed (0xff)`; `PP_ASSERT_WITH_CODE` returns early from `vega20_unforce_dpm_levels()` with `Failed to upload DPM Bootup Levels!`, aborting every DPM bring-up | rebuild a **stock** DKMS module and regenerate/verify the **initramfs** |

**L2 is why "everything stopped working".** Every BACO reset, PP re-upload and
AC cycle re-runs the DPM bring-up sequence, which the patched module aborts.
Remove L2 first, then clear L1, then re-arm DPM.

## 3. Driver internals that matter

From `vega20_hwmgr.c` / `amdgpu` (AMD DKMS driver, legacy `powerplay` path):

- `vega20_enable_dpm_tasks()` calls `NumOfDisplays`, `set_allowed_featuresmask`,
  `init_smc_table`, `RunBtc (0x18)`, `RunAfllBtc (0x57)`,
  `EnableAllSmuFeatures (0x6)`, `override_pcie_parameters`,
  `send_clock_ratio`, `setup_default_dpm_tables`, `GetPptLimit (0x53)`.
- **`setup_default_dpm_tables()` only initialises the driver-side soft min/max;**
  the actual `SetSoftMinByFreq`/`SetSoftMaxByFreq` upload happens in
  `vega20_unforce_dpm_levels()`, which runs **only** when
  `power_dpm_force_performance_level` changes. A warm boot therefore cannot
  clear a latched SMU DPM range on its own.
- A `pp_table` write calls `amd_powerplay_reset()` → full `EnableDPMTasks`.
  With a stock module this is what re-arms DPM (the confirmed fix).
- `pp_features` writes send `Enable/DisableSmuFeaturesLow/High` (`0x8`–`0xB`).
- `pp_power_profile_mode` writes send `SetWorkloadMask (0xE)`.
- The `*` in `pp_dpm_sclk` comes from `vega20_get_current_clk_freq()`, which
  sends `PPSMC_MSG_GetDpmClockFreq (0x2D)` to the SMU — **it is the real clock**.

## 4. Corrections to earlier wrong conclusions

| Earlier claim | Reality |
| --- | --- |
| "`response 0xff` means the SMU does not support hard GFXCLK limits" | `0xff` is `PPSMC_Result_Failed` = the SMU **received and processed** the message and returned a generic failure. `UnknownCmd` would be `0xFE`; a hang would give no reply. It is a state/prerequisite failure. |
| "`reset_method=4` is BACO" | Enum: `-1 auto, 0 legacy, 1 mode0, 2 MODE1, 3 MODE2, 4 LINK, 5 BACO, 6 PCI`. **4 is LINK** — hence `Specified reset method:4 isn't supported, using AUTO instead`. |
| "BACO succeeded so the card should have recovered" | BACO sends **no** `PrepareMp1For*` message and keeps the SMU always-on domain powered. `amdgpu_device_set_mp1_state()` sends `PrepareMp1ForShutdown (0x5A)` only for MODE1/LINK and `PrepareMp1ForReset (0x59)` only for MODE2. BACO cannot clear a latched SMU state. |
| "The SMU / VRM is hardware-faulted" | The same hardware ran eight consecutive boots with zero `0x28` errors, and reached 1700 MHz once a bad driver patch was removed and a cold drain was done. A hardware fault cannot appear and disappear in sync with a driver install. |
| "The PP table must be corrupt / needs patching" | Live `pp_table` md5 equals the stock reference at every measurement. Feature-mask edits, ULV toggles and patched PP tables all failed to change anything. |
| "Idle `pp_dpm_sclk` star at 859 MHz proves the cap" | A **healthy** Vega20 also idles at DPM0 (859 MHz). Only a load test decides. |

## 5. Timeline (why each fix worked once and then stopped)

| Date | Event | Outcome |
| --- | --- | --- |
| 2026-09-22 | True AC power cycle | PASS 1700 MHz / 147 W — cleared the first latch |
| 2026-10-11/12 | Failed S3 suspend (L0) | L1 latched; AC cycle on 10-13 no longer helped |
| 2026-10-19 | PP-table re-upload with a **stock** driver | PASS 1700 MHz / 151 W, then decayed after ~15 min idle |
| 2026-11-04 22:04 | Hard-DPM module installed (L2) | Every boot shows `0x28 → 0xff` and `Failed to upload DPM Bootup Levels!`; all resets/re-uploads from then on abort |
| 2026-11-05 | Stock DKMS rebuilt and verified on disk **and** in initramfs | `0x28` errors gone; card still capped → L1 isolated |
| 2026-11-07 (morning) | Software ladder reached `8: 1700Mhz *` then set `perf=auto` | 1700 MHz was real but immediately discarded |
| 2026-11-07 17:43 | MODE2 reset | Left the GPU wedged (`no suitable GPU device`, ~41 W idle) |
| **2026-11-07 18:14** | **True cold AC drain → boot → `restore-boost.sh --verify`** | **PASS 1700 MHz / 149 W / 10.9 TFLOPS** |

## 6. The minimal, correct model

```
failed S3  ->  SMU firmware latch (L1)  ->  cold AC drain clears it
                                          ->  pp_table re-upload re-arms DPM
                                          ->  verify-boost PASS
(bad driver patch L2 must be absent for the pp_table re-upload to complete)
```

Everything in that chain is software/firmware state. No board-level repair,
no VBIOS flash, no PSU swap.
