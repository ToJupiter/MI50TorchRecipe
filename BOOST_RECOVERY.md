# MI50 / Vega20 boost recovery — the ordered recipe

> **Confirmed 2026-11-07:** after following this order, `verify-boost.sh`
> reported `peak sclk=1700MHz  peak power=149W`, `achieved 10927.34 GFLOP/s
> fp32`, `RESULT: PASS — GPU boosts.`
>
> This is a **software + firmware state problem, not a hardware fault**. Nothing
> here requires replacing the PSU, the GPU, or the VBIOS.

Companion documents:

| File | What it contains |
| --- | --- |
| `gpu-boost/docs/01-ROOT-CAUSE.md` | Condensed knowledge: what the cap is, why it happens, what was ruled out |
| `gpu-boost/docs/02-REFERENCE.md` | Evidence tables, reset-method enum, wrong turns, FAQ, tool/env index |
| `gpu-boost/README.md` | Original investigation log (older; the ordering here supersedes it) |

---

## The symptom

Under full load the card stays at DPM level 0 instead of boosting:

| | Capped (broken) | Healthy |
| --- | --- | --- |
| SCLK | 859 MHz | 1654–1700 MHz |
| MCLK | 350 MHz | 350 / 800 MHz |
| SOCCLK / FCLK | 309 / 550 MHz | up to 971 / 1180 MHz |
| Package power | 43–49 W | 132–151 W |
| FP32 throughput | ~5.7–5.9 TFLOPS | ~10.9–11.8 TFLOPS |
| `throttle_status` | 0 | 0 |

Everything else works: telemetry, OpenCL/KFD, VRAM, all rings, `GFXCLK_DPM=Y`,
`pp_features` normal, PP table stock, SMU answers messages. **The SMU is alive;
its DPM/low-power state is parked.**

---

## Why the ORDER matters

Two independent software/firmware layers are stacked, and each step only works
after the previous one:

1. **L0 — the trigger.** A failed S3 suspend leaves the Vega20 SMU (MP1) in a
   half-completed power-down (`UNLOAD_TA 0x117`, `DFCstateControl 0x63 →
   0xffffffff`, `DisableAllSmuFeatures` / `PowerOffAsic` failing, `GPU Recovery
   Failed: -62`). This latches a minimum-power state in the SMU firmware.
2. **L1 — the cap.** That latched state survives warm reboots and BACO (BACO
   keeps the SMU always-on domain powered and sends no `PrepareMp1For*`
   message). Only a **true cold AC drain** (full SMU firmware reload) clears it.
3. **L2 — a historical self-inflicted obstacle.** A hard-DPM driver patch
   (`SetHardMinByFreq 0x28` for GFXCLK) makes the SMU return
   `PPSMC_Result_Failed (0xff)`, which aborts `vega20_unforce_dpm_levels()` on
   every boot. While that module is loaded, *no* reset or PP re-upload can work.
   **It must be removed first.**

Then, after the cold drain, DPM must be **re-armed** by re-uploading the card's
own PowerPlay table. A cold drain alone is not enough; the PP re-upload alone
is not enough. The order is:

```
stock driver  ->  (when capped) cold AC drain  ->  PP-table re-upload  ->  verify
```

---

## STEP 0 — One-time setup (do this once, while the card is healthy)

### 0.1 Make sure the driver module is stock

```bash
cd gpu-boost/tools
./mi50-diagnose.sh                 # NO sudo; needs the 'adm' group for journalctl -k
```

Sections 3 and 4 must say **stock** (both the on-disk module and the module
embedded in the initramfs), and the boot table must show `0` in the `0x28` /
`hardmin` / `dpmbootup` columns.

If it says **HARD-PATCHED**:

```bash
sudo ./mi50-rebuild-stock-dkms.sh  # verifies source, module AND initramfs
sudo reboot
./mi50-diagnose.sh                 # re-check: must be stock
```

Do not skip the initramfs verification — early boot loads `amdgpu` from
`/boot/initrd.img-*`, so a correct on-disk module alone is not enough.

### 0.2 Remove the trigger (prevent S3)

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

The original wedge came from a failed S3 suspend. If the machine never enters
S3, L0 cannot re-latch. (Keep `amdgpu.runpm=0` on the kernel command line.)

### 0.3 Install the boot-time re-arm guard

```bash
cd gpu-boost/systemd
sudo ./install-guard.sh --now
```

This installs a oneshot unit that re-uploads the card's own `pp_table` once at
every boot, so DPM is armed even if the SMU comes up lazy. It is reversible and
does not pin clocks. Do not start GPU jobs until it has finished (a few seconds).

---

## STEP 1 — Detect the cap (under load, never at idle)

```bash
cd gpu-boost/tools
./verify-boost.sh                  # 15 s FP32 load, prints PASS / FAIL
```

> **Important:** at idle a *healthy* Vega20 also sits at 859 MHz. Never judge
> the cap from an idle `pp_dpm_sclk` star or `gpu_metrics` — always use a load
> test.

- **PASS** (peak ≥ 1500 MHz) → nothing to do.
- **FAIL** → continue to STEP 2.

For the full picture (no sudo): `./mi50-diagnose.sh`.

---

## STEP 2 — Clear the SMU latch: TRUE cold AC drain (physical)

This is the one step that cannot be automated. Do it in this exact order:

```
1. shutdown -h now
2. PSU switch OFF  *and* unplug the AC cable
3. hold the case power button 10 s        (discharge the capacitors)
4. wait >= 5 minutes                     (SMU always-on rail must fully discharge)
5. restore AC, boot
```

Why not something gentler:

| Attempt | Why it does not clear L1 |
| --- | --- |
| warm `reboot` | GPU keeps power; SMU firmware is not reloaded |
| BACO reset | keeps the SMU always-on domain powered; sends **no** MP1 message |
| MODE1 / MODE2 reset | can reload SMU firmware, but was observed to leave the GPU wedged (`no suitable GPU device`, ~41 W idle) and then needed a cold drain anyway |
| `pp_table` re-upload alone | re-runs `EnableDPMTasks`, but cannot clear a latched firmware state |

---

## STEP 3 — Re-arm DPM: re-upload the card's own PowerPlay table

```bash
cd gpu-boost/tools
sudo ./restore-boost.sh --verify
```

`restore-boost.sh` copies the live `pp_table` to a temp file and writes it back.
That triggers `amd_powerplay_reset()` → `hwmgr_hw_fini/init` →
`vega20_enable_dpm_tasks()` (table upload, `RunBtc`, `RunAfllBtc`,
`EnableAllSmuFeatures`, default DPM tables). It re-uploads the card's **own**
table, so it is reversible by reboot and cannot corrupt the VBIOS.

Expected result:

```
DPM now: 1: 860Mhz *        (idle — normal, do not panic)
t= 2s busy=99%  sclk=1700Mhz  power=132W
...
== peak sclk=1700MHz  peak power=149W
achieved 10927.34 GFLOP/s fp32
RESULT: PASS — GPU boosts.
```

### One-command version of STEP 1 + STEP 3

```bash
sudo gpu-boost/tools/mi50-fix.sh
```

It runs the checks in order (already boosting? → is the module stock? →
re-arm + verify → otherwise print the cold-drain procedure). Re-run it after
the cold-drain boot.

---

## STEP 4 — Verify and make it stick

```bash
cd gpu-boost/tools
./verify-boost.sh                  # PASS
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
sudo ../systemd/install-guard.sh --now
```

If the cap returns after a long idle (this decay was observed within a single
boot), just re-run STEP 3 — or `sudo gpu-boost/tools/mi50-fix.sh`, which does the
detection and re-arm in order. The boot guard covers reboots; masking S3 removes
the original trigger. If decay is frequent on your host, add a systemd timer
that runs `restore-boost.sh` on an interval (separate from the oneshot boot
guard), but note that each re-upload briefly resets DPM, so avoid running it
during a training job.

---

## Fallback — software pin (when you cannot power-cycle yet)

If you need compute *now* and cannot do STEP 2:

```bash
sudo gpu-boost/tools/mi50-pin-clock.sh --verify
```

This sets `power_dpm_force_performance_level=high` and re-uploads `pp_table`,
which forces soft min = soft max = 1700 and makes the SMU report
`8: 1700Mhz *`. The `*` is the SMU's **real** clock
(`PPSMC_MSG_GetDpmClockFreq 0x2D`), not driver bookkeeping.

**Rules:**

- **Never** set `perf=auto` afterwards — `unforce_dpm_levels()` re-uploads
  soft min = 859 / max = 1700 and the card drops straight back to 859 MHz.
- It pins GFX/MCLK/SOCCLK at maximum **at all times** → higher idle power and
  heat. Keep the external blower on; watch junction temperature.
- Release with `sudo ./mi50-pin-clock.sh --release`.

This is a workaround for the wedged *autonomous* DPM boost, not a cure. Do
STEP 2 as soon as you can.

---

## Decision tree (one screen)

```
verify-boost.sh FAIL
  └─ mi50-diagnose.sh: HARD-PATCHED module?
       ├─ yes -> mi50-rebuild-stock-dkms.sh + reboot -> back to START
       └─ no  -> cold AC drain (>=5 min, PSU off + unplug) -> boot
                   └─ sudo mi50-fix.sh  (or restore-boost.sh --verify)
                        ├─ PASS -> mask suspend + install boot guard -> DONE
                        └─ FAIL -> mi50-pin-clock.sh --verify (workaround)
                                   then repeat cold AC drain
```

---

## What NOT to do

- **Do not** declare the SMU / VRM / PSU faulty. The SMU answers messages,
  produces valid `gpu_metrics`, enables `GFXCLK_DPM`, and holds 1700 MHz when
  driven. Hardware does not start/stop failing in sync with a driver patch.
- **Do not** reflash the VBIOS. The PP table and VBIOS are stock and unchanged.
- **Do not** use the hard-DPM patch, and **do not** force
  `amdgpu.reset_method=4` (that is `LINK`, not BACO).
- **Do not** judge the cap from an idle clock.
- **Do not** leave a GPU reset method forced; restore `reset_method=-1`.
- **Do not** run `kill -9` / `timeout` on GPU jobs or let a suspend fail —
  that is the original trigger.

---

## Tool index

All scripts live in `gpu-boost/tools/` and locate the card by PCI ID
(`1002:66a1`) at runtime. Nothing hardcodes `cardN`, `hwmonN`, a PCI BDF, a
kernel release, a DKMS version, a username, a hostname or a home directory.

| Tool | Root? | Purpose |
| --- | --- | --- |
| `mi50-common.sh` | — | Shared detection library (sourced, not executed) |
| `verify-boost.sh` | no | 15 s load test → PASS/FAIL |
| `mi50-diagnose.sh` | no | Deep no-sudo diagnosis with verdict |
| `restore-boost.sh` | **yes** | Re-upload the card's own `pp_table` (the fix) |
| `mi50-fix.sh` | **yes** | Ordered STEP 1 + STEP 3 driver |
| `mi50-rebuild-stock-dkms.sh` | **yes** | Remove a hard-patched module (L2) |
| `mi50-pin-clock.sh` | **yes** | Optional software pin / `--release` |
| `mi50-reset.sh` | **yes** | Last-resort MODE1/MODE2/BACO reset |
| `gpu_burn.c` / `Makefile` | — | OpenCL FP32 load generator |
| `systemd/install-guard.sh` | **yes** | Boot-time PP re-arm guard |

Overrides (all optional): `MI50_CARD`, `MI50_KERNEL`, `MI50_DKMS_VER`,
`MI50_REPO`, `MI50_BAD_SRCVERSION`, `MI50_SKIP_INITRAMFS`,
`HSA_OVERRIDE_GFX_VERSION`. See `gpu-boost/docs/02-REFERENCE.md`.

---

## Privacy note

The new/rewritten tools contain **no machine-identifying data**: no username,
hostname, home path, UUID, serial, IP address, motherboard model or BIOS
version. The card is located by PCI ID at runtime, and no tool depends on a
hardcoded checksum or kernel/DKMS version. The historical
`gpu-boost/README.md` still quotes public AMD firmware / PP-table checksums
(technical fingerprints, not personal data); its one motherboard model + BIOS
line has been generalised. `02-REFERENCE.md` → "Record your own fingerprints"
explains how to record *your* card's values privately.
