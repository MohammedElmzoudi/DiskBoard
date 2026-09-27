#!/bin/sh
# The ZIP shows this one file in Finder; application files live in .diskboard.
set -u
DISKBOARD_PACKAGE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$(uname -s)" != Darwin ]; then
    printf '%s\n' 'DiskBoard currently requires macOS.'
    DISKBOARD_STATUS=1
else
    DISKBOARD_PYTHON=''
    for DISKBOARD_CANDIDATE in "$(command -v python3 2>/dev/null || true)" /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
        if [ -n "$DISKBOARD_CANDIDATE" ] && [ -x "$DISKBOARD_CANDIDATE" ] && "$DISKBOARD_CANDIDATE" -c 'import sys; raise SystemExit(sys.version_info < (3,9))' 2>/dev/null; then
            DISKBOARD_PYTHON=$DISKBOARD_CANDIDATE
            break
        fi
    done
    if [ -z "$DISKBOARD_PYTHON" ]; then
        printf '%s\n' 'DiskBoard needs Python 3.9 or later. Install Python, then double-click this file again.'
        DISKBOARD_STATUS=1
    else
        "$DISKBOARD_PYTHON" -B "$DISKBOARD_PACKAGE/.diskboard/diskboard_setup.py"
        DISKBOARD_STATUS=$?
    fi
fi
if [ -t 0 ]; then
    printf '\nPress Return to close this window.'
    read -r DISKBOARD_REPLY
fi
exit "$DISKBOARD_STATUS"
