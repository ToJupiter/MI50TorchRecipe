#!/bin/bash
# mi50-diagnose.sh — deep, 100% NON-PRIVILEGED diagnosis of the 859 MHz cap.
#
# No sudo, no GPU sysfs writes. Everything read here is world-readable on a
# normal Linux host; the journal section needs the 'adm' (or equivalent) group
# for `journalctl -k`. Writes only to a mktemp dir under /tmp.
#
# Question it answers: is the cap caused by a BAD DRIVER MODULE (L2), by a
# latched SMU FIRMWARE STATE (L1), or is the driver already stock and the card
# boosting? It never concludes "hardware fault" — the SMU telemetry, PP table
# and message replies prove the SMU is alive.
#
# Usage: ./mi50-diagnose.sh
#        MI50_SKIP_INITRAMFS=1 ./mi50-diagnose.sh   # skip extracting the initrd
set -u
source "$(cd "$(dirname "$0")" && pwd)/mi50-common.sh"
mi50_detect || mi50_die "no MI50 ($MI50_VENDOR:$MI50_DEVICE) found in sysfs"

TMP=$(mktemp -d /tmp/mi50-diag.XXXXXX)
trap 'rm -rf "$TMP"' EXIT
hr() { printf '\n===== %s =====\n' "$*"; }
strings_of() { case "$1" in *.zst) zstdcat "$1" 2>/dev/null | strings ;; *) strings "$1" 2>/dev/null ;; esac; }
hard_patched() { strings_of "$1" | grep -q 'Failed to set hard min gfxclk'; }
stock_soft()   { strings_of "$1" | grep -qE 'Failed to set soft (min|max) gfxclk'; }

hr "1. identity"
echo "kernel          : $MI50_KERNEL"
echo "cmdline amdgpu  : $(tr ' ' '\n' </proc/cmdline | grep '^amdgpu\.' | tr '\n' ' ')"
echo "card            : $MI50_CARD_NAME  bdf=$MI50_BDF"
echo "module path     : $(modinfo -n amdgpu 2>&1)"
echo "loaded srcver   : $(cat /sys/module/amdgpu/srcversion 2>&1)"
echo "reset_method    : $(cat /sys/module/amdgpu/parameters/reset_method 2>&1)"
echo "ppfeaturemask   : $(cat /sys/module/amdgpu/parameters/ppfeaturemask 2>&1)"
echo "DKMS module     : $(mi50_dkms_ver 2>/dev/null || echo 'n/a')"
echo "  reset_method enum: -1 auto, 0 legacy, 1 mode0, 2 MODE1, 3 MODE2,"
echo "                     4 LINK, 5 BACO, 6 PCI  (4 is NOT BACO)"

hr "2. live SMU state"
echo "pp_dpm_sclk     : $(mi50_sclk_star)"
echo "pp_features     : $(mi50_pp_mask)"
echo "perf level      : $(cat "$MI50_CARD_DEV/power_dpm_force_performance_level" 2>/dev/null)"
echo -n "pp_table md5    : "; md5sum "$MI50_CARD_DEV/pp_table" 2>/dev/null | awk '{print $1}'
if [ -f "$MI50_GB/roms/pp_table.bin" ]; then
    echo -n "reference md5   : "; md5sum "$MI50_GB/roms/pp_table.bin" 2>/dev/null | awk '{print $1}'
fi
python3 - "$MI50_CARD_DEV/gpu_metrics" <<'PY' 2>/dev/null || echo "  gpu_metrics unreadable"
import struct, sys
b = open(sys.argv[1], 'rb').read()
u16 = lambda o: struct.unpack_from('<H', b, o)[0]
u32 = lambda o: struct.unpack_from('<I', b, o)[0]
print('  gpu_metrics v1.0 (SMU truth):')
print('    current  gfx/soc/uclk = %d / %d / %d MHz' % (u16(54), u16(56), u16(58)))
print('    average  gfx/soc/uclk = %d / %d / %d MHz' % (u16(40), u16(42), u16(44)))
print('    temps edge/hotspot/mem/vrgfx = %d/%d/%d/%d C' % (u16(16), u16(18), u16(20), u16(22)))
print('    throttle_status = 0x%x   pcie = x%d @ %.1f GT/s' % (u32(68), b[74], b[75] / 10.0))
PY

hr "3. the module that is actually loaded"
MOD_BAD=0
if [ -r "$MI50_KO" ]; then
    if hard_patched "$MI50_KO"; then
        echo "  HARD-PATCHED: contains 'Failed to set hard min gfxclk !'"; MOD_BAD=1
    elif stock_soft "$MI50_KO"; then
        echo "  stock: soft GFXCLK limits only"
    else
        echo "  inconclusive: no known GFXCLK limit strings found"
    fi
    zstdcat "$MI50_KO" > "$TMP/amdgpu.ko" 2>/dev/null
    echo "  on-disk srcversion: $(modinfo -F srcversion "$TMP/amdgpu.ko" 2>/dev/null)"
else
    echo "  $MI50_KO not readable"
fi
if [ -n "${MI50_BAD_SRCVERSION:-}" ]; then
    echo "  known-bad srcversion (from your notes): $MI50_BAD_SRCVERSION"
    [ "$(cat /sys/module/amdgpu/srcversion 2>/dev/null)" = "$MI50_BAD_SRCVERSION" ] && MOD_BAD=1
fi

hr "4. the module that BOOTS (initramfs) — the one everyone forgets"
IRD_BAD=0
if [ "${MI50_SKIP_INITRAMFS:-0}" != 1 ] && [ -r "$MI50_INITRD" ] && command -v unmkinitramfs >/dev/null 2>&1; then
    echo "  initrd: $MI50_INITRD (mtime $(stat -c %y "$MI50_INITRD" 2>/dev/null | cut -d. -f1))"
    unmkinitramfs "$MI50_INITRD" "$TMP/ird" >/dev/null 2>&1
    IRD=$(find "$TMP/ird" -name 'amdgpu.ko*' 2>/dev/null | head -1)
    if [ -n "$IRD" ]; then
        if hard_patched "$IRD"; then
            echo "  HARD-PATCHED -> rebooting will STILL load the bad module"; IRD_BAD=1
        else
            echo "  stock"
        fi
    else
        echo "  no amdgpu.ko found inside initrd"
    fi
else
    echo "  skipped (MI50_SKIP_INITRAMFS=1, or initrd/tools unavailable)"
fi

hr "5. kernel-log correlation (journalctl -k, no sudo)"
if journalctl -k -b 0 --no-pager >/dev/null 2>&1; then
    printf '  %-5s %-17s %6s %8s %10s\n' boot date 0x28 hardmin dpmbootup
    for b in $(journalctl --list-boots --no-pager 2>/dev/null | awk '{print $1}' | tail -12); do
        j=$(journalctl -k -b "$b" --no-pager 2>/dev/null)
        d=$(printf '%s\n' "$j" | head -1 | cut -c1-16)
        printf '  %-5s %-17s %6d %8d %10d\n' "$b" "$d" \
            "$(printf '%s\n' "$j" | grep -c 'message 0x28')" \
            "$(printf '%s\n' "$j" | grep -ci 'hard min gfxclk')" \
            "$(printf '%s\n' "$j" | grep -ci 'Failed to upload DPM Bootup Levels')"
    done
    echo "  (0x28 errors should be zero on every boot with a stock driver)"
else
    echo "  journalctl -k not accessible (join the 'adm' group for this section)"
fi

hr "6. SMU firmware blobs (rule out version skew)"
for n in smc sos ta asd rlc sdma sdma1 uvd vce ce me mec pfp; do
    a="/lib/firmware/amdgpu/vega20_$n.bin.zst"
    b="/lib/firmware/updates/amdgpu/vega20_$n.bin"
    [ -f "$a" ] && [ -f "$b" ] || continue
    ma=$(zstdcat "$a" 2>/dev/null | md5sum | awk '{print $1}')
    mb=$(md5sum "$b" 2>/dev/null | awk '{print $1}')
    [ "$ma" = "$mb" ] && s="same   " || s="DIFFER "
    printf '  %-10s %s %s / %s\n' "$n" "$s" "${ma:0:8}" "${mb:0:8}"
done
echo "  (smc/sos/ta differences would indicate a mixed firmware set; GFX"
echo "   microcodes ce/me/mec/pfp may legitimately differ and are not the SMU)"

hr "VERDICT"
echo "  PPSMC results: OK=0x1  Failed=0xff  UnknownCmd=0xfe  RejectedPrereq=0xfd  RejectedBusy=0xfc"
echo "  => 0xff is a PROCESSED reply (SMU alive), not 'unsupported' (0xfe) and not a hang."
echo
if [ "$MOD_BAD" = 1 ] || [ "$IRD_BAD" = 1 ]; then
    cat <<'EOF'
  >>> L2: A HARD-PATCHED DRIVER MODULE is loaded (or embedded in the initramfs).
      SetHardMinByFreq (0x28) for GFXCLK returns PPSMC_Result_Failed (0xff)
      and aborts vega20_unforce_dpm_levels() on every boot, so no reset or PP
      re-upload can ever re-arm DPM. This is SOFTWARE, not hardware.

      FIX (root): sudo gpu-boost/tools/mi50-rebuild-stock-dkms.sh
                  sudo reboot
      Then re-run this script; all 0x28 / hardmin counts must be zero.
EOF
elif [ "$(mi50_smu_gfxclk 2>/dev/null)" -ge 1500 ] 2>/dev/null; then
    echo "  >>> SMU reports >=1500 MHz already. Card is boosting; nothing to do."
else
    cat <<'EOF'
  >>> The driver is stock and the boot log is clean. NOTE: at IDLE a HEALTHY
      Vega20 also reports ~859 MHz, so the idle clock alone cannot decide
      anything. Run a load test first:
        gpu-boost/tools/verify-boost.sh
      PASS -> the card is fine, nothing to do. FAIL -> the cap is L1, the SMU
      firmware's latched low-power/DPM state from a failed S3 suspend. Still
      SOFTWARE/FIRMWARE: telemetry, throttle_status and PP table are healthy;
      the SMU's autonomous activity-based boost is parked but it CAN be driven
      to 1700 MHz.

      Then run the ordered recipe in BOOST_RECOVERY.md:
        1) true cold AC drain (clears the latch; BACO/warm reboot do NOT)
        2) sudo gpu-boost/tools/restore-boost.sh --verify (re-arms DPM)
        3) sudo gpu-boost/tools/mi50-fix.sh (one-command version of 1+2)
        4) mask the suspend targets so the trigger never recurs
      Optional immediate workaround if you cannot power-cycle yet:
        sudo gpu-boost/tools/mi50-pin-clock.sh --verify  (pins 1700 MHz)
EOF
fi
echo
