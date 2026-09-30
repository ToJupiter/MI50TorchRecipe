from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from . import bootstrap

bootstrap.configure()

import torch

log = logging.getLogger(__name__)


@dataclass(slots=True)
class DeviceReport:
    index: int
    name: str
    arch: str
    card: str
    pci_domain: int
    pci_bus: int
    pci_device: int
    memory_gb: float
    compute_units: int
    warp_size: int
    l2_cache_mb: float
    max_clock_mhz: float
    performance_level: str
    torch_version: str
    hip_version: str
    total_memory_bytes: int


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _sysfs_cards() -> list[tuple[str, Path]]:
    cards: list[tuple[str, Path]] = []
    for device_path in sorted(Path("/sys/class/drm").glob("card[0-9]/device")):
        cards.append((device_path.parent.name, device_path))
    return cards


def locate_card(index: int) -> str | None:
    properties = torch.cuda.get_device_properties(index)
    domain = getattr(properties, "pci_domain_id", 0)
    bus = getattr(properties, "pci_bus_id", None)
    device = getattr(properties, "pci_device_id", None)
    if bus is None or device is None:
        return None
    suffix = f"{domain:04x}:{bus:02x}:{device:02x}.0"
    for card, device_path in _sysfs_cards():
        if os.path.realpath(device_path).endswith(suffix):
            return card
    return None


def resolve_device_index(preferred: int | None = None, preferred_arch: str = "gfx906") -> int:
    if preferred is not None:
        return preferred
    count = torch.cuda.device_count()
    if count == 0:
        raise RuntimeError("torch.cuda.is_available() is False, no ROCm device visible")
    for index in range(count):
        properties = torch.cuda.get_device_properties(index)
        if getattr(properties, "gcnArchName", "").startswith(preferred_arch):
            return index
    log.warning("no %s device found among %d devices, defaulting to index 0", preferred_arch, count)
    return 0


def select_device(index: int | None = None) -> torch.device:
    if not torch.cuda.is_available():
        raise RuntimeError("torch.cuda.is_available() is False, no ROCm device visible")
    resolved = resolve_device_index(index)
    count = torch.cuda.device_count()
    if not 0 <= resolved < count:
        raise RuntimeError(f"device index {resolved} out of range, {count} devices visible")
    properties = torch.cuda.get_device_properties(resolved)
    log.info(
        "selected device %d: %s | %s | %s | %.2f GB | sysfs %s",
        resolved,
        properties.name,
        getattr(properties, "gcnArchName", "?"),
        _pci_string(resolved),
        properties.total_memory / 1e9,
        locate_card(resolved) or "unknown",
    )
    return torch.device(f"cuda:{resolved}")


def _pci_string(index: int) -> str:
    properties = torch.cuda.get_device_properties(index)
    return (
        f"pci {getattr(properties, 'pci_domain_id', 0):04x}:"
        f"{getattr(properties, 'pci_bus_id', 0):02x}:{getattr(properties, 'pci_device_id', 0):02x}.0"
    )


def _read_clock_mhz(card: str) -> float:
    hwmon_root = Path(f"/sys/class/drm/{card}/device/hwmon")
    if hwmon_root.is_dir():
        for hwmon in sorted(hwmon_root.glob("hwmon*")):
            value = _read_int(hwmon / "freq1_input")
            if value:
                return value / 1e6
    return 0.0


def _read_max_clock_mhz(card: str) -> float:
    path = Path(f"/sys/class/drm/{card}/device/pp_dpm_sclk")
    best = 0.0
    try:
        for line in path.read_text().splitlines():
            if ":" not in line:
                continue
            token = line.split(":", 1)[1].strip().split()[0]
            if token.lower().endswith("mhz"):
                best = max(best, float(token[:-3]))
    except (OSError, ValueError):
        pass
    return best if best else _read_clock_mhz(card)


def performance_level(card: str) -> str:
    try:
        return Path(f"/sys/class/drm/{card}/device/power_dpm_force_performance_level").read_text().strip()
    except OSError:
        return "unknown"


def describe_device(index: int | None = None) -> DeviceReport:
    resolved = resolve_device_index(index)
    properties = torch.cuda.get_device_properties(resolved)
    card = locate_card(resolved) or ""
    return DeviceReport(
        index=resolved,
        name=properties.name,
        arch=getattr(properties, "gcnArchName", ""),
        card=card,
        pci_domain=getattr(properties, "pci_domain_id", 0),
        pci_bus=getattr(properties, "pci_bus_id", 0),
        pci_device=getattr(properties, "pci_device_id", 0),
        memory_gb=properties.total_memory / 1e9,
        compute_units=properties.multi_processor_count,
        warp_size=properties.warp_size,
        l2_cache_mb=getattr(properties, "L2_cache_size", 0) / 1e6,
        max_clock_mhz=_read_max_clock_mhz(card) if card else 0.0,
        performance_level=performance_level(card) if card else "unknown",
        torch_version=torch.__version__,
        hip_version=getattr(torch.version, "hip", "") or "",
        total_memory_bytes=properties.total_memory,
    )


def list_devices() -> list[DeviceReport]:
    reports: list[DeviceReport] = []
    for index in range(torch.cuda.device_count()):
        reports.append(describe_device(index))
    return reports


def configure_backend() -> None:
    torch.backends.cudnn.benchmark = True
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    try:
        torch.set_float32_matmul_precision("highest")
    except Exception:
        pass
    log.debug("backend configured: cudnn.benchmark=True tf32=off matmul_precision=highest")


def resolve_dtype(name: str) -> torch.dtype:
    mapping = {
        "fp32": torch.float32,
        "float32": torch.float32,
        "fp16": torch.float16,
        "float16": torch.float16,
        "half": torch.float16,
        "bf16": torch.bfloat16,
        "bfloat16": torch.bfloat16,
    }
    try:
        return mapping[name.lower()]
    except KeyError as error:
        raise ValueError(f"unsupported dtype {name!r}, expected one of {sorted(set(mapping))}") from error


def dtype_bytes(name: str) -> int:
    return torch.tensor([], dtype=resolve_dtype(name)).element_size()


def theoretical_peak_tflops(dtype: str, sclk_mhz: float, compute_units: int) -> float:
    if sclk_mhz <= 0 or compute_units <= 0:
        return 0.0
    flops_per_clock_per_cu = 128.0
    peak = sclk_mhz * 1e6 * compute_units * flops_per_clock_per_cu / 1e12
    resolved = resolve_dtype(dtype)
    if resolved in (torch.float16, torch.bfloat16):
        peak *= 2.0
    return peak
