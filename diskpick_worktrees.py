"""Repository grouping and conservative last-activity evidence (never access time)."""
from pathlib import Path
import os
import re
import time
import diskpick_engine as engine

SKIP={'.git','node_modules','target','.venv','venv','env','__pycache__','.cache','.ruff_cache','.next'}

def git(root,*args):
 r=engine.run(['git','--no-optional-locks','-C',str(root),*args])
 if r.returncode or r.stderr:raise engine.Unsafe('Git inspection failed')
 return r.stdout.strip()

def repositories(paths):
 groups={}
 for p in paths:
  try:
   common=Path(git(p,'rev-parse','--path-format=absolute','--git-common-dir'))
   root=common.parent if common.name=='.git' else common
   groups.setdefault(str(root),[]).append(p)
  except (OSError,engine.Unsafe):continue
 return groups

def activity(root,now=None):
 """Estimate last use from source changes, Git activity and local Claude sessions.
 Reads alone cannot be inferred. Incomplete inspection must block retirement.
 """
 now=time.time() if now is None else now
 latest=0;source='';count=0
 def observe(p,label):
  nonlocal latest,source
  s=p.lstat()
  if p.is_symlink():return
  if s.st_mtime>latest:latest=s.st_mtime;source=label
 deadline=time.monotonic()+30
 for directory,dirs,files in os.walk(root,followlinks=False,onerror=lambda e:(_ for _ in ()).throw(e)):
  dirs[:]=[d for d in dirs if d not in SKIP and not (Path(directory)/d).is_symlink()]
  for name in files:
   if time.monotonic()>deadline or count>200000:raise engine.Unsafe('activity scan incomplete')
   if name=='.git':continue
   observe(Path(directory)/name,'working files');count+=1
 gitdir=Path(git(root,'rev-parse','--absolute-git-dir'))
 for rel in ('logs/HEAD','HEAD','index'):
  p=gitdir/rel
  if p.exists():observe(p,'Git '+rel)
 # Claude projects use an encoded absolute working-directory name.
 project=Path.home()/'.claude/projects'/re.sub(r'[^a-zA-Z0-9]','-',str(root))
 if project.exists():
  with engine.directory(project):pass
  for directory,dirs,files in os.walk(project,followlinks=False,onerror=lambda e:(_ for _ in ()).throw(e)):
   dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink()]
   for name in files:
    if time.monotonic()>deadline:raise engine.Unsafe('Claude activity scan incomplete')
    if name.endswith('.jsonl'):observe(Path(directory)/name,'Claude session')
 if not latest:raise engine.Unsafe('no reliable activity timestamps')
 return dict(timestamp=latest,days=max(0,(now-latest)/86400),source=source)

def enforce_idle_days(root,days):
 evidence=activity(root)
 if evidence['days']<days:raise engine.Unsafe('used more recently than the selected age threshold')
 return evidence
