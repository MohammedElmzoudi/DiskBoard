import hashlib,json,subprocess,tempfile,unittest,zipfile
from pathlib import Path
import build_release as release
class ReleaseTests(unittest.TestCase):
 def test_reproducible_allowlist_and_relocated_launch(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);a=root/'a.zip';b=root/'b.zip'
   self.assertEqual(release.build(a),release.build(b))
   with zipfile.ZipFile(a) as z:
    names=z.namelist();self.assertEqual([n for n in names if n.count('/')==1],['DiskBoard/Install DiskBoard.command'])
    self.assertTrue(all(n=='DiskBoard/Install DiskBoard.command' or n.startswith('DiskBoard/.diskboard/') for n in names))
    self.assertFalse(any('test_' in n or '.git/' in n or '.ansi' in n or 'tools.json' in n for n in names))
    self.assertEqual(z.getinfo('DiskBoard/Install DiskBoard.command').external_attr >> 16 & 0o777, 0o755)
    manifest=json.loads(z.read('DiskBoard/.diskboard/MANIFEST.json'))
    for name,sha in manifest['files'].items():self.assertEqual(hashlib.sha256(z.read('DiskBoard/.diskboard/'+name)).hexdigest(),sha)
    target=root/'Folder with spaces';z.extractall(target)
   out=subprocess.run(['sh',str(target/'DiskBoard/.diskboard/Start diskpick.command'),'--version'],text=True,capture_output=True,check=True)
   self.assertIn(release.VERSION,out.stdout)
   config=root/'empty.json';config.write_text('{"version":1,"discovery":false,"areas":[]}')
   out=subprocess.run(['sh',str(target/'DiskBoard/.diskboard/Start diskpick.command'),'rules','--config',str(config)],text=True,capture_output=True,check=True)
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

 def test_download_double_click_installs_persistent_command_in_new_terminal(self):
  import os,shutil
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();archive=root/'download.zip';release.build(archive)
   download=root/'Downloads';download.mkdir()
   with zipfile.ZipFile(archive) as z:z.extractall(download)
   visible=[p.name for p in (download/'DiskBoard').iterdir() if not p.name.startswith('.')]
   self.assertEqual(visible,['Install DiskBoard.command'])
   home=root/'Home with spaces';home.mkdir()
   env=dict(os.environ,HOME=str(home),SHELL='/bin/zsh')
   env.pop('ZDOTDIR',None)
   installer=download/'DiskBoard/Install DiskBoard.command'
   for _ in range(2):
    result=subprocess.run(['sh',str(installer)],env=env,text=True,capture_output=True,timeout=30)
    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
   launcher=home/'.local/bin/diskboard'
   self.assertIn(release.VERSION,subprocess.run([str(launcher),'--version'],env=env,text=True,capture_output=True,check=True).stdout)
   for mode in ('-lic','-ic'):
    result=subprocess.run(['/bin/zsh',mode,'command -v diskboard && diskboard --version'],env=env,text=True,capture_output=True,timeout=30)
    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    self.assertIn(release.VERSION,result.stdout)
   self.assertEqual((home/'.zprofile').read_text().count('# DiskBoard command'),1)
   self.assertEqual((home/'.zshrc').read_text().count('# DiskBoard command'),1)
   shutil.rmtree(download)
   self.assertIn(release.VERSION,subprocess.run([str(launcher),'--version'],env=env,text=True,capture_output=True,check=True).stdout)

 def test_download_installer_preserves_conflicts_and_rejects_tampering(self):
  import os
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();archive=root/'download.zip';release.build(archive)
   with zipfile.ZipFile(archive) as z:z.extractall(root)
   installer=root/'DiskBoard/Install DiskBoard.command'
   home=root/'home';home.mkdir();bin_dir=home/'.local/bin';bin_dir.mkdir(parents=True)
   command=bin_dir/'diskboard';command.write_text('KEEP MY COMMAND')
   env=dict(os.environ,HOME=str(home),SHELL='/bin/zsh');env.pop('ZDOTDIR',None)
   result=subprocess.run(['sh',str(installer)],env=env,text=True,capture_output=True,timeout=30)
   self.assertNotEqual(result.returncode,0);self.assertEqual(command.read_text(),'KEEP MY COMMAND')
   self.assertFalse((home/'.local/share/DiskBoard-installations').exists())
   command.unlink()
   payload=root/'DiskBoard/.diskboard/diskpick_version.py';payload.write_text('TAMPERED')
   result=subprocess.run(['sh',str(installer)],env=env,text=True,capture_output=True,timeout=30)
   self.assertNotEqual(result.returncode,0);self.assertIn('changed',result.stderr)
   self.assertFalse((home/'.local/share/DiskBoard-installations').exists())

 def test_download_installer_upgrades_only_recognized_previous_launcher(self):
  import os,shlex
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();archive=root/'download.zip';release.build(archive)
   with zipfile.ZipFile(archive) as z:z.extractall(root)
   home=root/'home';home.mkdir();bin_dir=home/'.local/bin';bin_dir.mkdir(parents=True)
   old_entry=home/'Downloads/old/diskboard.py'
   command=bin_dir/'diskboard'
   command.write_text('#!/bin/sh\n# DiskBoard launcher\nexec '+shlex.quote('/usr/bin/python3')+' -B '+shlex.quote(str(old_entry))+' "$@"\n')
   env=dict(os.environ,HOME=str(home),SHELL='/bin/zsh');env.pop('ZDOTDIR',None)
   result=subprocess.run(['sh',str(root/'DiskBoard/Install DiskBoard.command')],env=env,text=True,capture_output=True,timeout=30)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
   self.assertNotIn(str(old_entry),command.read_text())
   self.assertIn(release.VERSION,subprocess.run([str(command),'--version'],env=env,text=True,capture_output=True,check=True).stdout)

 def test_packaged_storage_and_groups_launch_from_relocated_zip(self):
  from capture_list import Terminal
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();archive=root/'build.zip';release.build(archive)
   with zipfile.ZipFile(archive) as z:z.extractall(root/'Relocated App')
   entry=root/'Relocated App/DiskBoard/.diskboard/diskboard.py'
   terminal=Terminal(['--demo'],entry=entry)
   try:
    terminal.until(b'Partial scan')
    terminal.send(b'2');terminal.until(b'Cleanup groups')
    terminal.send(b'1');terminal.until(b'Storage')
   finally:terminal.close()

 def test_packaged_real_cleanup_cancel_confirm_and_audit(self):
  from capture_list import cleanup_flow
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();archive=root/'build.zip';release.build(archive)
   with zipfile.ZipFile(archive) as z:z.extractall(root/'Relocated App')
   cleanup_flow(entry=root/'Relocated App/DiskBoard/.diskboard/diskboard.py')
