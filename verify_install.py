"""Verify the documented install against public source in a disposable home."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def verify():
    text = (ROOT / 'README.md').read_text()
    command = text.split('copy the complete block:\n\n```sh\n', 1)[1].split('\n```', 1)[0]
    with tempfile.TemporaryDirectory(prefix='diskboard-install-check-') as temporary:
        base = Path(temporary).resolve()
        home = base / 'Home with spaces'; home.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_') and k not in ('ZDOTDIR', 'PYTHONPATH')}
        env.update(HOME=str(home), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0')
        def shell(script):
            return subprocess.run(['/bin/zsh', '-f', '-c', script], cwd=base, env=env, capture_output=True, text=True, timeout=90)
        first = shell(command + ' && diskboard --version')
        assert first.returncode == 0, first.stderr
        checkout = home / '.local/share/DiskBoard'
        before = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], env=env, text=True).strip()
        assert subprocess.check_output(['git', '-C', str(checkout), 'status', '--porcelain'], env=env) == b''
        check = subprocess.run([str(home / '.local/bin/diskboard'), 'check-updates'], env=env, capture_output=True, text=True, timeout=90)
        assert check.returncode == 0 and 'up to date' in check.stdout, check.stdout + check.stderr
        assert shell('source "$HOME/.zshrc" && diskboard --version').stdout == first.stdout
        settings = (home / '.zshrc').read_bytes()
        assert shell(command).returncode != 0
        assert (home / '.zshrc').read_bytes() == settings
        # Guard existing files, real directories and dangling command links before cloning.
        for kind in ('file', 'directory', 'symlink'):
            other = base / kind; (other / '.local/bin').mkdir(parents=True)
            target = other / '.local/bin/diskboard'
            if kind == 'file':target.write_text('KEEP')
            elif kind == 'directory':target.mkdir()
            else:target.symlink_to(other / 'absent')
            env['HOME'] = str(other)
            assert shell(command).returncode != 0
            assert not (other / '.local/share/DiskBoard').exists()
            assert not (other / '.zshrc').exists()
            if kind == 'file':assert target.read_text() == 'KEEP'
            elif kind == 'directory':assert not list(target.iterdir())
            else:assert target.is_symlink()
        print('Public install passed: version, clean checkout, update check, new-shell PATH, repeat guard and existing-command protection.')
        print('Public source verified:', before, '/', first.stdout.strip())


if __name__ == '__main__':verify()
