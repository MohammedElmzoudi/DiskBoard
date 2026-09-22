"""Small dependency-free terminal renderer. No terminal content is treated as markup."""
import os
import shutil
import sys
from diskpick_engine import GIB

COLORS={'mint':'\033[38;2;111;232;191m','blue':'\033[38;2;127;184;255m',
        'dim':'\033[39m','white':'\033[39m',
        'amber':'\033[38;2;245;199;111m','red':'\033[38;2;248;140;151m'}


def tint(s,color,enabled=True):
    return COLORS[color]+s+'\033[0m' if enabled else s


def amount(n):
    if n is None:return 'scan error'
    if n<0:return '-'+amount(-n)
    if n>=GIB:return '%.2f GiB'%(n/GIB)
    if n>=1024**2:return '%.1f MiB'%(n/1024**2)
    return '%.0f KiB'%(n/1024)


def dashboard(rows,free,total,notice='',demo=False,color=None,page=0):
    if color is None:color=sys.stdout.isatty() and 'NO_COLOR' not in os.environ
    width=max(60,min(shutil.get_terminal_size((100,30)).columns-4,104))
    line='─'*width
    def p(s='',c='white'):print('  '+tint(s,c,color))
    p()
    p('◒  DiskBoard                                     STORAGE, WITH INTENTION.','mint')
    p('Pick the disposable. Keep the important.','dim')
    p(line,'dim')
    eligible=sum(r['eligible_bytes'] for r in rows)
    p('FREE  %-15s  ELIGIBLE  %-15s  RESERVE  40 GiB'%(amount(free),amount(eligible)))
    used=max(0,min(1,1-free/total)) if total else 0
    n=round(used*36)
    p('▰'*n+'▱'*(36-n)+'   %d%% used  ·  %s below reserve'%(used*100,amount(max(0,40*GIB-free))),
      'amber' if free<40*GIB else 'mint')
    if demo:p('DEMO DATA · synthetic preview; not your disk','amber')
    p(line,'dim')
    title_width=max(23,width-46)
    p(' #   %-*s %11s %11s   %s'%(title_width,'AREA','ON DISK','ELIGIBLE','STATE'),'dim')
    p()
    start=page*12
    for idx,row in enumerate(rows[start:start+12],start+1):
        name=safe(row['area']['title'])
        if len(name)>title_width:name=name[:title_width-1]+'…'
        status=row['status']
        c='mint' if status=='READY' else 'amber' if status in {'RECENT','SKIPPED'} else 'dim'
        p('%2d   %-*s %11s %11s   %s'%(idx,title_width,name,amount(row['total_bytes']),
                                      amount(row['eligible_bytes']),status),c)
    p()
    p(line,'dim')
    p('READY = eligible now. Busy/recent items are kept. BROWSE = view folder sizes.','dim')
    p('Preview first. Active/recent work stays. Retired checkouts keep Git history.','dim')
    if notice:p(notice,'mint')
    p()
    p('Run diskboard in a terminal to open the arrow-key workbench.','blue')
    p('Inventory page %d/%d · this report does not delete files.'%(page+1,max(1,(len(rows)+11)//12)),'dim')
    p()


def safe(value):
    return ''.join(c if c.isprintable() else '?' for c in str(value))


def details(rows):
    for i,row in enumerate(rows,1):
        a=row['area']
        print('\n %s [%s] — %s'%(safe(a['title']),a['id'],row['status']))
        print('    '+a['description'])
        for item in sorted(row.get('paths',[]),key=lambda p:p['bytes'] or 0,reverse=True):
            print('    %12s  %s'%(amount(item['bytes']),safe(item['path'])))
        for reason in dict.fromkeys(row['reasons']):print('    Kept: '+safe(reason))
        candidates=row.get('preview',[])
        if candidates:
            print('    WILL REMOVE (cache directories keep their top-level folder):')
            for item in candidates:print('    %12s  %s'%(amount(item['allocated_bytes']),safe(item['path'])))
        elif a['kind']=='inspect':
            print('    Browse only. No deletion is available for this area.')
        else:print('    No eligible files. Nothing will be removed.')


def prompt():
    return input('  '+tint('DiskBoard › ','mint',sys.stdout.isatty() and 'NO_COLOR' not in os.environ))
