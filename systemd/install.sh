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
DATA_ROOT="${LCSTATUS_DATA_ROOT:-$HOME/.local/share/local-connect-status}"
export LCSTATUS_DATA_ROOT="$DATA_ROOT"
export PYTHONDONTWRITEBYTECODE=1
DATA_ROOT="$(PYTHONPATH="$HERE/.." python3 - <<'PYROOT'
import os
from lcstatus.paths import Paths
root = os.environ['LCSTATUS_DATA_ROOT']
if root != root.strip() or any(ord(c) < 32 or ord(c) == 127 or c in ('\\', '"') for c in root):
    raise SystemExit('unit data root refuses surrounding whitespace and control characters, backslash, or double quote')
print(Paths.configured().root)
PYROOT
)"
# systemd specifiers and quoted paths are escaped by its unit grammar, never shell evaluation.
export LCSTATUS_DATA_ROOT="$DATA_ROOT"
PYTHONPATH="$HERE/.." python3 - "$HERE" "$DEST" <<'PYUNIT'
import os, sys
from pathlib import Path
source, destination = map(Path, sys.argv[1:])
raw_root = os.environ['LCSTATUS_DATA_ROOT'].replace('%', '%%')
root = raw_root.replace('\\', '\\\\').replace('"', '\\"')
for name in ('local-connect-status.service', 'local-connect-status-heavy.service',
             'local-connect-status-web.service', 'local-connect-status.timer',
             'local-connect-status-heavy.timer'):
    unit = source / name
    text = unit.read_text()
    lines = []
    for line in text.splitlines():
        key, sep, value = line.partition('=')
        if key == 'WorkingDirectory':
            value = value.replace('%h/.local/share/local-connect-status', raw_root)
        else:
            value = value.replace('%h/.local/share/local-connect-status', root)
        if key == 'Environment' and value.startswith('LCSTATUS_DATA_ROOT='): value = '"' + value + '"'
        if key == 'ExecStart':
            command, args = value.split(' -m ', 1)
            value = '"' + command + '" -m ' + args
            value = value.replace('--directory ' + root + '/site', '--directory "' + root + '/site"')
        if key == 'ExecStartPre': value = '/usr/bin/mkdir -p "' + root + '/site"'
        lines.append(key + sep + value)
    (destination / unit.name).write_text('\n'.join(lines) + '\n')
PYUNIT
systemctl --user daemon-reload
if [[ "${1:-}" == "--no-start" ]]; then
  systemctl --user enable local-connect-status.timer local-connect-status-heavy.timer local-connect-status-web.service
  echo "units installed without starting; bootstrap before starting them"
  exit 0
fi
if [[ ! -x "$DATA_ROOT/current/.venv/bin/python" ]]; then
  echo "no tested current release; use --no-start, then bootstrap" >&2; exit 1
fi
systemctl --user enable --now local-connect-status.timer local-connect-status-heavy.timer local-connect-status-web.service
systemctl --user list-timers --no-pager | grep local-connect-status || true
echo "dashboard: http://127.0.0.1:8790/"
