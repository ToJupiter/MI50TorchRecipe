#!/bin/bash
# mi50-pin-clock.sh — optional SOFTWARE-ONLY workaround that pins 1700 MHz.
#
# Use this ONLY when you cannot do a cold AC drain right away. It bypasses the
# wedged SMU "autonomous DPM boost" by forcing soft min = soft max = top level:
#   perf=high -> driver-side soft_min/soft_max = 1700
#   pp_table re-upload -> vega20_enable_dpm_tasks() re-applies that level to
#                        the SMU (observed: pp_dpm_sclk reports 8: 1700Mhz *)
# The '*' is the SMU's REAL clock (PPSMC_MSG_GetDpmClockFreq 0x2D), not driver
# bookkeeping. DO NOT set perf=auto afterwards — unforce_dpm_levels() re-uploads
# soft min=859/max=1700 and the SMU drops straight back to 859 MHz.
#
# Caveats: GFX/MCLK/SOCCLK stay at maximum at ALL times -> higher idle power and
# heat. Keep the external blower on and watch junction temperature. This is a
# workaround, not a cure; the real fix is the ordered recipe in BOOST_RECOVERY.md.
#
# Usage: sudo ./mi50-pin-clock.sh --verify     # apply pin + 15 s load test
#        sudo ./mi50-pin-clock.sh --release    # back to auto (drops to 859)
set -u
source "$(cd "$(dirname "$0")" && pwd)/mi50-common.sh"
mi50_detect || mi50_die "no MI50 ($MI50_VENDOR:$MI50_DEVICE) found in sysfs"
mi50_require_root

ORIG_MASK=$(mi50_pp_mask)
ORIG_PERF=$(cat "$MI50_CARD_DEV/power_dpm_force_performance_level" 2>/dev/null)

wr() {
    local f="$1" v="$2" label="$3"
    if printf '%s\n' "$v" > "$f" 2>/dev/null; then
        echo "  ok   : $label"
    else
        echo "  FAIL : $label (write rejected; continuing)"
    fi
}

if [ "${1:-}" = "--release" ]; then
    echo "releasing pin: perf=auto, restoring pp_features=$ORIG_MASK"
    wr "$MI50_CARD_DEV/power_dpm_force_performance_level" auto "perf=auto"
    wr "$MI50_CARD_DEV/pp_features" "$ORIG_MASK" "pp_features restored"
    sleep 2
    echo "  sclk: $(mi50_sclk_star)"
    echo "NOTE: if the SMU latch is still present this drops back to 859 MHz."
    exit 0
fi

echo "before: mask=$ORIG_MASK perf=$ORIG_PERF $(mi50_sclk_star) SMU=$(mi50_smu_gfxclk)MHz"
echo

echo "[1/4] perf=high -> driver-side soft_min = soft_max = 1700"
wr "$MI50_CARD_DEV/power_dpm_force_performance_level" high "perf=high"
sleep 1
echo "  sclk: $(mi50_sclk_star)  SMU=$(mi50_smu_gfxclk)MHz"

echo "[2/4] full PowerPlay reset (re-upload the card's own pp_table)"
cp "$MI50_CARD_DEV/pp_table" /tmp/mi50-pp-pin.bin
if cat /tmp/mi50-pp-pin.bin > "$MI50_CARD_DEV/pp_table" 2>/dev/null; then
    echo "  ok   : pp_table re-uploaded (EnableDPMTasks re-run)"
else
    echo "  FAIL : pp_table write rejected"
fi
sleep 2
echo "  sclk: $(mi50_sclk_star)  SMU=$(mi50_smu_gfxclk)MHz"

echo "[2b] fallback: manual + explicit max DPM levels if star is not level 8"
if ! mi50_sclk_star | grep -qE '^8:'; then
    wr "$MI50_CARD_DEV/power_dpm_force_performance_level" manual "perf=manual"
    wr "$MI50_CARD_DEV/pp_dpm_sclk" 8 "sclk level 8"
    wr "$MI50_CARD_DEV/pp_dpm_mclk" 2 "mclk level 2"
    wr "$MI50_CARD_DEV/pp_dpm_fclk" 7 "fclk level 7"
    cat /tmp/mi50-pp-pin.bin > "$MI50_CARD_DEV/pp_table" 2>/dev/null || true
    sleep 2
    echo "  sclk: $(mi50_sclk_star)  SMU=$(mi50_smu_gfxclk)MHz"
fi

echo "[3/4] re-apply ULV-off AFTER the reset (pp_table write restores pp_features)"
ULV_OFF=$(printf '0x%x' "$(( ORIG_MASK & ~0x40 ))")
wr "$MI50_CARD_DEV/pp_features" "$ULV_OFF" "pp_features=$ULV_OFF (ULV off)"
echo "  sclk: $(mi50_sclk_star)  SMU=$(mi50_smu_gfxclk)MHz"

echo "[4/4] KEEP perf=high — do NOT set auto"
echo "  perf now: $(cat "$MI50_CARD_DEV/power_dpm_force_performance_level")"
echo "  sclk: $(mi50_sclk_star)  SMU=$(mi50_smu_gfxclk)MHz"
echo
if mi50_sclk_star | grep -qE '^8:'; then
    echo "*** PIN OK: SMU reports level 8 (1700 MHz). ***"
else
    echo "*** PIN NOT HELD: $(mi50_sclk_star). Do the cold AC drain (BOOST_RECOVERY.md). ***"
fi
echo
echo "To release later: sudo $0 --release"
echo
if [ "${1:-}" = "--verify" ]; then
    "$MI50_TOOLS/verify-boost.sh"
else
    echo "Check under load: $MI50_TOOLS/verify-boost.sh"
fi
