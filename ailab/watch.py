from __future__ import annotations

import time
from dataclasses import dataclass

from . import bootstrap

bootstrap.configure()

import torch

from .device import (
    describe_device,
    locate_card,
    resolve_device_index,
    resolve_dtype,
    select_device,
)
from .telemetry import GpuTelemetry


@dataclass(slots=True)
class WatchConfig:
    size: int = 8192
    dtype: str = "fp32"
    interval: float = 1.0
    seconds: float = 0.0
    device_index: int | None = None


def run_watch(config: WatchConfig) -> None:
    index = resolve_device_index(config.device_index)
    device = select_device(index)
    report = describe_device(index)
    card = locate_card(index)
    telemetry = GpuTelemetry.for_card(card) if card else None
    dtype = resolve_dtype(config.dtype)
    size = config.size
    left = torch.randn(size, size, device=device, dtype=dtype)
    right = torch.randn(size, size, device=device, dtype=dtype)
    output = torch.empty(size, size, device=device, dtype=dtype)
    flops_per_matmul = 2.0 * size**3
    for _ in range(3):
        torch.matmul(left, right, out=output)
    torch.cuda.synchronize()
    print()
    print(
        f"watching {report.name} | {size}x{size} {config.dtype} | sysfs {card} | "
        f"max clock {report.max_clock_mhz:.0f} MHz | dpm {report.performance_level}"
    )
    print(f"{'elapsed':>8} {'TFLOPS':>8} {'avg':>8} {'clock':>7} {'power':>7} {'temp':>5} {'busy':>5}")
    total_ops = 0
    total_started = time.perf_counter()
    try:
        while True:
            ops = 0
            started = time.perf_counter()
            while time.perf_counter() - started < config.interval:
                for _ in range(2):
                    torch.matmul(left, right, out=output)
                torch.cuda.synchronize()
                ops += 2
            elapsed = time.perf_counter() - started
            total_ops += ops
            total_elapsed = time.perf_counter() - total_started
            sample = telemetry.read() if telemetry is not None else None
            instant = ops * flops_per_matmul / elapsed / 1e12
            average = total_ops * flops_per_matmul / total_elapsed / 1e12
            clock = sample.sclk_mhz if sample and sample.sclk_mhz else 0.0
            power = sample.power_w if sample and sample.power_w else 0.0
            temp = sample.temp_c if sample and sample.temp_c else 0.0
            busy = sample.busy_pct if sample and sample.busy_pct else 0.0
            print(
                f"{total_elapsed:7.1f}s {instant:8.2f} {average:8.2f} {clock:6.0f}M {power:6.1f}W {temp:4.0f}C {busy:4.0f}%",
                flush=True,
            )
            if config.seconds and total_elapsed >= config.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        torch.cuda.synchronize()
    total_elapsed = time.perf_counter() - total_started
    average = total_ops * flops_per_matmul / max(total_elapsed, 1e-9) / 1e12
    print(f"average over {total_elapsed:.1f}s: {average:.2f} TFLOPS ({total_ops} matmuls)")
    del left, right, output
    torch.cuda.empty_cache()
