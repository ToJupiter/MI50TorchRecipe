#!/bin/bash
# mi50-reset.sh — trigger an amdgpu ASIC reset with an EXPLICIT method.
#
# LAST RESORT. On Vega20 a MODE2 reset was observed to leave the GPU wedged
# (OpenCL: "no suitable GPU device", ~41 W idle) and needed a cold AC drain to
# recover. Prefer the ordered recipe in BOOST_RECOVERY.md (cold AC drain + PP
# re-upload) over any runtime GPU reset.
#
# Reset-method enum (amdgpu):
#   -1 auto   0 legacy   1 mode0   2 MODE1   3 MODE2   4 LINK   5 BACO   6 PCI
# NOTE: 4 is LINK, *not* BACO — a common source of "isn't supported" warnings.
# Only MODE1/MODE2 send PrepareMp1ForShutdown (0x5A) / PrepareMp1ForReset
# (0x59) to the SMU before a full ASIC reset (which reloads SMU firmware).
# BACO sends NO MP1 message and cannot clear a latched SMU state.
#
# Usage: sudo ./mi50-reset.sh [mode2|mode1|baco|auto]
# Requires root and a mounted debugfs. VRAM contents are lost; no GPU jobs.
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/mi50-common.sh"
mi50_detect || mi50_die "no MI50 ($MI50_VENDOR:$MI50_DEVICE) found in sysfs"
mi50_require_root

case "${1:-mode2}" in
    mode1|1)  M=2 ;;
    mode2|2)  M=3 ;;
    baco|5)   M=5 ;;
    auto|-1)  M=-1 ;;
    *) echo "usage: $0 [mode2|mode1|baco|auto]" >&2; exit 2 ;;
esac

REC="$MI50_DEBUGFS/amdgpu_gpu_recover"
[ -r "$REC" ] || mi50_die "missing $REC (mount debugfs: sudo mount -t debugfs none /sys/kernel/debug)"

OLD=$(cat /sys/module/amdgpu/parameters/reset_method 2>/dev/null || echo -1)
echo "reset_method: $OLD -> $M ; target: $MI50_BDF"
printf '%s\n' "$M" > /sys/module/amdgpu/parameters/reset_method
cat "$REC" >/dev/null
sleep 3
printf '%s\n' "$OLD" > /sys/module/amdgpu/parameters/reset_method
echo "reset requested; reset_method restored to $OLD"
echo "check: journalctl -k -b --no-pager | tail -60"
echo "then:  sudo $MI50_TOOLS/restore-boost.sh --verify"
echo "If OpenCL says 'no suitable GPU device' or idle power jumps, do a cold AC drain."
