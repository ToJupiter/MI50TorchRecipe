#!/bin/bash
# restore-boost.sh — clear the recurring MI50 DPM0 latch by re-uploading the
# card's own PowerPlay table. This is the 2026-10-19 fix (README §2 step 9).
# Requires root. Usage: sudo ./restore-boost.sh [--verify]
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
C=/sys/class/drm/card1/device

[ -r "$C/pp_table" ] || { echo "error: $C/pp_table not found" >&2; exit 1; }
if [ "$(cat "$C/vendor" 2>/dev/null)" != "0x1002" ] || [ "$(cat "$C/device" 2>/dev/null)" != "0x66a1" ]; then
    echo "error: card1 is not the MI50 (expected 1002:66a1)" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "run as root: sudo $0" >&2
    exit 1
fi

backup="/tmp/mi50-pp_table-$(date +%Y%m%d-%H%M%S).bin"
cp "$C/pp_table" "$backup"
echo "backup: $backup"
cat "$backup" > "$C/pp_table"
echo "re-uploaded the card's own pp_table (SMU DPM re-init)"
sleep 2
awk '/\*/{print "DPM now: "$0}' "$C/pp_dpm_sclk"

if [ "${1:-}" = "--verify" ]; then
    "$DIR/verify-boost.sh"
else
    echo "next: $DIR/verify-boost.sh"
fi
