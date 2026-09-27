"""Exercise the production storage tree in a PTY, with demo/disposable data."""
import json
from pathlib import Path
import tempfile
import time
from capture_list import Terminal


def demo_capture():
    terminal = Terminal(['--demo'], cols=110, rows=34)
    try:
        terminal.until(b'Partial scan'); terminal.capture('storage')
        terminal.send(b'+'); terminal.capture('storage-depth')
        terminal.send(b'\r'); terminal.capture('storage-zoom')
        terminal.send(b'e'); terminal.capture('storage-effort')
        terminal.send(b'\x1b'); terminal.send(b'2'); terminal.until(b'Cleanup groups')
        terminal.send(b'1'); terminal.until(b'Storage')
    finally:terminal.close()
    terminal = Terminal(['--demo'], cols=60, rows=20)
    try:
        terminal.until(b'Partial scan'); terminal.capture('storage-narrow')
    finally:terminal.close()


def fixture_flow():
    with tempfile.TemporaryDirectory(prefix='diskboard-tree-') as temporary:
        root = Path(temporary).resolve()
        folder = root / 'Project'; folder.mkdir()
        large = folder / 'large-file'; large.write_bytes(b'fixture' * 32768)
        before = large.read_bytes()
        config = root / 'areas.json'; config.write_text('{"version":1,"discovery":false,"areas":[]}')
        terminal = Terminal(['--storage-root', str(root), '--config', str(config)], cols=110, rows=34)
        try:
            terminal.until(b'Project'); terminal.until(b'Measured')
            terminal.send(b't'); terminal.until(b'Added to cleanup groups')
            terminal.send(b'\r'); terminal.until(b'large-file')
            terminal.send(b'+'); terminal.send(b'-')
            terminal.send(b'p'); terminal.until(b'Paused')
            terminal.send(b'm'); terminal.send(b'2'); terminal.until(b'Project')
            terminal.send(b'1'); terminal.until(b'Storage')
        finally:terminal.close()
        data = json.loads(config.with_suffix('.list.json').read_text())
        assert len(data['areas']) == 1 and data['areas'][0]['kind'] == 'inspect'
        assert data['areas'][0]['roots'] == [str(folder)]
        assert large.read_bytes() == before
        assert list(root.glob('.overview-*.json'))
        print('Passed storage PTY flow: overview, depth, zoom, pause, continue, track folder, cleanup-group navigation, cache and preserved files.')


if __name__ == '__main__':
    demo_capture()
    fixture_flow()
