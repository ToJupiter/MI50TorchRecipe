# PyTorch on ROCm 7.0.2 with a Radeon Pro VII / MI50 (gfx906)

This document is the complete recipe for a flawless PyTorch + ROCm 7.0.2 install on
this machine, plus the benchmark, training and inference harness used to measure the
card. Everything here was validated on the exact hardware below.

## 1. Hardware and software inventory

| Item | Value |
| --- | --- |
| GPU 0 (compute target) | AMD Radeon Pro VII / Radeon Instinct MI50, Vega 20 |
| PCI ID | `1002:66a1` at `0000:03:00.0` |
| ISA | `gfx906:sramecc+:xnack-` |
| Compute units | 60 |
| VRAM | 16 GB HBM2 (`17163091968` bytes) |
| Power cap | 190 W (`power1_cap` in sysfs) |
| Peak fp32 | 13.06 TFLOPS at 1700 MHz (60 CU × 64 lanes × 2 FLOP) |
| Peak fp16 | 26.11 TFLOPS at 1700 MHz (packed math) |
| bf16 / TF32 / matrix cores | none (bf16 is emulated, avoid it) |
| GPU 1 | Ryzen APU iGPU, `gfx90c` (`1002:1638`), shared GTT, not used for compute |
| ROCm | 7.0.2 at `/opt/rocm` |
| PyTorch | 2.10.0+rocm7.0, HIP 7.0.51831 |

The `sramecc+` marker means HBM ECC is enabled. It costs a few percent of bandwidth
and is why the card reports 16 GB instead of 32 GB.

## 2. Prerequisites

```bash
ls -l /dev/kfd /dev/dri
groups | grep -E 'render|video'
rocminfo | grep -m1 -E 'gfx[0-9]+'
rocm-smi
cat /sys/class/drm/card1/device/hwmon/hwmon1/power1_cap   # 190000000 (= 190 W)
```

Your user must be in the `render` and `video` groups. If `/dev/kfd` is missing, the
`amdgpu` kernel driver is not loaded or the GPU is wedged; reboot.

## 3. Install recipe (uv + pinned ROCm wheel)

`pyproject.toml` pins the ROCm wheel index explicitly:

```toml
[project]
name = "project"
version = "0.1.0"
requires-python = ">=3.11,<3.12"
dependencies = [
  "torch==2.10.0+rocm7.0",
  "torchvision",
  "triton-rocm==3.6.0; sys_platform == 'linux'",
]

[[tool.uv.index]]
name = "pytorch-rocm"
url = "https://download.pytorch.org/whl/rocm7.0"
explicit = true

[tool.uv.sources]
torch = { index = "pytorch-rocm" }
torchvision = { index = "pytorch-rocm" }
triton-rocm = { index = "pytorch-rocm" }
```

Then:

```bash
uv venv --python 3.11 .venv
uv sync
```

or, without `uv sync`:

```bash
uv pip install --python .venv/bin/python \
  --index-url https://download.pytorch.org/whl/rocm7.0 \
  "torch==2.10.0+rocm7.0" torchvision "triton-rocm==3.6.0"
```

Verify:

```bash
.venv/bin/python -c "import torch; print(torch.__version__, torch.version.hip, torch.cuda.is_available())"
```

## 4. The gfx906 rocBLAS fix (required)

The `torch==2.10.0+rocm7.0` wheel bundles rocBLAS Tensile kernels only for
`gfx908, gfx90a, gfx942, gfx950, gfx1030, gfx110x, gfx115x, gfx120x`. There is no
`gfx906` library inside the wheel, so the first matmul fails with:

```
rocBLAS error: Cannot read .../torch/lib/rocblas/library/TensileLibrary.dat: Illegal seek for GPU arch : gfx900
```

The system ROCm install at `/opt/rocm/lib/rocblas/library` **does** contain the gfx906
Tensile kernels (`TensileLibrary_lazy_gfx906.dat`, `Kernels.so-000-gfx906-xnack-.hsaco`).
Point the wheel's rocBLAS at them:

```bash
export ROCBLAS_TENSILE_LIBPATH=/opt/rocm/lib/rocblas/library
```

`ailab/bootstrap.py` applies this automatically for every entry point.

## 5. Environment variable hygiene (important)

The login shell on this machine exports variables that are actively harmful:

| Variable | Shell value | Action | Why |
| --- | --- | --- | --- |
| `HSA_OVERRIDE_GFX_VERSION` | `9.0.0` | **unset** | Forces the GPU to report gfx900; ROCm 7.0.2 supports gfx906 natively and the gfx900 Tensile library does not exist |
| `HIP_TRACE_API` | `0x2` | **unset** | Traces every HIP API call, crippling throughput (matmul 5.9 -> much higher, copy bandwidth roughly halved) |
| `HSA_ENABLE_SDMA` | `0` | **keep** | On this host, removing it wedged the `amdgpu` driver (stuck `flush_workqueue`, `gpu_busy_percent` I/O errors) and required a reboot |
| `HSA_ENABLE_INTERRUPT` | `0` | **keep** | Same as above: this platform hangs in interrupt mode |

`bootstrap.configure()` unsets the first two on every import of the package, before
`torch` is imported. Never remove the `HSA_ENABLE_*` overrides on this box.

## 6. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Illegal seek for GPU arch : gfx900` | `HSA_OVERRIDE_GFX_VERSION=9.0.0` and/or missing Tensile path | `unset HSA_OVERRIDE_GFX_VERSION`, set `ROCBLAS_TENSILE_LIBPATH` |
| `torch.cuda.is_available()` is `False` | `/dev/kfd` permissions or driver not loaded | add user to `render`/`video`, reboot |
| Process stuck in `D` state, `gpu_busy_percent` I/O error | driver wedge, often from interrupt/SDMA overrides | reboot; do not remove `HSA_ENABLE_SDMA=0` / `HSA_ENABLE_INTERRUPT=0` |
| Matmul much slower than expected | `HIP_TRACE_API` set, or clocks throttled by the 190 W cap | check `freq1_input` and `power1_input` while running |
| bf16 about half the fp32 speed | gfx906 has no bf16 ALUs, rocBLAS emulates it | use fp16 |
| `hipErrorNoBinaryForGpu` in a custom extension | code not built for gfx906 | compile with `-march=gfx906` or `PYTORCH_ROCM_ARCH=gfx906` |

## 7. The harness

```
main.py                 CLI: devices | bench | watch | train | infer | all
ailab/bootstrap.py      env sanitizing + Tensile path, runs before torch import
ailab/telemetry.py      sysfs telemetry (power, clocks, busy, VRAM, temp) and sampler
ailab/device.py         device discovery and PCI/sysfs/torch index mapping
ailab/benchmark.py      matmul sweep, conv2d, H2D/D2H/D2D bandwidth, sustained test
ailab/train.py          MLP training on a synthetic RAM or VRAM dataset
ailab/inference.py      llama-style decoder, prefill + KV-cache decode benchmark
ailab/watch.py          live TFLOPS / power / clock monitor
ailab/report.py         report tables
ailab/config.py         dataclass configs and model presets
ailab/data.py           synthetic dataset builders
ailab/model.py          model definitions
ailab/logging_setup.py  logging configuration
```

Zero-contention telemetry comes straight from sysfs, so measurements are not polluted
by `rocm-smi` calls:

```
/sys/class/drm/card1/device/hwmon/hwmon1/power1_input     # uW
/sys/class/drm/card1/device/hwmon/hwmon1/freq1_input      # sclk Hz
/sys/class/drm/card1/device/hwmon/hwmon1/freq2_input      # mclk Hz
/sys/class/drm/card1/device/gpu_busy_percent
/sys/class/drm/card1/device/mem_info_vram_used
```

## 8. Usage

```bash
.venv/bin/python main.py devices
.venv/bin/python main.py bench
.venv/bin/python main.py bench --sizes 4096,8192,16384 --dtypes fp32,fp16 --sustained-seconds 15
.venv/bin/python main.py watch --size 8192 --dtype fp16 --seconds 30
.venv/bin/python main.py train --preset tiny --data-source ram --ram-gb 16 --steps 300
.venv/bin/python main.py train --preset medium --data-source vram --steps 600
.venv/bin/python main.py infer --dtype fp16
.venv/bin/python main.py infer --prefill 128,512,1024 --batch 1,8,32
.venv/bin/python main.py all
.venv/bin/python main.py --device 0 --debug bench --sizes 4096 --iters 3
```

Stop a run with Ctrl-C, never `kill -9`/`timeout`: killing a process mid-GPU-work
can wedge `amdgpu` and force a reset. `watch --seconds N` and every other command
return cleanly on their own.

## 9. Measured results

Fresh run on this machine after a clean boot, DPM `auto`, 190 W cap.
Re-measure with `.venv/bin/python main.py all`.

### Compute

| Operation | dtype | ms | TFLOPS | % peak | W avg | TFLOPS/W | busy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| matmul 4096x4096 | fp32 | 14.455 | 9.51 | 72.8% | 184.3 | 0.052 | 97% |
| matmul 4096x4096 | fp16 | 13.841 | 9.93 | 38.0% | 193.0 | 0.051 | 100% |
| matmul 8192x8192 | fp32 | 120.661 | 9.11 | 69.8% | 189.7 | 0.048 | 99% |
| matmul 8192x8192 | fp16 | 67.159 | 16.37 | 62.7% | 186.5 | 0.088 | 99% |
| matmul 16384x16384 | fp32 | 911.015 | 9.66 | 74.0% | 189.1 | 0.051 | 100% |
| matmul 16384x16384 | fp16 | 512.440 | 17.17 | 65.7% | 189.2 | 0.091 | 100% |
| conv2d 32x64x112 | fp32 | 4.118 | 14.37 | 110.1% | 284.0 | 0.051 | 84% |
| conv2d 32x64x112 | fp16 | 2.812 | 21.05 | 80.6% | 210.0 | 0.100 | 99% |

The 4096² fp16 case is launch/overhead bound; 8192²/16384² show the real packed
ratio (~1.8x fp32). conv2d passes 100% because Winograd cuts the true operation
count below the nominal convolution FLOPs used for the percentage.

### Memory bandwidth

| Path | GB/s |
| --- | --- |
| D2D copy | 791.7 |
| H2D copy | 13.4 |
| D2H copy | 14.2 |

PCIe copies are limited by the x16 Gen3 link (~16 GB/s theoretical) and by
`HSA_ENABLE_SDMA=0`, which routes copies through compute shaders. HBM ECC shaves a
few percent off D2D.

### Sustained load (fp32 matmul 4096x4096, 10 s)

| Field | Value |
| --- | --- |
| TFLOPS | 9.37 (71.8% of the 13.06 fp32 peak) |
| clock | 1316 MHz avg |
| power | 188.8 W avg / 216 W peak (99.4% of the 190 W cap) |
| temperature | 67 C peak |
| energy | 1879.5 J |
| gpu busy | 99.4% |

Sustained fp16 over 8 s holds **16.34 TFLOPS at ~1390 MHz and 180-192 W**. The
card is power-limited, not thermally limited: the cap is what keeps the clock near
1300-1400 MHz instead of the 1700 MHz ceiling.

### Training (MLP, medium preset, VRAM-resident dataset)

| Preset | Parameters | Data source | Samples/s | TFLOPS | Power |
| --- | --- | --- | --- | --- | --- |
| medium | 3,148,801 | 4 GB VRAM | 240,737 | 4.545 | 109.8 W avg / 227 W peak |

59 steps over a 4.00 GB / 488,281-sample dataset in 2.008 s; loss 0.436 -> 0.074;
1212 MHz avg; 61 C peak. The tiny preset is data-pipeline bound by design; the
medium preset with VRAM-resident data is the one that shows real training
throughput.

### Inference (llama-style decoder, 27.6 M parameters, fp16)

| Stage | Length / batch | Latency | Throughput | TFLOPS | W |
| --- | --- | --- | --- | --- | --- |
| prefill | 128 | 3.770 ms | 33,950 tok/s | 1.93 | 112.0 |
| prefill | 256 | 4.455 ms | 57,466 tok/s | 3.36 | 142.0 |
| prefill | 512 | 7.007 ms | 73,068 tok/s | 4.50 | 157.0 |
| prefill | 1024 | 11.533 ms | 88,790 tok/s | 6.03 | 194.5 |
| decode | batch 1 | 5.561 ms/step | 180 tok/s | - | 72.5 |
| decode | batch 8 | 5.976 ms/step | 1,339 tok/s | - | 90.5 |
| decode | batch 32 | 5.933 ms/step | 5,394 tok/s | - | 125.0 |

Decode is memory-bandwidth bound (weights are only 55.3 MB fp16), which is why it
cannot approach the compute peak. Prefill is the compute-bound stage and is where
the card's FLOPs show up.
