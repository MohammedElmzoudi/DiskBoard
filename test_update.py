import tempfile,unittest,subprocess
from pathlib import Path
from unittest.mock import patch
import diskboard_update as u
class UpdateTests(unittest.TestCase):
 def test_zip_skips(self):
  with tempfile.TemporaryDirectory() as t:self.assertIn('Git checkout',u.update(Path(t)))
 def test_dirty_and_wrong_origin_never_fetch(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t).resolve();(p/'.git').mkdir()
   for responses in [[str(p),'evil'],[str(p),u.OFFICIAL,' M source.py']]:
    with patch.object(u,'git',side_effect=responses) as git:
     self.assertIn('skipped',u.update(p,True));self.assertFalse(any('fetch' in c.args for c in git.call_args_list))
 def test_real_fast_forward_and_divergence(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);remote=base/'remote';local=base/'local'
   def g(p,*a):return subprocess.run(['git',*a],cwd=p,text=True,capture_output=True,check=True).stdout.strip()
   remote.mkdir();g(remote,'init','-b','main');g(remote,'config','user.email','test@example.com');g(remote,'config','user.name','Test')
   (remote/'keep').write_text('one');g(remote,'add','.');g(remote,'commit','-m','one')
   g(base,'clone',str(remote),str(local));g(local,'config','user.email','test@example.com');g(local,'config','user.name','Test')
   (remote/'keep').write_text('two');g(remote,'commit','-am','two')
   with patch.object(u,'OFFICIAL',str(remote)):
    self.assertIn('available',u.update(local));self.assertEqual((local/'keep').read_text(),'one')
    self.assertIn('updated',u.update(local,True));self.assertEqual((local/'keep').read_text(),'two')
    self.assertIn('up to date',u.update(local,True))
    (local/'keep').write_text('local');g(local,'commit','-am','local')
    (remote/'keep').write_text('remote');g(remote,'commit','-am','remote')
    with self.assertRaises(subprocess.CalledProcessError):u.update(local,True)
    self.assertEqual((local/'keep').read_text(),'local')

 def test_busy_app_prevents_update(self):
  import fcntl,sys
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'.git').mkdir()
   with (root/'.git/diskboard-run.lock').open('w') as lock:
    fcntl.flock(lock,fcntl.LOCK_SH)
    with patch.object(u,'ROOT',root),patch.object(sys,'argv',['diskboard','update']),patch.object(u,'run') as run:
     self.assertEqual(u.start(),0);run.assert_not_called()

 def test_legacy_origin_fetches_canonical_repository(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t).resolve();(p/'.git').mkdir()
   responses=[str(p),u.LEGACY_OFFICIAL,'','main','origin/main','','same','same']
   with patch.object(u,'git',side_effect=responses) as git:
    self.assertIn('up to date',u.update(p))
    fetch=[c.args for c in git.call_args_list if 'fetch' in c.args]
    self.assertEqual(len(fetch),1)
    self.assertIn(u.OFFICIAL,fetch[0])
