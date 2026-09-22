"""Read-only discovery and fixed, narrowly scoped cleanup presets."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import stat
import time
import diskpick_engine as engine

DOWNLOADS = {
 'bun-downloads': ('.bun/install/cache', 'Bun download cache', {'bun','bunx'}),
 'npm-downloads': ('.npm/_cacache', 'npm download cache', {'npm','npx'}),
 'pip-downloads': ('Library/Caches/pip', 'pip download cache', {'pip','pip3','python','python3','uv'}),
 'yarn-downloads': ('Library/Caches/Yarn', 'Yarn download cache', {'yarn'}),
 'brew-downloads': ('Library/Caches/Homebrew/downloads', 'Homebrew downloads', {'brew','ruby','curl'}),
}
MIN_WORKTREE_AGE = 7*86400
BUILD_PATHS = ('main/site/static/js', 'node_modules/.cache')

def presets():
 return [dict(id=k,title=v[1],kind='downloads',description='Deletes download copies older than 7 days when the owner is idle. Future installs may download again.') for k,v in DOWNLOADS.items()]

def worktrees():
 """Discover registered checkouts without crawling dependency trees."""
 home=Path.home(); seeds=[]
 for p in home.iterdir():
  if not p.is_symlink() and p.is_dir() and ((p/'.git').is_dir() or (p/'.git').is_file()):seeds.append(p)
 base=home/'.codex/worktrees'
 if base.is_dir() and not base.is_symlink():
  for bucket in base.iterdir():
   if bucket.is_dir() and not bucket.is_symlink():
    seeds.extend(p for p in bucket.iterdir() if not p.is_symlink() and (p/'.git').exists())
 found={}
 for p in seeds:
  try:
   result=engine.run(['git','-C',str(p),'worktree','list','--porcelain','-z'],timeout=5)
   if result.returncode:continue
   for field in result.stdout.split('\0'):
    if field.startswith('worktree '):
     q=Path(field[9:])
     if q.is_absolute() and not q.is_symlink() and q.is_dir():found[str(q)]=q
  except (OSError,subprocess.TimeoutExpired):continue
 return sorted(found.values())

def generated_areas(paths):
 rows=[]
 for p in paths:
  if not any((p/r).is_dir() and not (p/r).is_symlink() for r in BUILD_PATHS):continue
  key=hashlib.sha256(str(p).encode()).hexdigest()[:12]
  rows.append(dict(id='build-'+key,title='Build cache · '+p.name,kind='generated',roots=[str(p)],description='Deletes ignored JS builds and tool caches in an idle checkout older than 7 days. Next dev launch may rebuild. Source, dependencies and worktree stay.'))
 return rows

def targets(area):
 if area['kind']=='downloads':return [Path.home()/DOWNLOADS[area['id']][0]]
 return [Path(area['roots'][0]).expanduser()/r for r in BUILD_PATHS]

def execute(area,cleaner):
 for p in targets(area):
  if not p.exists():continue
  try:
   if area['kind']=='downloads':
    def owner_guard():
     if not cleaner.apply and getattr(cleaner,'owner_checked',False):return
     names,commands=engine.processes()
     if any(token in commands for token in ('npm-cli.js','npx-cli.js','yarn.js','yarn.cjs','-m pip','/bin/pip')):raise engine.Unsafe('package manager is running')
     if names & DOWNLOADS[area['id']][2]:raise engine.Unsafe('package owner is running')
     cleaner.owner_checked=True
    cleaner.activity_guard=owner_guard
    owner_guard()
    # Clean old entries individually; one recent download need not block all old copies.
    with engine.directory(p) as fd:
     names=os.listdir(fd)
    # Regular files in these fixed download stores are cached downloads.
    for name in names:
     child=p/name
     if child.is_dir() and not child.is_symlink():
      try:cleaner.clean_tree(child,p,7*86400)
      except (OSError,engine.Unsafe,subprocess.TimeoutExpired) as exc:cleaner.skip(child,exc)
     elif child.is_file() and not child.is_symlink():clean_download_file(child,p,cleaner)
   else:
    root=Path(area['roots'][0]).expanduser()
    birth=getattr(root.stat(),'st_birthtime',None)
    if birth is None or time.time()-birth<7*86400:raise engine.Unsafe('checkout is recent or creation age unavailable')
    rel=str(p.relative_to(root))
    result=engine.run(['git','--no-optional-locks','-C',str(root),'ls-files','--',rel])
    if result.returncode or result.stderr or result.stdout:raise engine.Unsafe('tracked files or failed Git inspection')
    result=engine.run(['git','-C',str(root),'check-ignore','-q',rel])
    if result.returncode:raise engine.Unsafe('directory is not Git-ignored')
    cleaner.clean_tree(p,root,7*86400)
  except (OSError,engine.Unsafe,subprocess.TimeoutExpired) as exc:cleaner.skip(p,exc)


def clean_download_file(path,parent,cleaner):
 try:
  allowed=getattr(cleaner,'allowed_paths',None)
  if allowed is not None and str(path) not in allowed:return
  with engine.directory(parent) as fd:
   st=os.stat(path.name,dir_fd=fd,follow_symlinks=False)
   if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid():raise engine.Unsafe('unexpected download file')
   if time.time()-st.st_mtime<7*86400:raise engine.Unsafe('download is recent')
   getattr(cleaner,'activity_guard',lambda:None)()
   cleaner.check_idle(parent)
   saved=engine.fingerprint(st)
   if engine.fingerprint(os.stat(path.name,dir_fd=fd,follow_symlinks=False))!=saved:raise engine.Unsafe('download changed')
   cleaner.candidates+=1;cleaner.allocated+=st.st_blocks*512
   cleaner.log('candidate',path=str(path),allocated_bytes=st.st_blocks*512)
   if cleaner.apply:
    getattr(cleaner,'activity_guard',lambda:None)()
    cleaner.check_idle(parent)
    cleaner.log('intent',path=str(path),stat=saved)
    if engine.fingerprint(os.stat(path.name,dir_fd=fd,follow_symlinks=False))!=saved:raise engine.Unsafe('download changed before deletion')
    os.unlink(path.name,dir_fd=fd)
    cleaner.log('completed',path=str(path),allocated_bytes=st.st_blocks*512)
 except (OSError,engine.Unsafe,subprocess.TimeoutExpired) as exc:cleaner.skip(path,exc)


def check_worktree(root):
 """No ignored data, no dirty files, no detached HEAD, no base checkouts."""
 with engine.directory(root):pass
 if root.name in {'main copy','main_prs_to_review'} or re.fullmatch(r'main(?:[0-9]+|-master)?',root.name):raise engine.Unsafe('protected base worktree')
 if not (root/'.git').is_file() or (root/'.git').is_symlink():raise engine.Unsafe('not a linked worktree')
 born=getattr(root.stat(),'st_birthtime',None)
 if born is None or time.time()-born<MIN_WORKTREE_AGE:raise engine.Unsafe('worktree created within 7 days or age unavailable')
 result=engine.run(['git','--no-optional-locks','-C',str(root),'status','--porcelain','--untracked-files=all'])
 if result.returncode or result.stderr:raise engine.Unsafe('Git status inspection failed')
 if result.stdout:
  lines=result.stdout.splitlines()
  staged=sum(x[:1] not in {' ','?'} for x in lines)
  unstaged=sum(len(x)>1 and x[1] not in {' ','?'} for x in lines)
  untracked=sum(x.startswith('??') for x in lines)
  raise engine.Unsafe('Uncommitted work: %d staged, %d unstaged, %d untracked paths'%(staged,unstaged,untracked))
 result=engine.run(['git','--no-optional-locks','-C',str(root),'ls-files','--others','--ignored','--exclude-standard'])
 if result.returncode or result.stderr:raise engine.Unsafe('Ignored-file inspection failed')
 if result.stdout:raise engine.Unsafe('Ignored local data: %d paths; preserved'%len(result.stdout.splitlines()))
 result=engine.run(['git','-C',str(root),'symbolic-ref','--quiet','HEAD'])
 if result.returncode or not result.stdout.startswith('refs/heads/'):raise engine.Unsafe('detached or unreadable branch')
 branch=result.stdout.strip()
 engine.idle(root)
 return branch


def retire_worktree(area,cleaner):
 root=Path(area['roots'][0]).expanduser()
 try:
  branch=check_worktree(root)
  if 'min_idle_days' in area:
   from diskpick_worktrees import enforce_idle_days
   enforce_idle_days(root,area['min_idle_days'])
  result=engine.run(['du','-sk',str(root)],timeout=120)
  if result.returncode or result.stderr:raise engine.Unsafe('size inspection failed')
  size=int(result.stdout.split()[0])*1024
  cleaner.candidates+=1;cleaner.allocated+=size
  cleaner.log('candidate',path=str(root),allocated_bytes=size,retained_branch=branch)
  if not cleaner.apply:return
  if str(root) not in (getattr(cleaner,'allowed_paths',None) or set()):raise engine.Unsafe('worktree removal requires interactive preview confirmation')
  if check_worktree(root)!=branch:raise engine.Unsafe('branch changed')
  if 'min_idle_days' in area:enforce_idle_days(root,area['min_idle_days'])
  head=engine.run(['git','-C',str(root),'rev-parse','HEAD'])
  if head.returncode:raise engine.Unsafe('cannot preserve commit')
  recovery='refs/diskpick-retired/'+hashlib.sha256(str(root).encode()).hexdigest()[:12]+'-'+str(time.time_ns())
  saved=engine.run(['git','-C',str(root),'update-ref',recovery,head.stdout.strip()])
  if saved.returncode:raise engine.Unsafe('cannot create recovery reference')
  cleaner.log('intent',path=str(root),retained_branch=branch,recovery_ref=recovery,commit=head.stdout.strip())
  result=engine.run(['git','-C',str(root),'worktree','remove',str(root)],timeout=120)
  if result.returncode:raise engine.Unsafe(result.stderr.strip() or 'Git refused removal')
  cleaner.log('completed',path=str(root),allocated_bytes=size,retained_branch=branch)
 except (OSError,ValueError,engine.Unsafe,subprocess.TimeoutExpired) as exc:cleaner.skip(root,exc)
