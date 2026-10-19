#!/bin/bash
# verify-boost.sh — one-shot MI50 boost health check.
#
# Usage:  ./verify-boost.sh
# Runs a 15 s FP32 load and samples SCLK/MCLK/power/temp every second.
# Prints PASS/FAIL. On FAIL: FULL AC power cycle first; if that does not
# clear it, re-upload the PP table with ./restore-boost.sh (../README.md §2 step 9).
#
# No root needed for the sampling; OpenCL works as a normal user
# (HSA_OVERRIDE_GFX_VERSION is set below for ROCm 7.x + gfx906).

set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
C=/sys/class/drm/card1/device
H=$C/hwmon/hwmon1
export HSA_OVERRIDE_GFX_VERSION=9.0.0

if [[ ! -x "$DIR/gpu_burn" ]]; then
    echo "gpu_burn missing — building..."
    (cd "$DIR" && gcc -O2 -o gpu_burn gpu_burn.c -lOpenCL) || exit 1
fi

sclk() { awk '/\*/{print $2}' "$C/pp_dpm_sclk" 2>/dev/null; }
mclk() { awk '/\*/{print $2}' "$C/pp_dpm_mclk" 2>/dev/null; }
pw()   { echo $(( $(cat "$H/power1_input" 2>/dev/null || echo 0) / 1000000 )); }
tj()   { echo $(( $(cat "$H/temp2_input" 2>/dev/null || echo 0) / 1000 )); }

echo "== idle: sclk=$(sclk) mclk=$(mclk) power=$(pw)W junction=$(tj)C"

OUT=$(mktemp)
"$DIR/gpu_burn" 15 20000 > "$OUT" 2>&1 &
BP=$!

peak=0; peakp=0
for i in $(seq 1 15); do
    s=$(sclk); s=${s%Mhz}
    p=$(pw)
    [[ "${s:-0}" -gt "$peak" ]] 2>/dev/null && peak=$s
    [[ "${p:-0}" -gt "$peakp" ]] && peakp=$p
    printf "t=%2ds busy=%-4s sclk=%-8s mclk=%-8s power=%3dW junction=%2dC\n" \
        "$i" "$(cat $C/gpu_busy_percent 2>/dev/null)%" "$(sclk)" "$(mclk)" "$p" "$(tj)"
    sleep 1
done
wait "$BP"
cat "$OUT"; rm -f "$OUT"

echo "== peak sclk=${peak}MHz  peak power=${peakp}W"
if [[ "$peak" -ge 1500 ]]; then
    echo "RESULT: PASS — GPU boosts."
else
    echo "RESULT: FAIL — SCLK capped at ${peak}MHz."
    echo "Next: FULL AC power cycle (shutdown -> PSU off/unplug -> drain -> boot), then retry."
    echo "If still capped: sudo $DIR/restore-boost.sh --verify  (PP-table re-upload, README step 9)."
    echo "Then read ../README.md sections 2 and 3 before anything else."
fi
