"""Read-only storage tree correctness, bounded work, cache and worker lifecycle."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from diskpick_tree import Scanner, LARGEST_FILES
from diskpick_storage_ui import Overview, read_cache, write_cache


class TreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def file(self, name, size=4096):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x' * size)
        return path

    def finish(self, scanner):
        deadline = time.monotonic() + 8
        while not scanner.finished and time.monotonic() < deadline:
            scanner.advance(seconds=.1)
        self.assertTrue(scanner.finished)

    def test_totals_depth_zoom_and_large_file_rollup(self):
        paths = [self.file('project/cache/file-%02d' % n, (n + 1) * 4096) for n in range(15)]
        self.file('photos/large.raw', 512 * 1024)
        scanner = Scanner(self.root)
        try:
            self.finish(scanner)
            result = scanner.snapshot(depth=1)
            self.assertTrue(result['complete'])
            folders = [r for r in result['rows'] if r['kind'] == 'folder']
            self.assertEqual(folders[0]['name'], 'photos')
            self.assertEqual({r['level'] for r in result['rows']}, {1})
            expected = sum(p.stat().st_blocks * 512 for p in self.root.rglob('*')) + self.root.stat().st_blocks * 512
            self.assertEqual(result['bytes'], expected)
            before = scanner.entries
            detail = scanner.snapshot(focus='project', depth=3)
            self.assertEqual(scanner.entries, before, 'Changing depth must not scan anything')
            files = [r for r in detail['rows'] if r['kind'] == 'file']
            self.assertEqual(len(files), LARGEST_FILES)
            self.assertEqual({r['name'] for r in files}, {'file-%02d' % n for n in range(10, 15)})
            self.assertTrue(any(r['kind'] == 'other' for r in detail['rows']))
            self.assertTrue(all(p.read_bytes() == b'x' * ((n + 1) * 4096) for n, p in enumerate(paths)))
        finally:scanner.close()

    def test_entry_budget_stops_and_resume_completes(self):
        for n in range(300):self.file('folder/file-%d' % n, 0)
        scanner = Scanner(self.root)
        try:
            scanner.advance(max_entries=20, seconds=1)
            self.assertEqual(scanner.entries, 20)
            self.assertFalse(scanner.snapshot()['complete'])
            self.finish(scanner)
            self.assertEqual(scanner.entries, 301)
            self.assertTrue(scanner.snapshot()['complete'])
        finally:scanner.close()

    def test_symlinks_not_followed_hardlinks_count_once_sparse_allocation(self):
        file = self.file('data/file', 8192)
        (self.root / 'cycle').symlink_to(self.root)
        os.link(file, self.root / 'data' / 'hardlink')
        sparse = self.root / 'sparse'
        with sparse.open('wb') as stream:stream.truncate(1024 ** 3)
        scanner = Scanner(self.root)
        try:
            self.finish(scanner)
            snapshot = scanner.snapshot(depth=6)
            self.assertEqual(snapshot['skipped_links'], 1)
            expected = (file.stat().st_blocks + sparse.stat().st_blocks + self.root.stat().st_blocks + (self.root / 'data').stat().st_blocks) * 512
            self.assertEqual(snapshot['bytes'], expected)
            self.assertLess(snapshot['bytes'], 1024 ** 3)
        finally:scanner.close()

    def test_folder_limit_is_visible_and_never_claims_complete(self):
        for n in range(40):self.file('dir-%d/file' % n)
        scanner = Scanner(self.root, max_folders=10)
        try:
            self.finish(scanner)
            snapshot = scanner.snapshot()
            self.assertEqual(len(scanner.nodes), 10)
            self.assertTrue(snapshot['capped'])
            self.assertFalse(snapshot['complete'])
            self.assertGreater(snapshot['issues'], 0)
        finally:scanner.close()

    def test_permission_failure_keeps_partial_totals(self):
        self.file('denied/secret')
        scanner = Scanner(self.root)
        real = os.scandir
        def deny(fd):
            if os.fstat(fd).st_ino == (self.root / 'denied').stat().st_ino:raise PermissionError('Fixture permission denied')
            return real(fd)
        try:
            with patch('diskpick_tree.os.scandir', side_effect=deny):self.finish(scanner)
            result = scanner.snapshot()
            self.assertFalse(result['complete'])
            denied = next(r for r in result['rows'] if r['name'] == 'denied')
            self.assertFalse(denied['complete'])
            self.assertIn('permission denied', denied['reason'])
        finally:scanner.close()

    def test_other_filesystem_is_not_crossed(self):
        from types import SimpleNamespace
        self.file('mounted/data')
        inode = (self.root / 'mounted').stat().st_ino
        original = os.fstat
        scanner = Scanner(self.root)
        def mounted(fd):
            result = original(fd)
            return SimpleNamespace(st_dev=result.st_dev + 1) if result.st_ino == inode else result
        try:
            with patch('diskpick_tree.os.fstat', side_effect=mounted):self.finish(scanner)
            row = next(r for r in scanner.snapshot()['rows'] if r['name'] == 'mounted')
            self.assertFalse(row['complete'])
            self.assertEqual(row['bytes'], 0)
            self.assertIn('filesystem', row['reason'])
        finally:scanner.close()

    def test_worker_failure_is_visible_and_cache_corruption_ignored(self):
        from diskpick_storage_ui import cache_path
        config = self.root / 'areas.json'
        path = cache_path(self.root, config)
        path.write_text('not JSON')
        self.assertIsNone(read_cache(self.root, config))
        path.write_text(json.dumps(dict(version=1,root=str(self.root),rows=[],captured_at=time.time(),bytes=0,entries=0,issues=0,depth=1,elapsed=0)))
        self.assertIsNone(read_cache(self.root, config))
        worker = Overview(self.root / 'missing', config)
        try:
            deadline = time.monotonic() + 3
            while not worker.error and time.monotonic() < deadline:time.sleep(.01)
            self.assertIn('stopped', worker.error)
        finally:worker.close()

    def test_cache_is_timestamped_private_and_does_not_follow_symlinks(self):
        self.file('file')
        config = self.root / 'areas.json'
        scanner = Scanner(self.root)
        try:self.finish(scanner); snapshot = scanner.snapshot()
        finally:scanner.close()
        write_cache(snapshot, config)
        cached = read_cache(self.root, config)
        self.assertTrue(cached['cached'])
        self.assertEqual(cached['bytes'], snapshot['bytes'])
        from diskpick_storage_ui import cache_path
        path = cache_path(self.root, config)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        path.unlink(); path.symlink_to(self.root / 'file')
        write_cache(snapshot, config)
        self.assertEqual((self.root / 'file').read_bytes(), b'x' * 4096)
        self.assertIsNone(read_cache(self.root, config))

    def test_malformed_cache_shapes_and_nonfinite_numbers_are_ignored(self):
        from diskpick_storage_ui import cache_path
        config = self.root / 'areas.json'
        path = cache_path(self.root, config)
        scanner = Scanner(self.root)
        try:self.finish(scanner); valid = scanner.snapshot()
        finally:scanner.close()
        values = [[], None, 'invalid', dict(valid, captured_at=float('nan')),
                  dict(valid, captured_at=1e300), dict(valid, elapsed=float('inf')),
                  dict(valid, bytes=True), dict(valid, depth=99)]
        for value in values:
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                self.assertIsNone(read_cache(self.root, config))

    def test_failed_root_can_choose_a_new_folder_in_the_real_ui(self):
        from capture_list import Terminal
        self.file('keep.txt')
        config = self.root / 'areas.json'
        config.write_text('{"version":1,"discovery":false,"areas":[]}')
        terminal = Terminal(['--storage-root', str(self.root / 'missing'), '--config', str(config)])
        try:
            terminal.until(b'The scan stopped')
            terminal.send(b'o'); terminal.until(b'Scan Home')
            terminal.send(b'\x1bOB\x1bOB\r'); terminal.until(b'Folder path')
            terminal.send(str(self.root).encode() + b'\r')
            terminal.until(b'Measured')
            terminal.until(b'keep.txt')
        finally:terminal.close()
        self.assertEqual((self.root / 'keep.txt').read_bytes(), b'x' * 4096)

    def test_real_worker_view_change_pause_resume_and_exit(self):
        self.file('project/large', 8192)
        config = self.root / 'areas.json'
        worker = Overview(self.root, config)
        try:
            def wait(predicate):
                end = time.monotonic() + 5
                while time.monotonic() < end:
                    if worker.snapshot and predicate(worker.snapshot):return worker.snapshot
                    time.sleep(.02)
                self.fail('Worker did not reach the expected state')
            wait(lambda s:s['finished'])
            worker.send(action='view', focus='project', depth=4)
            result = wait(lambda s:s['focus']=='project' and s['depth']==4)
            self.assertEqual(result['rows'][0]['name'], 'large')
            worker.send(action='pause')
            wait(lambda s:not s['running'])
            worker.send(action='more', effort=0)
        finally:worker.close()
        self.assertIsNotNone(worker.process.poll())
        self.assertIsNotNone(read_cache(self.root, config))


if __name__ == '__main__':unittest.main()
