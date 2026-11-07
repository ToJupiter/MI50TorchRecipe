#!/bin/bash
# restore-boost.sh — re-arm DPM by re-uploading the card's OWN PowerPlay table.
#
# This is the confirmed fix step (validated 2026-11-07: 1700 MHz / 149 W /
# 10.9 TFLOPS). Writing pp_table triggers amd_powerplay_reset() ->
# hwmgr_hw_fini/init -> vega20_enable_dpm_tasks() (table upload, RunBtc,
# RunAfllBtc, EnableAllSmuFeatures, default DPM tables), which re-arms the
# SMU's DPM engine after a cold power cycle cleared the latch.
#
# IMPORTANT: this re-uploads the card's OWN current table (read from sysfs), so
# it is reversible by reboot and cannot corrupt the VBIOS. It is a runtime
# re-arm, NOT a permanent repair — the cap can return after a failed suspend
# or long idle. See BOOST_RECOVERY.md for the full ordered recipe.
#
# Usage: sudo ./restore-boost.sh [--verify]
# Requires root (sysfs write). Machine-independent (see mi50-common.sh).
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/mi50-common.sh"
mi50_detect || mi50_die "no MI50 ($MI50_VENDOR:$MI50_DEVICE) found in sysfs"
mi50_require_root

backup=$(mktemp /tmp/mi50-pp_table-XXXXXX.bin)
cp "$MI50_CARD_DEV/pp_table" "$backup"
echo "backup: $backup"
cat "$backup" > "$MI50_CARD_DEV/pp_table"
echo "re-uploaded the card's own pp_table (SMU DPM re-init)"
sleep 2
echo "DPM now: $(mi50_sclk_star)"
rm -f "$backup"

if [ "${1:-}" = "--verify" ]; then
    "$MI50_TOOLS/verify-boost.sh"
else
    echo "next: $MI50_TOOLS/verify-boost.sh"
fi
