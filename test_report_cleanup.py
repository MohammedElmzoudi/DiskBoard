"""Real report-to-audit cleanup, restricted to disposable Rust fixture sessions."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import diskpick_catalog as catalog
import diskpick_cli as cli
import diskpick_engine as engine
import diskpick_report_ui as view

class ReportCleanupTests(unittest.TestCase):
 def exercise(self,change_after_preview):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();debug=root/'target/debug';crate=debug/'incremental/example-abc';crate.mkdir(parents=True)
   (root/'Cargo.toml').write_text('[package]\nname="fixture"\nversion="0.1.0"\n');(debug/'.cargo-lock').touch()
   for name in ('s-100-abc-old','s-200-abc-new'):
    session=crate/name;session.mkdir()
    for file in ('query-cache.bin','dep-graph.bin','work-products.bin'):
     p=session/file;p.write_text('fixture');os.utime(p,(time.time()-86400,)*2)
    os.utime(session,(time.time()-86400,)*2);(crate/('-'.join(name.split('-')[:3])+'.lock')).touch()
   old=crate/'s-100-abc-old';new=crate/'s-200-abc-new';important=root/'important.txt';important.write_text('KEEP')
   rule=dict(id='fixture-rust',title='Fixture',kind='rust',roots=[str(debug)],description='Disposable test sessions.')
   catalog.validate({'version':1,'areas':[rule]})
   item=dict(id='old',title='Old session',path=str(old),risk='rebuildable',area_id='fixture-rust')
   # Isolate the catalog and audit. Simulate no compiler; actual file and open-file checks still run.
   with patch.object(catalog,'expand',return_value=[rule]),patch.object(cli,'STATE',root/'audit'),patch.object(engine,'processes',return_value=(set(),'')):
    rules,paths,blocked=view.prepare([item],[rule],cli.scan)
    self.assertEqual(paths,{str(old)});self.assertFalse(blocked)
    self.assertTrue((old/'query-cache.bin').exists())
    if change_after_preview:(old/'new-work').write_text('KEEP NEW WORK')
    result=cli.clean(rules,paths)
   self.assertEqual(important.read_text(),'KEEP');self.assertEqual((new/'query-cache.bin').read_text(),'fixture')
   events=[json.loads(line) for line in Path(result['audit']).read_text().splitlines()]
   self.assertEqual(events[-1]['event'],'finish')
   if change_after_preview:
    self.assertTrue((old/'query-cache.bin').exists());self.assertEqual((old/'new-work').read_text(),'KEEP NEW WORK')
    self.assertEqual(result['results'][0]['completed'],0)
   else:
    self.assertEqual(list(old.iterdir()),[]);self.assertEqual(result['results'][0]['completed'],1)
    self.assertEqual({e['path'] for e in events if e['event']=='completed'},{str(old)})
 def test_report_only_deletes_exact_preview_and_keeps_neighbors(self):self.exercise(False)
 def test_new_work_after_report_preview_is_kept(self):self.exercise(True)
