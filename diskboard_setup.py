"""Install the Finder ZIP into a stable user location without sudo."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

PACKAGE = Path(__file__).resolve().parent
MARKER = '.diskboard-installed.json'
PATH_LINE = '\n# DiskBoard command\ncase ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac\n'
OLD_LAUNCHER = re.compile(r'\A#!/bin/sh\n# DiskBoard launcher\nexec .+ -B .+/diskboard\.py "\$@"\n\Z')
OFFICIAL = {'https://github.com/MohammedElmzoudi/DiskBoard.git',
            'https://github.com/MohammedElmzoudi/diskpick.git'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_directory(path, create=False):
    if create:
        try:path.mkdir(mode=0o700)
        except FileExistsError:pass
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise RuntimeError('Unsafe installation folder; kept unchanged: ' + str(path))
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    os.close(fd)


def safe_file(path):
    try:st = path.lstat()
    except FileNotFoundError:return None
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1 or st.st_mode & 0o022:
        raise RuntimeError('Existing file needs manual review; kept unchanged: ' + str(path))
    return path.read_bytes()


def checked_package():
    if PACKAGE.is_symlink():raise RuntimeError('Package folder is a link; no files installed.')
    raw = safe_file(PACKAGE / 'MANIFEST.json')
    if raw is None:raise RuntimeError('The download is incomplete. Download the ZIP again.')
    manifest = json.loads(raw)
    if manifest.get('platform') != 'macOS' or not re.fullmatch(r'0\.2\.0', manifest.get('version', '')):
        raise RuntimeError('The download version does not match its installer.')
    files = manifest.get('files')
    if not isinstance(files, dict) or len(files) < 20 or 'diskboard.py' not in files:
        raise RuntimeError('The download is incomplete. Download the ZIP again.')
    for name, expected in files.items():
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._ -]*', name) or name == MARKER or name in ('.', '..'):
            raise RuntimeError('Invalid package file name.')
        data = safe_file(PACKAGE / name)
        if data is None or digest(data) != expected:
            raise RuntimeError('A download file is missing or changed: ' + name)
    return raw, files


def installed_matches(target, manifest_raw, files):
    if not target.exists() and not target.is_symlink():return False
    safe_directory(target)
    if safe_file(target / MARKER) != manifest_raw:
        raise RuntimeError('Existing DiskBoard files differ; kept unchanged: ' + str(target))
    if {p.name for p in target.iterdir()} != {*files, 'MANIFEST.json', MARKER}:
        raise RuntimeError('Existing DiskBoard folder has extra or missing files; kept unchanged.')
    for name, expected in files.items():
        data = safe_file(target / name)
        if data is None or digest(data) != expected:
            raise RuntimeError('Existing DiskBoard file was changed; kept unchanged: ' + name)
    return True


def launcher_body(app):
    return ('#!/bin/sh\n# DiskBoard packaged launcher\n'
            'for candidate in "$(command -v python3 2>/dev/null || true)" /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do\n'
            '  if [ -n "$candidate" ] && [ -x "$candidate" ] && "$candidate" -c \'import sys; raise SystemExit(sys.version_info < (3,9))\' 2>/dev/null; then\n'
            '    exec "$candidate" -B ' + shlex.quote(str(app)) + ' "$@"\n'
            '  fi\n'
            'done\n'
            'printf "%s\\n" "DiskBoard needs Python 3.9 or later." >&2\nexit 1\n')


def previous_launcher(path, home, app):
    try:st = path.lstat()
    except FileNotFoundError:return 'new'
    if stat.S_ISLNK(st.st_mode):
        official = home / '.local/share/DiskBoard/diskboard.py'
        if path.readlink() == official and (official.parent / '.git').is_dir():
            origin = subprocess.run(['git', '-C', str(official.parent), 'remote', 'get-url', 'origin'],
                                    capture_output=True, text=True, timeout=5)
            if origin.returncode == 0 and origin.stdout.strip() in OFFICIAL:
                return 'git'
        raise RuntimeError('Existing diskboard command is a link; kept unchanged: ' + str(path))
    old = safe_file(path)
    if old is None:return 'new'
    if old == launcher_body(app).encode():
        return 'packaged'
    for installed in (home / '.local/share/DiskBoard-installations').glob('*'):
        if not re.fullmatch(r'\d+\.\d+\.\d+', installed.name):continue
        safe_directory(installed)
        marker = safe_file(installed / MARKER)
        if marker is None:continue
        try:version = json.loads(marker).get('version')
        except (ValueError, AttributeError):continue
        if version == installed.name and old == launcher_body(installed / 'diskboard.py').encode():
            return 'packaged'
    if OLD_LAUNCHER.fullmatch(old.decode('utf-8', 'replace')):
        return 'legacy-package'
    raise RuntimeError('Another diskboard command exists; kept unchanged: ' + str(path))


def write_launcher(path, app):
    body = launcher_body(app)
    old = safe_file(path)
    if old == body.encode():return
    fd, temporary = tempfile.mkstemp(prefix='.diskboard-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(body);stream.flush();os.fsync(stream.fileno())
        os.chmod(temporary, 0o755)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def shell_profiles(home):
    shell = Path(os.environ.get('SHELL') or '/bin/zsh').name
    if shell == 'zsh':
        zdotdir = Path(os.environ.get('ZDOTDIR') or home)
        safe_directory(zdotdir)
        return [zdotdir / '.zprofile', zdotdir / '.zshrc']
    if shell == 'bash':return [home / '.bash_profile', home / '.bashrc']
    raise RuntimeError('Your shell is not zsh or bash. Install stopped before changing files.')


def update_profile(path):
    current = safe_file(path)
    if current is not None and b'# DiskBoard command\n' in current:
        if PATH_LINE.encode() not in current:
            raise RuntimeError('Existing DiskBoard PATH entry differs; kept unchanged: ' + str(path))
        return
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, 'ab') as stream:
        stream.write(PATH_LINE.encode());stream.flush();os.fsync(stream.fileno())


def install():
    if sys.platform != 'darwin' or sys.version_info < (3, 9):
        raise RuntimeError('DiskBoard needs macOS and Python 3.9 or later.')
    if os.geteuid() == 0:raise RuntimeError('Run as your normal user, without sudo.')
    home = Path.home()
    safe_directory(home)
    raw, files = checked_package()
    local = home / '.local';share = local / 'share';releases = share / 'DiskBoard-installations'
    bin_dir = local / 'bin';target = releases / '0.2.0';launcher = bin_dir / 'diskboard'
    for folder in (local, share, releases, bin_dir):
        if folder.exists() or folder.is_symlink():safe_directory(folder)
    profiles = shell_profiles(home)
    for profile in profiles:safe_file(profile)
    if bin_dir.exists():kind = previous_launcher(launcher, home, target / 'diskboard.py')
    else:kind = 'new'
    if kind != 'git' and releases.exists():installed_matches(target, raw, files)
    for folder in (local, share, releases, bin_dir):safe_directory(folder, create=True)
    if kind != 'git' and not installed_matches(target, raw, files):
        staging = Path(tempfile.mkdtemp(prefix='.diskboard-', dir=releases))
        try:
            for name in files:shutil.copy2(PACKAGE / name, staging / name, follow_symlinks=False)
            shutil.copy2(PACKAGE / 'MANIFEST.json', staging / 'MANIFEST.json', follow_symlinks=False)
            (staging / MARKER).write_bytes(raw)
            os.rename(staging, target)
        finally:
            if staging.exists():shutil.rmtree(staging)
        installed_matches(target, raw, files)
    if kind != 'git':write_launcher(launcher, target / 'diskboard.py')
    for profile in profiles:update_profile(profile)
    result = subprocess.run([str(launcher), '--version'], capture_output=True, text=True, timeout=10)
    if result.returncode or 'DiskBoard ' not in result.stdout:
        raise RuntimeError('The command did not start. Check Python, then run this installer again.')
    print('DiskBoard is installed. Open a new Terminal window and type: diskboard')
    if kind == 'git':print('Your existing official Git installation was kept and updates on launch.')
    else:print('Version: ' + result.stdout.strip())


if __name__ == '__main__':
    try:install()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print('Install could not finish: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
