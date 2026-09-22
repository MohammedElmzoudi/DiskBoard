#!/bin/sh
set -eu
DISKPICK_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export DISKPICK_DIR
DISKPICK_PYTHON=''
for DISKPICK_CANDIDATE in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if [ -x "$DISKPICK_CANDIDATE" ] && "$DISKPICK_CANDIDATE" -c 'import sys; raise SystemExit(sys.version_info < (3,9))' 2>/dev/null; then
        DISKPICK_PYTHON=$DISKPICK_CANDIDATE
        break
    fi
done
if [ -z "$DISKPICK_PYTHON" ]; then
    printf '%s\n' 'Install Python 3.9 or later, then run this installer again.'
    exit 1
fi
export DISKPICK_PYTHON
"$DISKPICK_PYTHON" -B - <<'PY'
import os, shlex, stat, sys
from pathlib import Path
root=Path(os.environ['DISKPICK_DIR'])
sys.path.insert(0,str(root))
from diskpick_engine import directory
if os.geteuid()==0:raise SystemExit('Run this installer as your normal user, without sudo.')
target=Path.home()/'.local/bin/diskboard'
body='#!/bin/sh\n# DiskBoard launcher\nexec '+shlex.quote(os.environ['DISKPICK_PYTHON'])+' -B '+shlex.quote(str(root/'diskboard.py'))+' "$@"\n'
with directory(Path.home()) as home:
    fd=os.dup(home)
    try:
        for name in ('.local','bin'):
            try:os.mkdir(name,0o700,dir_fd=fd)
            except FileExistsError:pass
            nxt=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=nxt
            st=os.fstat(fd)
            if st.st_uid!=os.getuid() or st.st_mode&0o022:raise SystemExit('Install directory is writable by another user; kept unchanged.')
        try:existing=os.open('diskboard',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        except FileNotFoundError:existing=None
        if existing is not None:
            with os.fdopen(existing) as stream:
                st=os.fstat(stream.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_uid!=os.getuid() or st.st_size>16384 or stream.read()!=body:
                    raise SystemExit('Existing diskboard differs; not overwritten: '+str(target))
        else:
            out=os.open('diskboard',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o755,dir_fd=fd)
            with os.fdopen(out,'w') as stream:stream.write(body);stream.flush();os.fsync(stream.fileno())
            os.fsync(fd)
    finally:os.close(fd)
print('Installed: '+str(target))
print('Run: diskboard')
print('If ~/.local/bin is not on PATH, run: ~/.local/bin/diskboard')
PY
