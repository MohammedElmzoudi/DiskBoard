"""Persistence, boundary and real cleanup integration for the saved-list interface."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import diskpick_catalog as catalog
import diskpick_cli as cli
import diskpick_engine as engine
import diskpick_list as model
import diskpick_list_ui as view


class SavedListTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.folder = self.root / 'My folder'
        self.folder.mkdir()
        self.config = self.root / 'areas.json'
        self.config.write_text('{"version":1,"areas":[]}')
        self.area = model.folder_area(str(self.folder))

    def test_folder_is_inspect_only_and_reopens_with_renamed_title(self):
        saved = model.SavedList([], self.config)
        saved.add(self.area)
        saved.replace([dict(self.area, title='My project')])
        reopened = model.SavedList([], self.config)
        self.assertEqual(reopened.areas[0]['title'], 'My project')
        self.assertEqual(reopened.areas[0]['kind'], 'inspect')
        self.assertEqual(reopened.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(self.config.read_text())['areas'], [])
        cleaner = Mock()
        catalog.execute_handler(reopened.areas[0], cleaner)
        self.assertEqual(cleaner.mock_calls, [])

    def test_empty_saved_list_does_not_repopulate(self):
        saved = model.SavedList([self.area], self.config)
        saved.replace([])
        self.assertEqual(model.SavedList([self.area], self.config).areas, [])
        self.assertTrue(self.folder.is_dir())

    def test_undo_does_not_lose_later_adds_or_renames(self):
        saved = model.SavedList([self.area], self.config)
        saved.replace([])
        second = dict(self.area, id='second', title='Another item', roots=[str(self.root)])
        saved.add(second)
        saved.replace([dict(second, title='Renamed later')])
        saved.restore(self.area, 0)
        self.assertEqual([a['title'] for a in saved.areas], ['My folder', 'Renamed later'])

    def test_concurrent_window_cannot_overwrite_list(self):
        a = model.SavedList([], self.config)
        b = model.SavedList([], self.config)
        a.add(self.area)
        with self.assertRaisesRegex(ValueError, 'another window'):
            b.replace([])
        self.assertEqual(model.SavedList([], self.config).areas, [self.area])

    def test_duplicate_folder_and_aliases_rejected(self):
        saved = model.SavedList([], self.config)
        saved.add(self.area)
        with self.assertRaisesRegex(ValueError, 'already'):
            saved.add(dict(self.area, id='another-id'))
        self.assertEqual(model.folder_area('"' + str(self.folder) + '"'), self.area)
        for value in ['', 'relative/folder', str(self.root / 'missing'), str(self.folder / '..')]:
            with self.subTest(value=value), self.assertRaises((ValueError, OSError, engine.Unsafe)):
                model.folder_area(value)

    def test_folder_symlink_is_not_followed(self):
        link = self.root / 'link'
        link.symlink_to(self.folder)
        with self.assertRaises(OSError):model.folder_area(str(link))

    def test_malformed_or_linked_settings_not_replaced(self):
        saved = model.SavedList([], self.config)
        saved.path.write_text('not JSON')
        with self.assertRaises(ValueError):model.SavedList([], self.config)
        with self.assertRaises(ValueError):saved.replace([])
        self.assertEqual(saved.path.read_text(), 'not JSON')
        saved.path.unlink()
        saved.path.symlink_to(self.config)
        with self.assertRaises(OSError):model.SavedList([], self.config)
        self.assertEqual(json.loads(self.config.read_text())['areas'], [])

    def test_hardlink_and_duplicate_keys_are_rejected(self):
        saved = model.SavedList([], self.config)
        os.link(self.config, saved.path)
        with self.assertRaises(ValueError):model.SavedList([], self.config)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            model.decode(b'{"version":1,"areas":[],"areas":[]}')
        with self.assertRaises(ValueError):model.decode(b'[]')

    def test_symlinked_settings_directory_rejected(self):
        link = self.root / 'linked'; link.symlink_to(self.folder)
        with self.assertRaises(OSError):model.SavedList([], link / 'areas.json')

    def test_demo_never_writes_settings(self):
        saved = model.SavedList([], self.config, demo=True)
        saved.add(self.area)
        self.assertFalse(saved.path.exists())


class ListReviewTests(unittest.TestCase):
    def screen(self, confirmation=0):
        screen = Mock()
        screen.wait.side_effect = lambda title, fn, **kw: fn()
        screen.menu.return_value = confirmation
        return screen

    def test_fresh_blocked_or_unmeasured_row_cannot_reach_cleaner(self):
        area = view.demo_areas()[0]
        for row in [dict(view.demo_scan([area])[0], eligible_bytes=0, preview=[], reasons=['Active work']),
                    dict(view.demo_scan([area])[0], total_bytes=None, reasons=['Failed size check'])]:
            clean = Mock()
            with patch.object(view, 'document', return_value=True):
                self.assertFalse(view.review_selected(self.screen(1), [area], lambda *a: [row], clean))
            clean.assert_not_called()

    def test_cancel_preview_or_confirmation_never_cleans(self):
        area = view.demo_areas()[0]
        for preview, confirm in [(False, 1), (True, 0), (True, None)]:
            clean = Mock()
            with patch.object(view, 'document', return_value=preview):
                self.assertFalse(view.review_selected(self.screen(confirm), [area], view.demo_scan, clean))
            clean.assert_not_called()

    def test_monitor_cannot_become_removable_from_size_metadata(self):
        row = view.demo_scan([next(a for a in view.demo_areas() if a['kind'] == 'inspect')])[0]
        row.update(eligible_bytes=1024, preview=[dict(path='/demo/do-not-delete')])
        self.assertFalse(view.removable(row))

    def test_background_scan_returns_immediately_and_reports_failure(self):
        gate = threading.Event()
        area = view.demo_areas()[0]
        def scan(*args):
            gate.wait(2)
            raise PermissionError('Fixture denied')
        started = time.monotonic()
        measurements = view.Measurements([area], scan)
        self.assertLess(time.monotonic() - started, .5)
        self.assertEqual(measurements.pending, {area['id']})
        gate.set()
        end = time.monotonic() + 2
        while measurements.pending and time.monotonic() < end:
            measurements.poll(); time.sleep(.01)
        row = measurements.rows[area['id']]
        self.assertEqual(view.state_label(row), 'Check failed')
        self.assertFalse(view.removable(row))

    def test_isolated_measurement_reads_fixture_and_can_be_stopped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'keep.txt').write_text('KEEP')
            area = model.folder_area(str(root))
            measurements = view.Measurements([area], cli.scan, isolate=True)
            try:
                end = time.monotonic() + 5
                while measurements.pending and time.monotonic() < end:
                    measurements.poll(); time.sleep(.01)
                self.assertFalse(measurements.pending)
                self.assertGreater(measurements.rows[area['id']]['total_bytes'], 0)
                self.assertEqual((root / 'keep.txt').read_text(), 'KEEP')
            finally:
                measurements.close()
            self.assertIsNotNone(measurements.process.poll())
            # Cancelling a just-started worker must also reap it, without waiting
            # for the scanner's per-directory timeout.
            measurements = view.Measurements([area], cli.scan, isolate=True)
            started = time.monotonic()
            measurements.close()
            self.assertLess(time.monotonic() - started, 2)
            self.assertIsNotNone(measurements.process.poll())

    def test_real_cleanup_removes_only_the_reviewed_fixture_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            profiles = root / 'profiles'
            profile = profiles / 'mcp-chrome-fixture'
            for name in ('Cache', 'Code Cache'):
                folder = profile / 'Default' / name
                folder.mkdir(parents=True)
                (folder / 'disposable').write_text('fixture data')
                os.utime(folder / 'disposable', (time.time() - 172800,) * 2)
                os.utime(folder, (time.time() - 172800,) * 2)
            (profile / 'Default' / 'Cookies').write_text('KEEP')
            area = dict(id='fixture-http', title='Fixture HTTP', description='Fixture only', kind='browser', cache='Cache')
            def fixture_handler(a, cleaner):
                cleaner.browsers(profiles, cache_names=(a['cache'],))
            def fixture_clean(areas, paths):
                self.assertEqual(paths, {str(profile / 'Default' / 'Cache')})
                self.assertTrue(all(root in Path(path).parents for path in paths))
                return cli.clean(areas, paths)
            with patch.object(cli, 'STATE', root / 'audit'), patch.object(engine, 'PROFILES', profiles):
                with patch.object(catalog, 'execute_handler', side_effect=fixture_handler), patch.object(view, 'document', return_value=True):
                    self.assertTrue(view.review_selected(self.screen(1), [area], cli.scan, fixture_clean))
            self.assertEqual(list((profile / 'Default' / 'Cache').iterdir()), [])
            self.assertTrue((profile / 'Default' / 'Code Cache' / 'disposable').exists())
            self.assertEqual((profile / 'Default' / 'Cookies').read_text(), 'KEEP')
            self.assertTrue(list((root / 'audit').glob('*.jsonl')))


if __name__ == '__main__':
    unittest.main()
