import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import diskpick_discovery as discovery
import diskpick_engine as engine
import diskpick_worktrees as worktrees
import diskpick_tui as tui

class WorktreeReviewTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name).resolve();self.repo=self.base/'root';self.repo.mkdir();self.tree=self.base/'feature'
  self.git('init');self.git('config','user.email','fixture@example.test');self.git('config','user.name','Fixture')
  (self.repo/'source').write_text('original');(self.repo/'.gitignore').write_text('private\n')
  self.git('add','.');self.git('commit','-m','initial');self.git('worktree','add','-b','feature',str(self.tree))
 def tearDown(self):self.tmp.cleanup()
 def git(self,*args,root=None):return subprocess.run(['git','-C',str(root or self.repo),*args],check=True,capture_output=True,text=True).stdout
 def check(self):
  with patch.object(discovery,'MIN_WORKTREE_AGE',0),patch.object(engine,'idle'):return discovery.check_worktree(self.tree)
 def test_unstaged_changes_block(self):
  (self.tree/'source').write_text('new')
  with self.assertRaisesRegex(engine.Unsafe,'1 unstaged'):self.check()
 def test_staged_changes_block(self):
  (self.tree/'source').write_text('new');self.git('add','source',root=self.tree)
  with self.assertRaisesRegex(engine.Unsafe,'1 staged'):self.check()
 def test_untracked_changes_block(self):
  (self.tree/'notes').write_text('important')
  with self.assertRaisesRegex(engine.Unsafe,'1 untracked'):self.check()
 def test_ignored_changes_block(self):
  (self.tree/'private').write_text('important')
  with self.assertRaisesRegex(engine.Unsafe,'Ignored local data'):self.check()
 def test_active_worktree_blocks(self):
  with patch.object(discovery,'MIN_WORKTREE_AGE',0),patch.object(engine,'idle',side_effect=engine.Unsafe('active')):
   with self.assertRaisesRegex(engine.Unsafe,'active'):discovery.check_worktree(self.tree)
 def test_fresh_activity_blocks_old_checkout(self):
  with self.assertRaisesRegex(engine.Unsafe,'more recently'):worktrees.enforce_idle_days(self.tree,14)
 def test_review_uses_oldest_first_and_keeps_unknown_visible(self):
  rows=[dict(path='a',timestamp=10,days=30),dict(path='b',timestamp=20,days=20),dict(path='c',timestamp=30,days=1),dict(path='d',timestamp=None,days=None)]
  self.assertEqual([r['path'] for r in tui.filtered(rows,14)],['d','a','b'])
 def test_new_edit_after_selection_is_preserved(self):
  (self.tree/'source').write_text('edit after preview')
  c=engine.Cleaner(True);c.allowed_paths={str(self.tree)}
  with patch.object(discovery,'MIN_WORKTREE_AGE',0),patch.object(engine,'idle'):
   discovery.retire_worktree(dict(roots=[str(self.tree)],min_idle_days=0),c)
  self.assertEqual((self.tree/'source').read_text(),'edit after preview')
 def test_git_common_directory_groups_linked_checkout(self):
  groups=worktrees.repositories([self.repo,self.tree]);self.assertEqual(len(groups),1)
  self.assertEqual(set(next(iter(groups.values()))),{self.repo,self.tree})
