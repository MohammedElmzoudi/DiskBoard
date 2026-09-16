import contextlib
import fcntl
import io
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import diskpick_engine as s


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.free = mock.patch.object(s, 'free_bytes', return_value=10*s.GIB)
        self.free.start()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        self.free.stop()
        self.temp.cleanup()

    def old(self, p):
        for node in sorted(p.rglob('*'), key=lambda n: -len(n.parts)) + [p]:
            if not node.is_symlink():
                os.utime(node, (time.time()-172800, time.time()-172800))

    def tree(self, name='cache'):
        p = self.root/name
        (p/'nested').mkdir(parents=True)
        (p/'nested/artifact').write_text('generated')
        self.old(p)
        return p

    def rust_fixture(self):
        debug = self.root/'debug'
        crate = debug/'incremental/bento_board-abc'
        crate.mkdir(parents=True)
        (debug/'.cargo-lock').touch()
        for name in ('s-100-abc-old', 's-200-abc-new', 's-300-abc-working'):
            p = crate/name
            p.mkdir()
            for n in ('query-cache.bin', 'dep-graph.bin', 'work-products.bin'):
                (p/n).write_text(name)
            (crate/('-'.join(name.split('-')[:3])+'.lock')).touch()
            self.old(p)
        return debug, crate

    def test_preview_does_not_modify_tree(self):
        p = self.tree()
        with s.directory(p) as fd:
            before = s.snapshot(fd)
        with mock.patch.object(s, 'idle'):
            s.Cleaner().clean_tree(p, p, 3600)
        with s.directory(p) as fd:
            self.assertEqual(before, s.snapshot(fd))

    def test_apply_only_removes_contents_leaves_root_and_sibling(self):
        p = self.tree()
        sibling = self.root/'important.txt'
        sibling.write_text('KEEP')
        with mock.patch.object(s, 'idle'):
            s.Cleaner(True).clean_tree(p, p, 3600)
        self.assertEqual(list(p.iterdir()), [])
        self.assertEqual(sibling.read_text(), 'KEEP')

    def test_symlink_root_is_rejected(self):
        p = self.tree()
        link = self.root/'redirect'
        link.symlink_to(p)
        with self.assertRaises(OSError):
            with s.directory(link):
                pass
        self.assertTrue((p/'nested/artifact').exists())

    def test_symlink_ancestor_is_rejected(self):
        p = self.tree()
        link = self.root/'redirect'
        link.symlink_to(p)
        with self.assertRaises(OSError):
            with s.directory(link/'nested'):
                pass

    def test_symlink_inside_tree_aborts_whole_tree(self):
        p = self.tree()
        victim = self.root/'personal'
        victim.write_text('KEEP')
        (p/'link').symlink_to(victim)
        with mock.patch.object(s, 'idle'), self.assertRaises(s.Unsafe):
            s.Cleaner(True).clean_tree(p, p, 0)
        self.assertEqual(victim.read_text(), 'KEEP')
        self.assertTrue((p/'nested/artifact').exists())

    def test_special_file_rejected(self):
        p = self.tree()
        os.mkfifo(p/'fifo')
        with s.directory(p) as fd, self.assertRaises(s.Unsafe):
            s.snapshot(fd)

    def test_recent_file_preserves_entire_cache(self):
        p = self.tree()
        (p/'new').write_text('recent')
        with self.assertRaises(s.Unsafe):
            s.Cleaner(True).clean_tree(p, p, 3600)
        self.assertTrue((p/'nested/artifact').exists())

    def test_active_directory_preserved(self):
        p = self.tree()
        with mock.patch.object(s, 'idle', side_effect=s.Unsafe('busy')):
            with self.assertRaises(s.Unsafe):
                s.Cleaner(True).clean_tree(p, p, 3600)
        self.assertTrue((p/'nested/artifact').exists())

    def test_changed_file_after_activity_check_preserved(self):
        p = self.tree()
        calls = []
        def change(*args):
            calls.append(1)
            if len(calls) == 2:
                (p/'nested/artifact').write_text('changed')
        with mock.patch.object(s, 'idle', side_effect=change), self.assertRaises(s.Unsafe):
            s.Cleaner(True).clean_tree(p, p, 3600)
        self.assertEqual((p/'nested/artifact').read_text(), 'changed')

    def test_audit_failure_prevents_deletion(self):
        p = self.tree()
        def log(event, **kw):
            if event == 'intent':
                raise OSError('disk full')
        with mock.patch.object(s, 'idle'), self.assertRaises(OSError):
            s.Cleaner(True, log).clean_tree(p, p, 3600)
        self.assertTrue((p/'nested/artifact').exists())

    def test_hardlinked_installed_file_is_not_modified(self):
        p = self.tree()
        installed = self.root/'installed'
        os.link(p/'nested/artifact', installed)
        with mock.patch.object(s, 'idle'):
            s.Cleaner(True).clean_tree(p, p, 3600)
        self.assertEqual(installed.read_text(), 'generated')

    def test_existing_exclusive_lock_blocks_second_holder(self):
        p = self.root/'lock'
        p.touch()
        with s.locked_file(p), self.assertRaises(BlockingIOError):
            with s.locked_file(p):
                self.fail('acquired busy lock')

    def test_missing_lock_is_not_created(self):
        p = self.root/'absent'
        with self.assertRaises(FileNotFoundError):
            with s.locked_file(p):
                pass
        self.assertFalse(p.exists())

    def test_lock_symlink_rejected(self):
        p = self.root/'lock'
        p.touch()
        link = self.root/'link'
        link.symlink_to(p)
        with self.assertRaises(OSError):
            with s.locked_file(link):
                pass

    def test_only_old_session_cleaned_newest_and_working_retained(self):
        debug, crate = self.rust_fixture()
        with mock.patch.object(s, 'processes', return_value=(set(), '')), mock.patch.object(s, 'idle'):
            s.Cleaner(True).rust(debug)
        self.assertEqual(list((crate/'s-100-abc-old').iterdir()), [])
        self.assertTrue((crate/'s-200-abc-new/query-cache.bin').exists())
        self.assertTrue((crate/'s-300-abc-working/query-cache.bin').exists())

    def test_active_compiler_preserves_every_session(self):
        debug, crate = self.rust_fixture()
        with mock.patch.object(s, 'processes', return_value=({'rustc'}, '')):
            s.Cleaner(True).rust(debug)
        self.assertTrue((crate/'s-100-abc-old/query-cache.bin').exists())

    def test_busy_session_lock_preserves_old_session(self):
        debug, crate = self.rust_fixture()
        with s.locked_file(crate/'s-100-abc.lock'):
            with mock.patch.object(s, 'processes', return_value=(set(), '')):
                s.Cleaner(True).rust(debug)
        self.assertTrue((crate/'s-100-abc-old/query-cache.bin').exists())

    def test_incomplete_newest_preserves_old(self):
        debug, crate = self.rust_fixture()
        (crate/'s-200-abc-new/query-cache.bin').unlink()
        with mock.patch.object(s, 'processes', return_value=(set(), '')):
            s.Cleaner(True).rust(debug)
        self.assertTrue((crate/'s-100-abc-old/query-cache.bin').exists())

    def test_browser_credentials_and_other_profiles_preserved(self):
        base = self.root/'profiles'
        profile = base/'mcp-chrome-abc'
        (profile/'Default/Cache').mkdir(parents=True)
        (profile/'Default/Cache/data').write_text('cache')
        (profile/'Default/Cookies').write_text('KEEP')
        (profile/'Default/Local Storage').mkdir()
        (profile/'Default/Local Storage/database').write_text('KEEP')
        self.old(profile)
        with mock.patch.object(s, 'idle'):
            s.Cleaner(True).browsers(base)
        self.assertEqual(list((profile/'Default/Cache').iterdir()), [])
        self.assertEqual((profile/'Default/Cookies').read_text(), 'KEEP')
        self.assertEqual((profile/'Default/Local Storage/database').read_text(), 'KEEP')

    def test_lsof_failure_is_not_interpreted_as_idle(self):
        with mock.patch.object(s, 'processes', return_value=(set(), '')):
            for rc, stdout, stderr in [(0, 'open', ''), (1, '', 'denied'), (2, '', '')]:
                result = subprocess.CompletedProcess([], rc, stdout, stderr)
                with mock.patch.object(s, 'run', return_value=result), self.assertRaises(s.Unsafe):
                    s.idle(self.root)

    def test_lsof_timeout_fails_closed(self):
        with mock.patch.object(s, 'processes', return_value=(set(), '')):
            with mock.patch.object(s, 'run', side_effect=subprocess.TimeoutExpired('lsof', 30)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    s.idle(self.root)

    def test_process_reference_blocks_cache(self):
        with mock.patch.object(s, 'processes', return_value=(set(), 'chrome '+str(self.root))):
            with self.assertRaises(s.Unsafe):
                s.idle(self.root)

    def test_traversal_rejected(self):
        with self.assertRaises(s.Unsafe):
            with s.directory(self.root/'..'):
                pass

    def test_newest_empty_metadata_preserves_old(self):
        debug, crate = self.rust_fixture()
        (crate/'s-200-abc-new/query-cache.bin').write_bytes(b'')
        with mock.patch.object(s, 'processes', return_value=(set(), '')):
            s.Cleaner(True).rust(debug)
        self.assertTrue((crate/'s-100-abc-old/query-cache.bin').exists())


if __name__ == '__main__':
    unittest.main()
