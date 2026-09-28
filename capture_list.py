"""Real PTY acceptance and captures. All paths and settings are disposable fixtures."""
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'evidence/list-ui'


class Terminal:
    def __init__(self, args, cols=100, rows=30, env=None, entry=None):
        self.profile = tempfile.TemporaryDirectory(prefix='diskboard-pty-home-')
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
        self.cols, self.rows = cols, rows
        self.history = b''
        self.process = subprocess.Popen([sys.executable, '-B', str(entry or ROOT / 'diskboard.py'), *args],
                                        stdin=slave, stdout=slave, stderr=slave,
                                        env={**os.environ, 'HOME': self.profile.name, 'TERM': 'xterm-256color', **(env or {})})
        os.close(slave)
        self.read()

    def read(self):
        data = b''
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if select.select([self.master], [], [], .15)[0]:
                try:chunk = os.read(self.master, 65536)
                except OSError:break
                if not chunk:break
                data += chunk
            elif data:
                break
        self.history += data
        return data

    def send(self, keys):
        os.write(self.master, keys)
        return self.read()

    def until(self, value):
        data = self.history
        end = time.monotonic() + 8
        while value not in data and time.monotonic() < end:
            data += self.read()
        assert value in data, (value, data[-6000:])

    def capture(self, name):
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / (name + '.ansi')).write_bytes(self.history)
        (OUT / (name + '.json')).write_text(json.dumps(dict(cols=self.cols, rows=self.rows)))

    def close(self):
        if self.process.poll() is None:
            self.send(b'q')
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=3)
                raise AssertionError('The list did not exit promptly.')
        assert self.process.returncode == 0, self.history[-4000:]
        os.close(self.master)
        self.profile.cleanup()


def demo_capture():
    terminal = Terminal(['--demo', '--groups'])
    try:
        terminal.until(b'Studio'); terminal.read()
        terminal.capture('list')
        terminal.send(b' '); terminal.capture('selected')
        terminal.send(b'c'); terminal.until(b'Review exact paths'); terminal.capture('review')
        terminal.send(b'\x1b'); terminal.send(b'a'); terminal.capture('add')
        terminal.send(b'\x1b'); terminal.send(b'o'); terminal.capture('options')
        terminal.send(b'\x1b')
    finally:terminal.close()
    terminal = Terminal(['--demo', '--groups'], cols=60, rows=20)
    try:
        terminal.until(b'Studio'); terminal.read(); terminal.capture('narrow')
        terminal.send(b'\r\x1bOB\x1bOB\r'); terminal.until(b'On disk'); terminal.capture('details-narrow')
        terminal.send(b'\x1b')
    finally:terminal.close()


def persistence_flow():
    with tempfile.TemporaryDirectory(prefix='diskboard-list-') as temporary:
        root = Path(temporary).resolve()
        folder = root / 'Saved files'; folder.mkdir()
        sentinel = folder / 'keep.txt'; sentinel.write_text('KEEP THIS FILE')
        config = root / 'areas.json'
        config.write_text(json.dumps(dict(version=1, discovery=False, areas=[])))
        args = ['--groups', '--config', str(config)]
        terminal = Terminal(args)
        try:
            terminal.until(b'No cleanup groups yet')
            terminal.send(b'a\x1bOB\x1bOB\r'); terminal.until(b'Folder path')
            terminal.send(str(folder).encode() + b'\r')
            terminal.until(b'Added to your list')
            terminal.send(b'o\r'); terminal.until(b'Rename item')
            terminal.send(b'\x15My project files\r')
            terminal.until(b'Name saved')
            terminal.send(b'/'); terminal.send(b'no-match\r'); terminal.until(b'No matching items')
            terminal.send(b'\x1b')
            terminal.send(b' '); terminal.until(b'No cleanup available')
        finally:terminal.close()
        terminal = Terminal(args)
        try:
            terminal.until(b'My project files')
            terminal.send(b'o\x1bOB\r'); terminal.until(b'Files kept')
            terminal.send(b'u'); terminal.until(b'Item restored')
            terminal.send(b'a\x1bOB\x1bOB\r'); terminal.send(str(root / 'missing').encode() + b'\r')
            terminal.until(b'Could not finish')
            terminal.send(b'\x1b')
            terminal.send(b'a\x1bOB\x1bOB\r'); terminal.send(b'/cancel-this\x1b')
        finally:terminal.close()
        data = json.loads(config.with_suffix('.list.json').read_text())
        assert [a['title'] for a in data['areas']] == ['My project files'], data
        assert data['areas'][0]['kind'] == 'inspect'
        assert sentinel.read_text() == 'KEEP THIS FILE'
        assert json.loads(config.read_text())['areas'] == []
        # The user can intentionally keep an empty list across restarts.
        terminal = Terminal(args)
        try:
            terminal.send(b'o\x1bOB\r'); terminal.until(b'No cleanup groups yet')
        finally:terminal.close()
        terminal = Terminal(args)
        try:terminal.until(b'No cleanup groups yet')
        finally:terminal.close()


def cleanup_flow(entry=None):
    """Real keyboard confirmation and real lsof, only under a disposable HOME."""
    with tempfile.TemporaryDirectory(prefix='diskboard-cleanup-fixture-') as temporary:
        root = Path(temporary).resolve()
        home = root / 'home'; home.mkdir()
        profile = home / 'Library/Caches/ms-playwright-mcp/mcp-chrome-fixture/Default'
        cache = profile / 'Cache'; cache.mkdir(parents=True)
        disposable = cache / 'old-data'; disposable.write_bytes(b'fixture' * 1024)
        cookies = profile / 'Cookies'; cookies.write_text('KEEP COOKIES')
        other = profile / 'Code Cache'; other.mkdir(); (other / 'keep').write_text('KEEP OTHER CACHE')
        for path in (disposable, cache):os.utime(path, (time.time() - 172800,) * 2)
        config = root / 'areas.json'
        config.write_text(json.dumps(dict(version=1, discovery=False, areas=[dict(
            id='fixture-http', title='Fixture HTTP cache', kind='browser', cache='Cache',
            description='Disposable release-test cache')])) )
        terminal = Terminal(['--groups', '--config', str(config)], env={'HOME': str(home)}, entry=entry)
        try:
            terminal.until(b'to review')
            terminal.send(b' c'); terminal.until(b'Review exact paths')
            assert disposable.exists(), 'Preview removed a file'
            terminal.send(b'r'); terminal.until(b'Remove reviewed files?')
            terminal.send(b'\r')  # Cancel is the default confirmation.
            assert disposable.exists(), 'Cancel removed a file'
            terminal.send(b'c'); terminal.until(b'Review exact paths')
            terminal.send(b'r'); terminal.send(b'\x1bOB\r')
            terminal.until(b'Review complete')
            assert not disposable.exists(), 'Confirmed cleanup did not remove the fixture'
            assert cookies.read_text() == 'KEEP COOKIES'
            assert (other / 'keep').read_text() == 'KEEP OTHER CACHE'
            audits = list((home / '.local/state/diskpick').glob('*.jsonl'))
            if not audits:audits = list(home.rglob('*.jsonl'))
            assert audits, 'Cleanup did not retain its audit'
            events = [json.loads(line) for line in audits[-1].read_text().splitlines()]
            assert {e['path'] for e in events if e['event'] == 'completed'} == {str(cache)}
            terminal.send(b'\x1b')
        finally:terminal.close()
        print('Passed real PTY cleanup: preview, default cancel, explicit confirmation, exact fixture cache removed; cookies and other cache preserved; audit verified.')


if __name__ == '__main__':
    demo_capture()
    persistence_flow()
    cleanup_flow()
    print('Passed real PTY flow: list, selection, review cancel, add, rename, filter, restart, remove, undo, invalid path, cancel and persisted empty list. Fixture files preserved.')
