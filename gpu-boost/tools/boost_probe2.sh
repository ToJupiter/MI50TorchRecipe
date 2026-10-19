#!/bin/bash
# boost_probe2.sh — historical variant of the forced-clock probe.
#
# KEPT FOR FIDELITY. This is (close to) the second probe that was run on
# 2026-09-22. **It contains a known mistake:** it writes "256" to pp_dpm_sclk,
# which is NOT a valid input — pp_dpm_*clk accepts level indices 0..31
# (amdgpu_read_mask() does `1 << level`), so 256 returns EINVAL and the force
# step silently does nothing. The run is still useful because it proves the
# driver rejects out-of-range input and shows the SMU-side state under load.
#
# Use boost_probe.sh for a correct forced-clock test.
#
# Run with:  sudo bash gpu-boost/tools/boost_probe2.sh

export HSA_OVERRIDE_GFX_VERSION=9.0.0
DIR="$(cd "$(dirname "$0")" && pwd)"
C=/sys/class/drm/card1/device
H=$C/hwmon/hwmon1
D=/sys/kernel/debug/dri/0000:03:00.0
LOG="${LOG:-/tmp/boost_probe2.log}"
exec > >(tee "$LOG") 2>&1

pf() {
    grep -E "MCLK|SCLK|PSTATE|VDDGFX|current SoC|GPU Load" $D/amdgpu_pm_info
}

snap() {
    echo "--- $1"
    echo "sclk table: $(awk '/\*/{print}' $C/pp_dpm_sclk)"
    echo "mclk table: $(awk '/\*/{print}' $C/pp_dpm_mclk)"
    pf
}

echo "### idle before"
snap idle

echo
echo "### force masks: sclk=256 (BUG: invalid, >31 -> EINVAL), mclk=4 (valid level 2)"
echo manual > $C/power_dpm_force_performance_level
printf '256\n' > $C/pp_dpm_sclk; echo "sclk write rc=$?"
printf '4\n'   > $C/pp_dpm_mclk; echo "mclk write rc=$?"
sleep 3
snap "forced (idle)"

echo
echo "### load test 15 s"
timeout -k 5 40 "$DIR/gpu_burn" 15 20000 &
BP=$!
sleep 5
snap "load t=5s"
sleep 6
snap "load t=11s"
wait $BP
echo "burner exit=$?"
sleep 1
snap "just after load"

echo
echo "### restore auto"
echo auto > $C/power_dpm_force_performance_level
sleep 2
snap "auto restored"

echo
echo "### dump VBIOS image"
cat $D/amdgpu_vbios > /tmp/card-vbios-legacy.rom 2>/dev/null
ls -l /tmp/card-vbios-legacy.rom
md5sum /tmp/card-vbios-legacy.rom
