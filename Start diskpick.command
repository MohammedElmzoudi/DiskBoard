#!/bin/sh
set -eu
DISKPICK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$DISKPICK_ROOT"
# The launcher does not install software or edit shell settings.
for DISKPICK_PYTHON in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if [ -x "$DISKPICK_PYTHON" ] && "$DISKPICK_PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 9))' 2>/dev/null; then
        exec "$DISKPICK_PYTHON" -B "$DISKPICK_ROOT/diskpick.py" "$@"
    fi
done
printf '%s\n' 'diskpick needs Python 3.9 or later.' 'Install Python, then open this file again.' 'See START-HERE.md for requirements and setup.'
if [ -t 0 ]; then read -r DISKPICK_REPLY; fi
exit 1
