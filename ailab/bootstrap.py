from __future__ import annotations

import os
from pathlib import Path

REMOVED_VARIABLES = ("HSA_OVERRIDE_GFX_VERSION", "HIP_TRACE_API")
TARGET_GFX_ARCH = "gfx906"
TENSILE_CANDIDATES = (
    "/opt/rocm/lib/rocblas/library",
    "/opt/rocm/rocblas/lib/library",
)


def _find_system_tensile_library() -> str | None:
    existing = os.environ.get("ROCBLAS_TENSILE_LIBPATH")
    if existing and Path(existing, f"TensileLibrary_lazy_{TARGET_GFX_ARCH}.dat").exists():
        return existing
    for candidate in TENSILE_CANDIDATES:
        if Path(candidate, f"TensileLibrary_lazy_{TARGET_GFX_ARCH}.dat").exists():
            return candidate
    return None


_applied: list[str] = []


def configure() -> list[str]:
    global _applied
    if _applied:
        return list(_applied)
    for name in REMOVED_VARIABLES:
        value = os.environ.pop(name, None)
        if value is not None:
            _applied.append(f"unset {name}={value}")
    tensile_library = _find_system_tensile_library()
    if tensile_library and os.environ.get("ROCBLAS_TENSILE_LIBPATH") != tensile_library:
        os.environ["ROCBLAS_TENSILE_LIBPATH"] = tensile_library
        _applied.append(f"set ROCBLAS_TENSILE_LIBPATH={tensile_library}")
    return list(_applied)
