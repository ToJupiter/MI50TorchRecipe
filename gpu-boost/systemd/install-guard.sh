#!/bin/bash
# install-guard.sh — render the boot guard template with THIS repo's path and
# install/enable it. Nothing host-specific is hardcoded in the committed files.
#
# Usage: sudo ./install-guard.sh [--now]
#   --now  also start it immediately (otherwise it runs from the next boot)
#
# Uninstall: sudo systemctl disable --now mi50-boost-guard.service
#            sudo rm -f /etc/systemd/system/mi50-boost-guard.service
#            sudo systemctl daemon-reload
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
UNIT=/etc/systemd/system/mi50-boost-guard.service

[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo $0" >&2; exit 1; }
[ -f "$HERE/mi50-boost-guard.service.in" ] || { echo "template missing" >&2; exit 1; }

tmp=$(mktemp /tmp/mi50-guard.XXXXXX)
sed "s|@REPO@|$REPO|g" "$HERE/mi50-boost-guard.service.in" > "$tmp"
install -m 0644 "$tmp" "$UNIT"
rm -f "$tmp"
echo "installed $UNIT (ExecStart=$REPO/gpu-boost/tools/restore-boost.sh)"
systemctl daemon-reload
systemctl enable mi50-boost-guard.service
if [ "${1:-}" = "--now" ]; then
    systemctl start mi50-boost-guard.service
    echo "started now; status:"; systemctl --no-pager status mi50-boost-guard.service | head -12
else
    echo "enabled for next boot. Start now with: sudo systemctl start mi50-boost-guard.service"
fi
