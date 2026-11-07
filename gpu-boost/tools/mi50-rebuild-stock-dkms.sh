#!/bin/bash
# mi50-rebuild-stock-dkms.sh — rebuild a GENUINELY STOCK amdgpu DKMS module and
# PROVE it (both on disk AND inside the initramfs) before rebooting.
#
# Use this only if mi50-diagnose.sh reports a hard-patched module (L2). The
# hard-DPM experiment changed the GFXCLK limit upload from soft (0x26/0x27) to
# hard (0x28/0x29); the SMU returns PPSMC_Result_Failed (0xff) for 0x28, which
# aborts vega20_unforce_dpm_levels() -> "Failed to upload DPM Bootup Levels!"
# on every boot. No reset / PP re-upload can work until this module is gone.
#
# Two traps this script avoids (learned the hard way):
#   * DKMS 3.x (Ubuntu 24.04) does NOT copy the source at `dkms add` — it only
#     creates the `source` symlink. The build tree appears at `dkms build`.
#   * Early boot loads amdgpu from /boot/initrd.img-*, so a correct on-disk
#     module is not enough: the initramfs must be regenerated AND verified.
#   * `grep -q` on a live pipe under `set -o pipefail` reports failure because
#     the producer gets SIGPIPE — always grep a temp FILE instead.
#
# Machine-independent: DKMS version is auto-detected, kernel from uname -r,
# and "stock" is decided by the source's own strings, not by a hardcoded md5.
#
# Usage: sudo ./mi50-rebuild-stock-dkms.sh
#        MI50_VERIFY_ONLY=1 sudo ./mi50-rebuild-stock-dkms.sh  (no rebuild)
#        MI50_KERNEL=... / MI50_DKMS_VER=... overrides
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/mi50-common.sh"
mi50_require_root

DKVS=$(mi50_dkms_ver) || mi50_die "cannot determine the amdgpu DKMS version (set MI50_DKMS_VER)"
MOD=${DKVS%%/*}
VER=${DKVS#*/}
SRC="/usr/src/$MOD-$VER"
DKMS_DIR="/var/lib/dkms/$MOD/$VER"
HW="$SRC/amd/pm/powerplay/hwmgr/vega20_hwmgr.c"
KO="/lib/modules/$MI50_KERNEL/updates/dkms/$MOD.ko.zst"
INITRD="/boot/initrd.img-$MI50_KERNEL"
VERIFY_ONLY=${MI50_VERIFY_ONLY:-0}

[ -d "$SRC" ] || mi50_die "missing DKMS source $SRC"
echo "DKMS module : $DKVS"
echo "kernel      : $MI50_KERNEL"
echo "source      : $HW"

strings_of() { case "$1" in *.zst) zstdcat "$1" 2>/dev/null | strings ;; *) strings "$1" 2>/dev/null ;; esac; }
assert_stock() {
    local ko="$1" label="$2" tmp
    [ -r "$ko" ] || { echo "ERROR ($label): $ko missing/unreadable" >&2; return 1; }
    tmp=$(mktemp /tmp/mi50-strings.XXXXXX)
    if ! strings_of "$ko" > "$tmp"; then
        echo "ERROR ($label): cannot decompress $ko" >&2; rm -f "$tmp"; return 1
    fi
    if grep -qE 'Failed to set hard (min|max) gfxclk' "$tmp"; then
        echo "ERROR ($label): $ko still contains the patched GFXCLK path" >&2
        rm -f "$tmp"; return 1
    fi
    if ! grep -qE 'Failed to set soft (min|max) gfxclk' "$tmp"; then
        echo "ERROR ($label): $ko has no stock soft-GFXCLK limit string" >&2
        rm -f "$tmp"; return 1
    fi
    echo "OK ($label): stock soft GFXCLK limits -> $ko"
    rm -f "$tmp"
}
assert_source_stock() {
    local f="$1" tmp
    tmp=$(mktemp /tmp/mi50-src.XXXXXX)
    cp "$f" "$tmp"
    if grep -q 'Failed to set hard min gfxclk' "$tmp"; then
        echo "ERROR: source $f is still hard-patched" >&2; rm -f "$tmp"; return 1
    fi
    if ! grep -q 'Failed to set soft min gfxclk' "$tmp"; then
        echo "ERROR: source $f has no stock soft-min GFXCLK string" >&2
        rm -f "$tmp"; return 1
    fi
    echo "source OK: stock soft GFXCLK limits -> $f"
    rm -f "$tmp"
}

if [ "$VERIFY_ONLY" != 1 ]; then
    # 1. Restore the stock source if a pre-patch backup exists.
    if [ -f "$HW.pre-harddpm" ] && ! assert_source_stock "$HW" >/dev/null 2>&1; then
        # cp (NOT cp -a) + touch: refresh the .c mtime so make cannot reuse a
        # stale .o from the patched build (cp -a would preserve the old mtime).
        cp "$HW.pre-harddpm" "$HW"
        touch "$HW"
        echo "restored stock vega20_hwmgr.c from .pre-harddpm (mtime refreshed)"
    fi
    assert_source_stock "$HW"

    # 2. Remove the stale module first so nothing can be re-copied.
    rm -f "$KO"
    echo "removed stale $KO (if present)"

    # 3. Full re-add -> build -> install. Never assume a build tree exists.
    dkms remove "$DKVS" --all --force 2>/dev/null || true
    rm -rf "$DKMS_DIR"
    dkms add -m "$MOD" -v "$VER"
    dkms build "$DKVS" -k "$MI50_KERNEL"
    dkms install "$DKVS" -k "$MI50_KERNEL" --force

    # 3b. If a build tree survived, confirm it is stock (informational).
    BT="$DKMS_DIR/build/amd/pm/powerplay/hwmgr/vega20_hwmgr.c"
    [ -f "$BT" ] && assert_source_stock "$BT" || \
        echo "DKMS builds from $SRC (already verified); no separate build tree"
else
    echo "MI50_VERIFY_ONLY=1: reusing the already-installed module (no rebuild)"
fi

# 4. Verify the on-disk module and that its srcversion changed.
assert_stock "$KO" "on-disk"
zstdcat "$KO" > /tmp/mi50-amdgpu-verify.ko
newsrc=$(modinfo -F srcversion /tmp/mi50-amdgpu-verify.ko 2>/dev/null || echo unknown)
rm -f /tmp/mi50-amdgpu-verify.ko
echo "srcversion: $newsrc"
if [ -n "${MI50_BAD_SRCVERSION:-}" ]; then
    [ "$newsrc" != "$MI50_BAD_SRCVERSION" ] || \
        mi50_die "srcversion unchanged — DKMS did not actually rebuild"
fi

# 5. Regenerate the initramfs and verify the module that EARLY BOOT loads.
update-initramfs -u -k "$MI50_KERNEL"
if [ -r "$INITRD" ] && command -v unmkinitramfs >/dev/null 2>&1; then
    ITMP=$(mktemp -d /tmp/mi50-initrd.XXXXXX)
    if unmkinitramfs "$INITRD" "$ITMP" >/dev/null 2>&1; then
        IRDKO=$(find "$ITMP" -name "$MOD.ko*" 2>/dev/null | head -1)
        if [ -n "$IRDKO" ]; then
            if ! assert_stock "$IRDKO" "initramfs"; then
                rm -rf "$ITMP"
                mi50_die "initramfs still has the bad module; retry: sudo update-initramfs -c -k $MI50_KERNEL"
            fi
        else
            echo "WARN: no $MOD.ko inside $INITRD (module may load from rootfs)" >&2
        fi
    else
        echo "WARN: could not inspect $INITRD; verify manually after reboot" >&2
    fi
    rm -rf "$ITMP"
else
    echo "WARN: initramfs inspection unavailable; verify manually after reboot" >&2
fi

echo
echo "done. reboot into $MI50_KERNEL, then (no sudo):"
echo "  journalctl -k -b --no-pager | grep -iE 'hard min gfxclk|message 0x28'  # MUST be empty"
echo "  $MI50_TOOLS/mi50-diagnose.sh"
echo "  $MI50_TOOLS/verify-boost.sh"
