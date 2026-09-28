"""Run release acceptance with an owned HOME and temp directory, never user defaults."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent

def verify():
    with tempfile.TemporaryDirectory(prefix='diskboard-release-check-') as temporary:
        fixture = Path(temporary).resolve()
        home = fixture / 'home'; home.mkdir()
        scratch = fixture / 'tmp'; scratch.mkdir()
        env = dict(os.environ, HOME=str(home), TMPDIR=str(scratch),
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                   PYTHONDONTWRITEBYTECODE='1')
        for key in ('XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'ZDOTDIR', 'PYTHONPATH'):
            env.pop(key, None)
        for args in (['-m', 'unittest', 'discover', '-v'], ['capture_pty.py'], ['capture_list.py'], ['capture_groups.py'], ['capture_storage.py']):
            subprocess.run([sys.executable, '-B', *args], cwd=ROOT, env=env, check=True)
    print('Release acceptance passed. All tests used a disposable HOME and temporary fixtures.')

if __name__ == '__main__':verify()
