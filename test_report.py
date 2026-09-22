import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import diskpick_report as report
import diskpick_report_ui as view

class ReportTests(unittest.TestCase):
 def setUp(self):
  self.data=view.demo_report();self.token='demo'
 def frame(self,data=None,token='demo'):
  return report.START+' '+token+'\n'+json.dumps(data or self.data,indent=2)+'\n'+report.END+' '+token+'\n'
 def test_complete_report(self):
  self.assertEqual(report.extract('Agent prose\n'+self.frame(),'demo'),self.data)
 def test_partial_old_or_echoed_reports_ignored(self):
  self.assertIsNone(report.extract(self.frame(),'new-scan'))
  self.assertIsNone(report.extract(self.frame().split(report.END)[0],'demo'))
  self.assertIsNone(report.extract('echo '+self.frame(),'demo'))
  self.assertIsNone(report.extract(report.prompt('demo'),'demo'))
 def test_last_started_frame_prevents_stale_fallback(self):
  self.assertIsNone(report.extract(self.frame()+report.START+' demo\n{','demo'))
  with self.assertRaises(ValueError):report.extract(self.frame()+report.START+' demo\ninvalid\n'+report.END+' demo','demo')
 def test_latest_complete_replaces(self):
  other=copy.deepcopy(self.data);other['summary']='A new scan is complete.'
  self.assertEqual(report.extract(self.frame()+self.frame(other),'demo')['summary'],other['summary'])
 def test_rejects_duplicates_large_and_executable_fields(self):
  with self.assertRaises(ValueError):report.extract(self.frame().replace('"version": 1','"version": 1, "version": 1'),'demo')
  d=copy.deepcopy(self.data);d['command']='rm -rf /'
  with self.assertRaises(ValueError):report.validate(d,'demo')
  with self.assertRaises(ValueError):report.extract(report.START+' demo\n'+(' '*report.MAX_BYTES)+'{}'+(' '*report.MAX_BYTES)+'x\n'+report.END+' demo','demo')
 def test_paths_cannot_overlap_or_traverse(self):
  for path in ('/','relative','/a/../b','/demo/project/target','/demo/project'):
   d=copy.deepcopy(self.data);d['groups'][1]['items'][0]['path']=path
   with self.subTest(path=path),self.assertRaises(ValueError):report.validate(d,'demo')
 def test_rejects_control_text_and_invalid_sizes(self):
  for value in (-1,True,'100',float('nan')):
   d=copy.deepcopy(self.data);d['groups'][0]['items'][0]['bytes']=value
   with self.assertRaises(ValueError):report.validate(d,'demo')
  d=copy.deepcopy(self.data);d['summary']='escape\x1b[0m'
  with self.assertRaises(ValueError):report.validate(d,'demo')
 def test_null_size_is_visible(self):
  self.assertIn('unmeasured',view.total(self.data['groups'][1]['items']))
 def test_only_trusted_preview_paths_match(self):
  item=self.data['groups'][0]['items'][0]
  rule={'id':'demo-build','kind':'generated'}
  rows=[{'area':rule,'preview':[{'path':'/demo/project/target/cache'},{'path':'/demo/project/target-other'},{'path':'/important'}]}]
  self.assertEqual(report.matched_paths(item,rows),([rule],{'/demo/project/target/cache'}))
  for risk,area in [('review','demo-build'),('keep','demo-build'),('rebuildable','invented'),('rebuildable',None)]:
   altered=dict(item,risk=risk,area_id=area)
   self.assertEqual(report.matched_paths(altered,rows),([],set()))
 def test_inspect_handler_cannot_delete(self):
  item=self.data['groups'][0]['items'][0]
  self.assertEqual(report.matched_paths(item,[{'area':{'id':'demo-build','kind':'inspect'},'preview':[{'path':item['path']}]}]),([],set()))
 def test_prepare_uses_existing_rules_and_deduplicates(self):
  item=self.data['groups'][0]['items'][0];rule={'id':'demo-build','kind':'generated','roots':['/trusted']}
  def scan(areas,quiet):
   self.assertEqual(areas,[rule]);return [{'area':rule,'preview':[{'path':item['path']}]}]
  with patch.object(view.catalog,'expand',return_value=[rule]):
   rules,paths,blocked=view.prepare([item],[],scan)
  self.assertEqual(rules,[rule]);self.assertEqual(paths,{item['path']});self.assertEqual(blocked,[])
 def test_feed_does_not_replay_same_report(self):
  feed=report.PaneFeed();feed.pane='%0';feed.token='demo'
  with patch.object(feed,'command',side_effect=['%1',self.frame(),'%1',self.frame()+'More UI output']):
   self.assertIsNotNone(feed.poll());self.assertIsNone(feed.poll())
 def test_cleanup_forwards_only_reviewed_paths(self):
  class Screen:
   def wait(self,title,fn,**kw):return fn()
   def menu(self,*args):return 1
   def message(self,*args):pass
  rule={'id':'safe','kind':'generated'}
  with patch.object(view,'prepare',return_value=([rule],{'/fixture/cache'},[])):
   calls=[]
   def clean(rules,paths):
    calls.append((rules,paths));return {'results':[],'net_change_bytes':0}
   self.assertTrue(view.cleanup(Screen(),[],[],None,clean))
   self.assertEqual(calls,[([rule],{'/fixture/cache'})])
 def test_cancel_does_not_call_clean(self):
  class Screen:
   def wait(self,title,fn,**kw):return fn()
   def menu(self,*args):return 0
  with patch.object(view,'prepare',return_value=([{'id':'safe'}],{'/fixture/cache'},[])),patch('diskpick_cli.clean') as clean:
   self.assertFalse(view.cleanup(Screen(),[],[],None,clean));clean.assert_not_called()
 def test_cli_indented_and_bulleted_markers(self):
  shown='\n'.join('  '+line for line in self.frame().splitlines()).replace('  DISKPICK_REPORT_BEGIN','⏺ DISKPICK_REPORT_BEGIN')
  self.assertEqual(report.extract(shown,'demo'),self.data)
 def test_terminal_borders(self):
  self.assertEqual(report.extract('\n'.join('│ '+line for line in self.frame().splitlines()),'demo'),self.data)

class LiveReportTests(unittest.TestCase):
 @unittest.skipUnless(__import__('shutil').which('tmux'),'tmux not installed')
 def test_real_wrapped_terminal_report(self):
  import diskpick_panes as panes
  import shutil,subprocess,sys,time,uuid
  tmux=shutil.which('tmux');socket='diskpick-report-test-'+uuid.uuid4().hex
  data=view.demo_report();frame=report.START+' demo\n'+json.dumps(data)+'\n'+report.END+' demo'
  # A long compact JSON line is physically wrapped by a narrow real terminal.
  cmd=[sys.executable,'-u','-c','import time; print('+repr(frame)+',flush=True); time.sleep(60)']
  topcmd=[sys.executable,'-c','import time; time.sleep(60)']
  try:
   session,top,bottom=panes.create(tmux,cmd,topcmd,socket=socket,width=72,height=30)
   feed=report.PaneFeed();feed.pane=top;feed.token='demo'
   result=None
   with patch.object(panes,'SOCKET',socket):
    for _ in range(20):
     result=feed.poll()
     if result:break
     time.sleep(.1)
   self.assertEqual(result,data)
  finally:subprocess.run([tmux,'-L',socket,'kill-server'],capture_output=True)

class EncodedReportTests(unittest.TestCase):
 def test_cli_hard_wrap_preserves_exact_paths_and_spaces(self):
  data=view.demo_report();data['groups'][0]['items'][0]['path']='/demo/a folder with spaces/cache'
  framed=report.frame(data)
  # Emulate hard wraps and the indentation added by an actual CLI renderer.
  import textwrap
  shown='\n'.join('  '+part for line in framed.splitlines() for part in textwrap.wrap(line,width=91,break_long_words=True,break_on_hyphens=False))
  self.assertEqual(report.extract(shown,'demo'),data)
 def test_corrupt_encoding_rejected(self):
  with self.assertRaises(ValueError):report.extract(report.START+' demo\nBASE64 !!!!\n'+report.END+' demo','demo')
 def test_decoded_json_still_has_all_safety_checks(self):
  import base64
  data=view.demo_report();data['groups'][0]['items'][0]['path']='/'
  frame=report.START+' demo\nBASE64 '+base64.b64encode(json.dumps(data).encode()).decode()+'\n'+report.END+' demo'
  with self.assertRaises(ValueError):report.extract(frame,'demo')
 def test_frame_rejects_invalid_scan_id_types(self):
  for token in ('\nmalicious',123,True,None,{},[]):
   data=view.demo_report();data['scan_id']=token
   with self.subTest(token=token),self.assertRaises(ValueError):report.frame(data)
