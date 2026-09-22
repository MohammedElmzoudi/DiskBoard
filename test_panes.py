import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import diskpick_panes as panes
import diskpick_cli as cli

class PaneTests(unittest.TestCase):
 def test_noninteractive_rejects_panes(self):
  with patch('sys.stdin.isatty',return_value=False):
   with self.assertRaises(SystemExit):cli.main(['--agent','claude'])
 def test_agent_and_workbench_conflict(self):
  with patch('sys.stdin.isatty',return_value=True):
   with self.assertRaises(SystemExit):cli.main(['--agent','claude','--workbench'])
 def test_no_shell_interpolation(self):
  calls=[]
  def fake(tmux,*args,**kw):
   calls.append(args)
   return '%1' if args[0]=='new-session' else '%2' if args[0]=='split-window' else ''
  with patch.object(panes,'run',side_effect=fake):
   panes.create('tmux',['/tmp/my cli','literal; do not execute'],['/tmp/python','/tmp/a b.py'],root=Path('/tmp/my project'),session='workspace-test')
  import shlex
  split=next(c for c in calls if c[0]=='split-window')
  self.assertEqual(shlex.split(split[-1]),['/tmp/my cli','literal; do not execute'])
  self.assertIn('/tmp/my project',split)
  self.assertTrue(any(c[:3]==('bind-key','-n','S-Up') for c in calls))
  self.assertTrue(any(c[:3]==('bind-key','-n','S-Down') for c in calls))
 def test_setup_failure_does_not_kill_agent(self):
  calls=[]
  def fake(tmux,*args,**kw):
   calls.append(args)
   if args[0]=='split-window':raise RuntimeError('fixture error')
   return '%1'
  with patch.object(panes,'run',side_effect=fake),self.assertRaisesRegex(RuntimeError,'retained workspace'):
   panes.create('tmux',['agent'],['top'],session='workspace-test')
  self.assertFalse(any(c[0].startswith('kill') for c in calls))

@unittest.skipUnless(shutil.which('tmux'),'tmux not installed')
class RealPaneTests(unittest.TestCase):
 def test_real_layout_cwd_and_retained_process(self):
  import uuid
  tmux=shutil.which('tmux');socket='diskpick-test-'+uuid.uuid4().hex
  with tempfile.TemporaryDirectory() as tmp:
   try:
    cmd=[sys.executable,'-u','-c','import os,time; print(os.getcwd(),flush=True); time.sleep(120)']
    session,top,bottom=panes.create(tmux,cmd,cmd,Path(tmp),socket=socket,width=100,height=40)
    output=panes.run(tmux,'list-panes','-t',session,'-F','#{pane_id}|#{pane_top}|#{pane_height}|#{pane_current_path}|#{pane_active}',socket=socket)
    rows=[l.split('|') for l in output.splitlines()]
    self.assertEqual(len(rows),2);self.assertEqual(rows[0][0],top);self.assertEqual(rows[1][0],bottom)
    self.assertLess(int(rows[0][1]),int(rows[1][1]));self.assertEqual(rows[0][4],'1')
    self.assertTrue(all(Path(r[3]).resolve()==Path(tmp).resolve() for r in rows))
    keys=panes.run(tmux,'list-keys','-T','root',socket=socket)
    self.assertIn('S-Up',keys);self.assertIn('S-Down',keys)
    pid=panes.run(tmux,'display-message','-p','-t',bottom,'#{pane_pid}',socket=socket)
    panes.run(tmux,'respawn-pane','-k','-t',top,socket=socket)
    self.assertEqual(pid,panes.run(tmux,'display-message','-p','-t',bottom,'#{pane_pid}',socket=socket))
   finally:
    subprocess.run([tmux,'-L',socket,'kill-server'],capture_output=True)
