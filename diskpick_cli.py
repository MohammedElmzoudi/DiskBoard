import argparse
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid
import diskpick_catalog as catalog
import diskpick_engine as engine
import diskpick_ui as ui

STATE=Path.home()/'.local/state/diskpick'


def disk():
    s=os.statvfs(Path.home())
    return s.f_bavail*s.f_frsize,s.f_blocks*s.f_frsize


def scan(areas,quiet=False):
    rows=[]
    for i,area in enumerate(areas,1):
        if sys.stdout.isatty() and not quiet:
            print('\r  Checking %d/%d · %-40s'%(i,len(areas),area['title']),end='',flush=True)
        rows.append(catalog.scan(area))
    if sys.stdout.isatty() and not quiet:print('\r'+' '*85+'\r',end='')
    return rows


@contextlib.contextmanager
def audit():
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    with engine.directory(STATE) as parent:
        lock=os.open('run.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=parent)
        try:
            st=os.fstat(lock)
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1:
                raise engine.Unsafe('Unexpected maintenance lock')
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            name=time.strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8]+'.jsonl'
            fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
            with os.fdopen(fd,'w') as journal:
                def log(event,**kw):
                    journal.write(json.dumps(dict(event=event,time=time.time(),**kw))+'\n')
                    journal.flush()
                    os.fsync(journal.fileno())
                yield log,STATE/name
        finally:os.close(lock)


def clean(areas):
    if sys.platform!='darwin' or os.geteuid()==0:
        raise ValueError('Cleanup currently supports macOS as your normal user. Do not use sudo.')
    results=[]
    before,_=disk()
    with audit() as (log,path):
        log('start',area_ids=[a['id'] for a in areas],free_bytes=before)
        for area in areas:
            if area['kind']=='inspect':
                results.append(dict(id=area['id'],status='manual',completed=0))
                continue
            events=[]
            def record(event,**kw):
                log(event,area_id=area['id'],**kw)
                events.append(dict(event=event,**kw))
            cleaner=engine.Cleaner(True,record)
            with contextlib.redirect_stdout(io.StringIO()):
                catalog.execute_handler(area,cleaner)
            completed=sum(e['event']=='completed' for e in events)
            results.append(dict(id=area['id'],status='cleaned' if completed else 'kept',
                                completed=completed,skips=cleaner.skipped,
                                reasons=[e['reason'] for e in events if e['event']=='skip']))
        after,_=disk()
        log('finish',free_bytes=after,results=results)
    return dict(results=results,free_bytes=after,net_change_bytes=after-before,audit=str(path))


def demo_rows():
    # Explicit synthetic data: no filesystem scanning or deletion in this mode.
    entries=[('rust','Rust incremental sessions',18.6,6.2,'READY'),
             ('browser-http','Test browser · HTTP',2.4,1.8,'READY'),
             ('browser-code','Test browser · code',0.84,0,'RECENT'),
             ('browser-gpu','Test browser · GPU',0.32,0.32,'READY'),
             ('packages','Package download stores',4.6,0,'MANUAL'),
             ('ad-qa','Advertisement exports + QA',24.2,0,'MANUAL'),
             ('worktrees','Development worktrees',13.9,0,'MANUAL')]
    return [dict(area=dict(id=id,title=title,description='Demonstration data; no files are deleted.',kind='inspect'),
                 total_bytes=int(total*engine.GIB),eligible_bytes=int(eligible*engine.GIB),
                 candidates=int(eligible>0),status=status,reasons=[])
            for id,title,total,eligible,status in entries]


def interactive(areas,demo=False):
    notice=''
    rows=demo_rows() if demo else scan(areas)
    free,total=(12.4*engine.GIB,460*engine.GIB) if demo else disk()
    while True:
        if sys.stdout.isatty():print('\033[2J\033[H',end='')
        ui.dashboard(rows,free,total,notice,demo)
        try:value=ui.prompt().strip()
        except EOFError:return 0
        if value.lower() in {'q','quit','exit'}:return 0
        if value.lower()=='d':
            ui.details(rows)
            input('\n  Enter to return ')
            continue
        if value.lower()=='r':
            rows=demo_rows() if demo else scan(areas)
            free,total=(12.4*engine.GIB,460*engine.GIB) if demo else disk()
            notice='Refreshed. No files deleted.'
            continue
        try:
            indices=catalog.parse_selection(value,len(rows))
            selected=[rows[n-1] for n in indices]
            if demo:
                recovered=sum(r['eligible_bytes'] for r in selected)
                for row in selected:
                    row['total_bytes']-=row['eligible_bytes']
                    row['eligible_bytes']=0
                    if row['status']=='READY':row['status']='KEPT' if row['total_bytes'] else 'EMPTY'
                free+=recovered
                notice='DEMO · simulated %s recovery · no real files deleted'%ui.amount(recovered)
            else:
                print('  Rechecking selected areas before cleanup…',flush=True)
                result=clean([r['area'] for r in selected])
                count=sum(r['completed'] for r in result['results'])
                notice='%d cache trees cleaned · net disk change %s · audit saved'%(count,ui.amount(result['net_change_bytes']))
                rows=scan(areas)
                free,total=disk()
        except (ValueError,OSError,engine.Unsafe) as exc:
            notice=str(exc)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Pick disposable caches. Keep the important.')
    parser.add_argument('command',nargs='?',choices=['scan','clean','config','status'])
    parser.add_argument('--areas',help='Stable area IDs, comma or space separated (for agents)')
    parser.add_argument('--yes',action='store_true',help='Required for noninteractive cleanup')
    parser.add_argument('--json',action='store_true',help='Machine-readable output')
    parser.add_argument('--config',type=Path,help='Explicit area catalog')
    parser.add_argument('--demo',action='store_true',help='Interactive synthetic demo; never scans or deletes files')
    args=parser.parse_args(argv)
    if args.demo:
        if args.command or args.areas or args.yes or args.json:
            parser.error('--demo is only an isolated interactive demonstration')
        return interactive([],demo=True)
    if args.command=='config':
        print(json.dumps({'user_config':str(catalog.CONFIG),'template':str(Path(catalog.__file__).with_name('areas.json'))},indent=2))
        return 0
    if args.command=='status':
        free,total=disk()
        print(json.dumps(dict(free_bytes=free,total_bytes=total)) if args.json else 'Free: '+ui.amount(free))
        return 0
    areas=catalog.load(args.config)
    if args.command=='clean':
        if not args.yes or not args.areas:
            parser.error('Noninteractive cleanup requires --areas <stable IDs> --yes')
        keys=list(dict.fromkeys(re.split(r'[\s,]+',args.areas.strip())))
        byid={a['id']:a for a in areas}
        if any(k not in byid for k in keys):parser.error('Unknown area ID; nothing cleaned')
        result=clean([byid[k] for k in keys])
        print(json.dumps(result,indent=2))
        return 0
    if args.areas or args.yes:parser.error('--areas and --yes require clean')
    if args.command=='scan' or args.json or not sys.stdin.isatty():
        rows=scan(areas,args.json)
        free,total=disk()
        if args.json:print(json.dumps(dict(free_bytes=free,total_bytes=total,areas=rows),indent=2))
        else:ui.dashboard(rows,free,total)
        return 0
    return interactive(areas)


def entry():
    try:return main()
    except KeyboardInterrupt:
        print('\n  Stopped. No broader cleanup attempted.',file=sys.stderr)
        return 130
    except (ValueError,OSError,engine.Unsafe,subprocess.TimeoutExpired) as exc:
        print('diskpick: '+str(exc),file=sys.stderr)
        return 1

