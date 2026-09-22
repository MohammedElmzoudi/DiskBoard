import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending={pool.submit(catalog.scan,a):a for a in areas}
        for future in as_completed(pending):
            rows.append(future.result())
            if sys.stdout.isatty() and not quiet:
                print('\r  Measured %d/%d · %-40s'%(len(rows),len(areas),ui.safe(pending[future]['title'])[:40]),end='',flush=True)
    if sys.stdout.isatty() and not quiet:print('\r'+' '*85+'\r',end='')
    # Put actionable recovery first, then the largest monitored areas.
    return sorted(rows,key=lambda r:(-r['eligible_bytes'],-(r['total_bytes'] or 0),r['area']['id']))


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


def clean(areas, approved_paths=None):
    if sys.platform!='darwin' or os.geteuid()==0:
        raise ValueError('Cleanup currently supports macOS as your normal user. Do not use sudo.')
    results=[]
    before,_=disk()
    with audit() as (log,path):
        log('start',area_ids=[a['id'] for a in areas],free_bytes=before)
        for area in areas:
            if area['kind']=='inspect':
                results.append(dict(id=area['id'],status='inspect-only',completed=0))
                continue
            events=[]
            def record(event,**kw):
                log(event,area_id=area['id'],**kw)
                events.append(dict(event=event,**kw))
            cleaner=engine.Cleaner(True,record)
            cleaner.allowed_paths=approved_paths
            with contextlib.redirect_stdout(io.StringIO()):
                catalog.execute_handler(area,cleaner)
            completed=sum(e['event']=='completed' for e in events)
            results.append(dict(id=area['id'],status='cleaned' if completed else 'kept',
                                completed=completed,skips=cleaner.skipped,
                                reasons=[e['reason'] for e in events if e['event']=='skip']))
        after,_=disk()
        log('finish',free_bytes=after,results=results)
    return dict(results=results,free_bytes=after,net_change_bytes=after-before,audit=str(path))


def main(argv=None):
    parser=argparse.ArgumentParser(description='Pick disposable caches. Keep the important.')
    from diskpick_version import VERSION
    parser.add_argument('--version',action='version',version='DiskBoard '+VERSION)
    parser.add_argument('command',nargs='?',choices=['scan','clean','config','status','prompt','rules','frame'])
    parser.add_argument('--areas',help='Stable area IDs, comma or space separated (for agents)')
    parser.add_argument('--yes',action='store_true',help='Required for noninteractive cleanup')
    parser.add_argument('--json',action='store_true',help='Machine-readable output')
    parser.add_argument('--config',type=Path,help='Explicit area catalog')
    parser.add_argument('--demo',action='store_true',help='Interactive synthetic demo; never scans or deletes files')
    parser.add_argument('--workbench',action='store_true',help='Storage UI only, without an agent pane')
    parser.add_argument('--agent',choices=['claude','codex'],help='Start split panes with this CLI')
    parser.add_argument('--resume',help='Reattach a retained diskpick agent workspace')
    parser.add_argument('--report-demo',action='store_true',help='Agent report demo; no disk scan or deletion')
    args=parser.parse_args(argv)
    if args.command=='frame':
        if args.demo or args.report_demo or args.agent or args.resume or args.workbench or args.json or args.yes or args.areas or args.config:parser.error('frame accepts only JSON on standard input')
        import diskpick_report
        raw=sys.stdin.read(diskpick_report.MAX_BYTES+1)
        if len(raw.encode('utf-8'))>diskpick_report.MAX_BYTES:raise ValueError('The report exceeds 256 KiB.')
        try:data=diskpick_report.decode_json(raw)
        except (json.JSONDecodeError,RecursionError) as exc:raise ValueError('Invalid report JSON.') from exc
        print(diskpick_report.frame(data),end='')
        return 0
    if args.report_demo:
        if args.command or args.demo or args.agent or args.resume or args.workbench or args.json or args.yes or args.areas:parser.error('--report-demo cannot be combined with other modes')
        import diskpick_report_ui
        return diskpick_report_ui.launch([],scan,clean,demo=True)
    if args.command=='prompt':
        if args.json or args.yes or args.areas or args.agent or args.resume or args.workbench:parser.error('prompt does not accept cleanup or pane options')
        import diskpick_report
        print(diskpick_report.prompt(diskpick_report.new_token(),args.config))
        return 0
    pane_options=args.workbench or args.agent or args.resume
    if pane_options and (args.command or args.json or args.demo or args.areas or args.yes):parser.error('Pane options require the interactive workbench')
    if sum(bool(x) for x in (args.workbench,args.agent,args.resume))>1:parser.error('Choose only one pane option')
    if pane_options and not sys.stdin.isatty():parser.error('Pane options require an interactive terminal')
    if args.demo:
        if args.command or args.areas or args.yes or args.json:
            parser.error('--demo is only an isolated interactive demonstration')
        import diskpick_tui
        return diskpick_tui.launch([],scan,clean,demo=True)
    if args.command=='config':
        print(json.dumps({'user_config':str(catalog.CONFIG),'template':str(Path(catalog.__file__).with_name('areas.json'))},indent=2))
        return 0
    if args.command=='status':
        free,total=disk()
        print(json.dumps(dict(free_bytes=free,total_bytes=total)) if args.json else 'Free: '+ui.amount(free))
        return 0
    areas=catalog.load(args.config)
    if args.command=='rules':
        if args.yes or args.areas:parser.error('rules is read-only and does not accept cleanup options')
        expanded=catalog.expand(areas)
        print(json.dumps({'version':1,'notice':'Rules only. No size or safety check has run.', 'areas':[dict(a,paths=[str(p) for p in catalog.roots(a)]) for a in expanded]},indent=2))
        return 0
    if args.command is None and (args.areas or args.yes):parser.error('--areas and --yes require clean')
    if args.command is None and not args.json and sys.stdin.isatty():
        if not args.workbench:
            import diskpick_panes
            result=diskpick_panes.launch(args.agent,args.config,args.resume)
            if result is not None:return result
        import diskpick_report_ui
        return diskpick_report_ui.launch(areas,scan,clean,config=args.config)
    if args.command!='clean':areas=catalog.expand(areas)
    elif args.areas:
        requested=set(re.split(r'[\s,]+',args.areas.strip()))
        if requested-set(a['id'] for a in areas):areas=catalog.expand(areas)
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
        else:
            for page in range(max(1,(len(rows)+11)//12)):ui.dashboard(rows,free,total,page=page)
        return 0
    return 0


def entry():
    try:return main()
    except KeyboardInterrupt:
        print('\n  Stopped. No broader cleanup attempted.',file=sys.stderr)
        return 130
    except (ValueError,OSError,engine.Unsafe,subprocess.TimeoutExpired) as exc:
        print('diskpick: '+str(exc),file=sys.stderr)
        return 1

