from __future__ import annotations

from typing import Sequence

from .benchmark import BenchReport, OpResult
from .device import DeviceReport
from .inference import InferReport, InferResult
from .train import TrainStats

WIDTH = 88


def _rule(character: str = "=") -> None:
    print(character * WIDTH)


def _title(text: str) -> None:
    _rule()
    print(text)
    _rule()


def _key_value(label: str, value: str, label_width: int = 20) -> None:
    print(f"  {label:<{label_width}} {value}".rstrip())


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    widths = [len(str(header)) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    header_line = "  ".join(
        f"{str(header):>{widths[index]}}" for index, header in enumerate(headers)
    )
    print(f"  {header_line}")
    for row in rows:
        print("  " + "  ".join(f"{str(cell):>{widths[index]}}" for index, cell in enumerate(row)))


def _watts(value: float) -> str:
    return "n/a" if value <= 0 else f"{value:.1f}"


def _pci_address(report: DeviceReport) -> str:
    return f"{report.pci_domain:04x}:{report.pci_bus:02x}:{report.pci_device:02x}.0"


def print_device(report: DeviceReport) -> None:
    _title("device")
    _key_value("device index", str(report.index))
    _key_value("torch", report.torch_version)
    _key_value("backend", f"hip {report.hip_version}")
    _key_value("name", report.name)
    _key_value("arch", report.arch)
    _key_value("pci", _pci_address(report))
    _key_value("sysfs card", report.card or "unknown")
    _key_value("memory", f"{report.memory_gb:.2f} GB")
    _key_value("compute units", str(report.compute_units))
    _key_value("warp size", str(report.warp_size))
    _key_value("l2 cache", f"{report.l2_cache_mb:.1f} MB")
    _key_value("clock max", f"{report.max_clock_mhz:.0f} MHz")
    _key_value("dpm level", report.performance_level)


def print_devices(reports: list[DeviceReport]) -> None:
    _title("visible devices")
    _table(
        ("index", "name", "arch", "pci", "sysfs card", "vram GB", "cu"),
        [
            (
                report.index,
                report.name,
                report.arch,
                _pci_address(report),
                report.card or "unknown",
                f"{report.memory_gb:.2f}",
                report.compute_units,
            )
            for report in reports
        ],
    )
    print()
    _key_value("note", "torch index 0 is not guaranteed to be the discrete GPU")
    _key_value("", "use --device N to pin a device, or rely on the gfx906 auto-selection")


def benchmark_rows(results: list[OpResult]) -> list[tuple[object, ...]]:
    return [
        (
            result.name,
            result.dtype,
            f"{result.time_ms:.3f}",
            f"{result.tflops:.2f}",
            f"{result.efficiency_pct:.1f}",
            _watts(result.power_avg_w),
            _watts(result.power_peak_w),
            f"{result.tflops / result.power_avg_w:.4f}" if result.power_avg_w else "n/a",
            f"{result.busy_avg_pct:.0f}",
        )
        for result in results
    ]


def print_bench(report: BenchReport) -> None:
    print()
    print_device(report.device)
    print()
    _title("benchmark: compute")
    _table(
        ("operation", "dtype", "ms", "TFLOPS", "%peak", "W avg", "W peak", "TFLOPS/W", "busy %"),
        benchmark_rows([result for result in report.results if result.tflops > 0]),
    )
    bandwidth = [result for result in report.results if result.bandwidth_gbs > 0]
    if bandwidth:
        print()
        _title("benchmark: memory bandwidth")
        _table(
            ("operation", "dtype", "ms", "GB/s", "W avg"),
            [
                (result.name, result.dtype, f"{result.time_ms:.3f}", f"{result.bandwidth_gbs:.1f}", _watts(result.power_avg_w))
                for result in bandwidth
            ],
        )
    if report.sustained is not None:
        sustained = report.sustained
        print()
        _title("benchmark: sustained fp32 matmul")
        _key_value("operation", sustained.name)
        _key_value("TFLOPS", f"{sustained.tflops:.2f} ({sustained.efficiency_pct:.1f}% of {sustained.peak_tflops:.2f} peak)")
        _key_value("power", f"{_watts(sustained.power_avg_w)} W avg / {_watts(sustained.power_peak_w)} W peak")
        if report.power_cap_w:
            _key_value("power cap", f"{report.power_cap_w:.0f} W ({100.0 * sustained.power_avg_w / report.power_cap_w:.1f}% of cap)")
        _key_value("clock", f"{sustained.sclk_avg_mhz:.0f} MHz avg")
        _key_value("temperature", f"{sustained.temp_c:.1f} C peak")
        _key_value("energy", f"{sustained.energy_j:.1f} J")
        _key_value("gpu busy", f"{sustained.busy_avg_pct:.1f}% avg")
        _key_value("vram used", f"{sustained.vram_peak_gb:.2f} GB peak")


def print_train(stats: TrainStats) -> None:
    print()
    _title("training")
    _key_value("preset", stats.preset)
    _key_value("parameters", f"{stats.parameters:,}")
    _key_value("dataset", f"{stats.dataset.source}, {stats.dataset.samples:,} samples, {stats.dataset.gb:.2f} GB")
    _key_value("batch size", f"{stats.batch_size:,}")
    _key_value("steps", f"{stats.steps:,}")
    _key_value("samples", f"{stats.samples:,}")
    _key_value("time", f"{stats.seconds:.3f} s")
    _key_value("throughput", f"{stats.samples_per_second:,.0f} samples/s ({stats.steps_per_second:,.2f} steps/s)")
    _key_value("train TFLOPS", f"{stats.tflops:.3f} ({stats.efficiency_pct:.1f}% of {stats.peak_tflops:.2f} peak)")
    _key_value("loss", f"{stats.loss_history[0]:.6f} -> {stats.final_loss:.6f}")
    telemetry = stats.telemetry
    _key_value("power", f"{_watts(telemetry.power_avg_w)} W avg / {_watts(telemetry.power_peak_w)} W peak")
    _key_value("clock", f"{telemetry.sclk_avg_mhz:.0f} MHz avg")
    _key_value("gpu busy", f"{telemetry.busy_avg_pct:.1f}% avg / {telemetry.busy_peak_pct:.0f}% peak")
    _key_value("temperature", f"{telemetry.temp_peak_c:.1f} C peak")
    _key_value("energy", f"{telemetry.energy_j:.1f} J")
    _key_value("vram", f"{stats.peak_torch_alloc_gb:.2f} GB torch alloc / {stats.peak_torch_reserved_gb:.2f} GB reserved")
    if stats.step_losses:
        print()
        _table(("step", "loss"), [(step, f"{loss:.6f}") for step, loss in stats.step_losses])


def print_infer(report: InferReport) -> None:
    print()
    print_device(report.device)
    print()
    _title("inference")
    _key_value("architecture", "llama-style decoder, rmsnorm + rope + swiglu")
    _key_value("parameters", f"{report.parameters:,}")
    _key_value("weights", f"{report.weight_mb:.1f} MB ({report.dtype})")
    if report.power_cap_w:
        _key_value("power cap", f"{report.power_cap_w:.0f} W")
    if report.prefill:
        print()
        _title("inference: prefill (batch 1)")
        _table(
            ("length", "ms", "tokens/s", "TFLOPS", "%peak", "W avg", "GB/s"),
            [
                (
                    result.length,
                    f"{result.time_ms:.3f}",
                    f"{result.tokens_per_second:,.0f}",
                    f"{result.tflops:.2f}",
                    f"{result.efficiency_pct:.1f}",
                    _watts(result.power_avg_w),
                    "n/a",
                )
                for result in report.prefill
            ],
        )
    if report.decode:
        print()
        _title("inference: decode (kv cache, greedy)")
        _table(
            ("batch", "ms/step", "tokens/s", "TFLOPS", "GB/s", "W avg", "busy %"),
            [
                (
                    result.batch,
                    f"{result.time_ms / result.length:.3f}",
                    f"{result.tokens_per_second:,.0f}",
                    f"{result.tflops:.3f}",
                    f"{result.bandwidth_gbs:.1f}",
                    _watts(result.power_avg_w),
                    f"{result.busy_avg_pct:.0f}",
                )
                for result in report.decode
            ],
        )
    if report.sample_tokens:
        print()
        _key_value("sample tokens", " ".join(str(token) for token in report.sample_tokens))
