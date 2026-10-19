# MI50 / Vega20 boost cap — investigation, root cause, and fix

**Symptom.** The card never left DPM level 0: SCLK pinned at **859 MHz** under
any load, MCLK at 350 MHz, VDDGFX at its minimum (~740 mV), ~45–50 W package
power instead of ~190 W, ≈ 5.9 TFLOPS FP32 instead of ≈ 13 TFLOPS.

**Root cause.** A **latched SMU / power-protection state on the card** that
survives a warm reboot (and a GPU driver reinit). It was not the driver, not the
firmware, not the VBIOS tables, not thermals, not the power cap.

**Fix (2026-09-22).** A **full AC power cycle**:

```
shutdown -h now  →  PSU switch OFF (or unplug)  →  hold power button 5–10 s
→  wait ~60 s  →  restore power  →  boot
```

After this the card boosts to **1700 MHz** under load. Verified immediately
after the AC cycle with `tools/verify-boost.sh` (see
`logs/2026-09-22-postfix-verification.log`):

```
99% busy -> sclk 1700 MHz, mclk 350/800 MHz, power 137-147 W,
junction 43 -> 77 C, FP32 11.77 TFLOPS   (was 5.83 TFLOPS when capped)
```

No software change was necessary for this first latch.

**Fix 2 (2026-10-19) — re-upload the PowerPlay table.** A second, distinct
latch appeared after a failed S3 suspend. It survived a **true AC power cycle**
(PSU off, AC unplugged, 120 s), both the AMD DKMS and the in-tree driver, and
was visible in Windows too. VBIOS, SMU firmware and the PP table were
unchanged. Writing the card's own `pp_table` back to the SMU re-armed DPM
immediately (1700 MHz / 151 W / 11.75 TFLOPS). It can recur after idle; re-run
the script. Recipe: **§2 Step 9**; tool: `sudo tools/restore-boost.sh --verify`.

> If the cap comes back: run **§0 Fast path**. If a full AC cycle does not
> clear it, try **Step 9** (PP-table re-upload) before anything else. Read §3
> before removing drivers or reflashing anything.

---

## 0. Fast path when it regresses

```bash
cd gpu-boost/tools
./verify-boost.sh          # 15 s load + sampling, prints PASS/FAIL
```

- **PASS** (peak SCLK ≥ 1500 MHz, power climbing) → nothing to do.
- **FAIL** (SCLK stays 859 MHz) → do the full AC power cycle above and re-run.
- **Still FAIL after AC cycle** → re-upload the PP table (**§2 Step 9**):
  `sudo tools/restore-boost.sh --verify`. This is the 2026-10-19 fix; it works
  even when a true AC cycle does not.
- **Still FAIL after Step 9** → follow §2 from the top. Do **not** start by
  removing drivers / reflashing the VBIOS; §3 lists the dead ends already
  explored (each one costs hours).

---

## 1. System inventory

| Item | Value |
|---|---|
| Board | ASUS PRIME B450M-A II, BIOS 3802 |
| CPU/APU | AMD Cezanne (Renoir family), iGPU `1002:1638` = card2, drives the display |
| Super-I/O | ITE IT8655E, out-of-tree `it87` DKMS (`bc06d34.20260913`), `hwmon5` |
| GPU | Vega20 `1002:66a1` rev 06, 60 CU, 16 GB HBM2, "Radeon Pro VII" by VBIOS, **card1 / `0000:03:00.0`**, hwmon1 |
| GPU VBIOS | `113-D1640600-104`, ver `016.004.000.064.015319`, build 2020-08-18, SSID `1002:081E` |
| GPU board (physical) | 1× mini-DP, 2× 8-pin, passive; pre-flashed by a previous owner, **no original backup available** |
| PCIe topology | `00:01.1 → 01:00.0 (1002:14a0) → 02:00.0 (1002:14a1) → 03:00.0 GPU`; Gen4 to the GPU, Gen3 x16 to the CPU |
| Kernel | `6.8.0-139-generic` |
| Drivers tested | in-tree `amdgpu 3.57.0` **and** AMD DKMS `amdgpu-dkms 1:6.14.14.30100200-2226257.24.04` (ROCm 7.0.2 repo `30.10.2`) — identical behaviour |
| SMU firmware | `vega20_smc.bin`, reported version `0x00283c00` (00.40.60.00) |
| Userspace | ROCm 7.0.2 + `nullkalahar/mi50-rocm7` userspace mod, `HSA_OVERRIDE_GFX_VERSION=9.0.0` |
| Fan | external blower wired to mobo CHA_FAN (`hwmon5/pwm3`), see `../fan-control/` |

Notes that matter for future debugging:

- **`HSA_OVERRIDE_GFX_VERSION=9.0.0`** (in `~/.bashrc`) is required: ROCm 7.x
  dropped gfx906. The `nullkalahar/mi50-rocm7` mod is userspace-only (rocBLAS
  tensor files from ROCm 6.4) and cannot affect clocks/DPM.
- The GPU's own fan header is empty; `hwmon1/pwm1` is a phantom. The real fan is
  on the motherboard Super-I/O.
- `hwmon1` exposes **`power1_input`**, not `power1_average` (sampling scripts
  must use that or they read 0 W).
- The GPU has **no `03:00.1` audio function** even with the Pro VII BIOS.
- The two undocumented PCI bridges (`1002:14a0/14a1`) are **normal** — see §3.5.

---

## 2. The diagnostic pathway that works

### Step 1 — Reproduce it with a real load

```bash
cd gpu-boost/tools
gcc -O2 -o gpu_burn gpu_burn.c -lOpenCL   # or use the prebuilt ./gpu_burn
./gpu_burn 20 20000 & ./monitor.sh 22
```

- Broken: every sample `sclk=859Mhz ... power≈45W`, burner prints ≈ 5.8–5.9 TFLOPS.
- Healthy: `sclk=1654Mhz/1700Mhz`, power climbs toward 150–190 W, junction temp rises.

`gpu_burn` is a self-contained OpenCL FP32 FMA loop (no ROCm/HIP deps; it also
works through the `HSA_OVERRIDE_GFX_VERSION=9.0.0` shim).

### Step 2 — Exclude thermal / power / feature limits

```bash
rocm-smi --showtemp --showpower
cat /sys/class/drm/card1/device/hwmon/hwmon1/power1_cap     # 190 W
cat /sys/class/drm/card1/device/pp_features                  # GFXCLK_DPM must be Y
```

In the broken state: edge/hotspot ~50 °C, 45 W of a 190 W cap, `GFXCLK_DPM=Y`.
Conclusion: nothing was limiting it from the driver's side.

### Step 3 — Exclude the driver

```bash
modinfo amdgpu | grep -E "filename|version"
cat /sys/module/amdgpu/srcversion
dkms status
```

We removed the ROCm 7.0.2 DKMS and rebooted onto the in-tree 6.8 module:

```bash
sudo dkms remove amdgpu/6.14.14-2226257.24.04 --all
sudo update-initramfs -u && sudo reboot
# then verify: loaded srcversion == in-tree amdgpu.ko.zst srcversion
```

Result: **identical cap**. The driver is not the cause. (Leaving the DKMS out is
fine; both work.)

### Step 4 — Exclude the firmware

```bash
md5sum /lib/firmware/updates/amdgpu/vega20_smc.bin
zstdcat /lib/firmware/amdgpu/vega20_smc.bin.zst | md5sum
```

Both `69e72dea2cf023116b59e2f3fd5e6e6e`, and that exact file has been in
linux-firmware since 2021 (git.kernel.org tags) — i.e. the same firmware every
working MI50 uses. Also note Vega20 loads its SMU firmware **from this file**
(`amdgpu_cgs.c`, `CHIP_VEGA20` → `amdgpu/vega20_smc.bin`), not from the VBIOS.

### Step 5 — Inspect the VBIOS; do not assume the flash is bad

```bash
cp /sys/class/drm/card1/device/pp_table .     # world-readable
python3 parse_pptable.py                      # full SMU PP table
sudo cat /sys/kernel/debug/dri/0000:03:00.0/amdgpu_vbios > card-vbios-legacy.rom
```

- `/sys/bus/pci/devices/0000:03:00.0/rom` reads **0 bytes** — amdgpu fetched the
  VBIOS from the ACPI **VFCT** table and disabled the ROM BAR. Use the debugfs
  `amdgpu_vbios` blob (root only). It yields the 59 904-byte legacy image
  (`0x75 × 512`), which contains all ATOM tables.
- Comparing `card-vbios-legacy.rom` with the genuine stock ROM
  (`roms/stock-provii-200818.rom`, md5 `64d1c521a9fd0ae594e4ca9d9e14f8c7`,
  fetched via the Internet Archive) shows **exactly 3 differing bytes**, and
  they are checksum-neutral:

  | Offset | Field | Stock | Card |
  |---|---|---|---|
  | `0x21` | ROM header checksum byte | `0x94` | `0x92` (−2) |
  | `0xD864` | `firmwareinfo.firmware_capability` | `0x04` | `0x05` (+1) |
  | `0xD878` | `firmwareinfo.mem_module_id` | `0x00` | `0x01` (+1) |

  Sum of changes = 0 → someone edited it deliberately with a proper tool
  (almost certainly to match this board's HBM module). **The power/voltage
  tables are byte-identical to stock.** Reflashing was therefore the wrong move.

### Step 6 — Force the clock (and understand the sysfs semantics first)

`pp_dpm_*clk` takes a **list of level indices** (0–31). The kernel converts each
to a mask bit: `*mask |= 1 << level` (`amdgpu_read_mask()`), so:

```bash
# level index 8 = 1700 MHz, level index 2 = 1000 MHz (MCLK)
echo manual | sudo tee /sys/class/drm/card1/device/power_dpm_force_performance_level
echo 8      | sudo tee /sys/class/drm/card1/device/pp_dpm_sclk
echo 2      | sudo tee /sys/class/drm/card1/device/pp_dpm_mclk
# watch the SMU's own view:
sudo cat /sys/kernel/debug/dri/0000:03:00.0/amdgpu_pm_info | grep -E "SCLK|MCLK|VDDGFX|Load"
# restore:
echo auto   | sudo tee /sys/class/drm/card1/device/power_dpm_force_performance_level
```

Broken state: writes return success, soft-min/soft-max are sent to the SMU
(`PPSMC_MSG_SetSoftMinByFreq`/`SetSoftMaxByFreq`), yet the SMU keeps running
856 MHz at 737 mV with **GPU Load 99 %**. That is the signature of a card-side
SMU block, not a software bug.

Watch out: `PSTATE_SCLK` in `amdgpu_pm_info` is the SMU **STABLE_PSTATE** sensor
(`AMDGPU_PP_SENSOR_STABLE_PSTATE_SCLK`) and was stuck at 1316 MHz regardless of
what was forced. It is *not* the requested level.

### Step 7 — Full AC power cycle (the actual fix)

1. `shutdown -h now`
2. Switch the PSU off at the back, or unplug the AC cable.
3. Press the front power button 5–10 s (drain the standby rail).
4. Wait ~60 s.
5. Restore power, boot, and re-run step 1.

Why this is plausible for Vega20: the SMU keeps protection/latch state
(thermal, power, AVFS) in an always-on domain that a warm reset / driver reload
does not clear, but removal of AC does.

### Step 8 — Confirm

`verify-boost.sh` should print PASS: SCLK rising through the DPM levels
(1153 → 1316 → … → 1700 MHz), power climbing, temps rising under load.

### Step 9 — If a true AC cycle does not clear it: re-upload the PP table (2026-10-19)

This is a **second latch variant**, first seen after a failed S3 suspend
(2026-10-11 19:27: `UNLOAD_TA(0x2)` returned `0x117`, PSP mode1 reset failed,
SMU messages then returned `0xffffffff`; repeated 2026-10-12 21:15). It
survives a **true** AC cycle (PSU switch off + AC cable unplugged + 120 s),
the DKMS and in-tree drivers, and is visible in Windows. The VBIOS dump
(`526c3dfa…`), SMU firmware (`69e72dea…`) and live `pp_table` (`80d94c6a…`)
are unchanged. The SMU's **active PowerPlay/DPM state** is wedged, not the
driver, the VBIOS or the power supply.

Writing the card's own table back through sysfs forces the driver to re-run
`vega20_init_smc_table` and re-arm DPM:

```bash
cd gpu-boost/tools
sudo ./restore-boost.sh --verify
# or manually:
sudo cp /sys/class/drm/card1/device/pp_table /tmp/pp_stock.bin
sudo sh -c 'cat /tmp/pp_stock.bin > /sys/class/drm/card1/device/pp_table'
./verify-boost.sh
```

Verified 2026-10-19: **1700 MHz / 151 W / 11.75 TFLOPS** immediately after the
write; before it, 859 MHz / 49 W / 5.96 TFLOPS. On Windows the same capped
state showed ~77 W in Furmark; the Pro VII vBIOS is locked to 190 W and a
healthy card draws ~190 W there, so that number is the cap, not proof of
adequate power (unless it was the PCIe-slot rail only — check the aux rails).

This is a **runtime re-arm, not a permanent repair**: on the same boot it
returned after ~15 minutes of idle (no reboot, no suspend). Re-run the script
when it returns. Full transcript: `logs/2026-10-19-pptable-reupload-fix.log`.

Writing `pp_table` is the same mechanism UPP uses; it only re-uploads the
current (stock) table, so it is reversible by rebooting. Do not confuse it
with the AC-cycle fix — both are real, but they clear different latch states.

---

## 3. Wrong turns, traps, and corrections

Recorded so nobody repeats them.

1. **“The ROCm 7.0.2 DKMS driver (amdgpu 6.14.14) caps the card.”** *Wrong.*
   Removed the DKMS, rebooted onto in-tree 6.8 — byte-for-byte the same cap.
2. **“AMD's `amdgpu-dkms-firmware` overrides distro firmware with something
   incompatible.”** *Wrong.* `/lib/firmware/updates/amdgpu/vega20_*.bin` are
   byte-identical (md5) to the distro `.zst` originals and unchanged since 2021.
3. **“The cross-flashed Pro VII BIOS has broken power/voltage tables.”**
   *Wrong.* Only 3 checksum-neutral bytes differ from the genuine stock image
   (step 5). The PP table parses perfectly (190 W, 9 GFX levels, UCLK 1000).
4. **“Reflash the VBIOS.”** *Wrong next step* given (3) — and dangerous here,
   because the previous owner did not keep a backup. If a reflash is ever
   needed: first make a **full** flash dump with `amdvbflash -s` (the debugfs
   image lacks the UEFI/padding part of the 512 KB ROM).
5. **“The undocumented `1002:14a0/14a1` bridges prove a frankenboard.”**
   *Wrong.* `pci.ids` documents the same pattern for Navi 10:
   `1478 "Navi 10 XL Upstream Port of PCI Express Switch"`,
   `1479 "… Downstream Port …"`. These are GPU-die internal PCIe switch ports
   (Vega20 is Gen4-capable). The `1× mini-DP` observation is still unusual,
   but the bridges are not evidence.
6. **`pp_dpm_sclk` bitmask confusion.** I first assumed writing `8` meant
   “level 8”, then — after seeing `PSTATE_SCLK 1316` — wrongly concluded that
   `8` had selected level 3. The truth: input numbers are **level indices**
   (`1 << level`, max 31), so `echo 8` *is* level 8 = 1700 MHz, and 1316 was an
   unrelated sensor. `echo 256` is simply out of range (`EINVAL`).
7. **“ULV / ppfeaturemask / COMPUTE profile / BAPM will unlock it.”** All
   no-ops in the broken state — the SMU wasn't acting on any request. Don't
   chase these when the SMU refuses soft-min/max.
8. **“PCIe aux power / riser problem.”** Ruled out: both 8-pins seated, card
   directly in the CPU x16 slot; Gen3/Gen4 link trained fine.
9. **Tooling bug (self-inflicted):** the first `gpu_burn` queued kernels
   asynchronously for N seconds without `clFinish` per launch; the final
   `clFinish` then waited on a huge backlog and looked like a GPU hang. Fixed
   in `tools/gpu_burn.c` (one `clFinish` per launch).
10. **Sampler bug:** `power1_average` does not exist on the in-tree driver;
    use `power1_input` (µW) or you will log “0 W” forever.
11. **Dead ends that cost time but are now ruled out:** kernel `amdgpu.dpm`,
    `bapm`, `ppfeaturemask` variants; `pp_power_profile_mode=5`; SMU feature
    masks; PCIe ASPM; ECC on/off.

---

## 4. Reference data

### GFX DPM levels (`pp_dpm_sclk`)

| Level | MHz |
|---|---|
| 0 | 859 |
| 1 | 860 |
| 2 | 1153 |
| 3 | 1316 |
| 4 | 1425 |
| 5 | 1514 |
| 6 | 1583 |
| 7 | 1654 |
| 8 | 1700 |

MCLK: `0:350 1:800 2:1000` · FCLK: 550…1180 · SOC idle 309.

### Limits (from VBIOS tables / hwmon)

| Limit | Value |
|---|---|
| Power cap | 190 W (default = max; OD disabled) |
| Edge | 100 °C soft / 105 °C shutdown |
| Hotspot | 105 °C / 110 °C |
| HBM | 94 °C / 99 °C |
| VDDGFX | 737.5 mV min / 1162.5 mV max (table units 1/4 mV) |
| TDC Gfx / Soc | 330 A / 50 A |

### Firmware / VBIOS fingerprints

| Thing | Value |
|---|---|
| `vega20_smc.bin` md5 | `69e72dea2cf023116b59e2f3fd5e6e6e` (since ~2021; older: 2020-06 `00324f7f0da6c8b578dce1452decfe94`, 2019 `4e29d77555e0596910e9fcbbd8b6b4ef`) |
| SMU FW version | `0x00283c00` (00.40.60.00) |
| Stock Pro VII 16 GB 200818 ROM | `roms/stock-provii-200818.rom`, md5 `64d1c521a9fd0ae594e4ca9d9e14f8c7` |
| Card's VBIOS (legacy image) | `roms/card-vbios-legacy.rom`, md5 `526c3dfaeb2f9befeb9542a86a643c88` |
| Card VBIOS vs stock | 3 checksum-neutral bytes (see §2 step 5) |
| `pp_table.bin` | `roms/pp_table.bin` (VBIOS SMU table as exposed by sysfs) |

### Environment

- 2026-10-19 tests: kernel `6.8.0-147-generic`, in-tree amdgpu
  (srcversion `14194004E04B80BBA4F990C`); the card still capped until the
  PP table was re-uploaded (Step 9).
- ROCm 7.0.2 userspace needs `export HSA_OVERRIDE_GFX_VERSION=9.0.0` (gfx906
  dropped upstream). Device then reports as `gfx900:xnack-`.
- The GPU is `card1`; the iGPU is `card2` and owns the console.

---

## 5. Tools

| File | What it does |
|---|---|
| `tools/gpu_burn.c` | Self-contained OpenCL FP32 FMA burner (`gcc … -lOpenCL`). Usage: `./gpu_burn [seconds] [iters] [threads]`. Burner prints achieved GFLOP/s. Prebuilt `tools/gpu_burn` included. |
| `tools/monitor.sh` | Samples `pp_dpm_sclk/mclk`, `gpu_busy_percent`, `power1_input`, junction temp. Usage: `./monitor.sh [seconds]`. |
| `tools/verify-boost.sh` | One-shot health check with PASS/FAIL verdict; tells you to do the AC power cycle on FAIL. |
| `tools/restore-boost.sh` | 2026-10-19 DPM0 recovery: backs up and re-uploads the live `pp_table` to re-arm SMU DPM. Needs root; `--verify` runs the health check afterwards. |
| `tools/boost_probe.sh` | Full forced-clock diagnostic (needs root): forces level 8 + MCLK level 2, runs load, dumps `amdgpu_pm_info`/firmware info, restores auto. |
| `tools/boost_probe2.sh` | Historical variant (kept for fidelity). **Note:** it writes `256`, which is invalid — level indices max 31. Use `boost_probe.sh`. |
| `tools/parse_pptable.py` | Parses `roms/pp_table.bin` (or live `pp_table`) into limits, DPM descriptors, frequency tables, fan/AVFS fields. |
| `tools/parse_avfs.py` | Float-decodes the DpmDescriptor/AVFS-related fields of the PP table. |
| `tools/parse_rom.py` | Parses the ATOM BIOS master data table of a dumped ROM: finds `smc_dpm_info`, `firmwareinfo`, `powerplayinfo`, I2C/VRM mappings. Edit the input path at the top if needed. |

---

## 6. Artifacts

```
roms/
  stock-provii-200818.rom    genuine AMD Radeon Pro VII 16 GB BIOS (reference)
  card-vbios-legacy.rom      the card's own VBIOS image (59904 B legacy part)
  pp_table.bin               card's SMU PowerPlay table (live copy)
  README.md                  what these are / how to regenerate
logs/
  2026-09-22-probe1-forced-clock.log   broken-state forced-clock test
  2026-09-22-probe2-forced-clock.log   broken-state second pass (mask syntax)
  2026-09-22-amdgpu_pm_info-example.txt  full pm_info dump (broken state)
  2026-10-19-pptable-reupload-fix.log  DPM0 latch surviving AC + PP-table fix
```

The logs are curated transcripts of the failing runs (repeated
`amdgpu_pm_info` blocks trimmed). They can be regenerated with the probe
scripts whenever a regression is suspected.
