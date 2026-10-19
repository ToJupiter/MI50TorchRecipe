#!/bin/bash
# boost_probe.sh — forced-clock diagnostic for MI50 / Vega20. Needs root:
#   sudo bash gpu-boost/tools/boost_probe.sh
#
# What it does (and restores `auto` at the end):
#   1. snapshots idle state,
#   2. forces manual + SCLK level 8 (1700 MHz) + MCLK level 2 (1000 MHz),
#   3. tries power_dpm_force_performance_level=high,
#   4. runs a 20 s load while sampling,
#   5. dumps amdgpu_pm_info + firmware info,
#   6. dumps the VBIOS via debugfs (/sys/.../rom reads 0 bytes on VFCT systems).
#
# LEVEL-INDEX SEMANTICS (important):
#   pp_dpm_sclk accepts a list of LEVEL INDICES 0..31. The kernel converts each
#   to a bitmask via `*mask |= 1 << level` (amdgpu_read_mask()). So `echo 8`
#   selects level 8 (1700 MHz). `echo 256` is INVALID (>31) and returns EINVAL.
#
# Healthy result: SCLK rises toward 1700 MHz and VDDGFX climbs (~850-1100 mV).
# Broken result: SMU ACKs the writes but stays at 859 MHz / ~740 mV / GPU Load 99%
#   -> do a FULL AC power cycle before any driver/firmware/VBIOS action.

export HSA_OVERRIDE_GFX_VERSION=9.0.0
DIR="$(cd "$(dirname "$0")" && pwd)"
C=/sys/class/drm/card1/device
H=$C/hwmon/hwmon1
D=/sys/kernel/debug/dri/0000:03:00.0
BURN="$DIR/gpu_burn"
LOG="${LOG:-/tmp/boost_probe.log}"
exec > >(tee "$LOG") 2>&1

snap() {
    local tag="$1"
    echo
    echo "--- $tag"
    echo "perf_level=$(cat $C/power_dpm_force_performance_level 2>&1)"
    echo "sclk table:"; sed 's/^/    /' $C/pp_dpm_sclk
    echo "mclk table:"; sed 's/^/    /' $C/pp_dpm_mclk
    printf "    power=%s W  temp edge/junc/mem=%s/%s/%s C  busy=%s%%\n" \
        "$(( $(cat $H/power1_input 2>/dev/null || echo 0) / 1000000 ))" \
        "$(( $(cat $H/temp1_input 2>/dev/null || echo 0) / 1000 ))" \
        "$(( $(cat $H/temp2_input 2>/dev/null || echo 0) / 1000 ))" \
        "$(( $(cat $H/temp3_input 2>/dev/null || echo 0) / 1000 ))" \
        "$(cat $C/gpu_busy_percent 2>/dev/null)"
    echo "    rocm-smi: $(rocm-smi --showclocks 2>/dev/null | awk '/^GPU\[0\]/{printf "%s ",$0}')"
}

pm_info() {
    for f in /sys/kernel/debug/dri/*/amdgpu_pm_info; do
        [ -r "$f" ] || continue
        echo "=== $f"
        cat "$f"
    done | awk '!/^\tGraphics|^\tMemory Controller|^\tSystem Direct|^\tBus Interface|^\tUnified Video|^\tVideo Compression|^\tHost Data|^\tDigital Right|^\tRom |^\tData Fabric|^\tVCN |^\tJPEG |^\tRepeater|^\tPerfmon|^\tAddress Translation|^\tClock Gating|^\tInterrupt Handler|^\t\t/'
}

echo "############ 0. initial state"
snap initial
echo; pm_info
for f in /sys/kernel/debug/dri/*/amdgpu_firmware_info; do
    [ -r "$f" ] && { echo "=== $f"; grep -E "SMC|VBIOS" "$f"; }
done

echo
echo "############ 1. force manual + SCLK level 8 (1700 MHz) + MCLK level 2 (1000 MHz)"
echo manual > $C/power_dpm_force_performance_level
if echo 8 > $C/pp_dpm_sclk 2>/tmp/w1.err; then echo "sclk write OK"; else echo "sclk write FAILED: $(cat /tmp/w1.err)"; fi
if echo 2 > $C/pp_dpm_mclk 2>/tmp/w2.err; then echo "mclk write OK"; else echo "mclk write FAILED: $(cat /tmp/w2.err)"; fi
sleep 3
snap "manual + forced level 8/2 (idle)"

echo
echo "############ 2. power_dpm_force_performance_level=high"
echo high > $C/power_dpm_force_performance_level 2>/tmp/w3.err || echo "high write FAILED: $(cat /tmp/w3.err)"
sleep 3
snap "perf=high"

echo
echo "############ 3. load test under forced state (20 s)"
timeout -k 5 40 "$BURN" 20 20000 &
BP=$!
sleep 4;  snap "load t=4s"
sleep 8;  snap "load t=12s"
wait $BP
echo "burner exit=$?"
echo; pm_info

echo
echo "############ 4. restore auto"
echo auto > $C/power_dpm_force_performance_level
sleep 2
snap "auto restored"

echo
echo "############ 5. dump VBIOS image (debugfs; /sys/.../rom is 0 bytes on VFCT systems)"
cat $D/amdgpu_vbios > /tmp/card-vbios-legacy.rom 2>/dev/null
ls -l /tmp/card-vbios-legacy.rom 2>/dev/null
md5sum /tmp/card-vbios-legacy.rom 2>/dev/null

echo
echo "############ 6. last kernel messages"
journalctl -k --since "-3 min" --no-pager | tail -15
