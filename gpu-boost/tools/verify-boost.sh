#!/bin/bash
# verify-boost.sh — one-shot MI50 boost health check. NO root needed.
#
# Usage: ./verify-boost.sh [seconds=15]
#
# Runs an OpenCL FP32 load and samples SCLK / MCLK / power / junction temp from
# sysfs once per second, then prints PASS or FAIL.
#   PASS  peak SCLK >= 1500 MHz  (healthy: 1700 MHz, ~140-150 W, ~11 TFLOPS)
#   FAIL  SCLK stays at ~859 MHz (capped: ~45-49 W, ~5.8 TFLOPS)
#
# Machine-independent: the card is located by PCI ID (see mi50-common.sh), so
# cardN / hwmonN / BDF / kernel are never hardcoded. The load generator is
# built from gpu_burn.c in this directory if the binary is missing.
#
# On FAIL, follow the ordered runbook: BOOST_RECOVERY.md at the repo root.
set -u
source "$(cd "$(dirname "$0")" && pwd)/mi50-common.sh"
mi50_detect || mi50_die "no MI50 ($MI50_VENDOR:$MI50_DEVICE) found in sysfs"

# ROCm 7.x dropped gfx906; this shim lets OpenCL enumerate the MI50 as gfx900.
export HSA_OVERRIDE_GFX_VERSION=${HSA_OVERRIDE_GFX_VERSION:-9.0.0}

SECS=${1:-15}
ITERS=${2:-20000}

# Prefer an executable in-tree binary. If the repo's prebuilt copy is present
# but not executable, compile a PRIVATE copy in /tmp instead of overwriting a
# tracked file (keeps `git status` clean and avoids machine-specific binaries).
BURN="$MI50_TOOLS/gpu_burn"
BURN_TMP=""
if [ ! -x "$BURN" ]; then
    [ -f "$MI50_TOOLS/gpu_burn.c" ] || mi50_die "gpu_burn.c missing from $MI50_TOOLS"
    BURN_TMP=$(mktemp /tmp/mi50-gpu_burn.XXXXXX)
    echo "building a private gpu_burn in $BURN_TMP ..."
    (cd "$MI50_TOOLS" && gcc -O2 -o "$BURN_TMP" gpu_burn.c -lOpenCL) || \
        { rm -f "$BURN_TMP"; mi50_die "build failed: need gcc and OpenCL headers (-lOpenCL)"; }
    BURN="$BURN_TMP"
fi
cleanup() { [ -n "$BURN_TMP" ] && rm -f "$BURN_TMP"; }
trap cleanup EXIT

sclk() { awk '/\*/{print $2}' "$MI50_CARD_DEV/pp_dpm_sclk" 2>/dev/null; }
mclk() { awk '/\*/{print $2}' "$MI50_CARD_DEV/pp_dpm_mclk" 2>/dev/null; }

echo "card=$MI50_CARD_NAME bdf=$MI50_BDF kernel=$MI50_KERNEL"
echo "== idle: sclk=$(sclk) mclk=$(mclk) power=$(mi50_power_w)W junction=$(mi50_temp_c)C"

OUT=$(mktemp)
"$BURN" "$SECS" "$ITERS" >"$OUT" 2>&1 &
BP=$!

peak=0
peakp=0
for i in $(seq 1 "$SECS"); do
    s=$(sclk); s=${s%Mhz}
    p=$(mi50_power_w)
    [[ "${s:-0}" -gt "$peak" ]] 2>/dev/null && peak=$s
    [[ "${p:-0}" -gt "$peakp" ]] 2>/dev/null && peakp=$p
    printf 't=%2ds busy=%-4s sclk=%-8s mclk=%-8s power=%3dW junction=%2dC\n' \
        "$i" "$(cat "$MI50_CARD_DEV/gpu_busy_percent" 2>/dev/null)%" \
        "$(sclk)" "$(mclk)" "$p" "$(mi50_temp_c)"
    sleep 1
done
wait "$BP" 2>/dev/null
cat "$OUT"
rm -f "$OUT"

echo "== peak sclk=${peak}MHz  peak power=${peakp}W"
if [ "$peak" -ge 1500 ]; then
    echo "RESULT: PASS — GPU boosts."
    exit 0
fi

echo "RESULT: FAIL — SCLK capped at ${peak}MHz."
echo
echo "Next, IN ORDER (see BOOST_RECOVERY.md):"
echo "  0. $MI50_TOOLS/mi50-diagnose.sh                     # no sudo; confirm stock driver"
echo "  1. TRUE cold AC drain: shutdown -> PSU OFF + UNPLUG -> hold power 10 s"
echo "     -> wait >=5 min -> restore AC -> boot            # clears the SMU latch"
echo "  2. sudo $MI50_TOOLS/restore-boost.sh --verify        # re-arm DPM"
echo "  3. sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target"
exit 1
