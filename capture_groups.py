"""Complete age-group acceptance using real PTY input and disposable files only."""
import json
import os
from pathlib import Path
import tempfile
import time
from capture_list import Terminal

DOWN = b'\x1bOB'


def flow(entry=None):
    with tempfile.TemporaryDirectory(prefix='diskboard-age-flow-') as temporary:
        root = Path(temporary).resolve()
        home = root / 'home'; home.mkdir()
        folders = [home / 'Downloads', home / 'Exports']
        for folder in folders:folder.mkdir()
        olds = []
        for folder in folders:
            old = folder / 'old.txt'; old.write_text('OLD DISPOSABLE FIXTURE')
            os.utime(old, (time.time() - 11 * 86400,) * 2); olds.append(old)
            (folder / 'recent.txt').write_text('KEEP RECENT FILE')
        config = root / 'areas.json'
        config.write_text('{"version":1,"discovery":false,"areas":[]}')
        args = ['--config', str(config)]
        env = {'HOME': str(home)}
        t = Terminal(args, env=env, entry=entry)
        try:
            t.until(b'No cleanup groups yet')
            t.send(b'a\r'); t.until(b'Group name')
            t.send(b'Downloads and exports\r')
            for folder in folders:
                t.send(DOWN + b'\r'); t.until(b'Folder path')
                t.send(str(folder).encode() + b'\r')
            t.until(b'2 folders'); t.capture('age-group-editor')
            t.send(b'\r'); t.until(b'Added to your list'); t.until(b'2 files')
            t.capture('age-groups')
        finally:t.close()
        saved = json.loads(config.with_suffix('.list.json').read_text())['areas']
        assert len(saved) == 1 and saved[0]['older_than_days'] == 10
        assert saved[0]['roots'] == [str(p) for p in folders]
        t = Terminal(args, env=env, entry=entry)
        try:
            t.until(b'Downloads and exports'); t.until(b'2 files')
            # Change the persisted rule through the real editor, then restore 10 days.
            for days in (15, 10):
                t.send(b'\r' + DOWN + b'\r'); t.until(b'Include subfolders')
                t.send(DOWN + DOWN + b'\r'); t.send(b'\x15' + str(days).encode() + b'\r')
                t.send(b'\r'); t.until(b'Group saved')
                if days == 15:
                    t.send(b'c'); t.until(b'Nothing to remove'); t.send(b'\x1b')
            # Click the Review toolbar action using an actual terminal mouse report.
            t.send(b'\x1b[M' + bytes([32, 48, 36])); t.until(b'Review exact paths'); t.capture('age-review')
            assert all(p.exists() for p in olds)
            t.send(b'r'); t.until(b'Remove reviewed files?')
            t.send(b'\r')  # Default Cancel keeps every file.
            assert all(p.exists() for p in olds)
            t.send(b'c'); t.send(b'r'); t.send(DOWN + b'\r')
            t.until(b'2 cleanup operations completed'); t.capture('age-result')
            assert all(not p.exists() for p in olds)
            assert all((p / 'recent.txt').read_text() == 'KEEP RECENT FILE' for p in folders)
            t.send(b'\x1b'); t.send(b'c'); t.until(b'Nothing to remove'); t.send(b'\x1b')
        finally:t.close()
        audit = list((home / '.local/state/diskpick').glob('*.jsonl'))
        assert audit and sum('"event": "completed"' in line for p in audit for line in p.read_text().splitlines()) == 2
        print('PASS: default groups, two-folder age rule, persistence, preview, cancel, exact real deletion, recent files kept, audit, empty result.')


if __name__ == '__main__':flow()
