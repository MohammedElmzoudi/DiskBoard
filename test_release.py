import hashlib,json,subprocess,tempfile,unittest,zipfile
from pathlib import Path
import build_release as release
class ReleaseTests(unittest.TestCase):
 def test_reproducible_allowlist_and_relocated_launch(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);a=root/'a.zip';b=root/'b.zip'
   self.assertEqual(release.build(a),release.build(b))
   with zipfile.ZipFile(a) as z:
    names=z.namelist();self.assertTrue(all(n.startswith('diskpick/') for n in names))
    self.assertFalse(any('test_' in n or '.git/' in n or '.ansi' in n or 'tools.json' in n for n in names))
    manifest=json.loads(z.read('diskpick/MANIFEST.json'))
    for name,sha in manifest['files'].items():self.assertEqual(hashlib.sha256(z.read('diskpick/'+name)).hexdigest(),sha)
    target=root/'Folder with spaces';z.extractall(target)
   out=subprocess.run(['sh',str(target/'diskpick/Start diskpick.command'),'--version'],text=True,capture_output=True,check=True)
   self.assertIn(release.VERSION,out.stdout)
   config=root/'empty.json';config.write_text('{"version":1,"discovery":false,"areas":[]}')
   out=subprocess.run(['sh',str(target/'diskpick/Start diskpick.command'),'rules','--config',str(config)],text=True,capture_output=True,check=True)
   self.assertEqual(json.loads(out.stdout)['areas'],[])
 def test_existing_artifact_is_not_overwritten(self):
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp)/'keep.zip';target.write_text('KEEP')
   with self.assertRaises(FileExistsError):release.build(target)
   self.assertEqual(target.read_text(),'KEEP')
 def test_install_repeat_and_symlink_boundary(self):
  import os
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();home=root/'home';home.mkdir();env=dict(os.environ,HOME=str(home))
   script=release.ROOT/'install.sh'
   for _ in range(2):subprocess.run(['sh',str(script)],env=env,check=True,capture_output=True)
   launcher=home/'.local/bin/diskboard';self.assertTrue(launcher.exists())
   out=subprocess.run([str(launcher),'--version'],env=env,check=True,capture_output=True,text=True);self.assertIn(release.VERSION,out.stdout)
   launcher.write_text('KEEP DIFFERENT COMMAND')
   failure=subprocess.run(['sh',str(script)],env=env,capture_output=True);self.assertNotEqual(failure.returncode,0)
   self.assertEqual(launcher.read_text(),'KEEP DIFFERENT COMMAND')
   other=root/'other-home';other.mkdir();outside=root/'outside';outside.mkdir();(other/'.local').symlink_to(outside)
   failure=subprocess.run(['sh',str(script)],env=dict(os.environ,HOME=str(other)),capture_output=True)
   self.assertNotEqual(failure.returncode,0);self.assertEqual(list(outside.iterdir()),[])
