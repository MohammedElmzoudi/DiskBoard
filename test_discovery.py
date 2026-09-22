import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import diskpick_catalog as catalog
import diskpick_discovery as discovery
import diskpick_engine as engine

class DiscoveryTests(unittest.TestCase):
 def test_fixed_roots(self):
  a=discovery.presets()[0];a['roots']=['~/Documents']
  with self.assertRaises(ValueError):catalog.validate({'version':1,'areas':[a]})
 def test_real_size_and_symlink(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();(root/'file').write_bytes(b'x'*8192)
   self.assertGreaterEqual(catalog.size(root,time.monotonic()+10),8192)
   (root/'link').symlink_to(root,target_is_directory=True)
   self.assertIsNone(catalog.size(root/'link',time.monotonic()+10))
 def test_timeout_not_zero(self):
  import subprocess
  with tempfile.TemporaryDirectory() as tmp,patch.object(engine,'run',side_effect=subprocess.TimeoutExpired('du',1)):
   self.assertIsNone(catalog.size(Path(tmp).resolve(),time.monotonic()+10))
 def test_preview_excludes_new_paths(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();(root/'keep').write_text('keep')
   c=engine.Cleaner(True);c.allowed_paths=set();c.clean_tree(root,root,0)
   self.assertTrue((root/'keep').exists())
 def test_download_scope_age_and_preview(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(engine,'idle'):
   root=Path(tmp).resolve();old=root/'old.tar.gz';new=root/'new';old.write_text('download');new.write_text('keep')
   os.utime(old,(time.time()-9*86400,)*2)
   c=engine.Cleaner(False);discovery.clean_download_file(old,root,c)
   self.assertTrue(old.exists());self.assertEqual(c.candidates,1)
   c=engine.Cleaner(True);discovery.clean_download_file(old,root,c);discovery.clean_download_file(new,root,c)
   self.assertFalse(old.exists());self.assertEqual(new.read_text(),'keep')
 def test_busy_download_stays(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(engine,'idle',side_effect=engine.Unsafe('busy')):
   root=Path(tmp).resolve();p=root/'old';p.write_text('keep');os.utime(p,(time.time()-9*86400,)*2)
   discovery.clean_download_file(p,root,engine.Cleaner(True));self.assertTrue(p.exists())
 def test_recent_checkout_stays(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();p=root/'main/site/static/js';p.mkdir(parents=True);(p/'source.js').write_text('source')
   discovery.execute(dict(kind='generated',roots=[str(root)]),engine.Cleaner(True))
   self.assertTrue((p/'source.js').exists())

 def test_worktree_retirement_keeps_branch_and_refuses_ignored_data(self):
  import subprocess
  with tempfile.TemporaryDirectory() as tmp:
   base=Path(tmp).resolve();repo=base/'repo';checkout=base/'old-feature';repo.mkdir()
   def git(*args):return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,text=True).stdout
   git('init');git('config','user.email','fixture@example.test');git('config','user.name','Fixture')
   (repo/'source').write_text('valuable');(repo/'.gitignore').write_text('private\n')
   git('add','.');git('commit','-m','fixture');git('worktree','add','-b','feature',str(checkout))
   area=dict(kind='worktree',roots=[str(checkout)])
   with patch.object(discovery,'MIN_WORKTREE_AGE',0),patch.object(engine,'idle'):
    (checkout/'private').write_text('KEEP')
    c=engine.Cleaner(True);c.allowed_paths={str(checkout)};discovery.retire_worktree(area,c)
    self.assertTrue((checkout/'private').exists())
    (checkout/'private').unlink()
    c=engine.Cleaner(False);discovery.retire_worktree(area,c);self.assertTrue(checkout.exists());self.assertEqual(c.candidates,1)
    c=engine.Cleaner(True);c.allowed_paths={str(checkout)};discovery.retire_worktree(area,c)
    self.assertFalse(checkout.exists());self.assertTrue(git('rev-parse','feature').strip())
    self.assertIn('refs/diskpick-retired/',git('for-each-ref','refs/diskpick-retired/'))
    self.assertEqual((repo/'source').read_text(),'valuable')
 def test_worktree_active_and_base_refused(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve()/'main4';root.mkdir()
   with self.assertRaises(engine.Unsafe):discovery.check_worktree(root)
