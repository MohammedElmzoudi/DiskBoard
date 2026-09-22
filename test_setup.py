import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import diskpick_setup as setup
import diskpick_panes as panes

class Screen:
 def __init__(self,*answers):self.answers=iter(answers);self.menus=[];self.messages=[]
 def menu(self,*args):self.menus.append(args);return next(self.answers)
 def message(self,*args):self.messages.append(args)

class SetupTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.config=self.root/'tools.json'
  self.exe=self.root/'codex';self.exe.write_text('#!/bin/sh\nexit 0\n');self.exe.chmod(0o700)
  self.patches=[patch.object(setup,'CONFIG',self.config),patch.object(setup,'SESSION',{})]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.temp.cleanup()
 def test_yes_saves_only_diskpick_config_and_is_idempotent(self):
  shell=self.root/'.zshrc';shell.write_text('KEEP SHELL\n');before=os.environ.get('PATH')
  self.assertEqual(setup.confirm(Screen(0),'codex',str(self.exe)),str(self.exe))
  ino=self.config.stat().st_ino
  setup.remember('codex',str(self.exe))
  self.assertEqual(self.config.stat().st_ino,ino)
  self.assertEqual(json.loads(self.config.read_text()),{'version':1,'tools':{'codex':str(self.exe)}})
  self.assertEqual(shell.read_text(),'KEEP SHELL\n');self.assertEqual(os.environ.get('PATH'),before)
  self.assertEqual(self.config.stat().st_mode&0o777,0o600)
 def test_no_does_not_write(self):
  self.assertIsNone(setup.confirm(Screen(1),'codex',str(self.exe)))
  self.assertFalse(self.config.exists());self.assertEqual(setup.SESSION,{})
 def test_once_does_not_write(self):
  self.assertEqual(setup.confirm(Screen(2),'codex',str(self.exe)),str(self.exe));self.assertFalse(self.config.exists())
 def test_existing_path_wins_over_saved_setting(self):
  setup.remember('codex',str(self.exe))
  other=self.root/'other';other.write_text('#!/bin/sh\n');other.chmod(0o700)
  with patch.object(setup.shutil,'which',return_value=str(other)):
   self.assertEqual(setup.resolve('codex'),str(other))
 def test_saved_tool_works_with_restricted_path(self):
  setup.remember('codex',str(self.exe))
  with patch.object(setup.shutil,'which',return_value=None):self.assertEqual(setup.resolve('codex'),str(self.exe))
 def test_corrupt_and_symlink_settings_are_not_overwritten(self):
  for body in ('invalid','{"version":1,"tools":{},"tools":{}}'):
   self.config.write_text(body)
   with self.assertRaises(ValueError):setup.remember('codex',str(self.exe))
   self.assertEqual(self.config.read_text(),body)
  self.config.unlink();target=self.root/'important';target.write_text('KEEP');self.config.symlink_to(target)
  with self.assertRaises(OSError):setup.remember('codex',str(self.exe))
  self.assertEqual(target.read_text(),'KEEP')
 def test_hardlinked_settings_preserved(self):
  target=self.root/'important';target.write_text('{"version":1,"tools":{}}');os.link(target,self.config)
  with self.assertRaises(ValueError):setup.remember('codex',str(self.exe))
  self.assertEqual(target.read_text(),'{"version":1,"tools":{}}')
 def test_failed_atomic_replace_preserves_settings(self):
  setup.remember('codex',str(self.exe));old=self.config.read_bytes()
  with patch.object(setup.os,'rename',side_effect=OSError('disk full')):
   with self.assertRaises(OSError):setup.remember('claude',str(self.exe))
  self.assertEqual(self.config.read_bytes(),old);self.assertFalse(list(self.root.glob('*.tmp')))
 def test_save_failure_offers_use_once(self):
  self.config.write_text('broken');screen=Screen(0,1)
  self.assertEqual(setup.confirm(screen,'codex',str(self.exe)),str(self.exe));self.assertEqual(self.config.read_text(),'broken')
 def test_merge_preserves_other_tool(self):
  setup.remember('codex',str(self.exe));setup.remember('claude',str(self.exe))
  self.assertEqual(set(setup.saved()),{'codex','claude'})
 def test_removed_or_non_executable_path_refused(self):
  self.exe.chmod(0o600);self.assertIsNone(setup.executable(self.exe))
  with self.assertRaises(ValueError):setup.remember('codex',str(self.exe))
  self.assertIsNone(setup.executable('echo bad; rm something'))
 def test_duplicate_symlinks_collapse(self):
  # Search fixture install roots only. Both names point to the same inode.
  fakehome=self.root/'home';(fakehome/'.local/bin').mkdir(parents=True);(fakehome/'.volta/bin').mkdir(parents=True)
  (fakehome/'.local/bin/codex').symlink_to(self.exe);(fakehome/'.volta/bin/codex').symlink_to(self.exe)
  original=setup.executable
  with patch.object(setup.Path,'home',return_value=fakehome),patch.object(setup,'executable',side_effect=lambda p:original(p) if str(p).startswith(str(fakehome)) else None):
   self.assertEqual(len(setup.candidates('codex')),1)
 def test_missing_dependency_can_return_to_chooser(self):
  with patch.object(setup,'resolve',return_value=None),patch.object(setup,'candidates',return_value=[]):
   self.assertIsNone(setup.ensure(Screen(3),'codex'))
 def test_no_tmux_needed_for_workbench_only(self):
  with patch.object(panes,'choose',return_value='none'),patch.object(setup,'prompt_tool') as prompt:
   self.assertIsNone(panes.launch());prompt.assert_not_called()
 def test_symlink_config_parent_preserved(self):
  actual=self.root/'real';actual.mkdir();link=self.root/'redirect';link.symlink_to(actual)
  with patch.object(setup,'CONFIG',link/'tools.json'),self.assertRaises(OSError):setup.remember('codex',str(self.exe))
  self.assertFalse((actual/'tools.json').exists())
 def test_ephemeral_tmux_path_works_without_saved_settings(self):
  with patch.object(setup.shutil,'which',return_value=None),patch.dict(os.environ,{'DISKPICK_TMUX':str(self.exe)}):
   self.assertEqual(setup.resolve('tmux'),str(self.exe));self.assertFalse(self.config.exists())
 def test_busy_settings_lock_preserves_file(self):
  import fcntl
  setup.remember('codex',str(self.exe));before=self.config.read_bytes()
  with (self.root/'.tools.lock').open('r+') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   with self.assertRaises(BlockingIOError):setup.remember('claude',str(self.exe))
  self.assertEqual(self.config.read_bytes(),before)

 def test_other_user_writable_settings_are_preserved(self):
  self.config.write_text('{"version":1,"tools":{}}');self.config.chmod(0o666)
  with self.assertRaises(ValueError):setup.remember('codex',str(self.exe))
  self.assertEqual(self.config.read_text(),'{"version":1,"tools":{}}')
