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

CONFIG = Path.home()/'.config/areas.json'


def validate(data):
    if data.get('version') != 1 or not isinstance(data.get('areas'), list):
        raise ValueError('Expected version: 1 and an areas list')
    seen = set()
    for area in data['areas']:
        if not isinstance(area, dict):
            raise ValueError('Each area must be an object')
        if set(area) - {'id','title','kind','roots','cache','description'}:
            raise ValueError('Unknown area field; executable commands are not supported')
        key = area.get('id', '')
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', key) or key in seen:
            raise ValueError('Area IDs must be unique lowercase names')
        seen.add(key)
        for field in ('title', 'description'):
            value = area.get(field, '')
            if not isinstance(value, str) or not value or len(value)>180 or not value.isprintable():
                raise ValueError('Area %s needs a printable %s' % (key, field))
        if area.get('kind') not in {'rust','browser','inspect'}:
            raise ValueError('Unknown handler kind for '+key)
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
    return validate(json.loads(chosen.read_text()))


def size(path, deadline):
    """Bounded, non-following inventory. None means incomplete, not zero."""
    total = 0
    try:
        with engine.directory(path) as root:
            device = os.fstat(root).st_dev
            def visit(fd):
                nonlocal total
                if time.monotonic()>deadline:
                    raise TimeoutError()
                for name in os.listdir(fd):
                    if time.monotonic()>deadline:
                        raise TimeoutError()
                    st = os.stat(name,dir_fd=fd,follow_symlinks=False)
                    if st.st_dev != device:
                        raise OSError('nested filesystem')
                    if stat.S_ISREG(st.st_mode):
                        total += st.st_blocks*512
                    elif stat.S_ISDIR(st.st_mode):
                        with engine.child_directory(fd,(name,)) as child:
                            visit(child)
            visit(root)
    except FileNotFoundError:
        return 0
    except (OSError, TimeoutError, engine.Unsafe):
        return None
    return total


def roots(area):
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
    inventory_error=None
    try:
        paths=roots(area)
        if area['kind']=='rust':paths=[p/'incremental' for p in paths]
        sizes=[size(p,time.monotonic()+3) for p in paths]
        total=None if any(s is None for s in sizes) else sum(sizes)
        if total is None:inventory_error='Inventory incomplete (access restriction or time limit); unknown is not zero.'
        if area['kind']!='inspect':
            with contextlib.redirect_stdout(io.StringIO()):
                execute_handler(area,cleaner)
    except (OSError,engine.Unsafe) as exc:
        inventory_error=str(exc)
        total=None
    reasons=[e['reason'] for e in events if e['event']=='skip']
    if inventory_error:reasons.append(inventory_error)
    if area['kind']=='inspect':status='MANUAL'
    elif area['kind']=='rust' and not area.get('roots'):status='SETUP'
    elif cleaner.candidates:status='READY'
    elif total==0 and not reasons:status='EMPTY'
    elif any('recent' in x for x in reasons):status='RECENT'
    elif reasons:status='SKIPPED'
    else:status='KEPT'
    return dict(area=area,total_bytes=total,eligible_bytes=cleaner.allocated,
                candidates=cleaner.candidates,status=status,reasons=reasons)


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
