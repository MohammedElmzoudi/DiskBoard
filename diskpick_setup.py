"""Remember existing tools for diskpick only. Never edit PATH or install binaries."""
import contextlib
import curses
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import textwrap
import uuid
import webbrowser
import diskpick_engine as engine

TOOLS={'claude','codex','tmux'}
CONFIG=Path.home()/'.config/diskpick/tools.json'
SESSION={}
GUIDES={'codex':'https://developers.openai.com/codex/cli/',
        'claude':'https://code.claude.com/docs/en/setup',
        'tmux':'https://github.com/tmux/tmux/wiki/Installing'}


def executable(path):
    try:
        p=Path(path).expanduser()
        if not p.is_absolute() or not str(p).isprintable():return None
        real=p.resolve(strict=True);s=real.stat()
        if not stat.S_ISREG(s.st_mode) or not os.access(real,os.X_OK):return None
        if s.st_mode & stat.S_IWOTH:return None
        return str(p)  # Keep a stable package-manager symlink across upgrades.
    except (OSError,ValueError,RuntimeError):return None


def read_at(fd):
    try:f=os.open('tools.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
    except FileNotFoundError:return {'version':1,'tools':{}}
    with os.fdopen(f) as stream:
        s=os.fstat(stream.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or s.st_nlink!=1 or s.st_mode & 0o022 or s.st_size>16384:
            raise ValueError('The tool settings are not a regular private file.')
        def unique(items):
            result={}
            for key,value in items:
                if key in result:raise ValueError('The tool settings contain duplicate keys.')
                result[key]=value
            return result
        try:data=json.load(stream,object_pairs_hook=unique)
        except RecursionError as exc:raise ValueError('The tool settings are nested too deeply.') from exc
    if not isinstance(data,dict) or set(data)!={'version','tools'} or type(data['version']) is not int or data['version']!=1 or not isinstance(data['tools'],dict):
        raise ValueError('The tool settings have an unsupported format.')
    if set(data['tools'])-TOOLS or any(not isinstance(p,str) or not Path(p).is_absolute() or not p.isprintable() for p in data['tools'].values()):
        raise ValueError('The saved tool paths are invalid.')
    return data


def saved():
    try:
        with engine.directory(CONFIG.parent) as fd:return read_at(fd)['tools']
    except FileNotFoundError:return {}


def resolve(name):
    if name not in TOOLS:raise ValueError('Unknown tool.')
    # Match the user's shell first, without adding or repeating PATH entries.
    on_path=shutil.which(name)
    if on_path and executable(on_path):return executable(on_path)
    if name=='tmux' and executable(os.environ.get('DISKPICK_TMUX','')):return os.environ['DISKPICK_TMUX']
    if executable(SESSION.get(name,'')):return SESSION[name]
    try:return executable(saved().get(name,''))
    except (OSError,ValueError,engine.Unsafe):return None


def candidates(name):
    if name not in TOOLS:raise ValueError('Unknown tool.')
    home=Path.home()
    paths=[home/'.local/bin'/name,home/'.npm-global/bin'/name,home/'.volta/bin'/name,
           Path('/opt/homebrew/bin')/name,Path('/usr/local/bin')/name]
    if name=='codex':
        paths += [base/app/'Contents/Resources/codex' for base in (Path('/Applications'),home/'Applications') for app in ('Codex.app','ChatGPT.app')]
    found=[];seen=set()
    for p in paths:
        value=executable(p)
        if value:
            st=Path(value).stat();identity=(st.st_dev,st.st_ino)
            if identity not in seen:found.append(value);seen.add(identity)
    return found


@contextlib.contextmanager
def settings_directory():
    """Create only missing directories below home via non-following descriptors."""
    target=CONFIG.parent
    home=Path.home()
    try:parts=target.relative_to(home).parts;base=home
    except ValueError:
        # Explicit test configs may live in existing temporary directories.
        parts=();base=target
    with engine.directory(base) as start:
        fd=os.dup(start)
        try:
            for part in parts:
                if part in ('.','..'):raise ValueError('Invalid settings directory.')
                try:os.mkdir(part,0o700,dir_fd=fd)
                except FileExistsError:pass
                nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=nxt
            s=os.fstat(fd)
            if s.st_uid!=os.getuid() or s.st_mode & 0o022:raise ValueError('The settings directory must be owned by you and not writable by other users.')
            yield fd
        finally:os.close(fd)


def remember(name,path):
    if name not in TOOLS:raise ValueError('Unknown tool.')
    path=executable(path)
    if not path:raise ValueError('This executable is no longer available.')
    with settings_directory() as fd:
        lock=os.open('.tools.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=fd)
        try:
            s=os.fstat(lock)
            if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or s.st_nlink!=1 or s.st_mode & 0o022:raise ValueError('Unexpected settings lock.')
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            data=read_at(fd)
            if data['tools'].get(name)==path:return
            data['tools'][name]=path
            tmp='.tools-'+uuid.uuid4().hex+'.tmp'
            output=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
            try:
                with os.fdopen(output,'w') as stream:
                    json.dump(data,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
                os.rename(tmp,'tools.json',src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
            finally:
                try:os.unlink(tmp,dir_fd=fd)
                except FileNotFoundError:pass
        finally:os.close(lock)


def confirm(screen,name,path):
    lines=['Found: '+path,'Yes saves this path in diskpick settings only.','Your shell PATH and login stay as they are.']
    detail=' '.join(lines)
    choice=screen.menu('Add '+name+' to diskpick?','An existing installation is available.',
                       ['Yes — use and remember this installation','No — go back','Use once — do not save a setting','Show full path and changes'],
                       [detail, 'Return without changing any file.', 'Use this executable for this run only.', detail])
    if choice==3:
        screen.message('Setup details',detail+' Settings file: '+str(CONFIG))
        return confirm(screen,name,path)
    if choice not in (0,2):return None
    # Revalidate after confirmation; a removed executable must not get saved.
    path=executable(path)
    if not path:
        screen.message('Installation changed','This executable is no longer available. Scan again.');return None
    if choice==0:
        try:remember(name,path)
        except (OSError,ValueError,engine.Unsafe) as exc:
            screen.message('Could not confirm the saved setting',str(exc)+' Your shell and PATH were not changed. You can use this tool once instead.')
            if screen.menu('Use once instead?','No setting will be saved.',['No — go back','Yes — use once'])!=1:return None
    SESSION[name]=path
    return path


def enter_path(screen):
    win=screen.win;win.erase();screen.line(2,'Enter the full path to the installed executable.',curses.A_BOLD)
    screen.line(4,'Do not enter a command or arguments. Blank cancels.');screen.line(6,'Path: ');win.refresh()
    curses.echo();curses.curs_set(1)
    try:value=win.getstr(7,2,4096).decode('utf-8').strip()
    finally:curses.noecho();curses.curs_set(0)
    return value


def ensure(screen,name):
    existing=resolve(name)
    if existing:return existing
    while True:
        found=candidates(name)
        if found:
            if len(found)==1:return confirm(screen,name,found[0])
            choice=screen.menu('Choose an existing '+name,'No new copy is needed.',found+['Back'])
            if choice is None or choice==len(found):return None
            return confirm(screen,name,found[choice])
        choice=screen.menu('Set up '+name,'No executable was found in PATH or common install folders.',
                           ['Choose an installed executable','Open official installation guide','Check again','Back — choose another option'])
        if choice in (None,3):return None
        if choice==0:
            value=enter_path(screen)
            if not value:continue
            path=executable(value)
            if path:return confirm(screen,name,path)
            screen.message('Cannot use this path','Choose a regular executable file. This field does not run shell commands.')
        elif choice==1:
            if screen.menu('Open the installation guide?',GUIDES[name],['No — go back','Yes — open in browser'])==1:
                if not webbrowser.open(GUIDES[name]):screen.message('Open this address',GUIDES[name])


def prompt_tool(name):
    from diskpick_tui import Screen
    return curses.wrapper(lambda win:ensure(Screen(win),name))
