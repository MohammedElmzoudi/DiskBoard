"""Conservative updates for official Git installations. No reset, clean, or force."""
import fcntl
import os
from pathlib import Path
import subprocess
import stat

OFFICIAL='https://github.com/MohammedElmzoudi/DiskBoard.git'
# Existing installations keep working after the public repository rename.
LEGACY_OFFICIAL='https://github.com/MohammedElmzoudi/diskpick.git'
ROOT=Path(__file__).resolve().parent

def git(root,*args):
    env=os.environ.copy()
    for key in list(env):
        if key.startswith('GIT_'):env.pop(key)
    env['GIT_TERMINAL_PROMPT']='0'
    return subprocess.run(['git','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','-c','credential.helper=',*args],cwd=root,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=25).stdout.strip()

def update(root=ROOT,apply=False):
    root=Path(root)
    if not (root/'.git').is_dir() or (root/'.git').is_symlink():
        return 'Updates need an official Git checkout. See UPDATES.md for setup.'
    if git(root,'rev-parse','--show-toplevel')!=str(root.resolve()):return 'Update skipped: this is not the repository root.'
    if git(root,'remote','get-url','origin') not in (OFFICIAL,LEGACY_OFFICIAL):return 'Update skipped: origin is not the official repository.'
    if git(root,'status','--porcelain','--untracked-files=all'):return 'Update skipped: local changes are present. Your files are kept.'
    branch=git(root,'symbolic-ref','--quiet','--short','HEAD')
    upstream=git(root,'rev-parse','--abbrev-ref','@{upstream}')
    if not upstream.startswith('origin/') or upstream!='origin/'+branch:return 'Update skipped: branch tracking does not match origin.'
    # A literal trusted URL avoids user-configured remote push/fetch destinations.
    git(root,'fetch','--no-tags','--no-recurse-submodules',OFFICIAL,'refs/heads/'+branch)
    target=git(root,'rev-parse','FETCH_HEAD')
    before=git(root,'rev-parse','HEAD')
    if before==target:return 'DiskBoard is up to date.'
    git(root,'merge-base','--is-ancestor',before,target)
    if not apply:return 'An update is available. Run diskboard update to install it.'
    if git(root,'status','--porcelain','--untracked-files=all'):return 'Update skipped: files changed during the check.'
    git(root,'merge','--ff-only','--no-edit','--no-overwrite-ignore',target)
    return 'DiskBoard updated. The new version starts on your next launch.'

def run(apply=False):
    try:return update(apply=apply)
    except (OSError,subprocess.SubprocessError):return 'Update could not finish. Check your network and Git branch. No files were reset or removed.'

def start():
    """Hold a shared lease for the app lifetime; updates require an exclusive lease."""
    import sys
    root=ROOT
    lock=None
    if (root/'.git').is_dir() and not (root/'.git').is_symlink():
        fd=os.open(root/'.git/diskboard-run.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1 or st.st_mode&0o022:
            os.close(fd)
            raise RuntimeError('Unsafe update lock. Repository kept unchanged.')
        lock=os.fdopen(fd,'r+')
        explicit=len(sys.argv)>1 and sys.argv[1] in ('update','check-updates')
        interactive=len(sys.argv)==1 and sys.stdin.isatty()
        if explicit or interactive:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                if explicit:print('Update skipped: another DiskBoard window is open. Close it and try again.')
            else:
                print(run(apply=interactive or sys.argv[1]=='update'),flush=True)
            if explicit:
                lock.close()
                return 0
        fcntl.flock(fd,fcntl.LOCK_SH)
    elif len(sys.argv)>1 and sys.argv[1] in ('update','check-updates'):
        print(run());return 0
    from diskpick_cli import entry
    try:return entry()
    finally:
        if lock:lock.close()
