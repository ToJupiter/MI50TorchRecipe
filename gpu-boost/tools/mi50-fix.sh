#!/bin/bash
# mi50-fix.sh — run the boost recovery IN ORDER. One command for the software part.
#
# This encodes the ordered runbook from BOOST_RECOVERY.md so a future session
# does not have to remember which step comes first:
#   STEP 1  Is the card already boosting?           -> PASS, stop.
#   STEP 2  Is the driver module stock (on disk + initramfs)?
#           -> if hard-patched: stop, run mi50-rebuild-stock-dkms.sh, reboot.
#   STEP 3  Re-upload the card's own pp_table (+ verify) -> PASS, stop.
#   STEP 4  Still capped -> the SMU latch needs a PHYSICAL cold AC drain;
#           print the procedure and exit. Re-run this script after that boot.
#
# The cold AC drain cannot be automated (it requires a human at the PSU), but
# everything else is. Requires root for STEP 3. Machine-independent.
#
# Usage: sudo ./mi50-fix.sh
set -u
source "$(cd "$(dirname "$0")" && pwd)/mi50-common.sh"
mi50_detect || mi50_die "no MI50 ($MI50_VENDOR:$MI50_DEVICE) found in sysfs"
mi50_require_root

echo "card=$MI50_CARD_NAME bdf=$MI50_BDF kernel=$MI50_KERNEL"
echo

echo "===== STEP 1/4: is the card already boosting? ====="
# A quick 6 s load is enough to see whether SCLK climbs.
if "$MI50_TOOLS/verify-boost.sh" 6 2>&1 | tee /tmp/mi50-fix-step1.log | grep -q 'RESULT: PASS'; then
    echo "PASS — card boosts. Nothing to do."
    rm -f /tmp/mi50-fix-step1.log
    exit 0
fi
cat /tmp/mi50-fix-step1.log | tail -4
rm -f /tmp/mi50-fix-step1.log
echo "not boosting; continuing."
echo

echo "===== STEP 2/4: is the driver module stock? ====="
strings_of() { case "$1" in *.zst) zstdcat "$1" 2>/dev/null | strings ;; *) strings "$1" 2>/dev/null ;; esac; }
is_hard() { strings_of "$1" | grep -q 'Failed to set hard min gfxclk'; }
BAD=0
[ -r "$MI50_KO" ] && is_hard "$MI50_KO" && BAD=1
if [ -r "$MI50_INITRD" ] && command -v unmkinitramfs >/dev/null 2>&1; then
    T=$(mktemp -d /tmp/mi50-fix-initrd.XXXXXX)
    if unmkinitramfs "$MI50_INITRD" "$T" >/dev/null 2>&1; then
        K=$(find "$T" -name 'amdgpu.ko*' 2>/dev/null | head -1)
        [ -n "$K" ] && is_hard "$K" && BAD=1
    fi
    rm -rf "$T"
fi
if [ "$BAD" = 1 ]; then
    echo "HARD-PATCHED module detected (L2). Stop."
    echo "  sudo $MI50_TOOLS/mi50-rebuild-stock-dkms.sh"
    echo "  sudo reboot"
    echo "  then re-run: sudo $0"
    exit 2
fi
echo "stock. continuing."
echo

echo "===== STEP 3/4: re-arm DPM (pp_table re-upload) + verify ====="
if "$MI50_TOOLS/restore-boost.sh" --verify 2>&1 | tee /tmp/mi50-fix-step3.log | grep -q 'RESULT: PASS'; then
    echo "PASS — DPM re-armed, card boosts."
    rm -f /tmp/mi50-fix-step3.log
    exit 0
fi
tail -4 /tmp/mi50-fix-step3.log
rm -f /tmp/mi50-fix-step3.log
echo

echo "===== STEP 4/4: SMU latch survives software — PHYSICAL cold AC drain ====="
cat <<'EOF'
A warm reboot, BACO, MODE1/MODE2 resets and the PP re-upload above do NOT
clear a latched SMU firmware state. Do this, in this exact order:

  1. shutdown -h now
  2. PSU switch OFF *and* unplug the AC cable
  3. hold the case power button for 10 s (discharge the caps)
  4. wait >= 5 minutes (the SMU always-on rail must fully discharge)
  5. restore AC, boot
  6. sudo ./mi50-fix.sh          (this script; STEP 3 should now PASS)

Then make it permanent:
  sudo systemctl mask sleep.target suspend.target hibernate.target \
                      hybrid-sleep.target
  sudo ../systemd/install-guard.sh --now   # boot-time pp_table re-arm
EOF
exit 1
