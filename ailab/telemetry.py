from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(slots=True)
class GpuSample:
    timestamp: float
    power_w: float | None = None
    busy_pct: float | None = None
    sclk_mhz: float | None = None
    mclk_mhz: float | None = None
    temp_c: float | None = None
    vram_used_gb: float | None = None


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


@dataclass(slots=True)
class TelemetrySummary:
    duration_s: float = 0.0
    samples: int = 0
    power_avg_w: float = 0.0
    power_peak_w: float = 0.0
    energy_j: float = 0.0
    power_cap_w: float = 0.0
    busy_avg_pct: float = 0.0
    busy_peak_pct: float = 0.0
    sclk_avg_mhz: float = 0.0
    sclk_peak_mhz: float = 0.0
    mclk_avg_mhz: float = 0.0
    temp_peak_c: float = 0.0
    vram_peak_gb: float = 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


class GpuTelemetry:
    def __init__(self, card: str, device_path: Path):
        self.card = card
        self.device_path = device_path
        self.hwmon_path = self._locate_hwmon()
        self.power_cap_w = self._read_power_cap()

    def _locate_hwmon(self) -> Path | None:
        root = self.device_path / "hwmon"
        if not root.is_dir():
            return None
        return next(iter(sorted(root.glob("hwmon*"))), None)

    def _read_power_cap(self) -> float | None:
        if self.hwmon_path is None:
            return None
        micro_watts = _read_int(self.hwmon_path / "power1_cap")
        return micro_watts / 1e6 if micro_watts else None

    @classmethod
    def for_card(cls, card: str) -> "GpuTelemetry | None":
        device_path = Path(f"/sys/class/drm/{card}/device")
        if (device_path / "gpu_busy_percent").exists() or (device_path / "hwmon").is_dir():
            return cls(card, device_path)
        return None

    @classmethod
    def find(cls, device_index: int = 0) -> "GpuTelemetry | None":
        import torch

        try:
            target_bytes = torch.cuda.get_device_properties(device_index).total_memory
        except Exception:
            target_bytes = None
        fallback: tuple[str, Path] | None = None
        fallback_vram = -1
        for device_path in sorted(Path("/sys/class/drm").glob("card[0-9]/device")):
            vram = _read_int(device_path / "mem_info_vram_total") or 0
            if target_bytes is not None and vram == target_bytes:
                return cls(device_path.parent.name, device_path)
            if vram > fallback_vram and (device_path / "gpu_busy_percent").exists():
                fallback_vram = vram
                fallback = (device_path.parent.name, device_path)
        if fallback is not None:
            return cls(*fallback)
        return None

    def read(self) -> GpuSample:
        sample = GpuSample(timestamp=time.perf_counter())
        if self.hwmon_path is not None:
            micro_watts = _read_int(self.hwmon_path / "power1_average")
            if micro_watts is None:
                micro_watts = _read_int(self.hwmon_path / "power1_input")
            if micro_watts is not None:
                sample.power_w = micro_watts / 1e6
            sclk_hz = _read_int(self.hwmon_path / "freq1_input")
            if sclk_hz:
                sample.sclk_mhz = sclk_hz / 1e6
            mclk_hz = _read_int(self.hwmon_path / "freq2_input")
            if mclk_hz:
                sample.mclk_mhz = mclk_hz / 1e6
            milli_c = _read_int(self.hwmon_path / "temp1_input")
            if milli_c is not None:
                sample.temp_c = milli_c / 1000.0
        busy = _read_int(self.device_path / "gpu_busy_percent")
        if busy is not None:
            sample.busy_pct = float(busy)
        vram = _read_int(self.device_path / "mem_info_vram_used")
        if vram is not None:
            sample.vram_used_gb = vram / 1e9
        return sample

    @property
    def available(self) -> bool:
        return self.hwmon_path is not None or (self.device_path / "gpu_busy_percent").exists()


def warn_if_not_boosting(summary: TelemetrySummary, max_clock_mhz: float, card: str) -> None:
    if max_clock_mhz <= 0 or summary.sclk_avg_mhz <= 0:
        return
    ratio = summary.sclk_avg_mhz / max_clock_mhz
    if ratio < 0.8:
        log.warning(
            "GPU averaged %.0f MHz of %.0f MHz max (%.0f%%): DPM did not boost. "
            "Force it with: sudo sh -c 'echo high > /sys/class/drm/%s/device/power_dpm_force_performance_level'",
            summary.sclk_avg_mhz,
            max_clock_mhz,
            100.0 * ratio,
            card or "card1",
        )


def _integrate_energy(samples: list[GpuSample]) -> float:
    energy = 0.0
    previous: GpuSample | None = None
    for sample in samples:
        if sample.power_w is None:
            continue
        if previous is not None:
            dt = sample.timestamp - previous.timestamp
            if dt > 0 and previous.power_w is not None:
                energy += 0.5 * (sample.power_w + previous.power_w) * dt
        previous = sample
    return energy


class GpuMonitor:
    def __init__(self, telemetry: GpuTelemetry | None, interval: float = 0.1):
        self.telemetry = telemetry
        self.interval = interval
        self.samples: list[GpuSample] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0
        self._stopped = 0.0

    def start(self) -> "GpuMonitor":
        self.samples.clear()
        self._started = time.perf_counter()
        self._stopped = 0.0
        if self.telemetry is not None and self.telemetry.available:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="gpu-monitor", daemon=True)
            self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            self.samples.append(self.telemetry.read())
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._stopped = time.perf_counter()

    def __enter__(self) -> "GpuMonitor":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def summary(self) -> TelemetrySummary:
        elapsed = (self._stopped or time.perf_counter()) - self._started if self._started else 0.0
        summary = TelemetrySummary(duration_s=elapsed, samples=len(self.samples))
        if self.telemetry is not None and self.telemetry.power_cap_w:
            summary.power_cap_w = self.telemetry.power_cap_w
        powers = [s.power_w for s in self.samples if s.power_w is not None]
        if powers:
            summary.power_avg_w = _mean(powers)
            summary.power_peak_w = max(powers)
            summary.energy_j = _integrate_energy(self.samples)
        busy = [s.busy_pct for s in self.samples if s.busy_pct is not None]
        if busy:
            summary.busy_avg_pct = _mean(busy)
            summary.busy_peak_pct = max(busy)
        sclk = [s.sclk_mhz for s in self.samples if s.sclk_mhz is not None]
        if sclk:
            summary.sclk_avg_mhz = _mean(sclk)
            summary.sclk_peak_mhz = max(sclk)
        mclk = [s.mclk_mhz for s in self.samples if s.mclk_mhz is not None]
        if mclk:
            summary.mclk_avg_mhz = _mean(mclk)
        temps = [s.temp_c for s in self.samples if s.temp_c is not None]
        if temps:
            summary.temp_peak_c = max(temps)
        vram = [s.vram_used_gb for s in self.samples if s.vram_used_gb is not None]
        if vram:
            summary.vram_peak_gb = max(vram)
        return summary
