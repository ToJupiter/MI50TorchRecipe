from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from . import bootstrap

bootstrap.configure()

import torch
import torch.nn as nn

from .config import BenchConfig
from .device import (
    DeviceReport,
    describe_device,
    locate_card,
    resolve_device_index,
    resolve_dtype,
    select_device,
    theoretical_peak_tflops,
)
from .telemetry import GpuMonitor, GpuTelemetry, TelemetrySummary, warn_if_not_boosting

log = logging.getLogger(__name__)


@dataclass(slots=True)
class OpResult:
    name: str
    dtype: str
    time_ms: float
    tflops: float = 0.0
    peak_tflops: float = 0.0
    efficiency_pct: float = 0.0
    bandwidth_gbs: float = 0.0
    power_avg_w: float = 0.0
    power_peak_w: float = 0.0
    energy_j: float = 0.0
    busy_avg_pct: float = 0.0
    sclk_avg_mhz: float = 0.0
    temp_c: float = 0.0
    vram_peak_gb: float = 0.0


@dataclass
class BenchReport:
    device: DeviceReport
    results: list[OpResult] = field(default_factory=list)
    sustained: OpResult | None = None
    power_cap_w: float = 0.0


def _synchronize() -> None:
    torch.cuda.synchronize()


def _run_timed(telemetry: GpuTelemetry | None, warmup: int, iters: int, operation) -> tuple[float, TelemetrySummary]:
    for _ in range(warmup):
        operation()
    _synchronize()
    monitor = GpuMonitor(telemetry)
    monitor.start()
    started = time.perf_counter()
    for _ in range(iters):
        operation()
    _synchronize()
    elapsed = time.perf_counter() - started
    monitor.stop()
    return elapsed / iters, monitor.summary()


def _peak_for(dtype_name: str, device_report: DeviceReport) -> float:
    return theoretical_peak_tflops(dtype_name, device_report.max_clock_mhz, device_report.compute_units)


def _run_batched(
    telemetry: GpuTelemetry | None,
    warmup: int,
    iterations: int,
    seconds: float,
    operation,
    batch: int = 4,
) -> tuple[float, TelemetrySummary, int]:
    for _ in range(warmup):
        operation()
    _synchronize()
    monitor = GpuMonitor(telemetry)
    monitor.start()
    started = time.perf_counter()
    count = 0
    while count < iterations or time.perf_counter() - started < seconds:
        for _ in range(batch):
            operation()
        _synchronize()
        count += batch
    elapsed = time.perf_counter() - started
    monitor.stop()
    return elapsed / count, monitor.summary(), count


def benchmark_matmul(
    config: BenchConfig, index: int, telemetry: GpuTelemetry | None, device_report: DeviceReport
) -> list[OpResult]:
    device = select_device(index)
    results: list[OpResult] = []
    for size in config.sizes:
        for dtype_name in config.dtypes:
            dtype = resolve_dtype(dtype_name)
            log.info("matmul %dx%d %s: allocating operands", size, size, dtype_name)
            try:
                left = torch.randn(size, size, device=device, dtype=dtype)
                right = torch.randn(size, size, device=device, dtype=dtype)
                output = torch.empty(size, size, device=device, dtype=dtype)
            except torch.cuda.OutOfMemoryError:
                log.warning("matmul %dx%d %s skipped: out of memory", size, size, dtype_name)
                torch.cuda.empty_cache()
                continue

            def operation() -> None:
                torch.matmul(left, right, out=output)

            seconds, summary = _run_timed(telemetry, config.warmup, config.iters, operation)
            tflops = 2.0 * size**3 / seconds / 1e12
            peak = _peak_for(dtype_name, device_report)
            result = OpResult(
                name=f"matmul {size}x{size}",
                dtype=dtype_name,
                time_ms=seconds * 1e3,
                tflops=tflops,
                peak_tflops=peak,
                efficiency_pct=100.0 * tflops / peak if peak else 0.0,
                power_avg_w=summary.power_avg_w,
                power_peak_w=summary.power_peak_w,
                energy_j=summary.energy_j,
                busy_avg_pct=summary.busy_avg_pct,
                sclk_avg_mhz=summary.sclk_avg_mhz,
                temp_c=summary.temp_peak_c,
                vram_peak_gb=summary.vram_peak_gb,
            )
            log.info(
                "matmul %dx%d %-4s %9.3f ms | %7.2f TFLOPS | %5.1f%% of %.2f peak | %6.1f W avg | %5.1f%% busy",
                size,
                size,
                dtype_name,
                result.time_ms,
                result.tflops,
                result.efficiency_pct,
                result.peak_tflops,
                result.power_avg_w,
                result.busy_avg_pct,
            )
            results.append(result)
            del left, right, output
            torch.cuda.empty_cache()
    return results


def benchmark_conv(
    config: BenchConfig, index: int, telemetry: GpuTelemetry | None, device_report: DeviceReport
) -> list[OpResult]:
    device = select_device(index)
    results: list[OpResult] = []
    for dtype_name in config.dtypes:
        dtype = resolve_dtype(dtype_name)
        convolution = nn.Conv2d(
            config.conv_channels, config.conv_out_channels, kernel_size=3, padding=1
        ).to(device=device, dtype=dtype)
        features = torch.randn(
            config.conv_batch,
            config.conv_channels,
            config.conv_size,
            config.conv_size,
            device=device,
            dtype=dtype,
        )
        out_h = out_w = config.conv_size
        flops = (
            2.0
            * config.conv_batch
            * config.conv_out_channels
            * out_h
            * out_w
            * config.conv_channels
            * 9
        )

        def operation() -> None:
            convolution(features)

        seconds, summary = _run_timed(telemetry, config.warmup, config.iters, operation)
        tflops = flops / seconds / 1e12
        peak = _peak_for(dtype_name, device_report)
        result = OpResult(
            name=f"conv2d {config.conv_batch}x{config.conv_channels}x{config.conv_size}",
            dtype=dtype_name,
            time_ms=seconds * 1e3,
            tflops=tflops,
            peak_tflops=peak,
            efficiency_pct=100.0 * tflops / peak if peak else 0.0,
            power_avg_w=summary.power_avg_w,
            power_peak_w=summary.power_peak_w,
            energy_j=summary.energy_j,
            busy_avg_pct=summary.busy_avg_pct,
            sclk_avg_mhz=summary.sclk_avg_mhz,
            temp_c=summary.temp_peak_c,
            vram_peak_gb=summary.vram_peak_gb,
        )
        log.info(
            "conv2d %dx%dx%d %-4s %9.3f ms | %7.2f TFLOPS | %5.1f%% of %.2f peak",
            config.conv_batch,
            config.conv_channels,
            config.conv_size,
            dtype_name,
            result.time_ms,
            result.tflops,
            result.efficiency_pct,
            result.peak_tflops,
        )
        results.append(result)
        del convolution, features
        torch.cuda.empty_cache()
    return results


def benchmark_bandwidth(
    config: BenchConfig, index: int, telemetry: GpuTelemetry | None
) -> list[OpResult]:
    if "fp32" not in config.dtypes:
        return []
    device = select_device(index)
    numel = max(1, int(config.bandwidth_gb * 1e9 / 4))
    results: list[OpResult] = []
    device_source = torch.randn(numel, device=device)
    device_sink = torch.empty_like(device_source)
    pinned = torch.randn(numel, pin_memory=True)
    host_sink = torch.empty(numel, pin_memory=True)

    transfers = (
        ("copy d2d", lambda: device_sink.copy_(device_source), 2 * numel * 4),
        ("copy h2d", lambda: device_sink.copy_(pinned, non_blocking=True), numel * 4),
        ("copy d2h", lambda: host_sink.copy_(device_source, non_blocking=True), numel * 4),
    )
    for name, operation, moved_bytes in transfers:
        seconds, summary, _ = _run_batched(
            telemetry, config.warmup, config.bandwidth_iters, config.bandwidth_seconds, operation
        )
        gbs = moved_bytes / seconds / 1e9
        result = OpResult(
            name=name,
            dtype="fp32",
            time_ms=seconds * 1e3,
            bandwidth_gbs=gbs,
            power_avg_w=summary.power_avg_w,
            power_peak_w=summary.power_peak_w,
            energy_j=summary.energy_j,
            busy_avg_pct=summary.busy_avg_pct,
            sclk_avg_mhz=summary.sclk_avg_mhz,
            vram_peak_gb=summary.vram_peak_gb,
        )
        log.info("%-9s %.2f GB moved in %8.3f ms | %8.1f GB/s", name, moved_bytes / 1e9, result.time_ms, gbs)
        results.append(result)
    del device_source, device_sink, pinned, host_sink
    torch.cuda.empty_cache()
    return results


def benchmark_sustained(
    config: BenchConfig, index: int, telemetry: GpuTelemetry | None, device_report: DeviceReport
) -> OpResult:
    device = select_device(index)
    size = config.sustained_size
    left = torch.randn(size, size, device=device)
    right = torch.randn(size, size, device=device)
    output = torch.empty(size, size, device=device)
    for _ in range(config.warmup):
        torch.matmul(left, right, out=output)
    _synchronize()
    log.info("sustained fp32 matmul %dx%d for %.1f s", size, size, config.sustained_seconds)
    monitor = GpuMonitor(telemetry)
    monitor.start()
    started = time.perf_counter()
    iterations = 0
    deadline = started + config.sustained_seconds
    while time.perf_counter() < deadline:
        for _ in range(4):
            torch.matmul(left, right, out=output)
        _synchronize()
        iterations += 4
    elapsed = time.perf_counter() - started
    monitor.stop()
    summary = monitor.summary()
    tflops = iterations * 2.0 * size**3 / elapsed / 1e12
    peak = _peak_for("fp32", device_report)
    result = OpResult(
        name=f"sustained fp32 {size}x{size}",
        dtype="fp32",
        time_ms=elapsed * 1e3 / max(1, iterations),
        tflops=tflops,
        peak_tflops=peak,
        efficiency_pct=100.0 * tflops / peak if peak else 0.0,
        power_avg_w=summary.power_avg_w,
        power_peak_w=summary.power_peak_w,
        energy_j=summary.energy_j,
        busy_avg_pct=summary.busy_avg_pct,
        sclk_avg_mhz=summary.sclk_avg_mhz,
        temp_c=summary.temp_peak_c,
        vram_peak_gb=summary.vram_peak_gb,
    )
    log.info(
        "sustained: %d iters in %.2f s | %.2f TFLOPS | %.1f W avg / %.1f W peak | %.0f MHz avg | %.1f C peak",
        iterations,
        elapsed,
        tflops,
        summary.power_avg_w,
        summary.power_peak_w,
        summary.sclk_avg_mhz,
        summary.temp_peak_c,
    )
    warn_if_not_boosting(summary, device_report.max_clock_mhz, device_report.card)
    del left, right, output
    torch.cuda.empty_cache()
    return result


def run_benchmark(config: BenchConfig) -> BenchReport:
    index = resolve_device_index(config.device_index)
    device_report = describe_device(index)
    card = locate_card(index)
    telemetry = GpuTelemetry.for_card(card) if card else GpuTelemetry.find(index)
    if telemetry is None:
        log.warning("no sysfs telemetry found, power and clock metrics will be unavailable")
    else:
        log.info(
            "telemetry source %s | power cap %.0f W | sclk %.0f MHz",
            telemetry.device_path,
            telemetry.power_cap_w or 0.0,
            device_report.max_clock_mhz,
        )
    log.info(
        "benchmark start: index=%d card=%s arch=%s sizes=%s dtypes=%s iters=%d warmup=%d",
        index,
        card or "unknown",
        device_report.arch,
        config.sizes,
        config.dtypes,
        config.iters,
        config.warmup,
    )
    report = BenchReport(device=device_report)
    if telemetry is not None and telemetry.power_cap_w:
        report.power_cap_w = telemetry.power_cap_w
    report.results.extend(benchmark_matmul(config, index, telemetry, device_report))
    report.results.extend(benchmark_conv(config, index, telemetry, device_report))
    report.results.extend(benchmark_bandwidth(config, index, telemetry))
    report.sustained = benchmark_sustained(config, index, telemetry, device_report)
    log.info("benchmark complete: %d measurements", len(report.results))
    return report
