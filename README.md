# AILab — Radeon Pro VII (gfx906) on ROCm 7.0.2

A small, self-contained PyTorch lab for measuring and training on the AMD Radeon
Pro VII / Radeon Instinct MI50 (Vega 20, `gfx906`) under a ROCm 7.0.2 stack that
*does not officially support the card*. Everything here was validated on this
machine, and the repository exists to document the exact configuration that makes
it work and to record the numbers it produces.

- **Benchmark**: matmul sweep (fp32/fp16/bf16), conv2d, H2D/D2H/D2D bandwidth,
  sustained power-clamped run.
- **Watch**: live TFLOPS / clock / power / temperature monitor driven entirely by
  sysfs, so it never pollutes the measurement.
- **Train**: MLP training on a synthetic dataset resident in VRAM or system RAM.
- **Infer**: llama-style decoder, prefill throughput and KV-cache decode.

> The companion reference with the full install recipe and troubleshooting matrix
> is [`ROCm7_TORCH_RECIPES.md`](ROCm7_TORCH_RECIPES.md).

---

## Results snapshot

Measured after a clean boot, DPM in `auto`, 190 W cap. Full tables below.

| Metric | Value |
| --- | --- |
| fp16 matmul 16384² | **17.17 TFLOPS** |
| fp16 matmul 8192² | 16.37 TFLOPS sustained for 8 s |
| fp32 matmul 8192² | 9.11 TFLOPS |
| fp32 sustained (10 s) | 9.37 TFLOPS @ 1316 MHz, 188.8 W, 67 °C |
| D2D copy | 791.7 GB/s |
| Training (3.15 M MLP, VRAM data) | 240,737 samples/s · 4.55 TFLOPS |
| Inference prefill (1024 tok) | 6.03 TFLOPS · 88,790 tok/s |
| Inference decode (batch 32) | 5,394 tok/s |

The card is **power-limited, not thermally limited**: at the 190 W cap the
sustained clock settles around 1300–1400 MHz (77–82 % of the 1700 MHz maximum),
which is the intended steady state.

---

## Hardware and software

| Item | Value |
| --- | --- |
| GPU 0 (compute target) | AMD Radeon Pro VII / Radeon Instinct MI50, Vega 20 |
| PCI ID | `1002:66a1` at `0000:03:00.0` → sysfs `card1` |
| ISA | `gfx906:sramecc+:xnack-` |
| Compute units | 60 |
| VRAM | 16 GB HBM2 (`17163091968` bytes) |
| Power cap | 190 W |
| Peak fp32 | 13.06 TFLOPS at 1700 MHz |
| Peak fp16 | 26.11 TFLOPS at 1700 MHz (packed math) |
| bf16 / TF32 / matrix cores | none (bf16 is emulated by rocBLAS; avoid it) |
| GPU 1 | Ryzen APU iGPU, `gfx90c:xnack-` (`1002:1638`) at `0000:0c:00.0` → `card2`; not used |
| ROCm | 7.0.2 at `/opt/rocm` |
| PyTorch | 2.10.0+rocm7.0, HIP 7.0.51831 |
| Kernel | 6.8.0-142-generic |

The `sramecc+` marker means HBM ECC is enabled. It shaves a few percent off
bandwidth and is why the card reports 16 GB rather than 32 GB.

### The support caveat

ROCm 7.0.2 **does not list gfx906 as supported** (MI50 / Radeon Pro VII /
Radeon VII are marked unsupported). gfx906 Tensile kernels were removed from the
`rocBLAS` binaries shipped in ROCm wheels upstream, and `triton-rocm` has no
gfx906 backend. The configuration below restores gfx906 acceleration by pointing
the PyTorch wheel's rocBLAS at the Tensile kernels that *do* still ship inside the
system `/opt/rocm` install.

---

## Quick start

Prerequisites: Linux, `uv`, ROCm 7.0.2 installed at `/opt/rocm`, and the user in
the `render` and `video` groups.

```bash
uv venv --python 3.11 .venv
uv sync
```

`pyproject.toml` already pins the ROCm wheel index:

```toml
dependencies = [
  "torch==2.10.0+rocm7.0",
  "torchvision",
  "triton-rocm==3.6.0; sys_platform == 'linux'",
]

[[tool.uv.index]]
name = "pytorch-rocm"
url = "https://download.pytorch.org/whl/rocm7.0"
explicit = true
```

Verify:

```bash
.venv/bin/python -c "import torch; print(torch.__version__, torch.version.hip, torch.cuda.is_available())"
.venv/bin/python main.py devices
```

---

## Critical gfx906 configuration

### 1. rocBLAS Tensile path (required)

The wheel bundles Tensile kernels only for `gfx908, gfx90a, gfx942, gfx950,
gfx1030, gfx110x, gfx115x, gfx120x`. The first matmul would otherwise fail with:

```
rocBLAS error: Cannot read .../torch/lib/rocblas/library/TensileLibrary.dat: Illegal seek for GPU arch : gfx900
```

The system ROCm install ships the gfx906 kernels, so point the wheel at them:

```bash
export ROCBLAS_TENSILE_LIBPATH=/opt/rocm/lib/rocblas/library
```

`ailab/bootstrap.py` sets this automatically before `torch` is imported.

### 2. Environment hygiene (important)

This login shell exports variables that are actively harmful:

| Variable | Shell value | Action | Why |
| --- | --- | --- | --- |
| `HSA_OVERRIDE_GFX_VERSION` | `9.0.0` | **unset** | forces the card to report gfx900, whose Tensile kernels do not exist |
| `HIP_TRACE_API` | `0x2` | **unset** | traces every HIP call and roughly halves throughput |
| `HSA_ENABLE_SDMA` | `0` | **keep** | removing it wedges the `amdgpu` driver on this host |
| `HSA_ENABLE_INTERRUPT` | `0` | **keep** | same — the platform hangs in interrupt mode |

`bootstrap.configure()` unsets the first two on every entry point, before `torch`
is imported. Never remove the `HSA_ENABLE_*` overrides on this box.

---

## Harness layout

```
main.py                 CLI: devices | bench | watch | train | infer | all
ailab/bootstrap.py       env sanitizing + Tensile path, runs before torch import
ailab/telemetry.py       sysfs telemetry (power, clocks, busy, VRAM, temp) + sampler
ailab/device.py          device discovery and PCI/sysfs/torch index mapping
ailab/benchmark.py       matmul sweep, conv2d, H2D/D2H/D2D bandwidth, sustained test
ailab/train.py           MLP training on synthetic RAM or VRAM data
ailab/inference.py       llama-style decoder, prefill + KV-cache decode benchmark
ailab/watch.py           live TFLOPS / power / clock monitor
ailab/report.py          report tables
ailab/config.py          dataclass configs and model presets
ailab/data.py            synthetic dataset builders
ailab/model.py           model definitions
ailab/logging_setup.py   logging configuration
```

Telemetry is read straight from sysfs so `rocm-smi` is never in the hot path:

```
/sys/class/drm/card1/device/hwmon/hwmon1/power1_input   # µW
/sys/class/drm/card1/device/hwmon/hwmon1/freq1_input    # sclk Hz
/sys/class/drm/card1/device/hwmon/hwmon1/freq2_input    # mclk Hz
/sys/class/drm/card1/device/gpu_busy_percent
/sys/class/drm/card1/device/mem_info_vram_used
```

Device selection is PCI-based and arch-aware: with both a discrete `gfx906` card
and the Ryzen iGPU visible, the harness auto-selects the `gfx906` GPU. Use
`--device N` to pin one explicitly.

---

## Commands

Run everything gracefully with `.venv/bin/python`:

```bash
# Discover devices and the torch / PCI / sysfs mapping.
.venv/bin/python main.py devices

# Full benchmark: matmul 4096/8192/16384, fp32+fp16, conv2d, bandwidth, sustained.
.venv/bin/python main.py bench
.venv/bin/python main.py bench --sizes 4096,8192 --dtypes fp32,fp16 --iters 20
.venv/bin/python main.py bench --sustained-seconds 15

# Live monitor. Ctrl-C terminates cleanly; --seconds N stops on its own.
.venv/bin/python main.py watch --size 8192 --dtype fp16 --interval 1
.venv/bin/python main.py watch --size 8192 --dtype fp16 --seconds 30

# Training. Presets: tiny | small | medium | large. Data: vram | ram.
.venv/bin/python main.py train
.venv/bin/python main.py train --preset tiny --data-source ram --ram-gb 16 --steps 300
.venv/bin/python main.py train --preset medium --data-source vram --steps 600

# Inference (prefill + KV-cache decode).
.venv/bin/python main.py infer --dtype fp16
.venv/bin/python main.py infer --prefill 128,512,1024 --batch 1,8,32

# Everything back to back.
.venv/bin/python main.py all

# Pin a device or add debug logging.
.venv/bin/python main.py --device 0 --debug bench --sizes 4096 --iters 3
```

Stop a run with **Ctrl-C**, never `kill -9` or `timeout`: killing a process while
it holds the GPU can wedge `amdgpu` and force a reset. The watchdog hint printed
when the clock looks low is informational — see the DPM note below.

---

## Measured results

`main.py all` on this machine, clean boot, DPM `auto`, 190 W cap.

### Compute

| Operation | dtype | ms | TFLOPS | % peak | W avg | TFLOPS/W | busy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| matmul 4096×4096 | fp32 | 14.455 | 9.51 | 72.8 | 184.3 | 0.052 | 97 % |
| matmul 4096×4096 | fp16 | 13.841 | 9.93 | 38.0 | 193.0 | 0.051 | 100 % |
| matmul 8192×8192 | fp32 | 120.661 | 9.11 | 69.8 | 189.7 | 0.048 | 99 % |
| matmul 8192×8192 | fp16 | 67.159 | **16.37** | 62.7 | 186.5 | 0.088 | 99 % |
| matmul 16384×16384 | fp32 | 911.015 | 9.66 | 74.0 | 189.1 | 0.051 | 100 % |
| matmul 16384×16384 | fp16 | 512.440 | **17.17** | 65.7 | 189.2 | 0.091 | 100 % |
| conv2d 32×64×112 | fp32 | 4.118 | 14.37 | 110.1 | 284.0 | 0.051 | 84 % |
| conv2d 32×64×112 | fp16 | 2.812 | 21.05 | 80.6 | 210.0 | 0.100 | 99 % |

The fp16 4096² figure is launch/overhead bound; 8192² and 16384² show the real
packed-math throughput (~1.8× fp32). conv2d exceeds 100 % of the nominal peak
because cuDNN uses Winograd, which reduces the operation count below the direct
convolution FLOPs used for the percentage.

### Memory bandwidth

| Path | GB/s |
| --- | --- |
| D2D copy | 791.7 |
| H2D copy | 13.4 |
| D2H copy | 14.2 |

PCIe copies are limited by the x16 Gen3 link (~16 GB/s theoretical) and by
`HSA_ENABLE_SDMA=0`, which routes them through compute shaders. HBM ECC shaves a
few percent off D2D.

### Sustained load (fp32 matmul 4096², 10 s)

| Field | Value |
| --- | --- |
| TFLOPS | 9.37 (71.8 % of the 13.06 fp32 peak) |
| clock | 1316 MHz avg |
| power | 188.8 W avg / 216 W peak (99.4 % of the 190 W cap) |
| temperature | 67 °C peak |
| energy | 1879.5 J |
| gpu busy | 99.4 % |

The sustained fp16 point, measured over 8 s, holds **16.34 TFLOPS at ~1390 MHz
and 180–192 W**.

### Training — medium MLP, VRAM-resident data

| Field | Value |
| --- | --- |
| parameters | 3,148,801 |
| dataset | 488,281 samples × 2048 features = 4.00 GB in VRAM |
| steps | 59 |
| throughput | 240,737 samples/s (29.39 steps/s) |
| TFLOPS | 4.545 (34.8 % of fp32 peak) |
| loss | 0.436 → 0.074 |
| power | 109.8 W avg / 227 W peak |
| clock | 1212 MHz avg |
| temperature | 61 °C peak |
| VRAM | 4.35 GB allocated / 4.41 GB reserved |

The tiny preset is data-pipeline bound by design; the medium preset with
VRAM-resident data is the one that shows real training throughput.

### Inference — llama-style decoder, 27.6 M params, fp16

| Stage | Length / batch | Latency | Throughput | TFLOPS | W |
| --- | --- | --- | --- | --- | --- |
| prefill | 128 | 3.770 ms | 33,950 tok/s | 1.93 | 112.0 |
| prefill | 256 | 4.455 ms | 57,466 tok/s | 3.36 | 142.0 |
| prefill | 512 | 7.007 ms | 73,068 tok/s | 4.50 | 157.0 |
| prefill | 1024 | 11.533 ms | 88,790 tok/s | 6.03 | 194.5 |
| decode | batch 1 | 5.561 ms/step | 180 tok/s | — | 72.5 |
| decode | batch 8 | 5.976 ms/step | 1,339 tok/s | — | 90.5 |
| decode | batch 32 | 5.933 ms/step | 5,394 tok/s | — | 125.0 |

Decode is memory-bandwidth bound (weights are only 55.3 MB fp16); prefill is the
compute-bound stage and is where the FLOPs show up.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Illegal seek for GPU arch : gfx900` | `HSA_OVERRIDE_GFX_VERSION=9.0.0` and/or missing Tensile path | `unset HSA_OVERRIDE_GFX_VERSION`, set `ROCBLAS_TENSILE_LIBPATH` |
| `torch.cuda.is_available()` is `False` | `/dev/kfd` permissions or driver not loaded | add user to `render`/`video`, reboot |
| Process stuck in `D` state, `gpu_busy_percent` I/O error | driver wedge from interrupt/SDMA overrides or a killed GPU job | reboot; do not remove `HSA_ENABLE_SDMA=0` / `HSA_ENABLE_INTERRUPT=0` |
| Matmul much slower than expected | `HIP_TRACE_API` set, or clocks throttled by the 190 W cap | check `freq1_input` and `power1_input` while running |
| bf16 about half the fp32 speed | gfx906 has no bf16 ALUs; rocBLAS emulates it | use fp16 |
| `hipErrorNoBinaryForGpu` in a custom extension | code not built for gfx906 | compile with `-march=gfx906` or `PYTORCH_ROCM_ARCH=gfx906` |

### Clock / power note

The `warn_if_not_boosting` message may appear when the average clock is below the
1700 MHz maximum. Under sustained math this is expected: the 190 W cap is the
binding constraint, and forcing `power_dpm_force_performance_level=high` does not
raise the sustained clock. DPM writes are only meaningful after a clean boot —
see below.

### Known platform caveat — SMU state and shutdown

On this host, a hard power cut or a process killed mid-GPU-work can leave the SMU
in a degraded state where DPM boosts are ignored (stuck at the 859 MHz base clock)
until the machine is fully powered off (PSU switch off + ~60 s power-button
drain). Avoid `kill -9`/`timeout` on GPU jobs, and check `gpu_busy_percent` and
`fuser /dev/kfd` before shutting down. This is a firmware/ACPI handoff issue, not
a configuration error in this project.

---

## Limitations

- **No `torch.compile` / Triton**: `triton-rocm` has no gfx906 backend, so the
  distributive compiler and fused attention kernels are unavailable.
- **No bf16 or TF32**: only fp32 and packed fp16 are fast. fp16 training needs
  loss scaling.
- **No FlashAttention**: attention is materialized, so long-context inference is
  slower than on modern parts.
- **Power-bound**: ~1390 MHz sustained against a 1700 MHz ceiling; expect real
  training MFU well below the peak TFLOPS numbers above.
