"""Declarative area registry: configuration selects handlers, never shell commands."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import stat
import time
import diskpick_engine as engine
import diskpick_discovery as discovery
import subprocess
from concurrent.futures import ThreadPoolExecutor

CONFIG = Path.home()/'.config/diskpick/areas.json'
DISABLED=set()
DISCOVER=True


def validate(data):
    if data.get('version') != 1 or not isinstance(data.get('areas'), list):
        raise ValueError('Expected version: 1 and an areas list')
    seen = set()
    for area in data['areas']:
        if not isinstance(area, dict):
            raise ValueError('Each area must be an object')
        if set(area) - {'id','title','kind','roots','cache','description','min_idle_days'}:
            raise ValueError('Unknown area field; executable commands are not supported')
        if 'min_idle_days' in area and (area.get('kind')!='worktree' or type(area['min_idle_days']) is not int or not 0<=area['min_idle_days']<=36500):raise ValueError('Invalid idle-day threshold')
        key = area.get('id', '')
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', key) or key in seen:
            raise ValueError('Area IDs must be unique lowercase names')
        seen.add(key)
        for field in ('title', 'description'):
            value = area.get(field, '')
            if not isinstance(value, str) or not value or len(value)>180 or not value.isprintable():
                raise ValueError('Area %s needs a printable %s' % (key, field))
        if area.get('kind') not in {'rust','browser','inspect','downloads','generated','worktree'}:
            raise ValueError('Unknown handler kind for '+key)
        if area['kind']=='downloads' and (area['id'] not in discovery.DOWNLOADS or area.get('roots')):
            raise ValueError('Download handlers use fixed built-in paths')
        if area['kind'] in {'generated','worktree'} and len(area.get('roots',[]))!=1:
            raise ValueError('Generated build areas require one checkout root')
        roots = area.get('roots', [])
        if not isinstance(roots, list) or any(not isinstance(p,str) or not p.isprintable() for p in roots):
            raise ValueError('roots must be a list of paths')
        for root in roots:
            p = Path(root).expanduser()
            if not p.is_absolute() or '..' in p.parts:
                raise ValueError('Roots must be absolute or home-relative, without traversal')
            if area['kind']=='rust' and p.parts[-2:] != ('target','debug'):
                raise ValueError('Rust roots must end in target/debug')
        if area['kind']=='browser' and (roots or area.get('cache') not in engine.CACHE_NAMES):
            raise ValueError('Browser areas use the fixed test-profile root and an allowed cache name')
    return data['areas']


def load(path=None):
    chosen = Path(path) if path else CONFIG
    if not chosen.exists():
        if path:
            raise ValueError('Config does not exist: '+str(chosen))
        chosen = Path(__file__).with_name('areas.json')
    if chosen.is_symlink():
        raise ValueError('Config must not be a symlink')
    data=json.loads(chosen.read_text())
    global DISABLED,DISCOVER
    disabled=data.get('disabled',[])
    if not isinstance(disabled,list) or any(not isinstance(x,str) for x in disabled):raise ValueError('disabled must be a list of area IDs')
    DISABLED=set(disabled);DISCOVER=data.get('discovery',True) is not False
    return [a for a in validate(data) if a['id'] not in DISABLED]


def size(path, deadline):
    """Bounded, non-following inventory. None means incomplete, not zero."""
    try:path.lstat()
    except FileNotFoundError:return 0
    except OSError:return None
    try:
        with engine.directory(path):
            result=engine.run(['/usr/bin/du','-skx',str(path)],timeout=max(1,deadline-time.monotonic()))
        if result.returncode or result.stderr:return None
        return int(result.stdout.split()[0])*1024
    except (OSError, ValueError, subprocess.TimeoutExpired, engine.Unsafe):return None


def roots(area):
    if area['kind']=='worktree':return [Path(p).expanduser() for p in area['roots']]
    if area['kind'] in {'downloads','generated','worktree'}:return discovery.targets(area)
    if area['kind']=='browser':
        base=engine.PROFILES
        try:
            with engine.directory(base) as fd:
                return [base/n/'Default'/area['cache'] for n in os.listdir(fd)
                        if re.fullmatch(r'mcp-chrome-[a-zA-Z0-9_-]+',n)]
        except FileNotFoundError:
            return []
    return [Path(p).expanduser() for p in area.get('roots',[])]


def execute_handler(area, cleaner):
    if area['kind']=='worktree':
        discovery.retire_worktree(area,cleaner)
        return
    if area['kind'] in {'downloads','generated','worktree'}:
        discovery.execute(area,cleaner)
        return
    if area['kind']=='browser':
        cleaner.browsers(cache_names=(area['cache'],))
    elif area['kind']=='rust':
        for root in roots(area):
            cargo = root.parent.parent/'Cargo.toml'
            if not cargo.is_file() or cargo.is_symlink():
                cleaner.skip(root, 'No regular Cargo.toml at project root')
                continue
            cleaner.rust(root)


def scan(area):
    events=[]
    cleaner=engine.Cleaner(False, lambda event,**kw:events.append(dict(event=event,**kw)))
    cleaner.quiet=True
    inventory_error=None
    paths=[];sizes=[]
    try:
        paths=roots(area)
        if area['kind']=='rust':paths=[p/'incremental' for p in paths]
        with ThreadPoolExecutor(max_workers=4) as inventory:
            sizes=list(inventory.map(lambda p:size(p,time.monotonic()+120),paths))
        counted=[n for p,n in zip(paths,sizes) if not any(parent!=p and parent in p.parents for parent in paths)]
        total=None if any(n is None for n in counted) else sum(counted)
        if total is None:inventory_error='Size unavailable: permission denied, changed folder, or 120-second timeout. Use details to see paths; nothing is assumed empty.'
        if area['kind']!='inspect':
            execute_handler(area,cleaner)
    except (OSError,engine.Unsafe,subprocess.TimeoutExpired) as exc:
        inventory_error=str(exc)
        total=None
    reasons=[e['reason'] for e in events if e['event']=='skip']
    if inventory_error:reasons.append(inventory_error)
    if area['kind']=='inspect':status='BROWSE'
    elif area['kind']=='rust' and not area.get('roots'):status='SETUP'
    elif cleaner.candidates:status='READY'
    elif total==0 and not reasons:status='EMPTY'
    elif any('recent' in x for x in reasons):status='RECENT'
    elif reasons:status='SKIPPED'
    else:status='KEPT'
    return dict(area=area,total_bytes=total,eligible_bytes=cleaner.allocated,
                candidates=cleaner.candidates,status=status,reasons=reasons,
                paths=[dict(path=str(p),bytes=n) for p,n in zip(paths,sizes)],
                preview=[e for e in events if e['event']=='candidate'])


def parse_selection(text, count):
    tokens=re.split(r'[\s,]+',text.strip())
    if not text.strip() or any(not re.fullmatch(r'[0-9]+',t) for t in tokens if t):
        raise ValueError('Use numbers separated by spaces or commas, e.g. 1 4, 2')
    values=[]
    for token in tokens:
        if not token:continue
        value=int(token)
        if value<1 or value>count:
            raise ValueError('Choose numbers from 1 to %d; nothing was cleaned.'%count)
        if value not in values:values.append(value)
    if not values:raise ValueError('Choose at least one area')
    return values


def expand(areas):
    areas=list(areas)
    if not DISCOVER:return areas
    known={a['id'] for a in areas}
    # New built-ins are opt-in through selection; scanning never deletes.
    areas += [a for a in discovery.presets() if a['id'] not in known]
    for key,title,root in [('downloads-folder','Downloads (personal files)','~/Downloads'),('docker-data','Docker virtual disk','~/Library/Containers/com.docker.docker'),('app-caches','Application caches','~/Library/Caches'),('developer-tools','Xcode and developer data','~/Library/Developer'),('agent-history','Agent history and workspaces','~/.codex')]:
        if key not in known:
            areas.append(dict(id=key,title=title,kind='inspect',roots=[root],description='Size monitor only. May contain valuable data; never deleted by diskpick.'))
    paths=discovery.worktrees()
    if paths:
        areas=[a for a in areas if a['id']!='worktrees']
        areas.append(dict(id='worktrees',title='Development worktrees',kind='inspect',roots=[str(p) for p in paths],description='Registered checkouts. Browse individual sizes; source and worktrees are never automatically deleted.'))
        areas += [a for a in discovery.generated_areas(paths) if a['id'] not in known]
        for p in paths:
            if re.fullmatch(r'main(?:[0-9]+|-master)?',p.name):continue
            if not (p/'.git').is_file():continue
            born=getattr(p.stat(),'st_birthtime',time.time())
            if time.time()-born<7*86400:continue
            key='retire-'+discovery.hashlib.sha256(str(p).encode()).hexdigest()[:12]
            if key not in known:areas.append(dict(id=key,title='Retire checkout · '+p.name,kind='worktree',roots=[str(p)],description='Removes only a clean, idle linked checkout older than 7 days with NO ignored files. Retains its Git branch and commits.'))
    return [a for a in areas if a['id'] not in DISABLED]
