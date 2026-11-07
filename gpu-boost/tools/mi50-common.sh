#!/bin/bash
# mi50-common.sh — shared helpers for the MI50 / Vega20 boost-recovery tools.
#
# SOURCE this file; do not execute it directly.
#
# Design goal: ZERO host-specific values. The GPU is located by PCI ID
# (1002:66a1), hwmon by glob, the PCI BDF by readlink, the running kernel by
# uname, and the DKMS module version by scanning dkms status / /usr/src.
# Nothing here contains a username, hostname, home directory, serial, UUID or
# IP address. Override anything with the environment variables below.
#
# Env overrides:
#   MI50_CARD       cardN             (default: auto-detect by PCI ID)
#   MI50_KERNEL     kernel release    (default: uname -r)
#   MI50_DKMS_VER   dkms module/ver   (default: auto-detect)
#   MI50_REPO       repo root         (default: grandparent of tools/)
#   MI50_VENDOR/MI50_DEVICE           (default: 0x1002 / 0x66a1)

MI50_VENDOR=${MI50_VENDOR:-0x1002}
MI50_DEVICE=${MI50_DEVICE:-0x66a1}
MI50_TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MI50_GB="$(cd "$MI50_TOOLS/.." && pwd)"                 # .../gpu-boost
MI50_REPO="${MI50_REPO:-$(cd "$MI50_TOOLS/../.." && pwd)}"  # repo root
MI50_KERNEL="${MI50_KERNEL:-$(uname -r)}"

# Locate the MI50 and export the derived paths. Returns 1 if not found.
mi50_detect() {
    local d v dev p
    if [ -n "${MI50_CARD:-}" ]; then
        MI50_CARD_DEV="/sys/class/drm/$MI50_CARD/device"
    else
        MI50_CARD_DEV=""
        for d in /sys/class/drm/card[0-9]*/device; do
            [ -r "$d/vendor" ] && [ -r "$d/device" ] || continue
            v=$(cat "$d/vendor" 2>/dev/null || true)
            dev=$(cat "$d/device" 2>/dev/null || true)
            if [ "$v" = "$MI50_VENDOR" ] && [ "$dev" = "$MI50_DEVICE" ]; then
                MI50_CARD_DEV="$d"
                break
            fi
        done
    fi

    if [ -z "$MI50_CARD_DEV" ] || [ ! -r "$MI50_CARD_DEV/pp_dpm_sclk" ]; then
        return 1
    fi

    MI50_CARD_NAME=$(basename "$(dirname "$MI50_CARD_DEV")")
    # /sys/class/drm/cardN/device -> /sys/bus/pci/devices/0000:BB:DD.F
    MI50_BDF=$(basename "$(readlink -f "$MI50_CARD_DEV")")
    MI50_DEBUGFS="/sys/kernel/debug/dri/$MI50_BDF"

    # Pick the hwmonN sub-directory that actually exposes the GPU sensors.
    # (The .../hwmon parent itself is NOT a sensor directory.)
    MI50_HWMON=""
    for h in "$MI50_CARD_DEV"/hwmon/hwmon[0-9]*; do
        [ -d "$h" ] || continue
        if [ -r "$h/power1_input" ] || [ -r "$h/temp2_input" ]; then
            MI50_HWMON="$h"
            break
        fi
    done

    MI50_KO="/lib/modules/$MI50_KERNEL/updates/dkms/amdgpu.ko.zst"
    MI50_INITRD="/boot/initrd.img-$MI50_KERNEL"
    return 0
}

# Best-effort DKMS module/version string, e.g. amdgpu/6.14.14-XXXXXX.
mi50_dkms_ver() {
    if [ -n "${MI50_DKMS_VER:-}" ]; then printf '%s\n' "$MI50_DKMS_VER"; return 0; fi
    local v d
    v=$(dkms status 2>/dev/null | awk -F'[, ]+' '/^amdgpu\// {print $1; exit}')
    [ -n "$v" ] && { printf '%s\n' "$v"; return 0; }
    for d in /usr/src/amdgpu-*; do
        [ -d "$d" ] && { printf 'amdgpu/%s\n' "${d##*/amdgpu-}"; return 0; }
    done
    return 1
}

# --- small sampling helpers -------------------------------------------------
mi50_sclk_star()   { grep '\*' "$MI50_CARD_DEV/pp_dpm_sclk" 2>/dev/null; }
mi50_pp_mask()     { awk '/Current ppfeatures/ {print $3}' "$MI50_CARD_DEV/pp_features" 2>/dev/null; }
mi50_power_w() {
    local p; p=$(cat "$MI50_HWMON/power1_input" 2>/dev/null || echo 0)
    echo $(( ${p:-0} / 1000000 ))
}
mi50_temp_c() {
    local t; t=$(cat "$MI50_HWMON/temp2_input" 2>/dev/null || echo 0)
    echo $(( ${t:-0} / 1000 ))
}

# SMU's own reported current GFXCLK (gpu_metrics_v1_0, uint16 at offset 54).
mi50_smu_gfxclk() {
    python3 - "$MI50_CARD_DEV/gpu_metrics" <<'PY' 2>/dev/null || echo "?"
import struct, sys
b = open(sys.argv[1], 'rb').read()
print(struct.unpack_from('<H', b, 54)[0])
PY
}

mi50_require_root() {
    [ "$(id -u)" -eq 0 ] || { echo "error: run as root (sudo $0)" >&2; return 1; }
}
mi50_die() { echo "error: $*" >&2; exit 1; }
