"""Whole local flow: copy prompt, terminal report, live top-pane update. No AI calls."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
import diskpick_panes as panes
import diskpick_report as report
import diskpick_report_ui as view

@unittest.skipUnless(shutil.which('tmux'),'tmux not installed')
class ReportFlowTests(unittest.TestCase):
 def test_copy_and_live_report_without_real_clipboard_or_agent(self):
  tmux=shutil.which('tmux');socket='diskpick-ui-proof-'+uuid.uuid4().hex
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);prompt=root/'prompt.txt';response=root/'response.txt'
   # This private executable captures the test prompt. It never accesses the OS clipboard.
   clipboard=root/'pbcopy';clipboard.write_text('#!/bin/sh\ncat > '+__import__('shlex').quote(str(prompt))+'\n');clipboard.chmod(0o700)
   config=root/'areas.json';config.write_text('{"version":1,"areas":[],"discovery":false}')
   top=['/usr/bin/env','PATH='+str(root)+os.pathsep+os.environ['PATH'],'/usr/bin/python3','-B',str(Path(panes.__file__).with_name('diskpick.py')),'--workbench','--config',str(config)]
   bottom=['/usr/bin/python3','-u','-c','import pathlib,time; p=pathlib.Path('+repr(str(response))+');\nwhile not p.exists(): time.sleep(.1)\nprint(p.read_text(),flush=True);time.sleep(60)']
   def wait(fn):
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
     value=fn()
     if value:return value
     time.sleep(.1)
    self.fail('The report UI did not reach the expected state.')
   try:
    session,top_id,bottom_id=panes.create(tmux,bottom,top,root,socket,width=112,height=42)
    def capture():return panes.run(tmux,'capture-pane','-p','-J','-t',top_id,socket=socket)
    wait(lambda:'Copy a new scan prompt' in capture())
    panes.run(tmux,'send-keys','-t',top_id,'Enter',socket=socket)
    wait(lambda:prompt.exists() and 'JSON shape' in prompt.read_text())
    body=prompt.read_text();token=re.search(r'JSON shape \(replace SCAN_ID with ([0-9a-f]{32})',body).group(1)
    data=view.demo_report();data['scan_id']=token;data['summary']='The synthetic report reached the live top pane.'
    response.write_text(report.frame(data))
    shown=wait(lambda:(lambda text:text if data['summary'] in text else None)(capture()))
    self.assertIn('Old build output',shown);self.assertIn('Not measured',shown)
    # Lower-pane updates must not replay the report and erase a user's selection.
    panes.run(tmux,'send-keys','-t',top_id,'Down','Down','Down','Down','Down','Space',socket=socket)
    wait(lambda:'selected paths (1)' in capture())
    time.sleep(1.2)
    self.assertIn('selected paths (1)',capture())
   finally:subprocess.run([tmux,'-L',socket,'kill-server'],capture_output=True)
