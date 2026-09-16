#!/bin/sh
set -eu
DISKPICK_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export DISKPICK_DIR
/usr/bin/python3 - <<'PY'
import os, shlex
from pathlib import Path
root=Path(os.environ['DISKPICK_DIR'])
target=Path.home()/'.local/bin/diskpick'
target.parent.mkdir(parents=True,exist_ok=True)
for p in target.parents:
    if p.is_symlink():raise SystemExit('Refusing a symlinked install directory')
body='#!/bin/sh\n# diskpick launcher\nexec /usr/bin/python3 -B '+shlex.quote(str(root/'diskpick.py'))+' "$@"\n'
if os.path.lexists(target):
    if target.is_symlink() or target.read_text()!=body:
        raise SystemExit('Existing diskpick differs; not overwritten: '+str(target))
else:
    with target.open('x') as f:f.write(body)
    target.chmod(0o755)
print('Installed: '+str(target))
print('Run: diskpick')
print('If ~/.local/bin is not on PATH, run: ~/.local/bin/diskpick')
PY
