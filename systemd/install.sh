#!/usr/bin/env bash
# Install (or refresh) the user units. Idempotent. `--uninstall` removes them.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.config/systemd/user"
mkdir -p "$DEST"
if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl --user disable --now local-connect-status.timer local-connect-status-heavy.timer local-connect-status-web.service 2>/dev/null || true
  rm -f "$DEST"/local-connect-status.service "$DEST"/local-connect-status.timer \
        "$DEST"/local-connect-status-heavy.service "$DEST"/local-connect-status-heavy.timer \
        "$DEST"/local-connect-status-web.service
  systemctl --user daemon-reload
  echo "removed"; exit 0
fi
cp "$HERE"/local-connect-status.service "$HERE"/local-connect-status.timer \
   "$HERE"/local-connect-status-heavy.service "$HERE"/local-connect-status-heavy.timer \
   "$HERE"/local-connect-status-web.service "$DEST"/
systemctl --user daemon-reload
systemctl --user enable --now local-connect-status.timer local-connect-status-heavy.timer local-connect-status-web.service
systemctl --user list-timers --no-pager | grep local-connect-status || true
echo "dashboard: http://127.0.0.1:8790/"
