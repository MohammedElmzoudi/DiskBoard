import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import diskpick_age as age
import diskpick_catalog as catalog
import diskpick_engine as engine
from diskpick_list import cleanup_area, SavedList


class AgeGroupsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.a = self.root / 'Folder A'; self.a.mkdir()
        self.b = self.root / 'Folder B'; self.b.mkdir()
        self.old = self.make(self.a / 'old.txt', 11)
        self.recent = self.make(self.a / 'recent.txt', 2)
        self.other = self.make(self.b / 'older.txt', 20)
        self.area = cleanup_area([str(self.a), str(self.b)], 'Exports', 10)

    def make(self, path, days):
        path.write_text('fixture')
        stamp = time.time() - days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def apply(self, preview):
        events = []
        cleaner = engine.Cleaner(True, lambda event, **kw: events.append(dict(event=event, **kw)))
        cleaner.quiet = True
        cleaner.allowed_paths = {p['path']: p for p in preview}
        with patch.object(engine, 'idle'):
            age.execute(self.area, cleaner)
        return events

    def test_multiple_roots_filter_and_exact_delete(self):
        row = catalog.scan(self.area)
        self.assertEqual({p['path'] for p in row['preview']}, {str(self.old), str(self.other)})
        self.assertTrue(self.old.exists())  # preview is non-destructive
        events = self.apply(row['preview'])
        self.assertFalse(self.old.exists()); self.assertFalse(self.other.exists())
        self.assertTrue(self.recent.exists())
        self.assertEqual(sum(e['event'] == 'completed' for e in events), 2)
        self.assertEqual(sum(e['event'] == 'intent' for e in events), 2)
        self.assertTrue(self.a.exists()); self.assertTrue(self.b.exists())

    def test_changed_and_new_files_are_not_deleted(self):
        preview = age.inspect(self.area)['preview']
        self.old.write_text('changed after review')
        unseen = self.make(self.a / 'unseen.txt', 30)
        self.apply(preview)
        self.assertTrue(self.old.exists()); self.assertTrue(unseen.exists())
        self.assertFalse(self.other.exists())

    def test_parent_replacement_and_links_are_preserved(self):
        sub = self.a / 'nested'; sub.mkdir()
        old = self.make(sub / 'old.txt', 30)
        linked = self.a / 'linked'; linked.symlink_to(self.other)
        hard = self.a / 'hard'; os.link(self.other, hard)
        preview = age.inspect(self.area)['preview']
        self.assertNotIn(str(linked), [p['path'] for p in preview])
        self.assertNotIn(str(hard), [p['path'] for p in preview])
        moved = self.a / 'moved'; sub.rename(moved); sub.mkdir()
        replacement = self.make(sub / 'old.txt', 30)
        self.apply(preview)
        self.assertTrue(replacement.exists()); self.assertTrue((moved / 'old.txt').exists())
        self.assertTrue(self.other.exists())

    def test_recursive_rule_and_git_checkout_exclusion(self):
        sub = self.a / 'nested'; sub.mkdir(); nested = self.make(sub / 'old', 30)
        self.area['recursive'] = False
        self.assertNotIn(str(nested), [p['path'] for p in age.inspect(self.area)['preview']])
        self.area['recursive'] = True
        (sub / '.git').mkdir()
        self.assertNotIn(str(nested), [p['path'] for p in age.inspect(self.area)['preview']])

    def test_bounded_inspection_preserves_incomplete_root(self):
        with patch.object(age, 'MAX_ENTRIES', 1):
            row = age.inspect(self.area)
        self.assertEqual(row['preview'], [])
        self.assertTrue(any('limit' in s for s in row['reasons']))
        self.assertTrue(self.old.exists())

    def test_busy_inspection_failure_keeps_files(self):
        cleaner = engine.Cleaner(True); cleaner.quiet = True
        cleaner.allowed_paths = {p['path']: p for p in age.inspect(self.area)['preview']}
        with patch.object(engine, 'idle', side_effect=engine.Unsafe('busy')):
            age.execute(self.area, cleaner)
        self.assertTrue(self.old.exists()); self.assertTrue(self.other.exists())

    def test_no_preview_no_deletion_and_zero_byte_files(self):
        empty = self.a / 'empty'; self.make(empty, 30); empty.write_text('')
        stamp = time.time() - 30 * 86400; os.utime(empty, (stamp, stamp))
        cleaner = engine.Cleaner(True); cleaner.quiet = True
        age.execute(self.area, cleaner)
        self.assertTrue(self.old.exists())
        from diskpick_list_ui import removable
        single = cleanup_area([str(self.a)], 'Empty files')
        row = age.inspect(single)
        row['preview'] = [p for p in row['preview'] if p['path'] == str(empty)]
        row['eligible_bytes'] = 0
        self.assertTrue(removable(row))

    def test_persistence_and_invalid_rule(self):
        config = self.root / 'areas.json'
        saved = SavedList([], config)
        saved.add(self.area)
        restored = SavedList([], config)
        self.assertEqual(restored.areas, [self.area])
        with self.assertRaises(ValueError):cleanup_area([str(self.a)], 'Bad', 0)
        with self.assertRaises(ValueError):cleanup_area([str(Path.home())], 'Home')
