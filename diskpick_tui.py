"""Keyboard/mouse workbench. Uses terminal default colors, never a fixed palette."""
import curses
import concurrent.futures
import queue
import threading
import time
import textwrap
from pathlib import Path
import diskpick_engine as engine
import diskpick_discovery as discovery
import diskpick_worktrees as worktrees
import diskpick_catalog as catalog
import diskpick_ui as ui


def text(value):return ui.safe(value)

class Screen:
 def __init__(self,win):
  self.win=win
  try:curses.use_default_colors()
  except curses.error:pass
  win.bkgd(' ',curses.color_pair(0))
  curses.curs_set(0);win.keypad(True)
  try:curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
  except curses.error:pass
 def line(self,y,value,style=0):
  h,w=self.win.getmaxyx()
  if y<h-1:
   try:self.win.addnstr(y,2,text(value).ljust(max(0,w-4)) if style & curses.A_REVERSE else text(value),max(0,w-4),style)
   except curses.error:pass
 def draw(self,title,subtitle,items,index,detail='',footer='↑ ↓  Move   Enter  Open   Esc  Back',marked=None):
  self.win.erase();h,w=self.win.getmaxyx()
  compact=h<19
  self.line(0 if compact else 1,'DISKBOARD  /  '+title,curses.A_BOLD)
  self.line(1 if compact else 3,subtitle)
  self.line(2 if compact else 4,'─'*max(0,w-4))
  first=3 if compact else 6
  room=max(1,h-(7 if compact else 11));start=max(0,min(index-room//2,len(items)-room))
  for n,item in enumerate(items[start:start+room],start):
   prefix='› ' if n==index else '  '
   if marked is not None:prefix+=('[×] ' if n in marked else '[ ] ')
   self.line(first+n-start,prefix+item,curses.A_REVERSE if n==index else 0)
  self.line(h-4 if compact else h-5,detail)
  self.line(h-2 if compact else h-3,footer,curses.A_BOLD)
  if not compact:self.line(h-2,'%d / %d'%(min(index+1,len(items)),len(items)))
  self.win.refresh()
 def key(self,index,count):
  k=self.win.getch()
  if k in (curses.KEY_DOWN,):return min(count-1,index+1),None
  if k==curses.KEY_UP:return max(0,index-1),None
  if k==curses.KEY_NPAGE:return min(count-1,index+max(1,self.win.getmaxyx()[0]-11)),None
  if k==curses.KEY_PPAGE:return max(0,index-max(1,self.win.getmaxyx()[0]-11)),None
  if k==curses.KEY_HOME:return 0,None
  if k==curses.KEY_END:return max(0,count-1),None
  if k==curses.KEY_MOUSE:
   try:
    _,x,y,_,state=curses.getmouse()
    if state & getattr(curses,'BUTTON4_PRESSED',0):return max(0,index-3),None
    if state & getattr(curses,'BUTTON5_PRESSED',0):return min(count-1,index+3),None
   except curses.error:pass
   return index,None
  return index,k
 def menu(self,title,subtitle,items,details=None):
  index=0
  while True:
   self.draw(title,subtitle,items,index,details[index] if details else '')
   index,k=self.key(index,len(items))
   if k in (27,ord('q')):return None
   if k in (10,13,curses.KEY_ENTER):return index
 def message(self,title,body):self.menu(title,'', textwrap.wrap(text(body),max(20,self.win.getmaxyx()[1]-8))+['Back'])
 def prompt_days(self,current):
  self.win.erase();self.line(2,'Show worktrees unused for at least how many days?',curses.A_BOLD)
  self.line(4,'Current: %d days. Enter a whole number; blank keeps it.'%current)
  self.line(6,'Days: ');self.win.refresh();curses.echo();curses.curs_set(1)
  try:value=self.win.getstr(6,8,5).decode().strip()
  finally:curses.noecho();curses.curs_set(0)
  return int(value) if value.isdigit() and int(value)<=36500 else current
 def wait(self,title,fn,mutating=False):
  result=queue.Queue()
  def run():
   try:result.put((True,fn()))
   except Exception as exc:result.put((False,exc))
  threading.Thread(target=run,daemon=True).start();self.win.timeout(100)
  tick=0
  try:
   while result.empty():
    self.draw(title,'Applying only the reviewed selection; safety checks remain active.' if mutating else 'Inspecting only — no files are being deleted.',['Working '+'.'*(tick%4)],0,footer='Please wait for the reviewed operation to finish.' if mutating else 'Read-only checks. Large repositories can take a moment.')
    self.win.getch();tick+=1
   okay,value=result.get()
   if not okay:raise value
   return value
  finally:self.win.timeout(-1)


def inspect_tree(path):
 row={'path':str(path),'name':path.name,'branch':'—','bytes':None,'days':None,'source':'unavailable','reason':'','timestamp':None}
 try:
  row.update(worktrees.activity(path))
  row['branch']=worktrees.git(path,'symbolic-ref','--short','HEAD')
  row['bytes']=catalog.size(path,time.monotonic()+120)
  discovery.check_worktree(path)
  if row['bytes'] is None:raise engine.Unsafe('size scan incomplete')
 except Exception as exc:row['reason']=str(exc)
 return row

def scan_repo(paths):
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:return list(pool.map(inspect_tree,paths))

def filtered(rows,days):
 # Unknown activity remains visible and blocked, never silently considered old.
 return sorted([r for r in rows if r['days'] is None or r['days']>=days],key=lambda r:(r['timestamp'] is not None,r['timestamp'] or 0,r['path']))

def row_label(r):
 age='age unknown' if r['days'] is None else '%dd ago'%r['days']
 state='KEEP' if r['reason'] else 'READY'
 return '%-10s %9s  %-5s  %s'%(age,ui.amount(r['bytes']),state,r['name'])

def tree_browser(screen,root,paths,clean,demo_rows=None):
 rows=demo_rows if demo_rows is not None else screen.wait('Inspecting '+Path(root).name,lambda:scan_repo(paths));days=14;selected=set();index=0
 while True:
  visible=filtered(rows,days);selected.intersection_update(r['path'] for r in visible if not r['reason'])
  actions=['Unused for at least %d days  ·  Change filter'%days,('Clear selection' if selected and selected=={r['path'] for r in visible if not r['reason']} else 'Select all eligible worktrees'),'Review %d selected worktrees'%len(selected),'Refresh activity and safety checks']
  labels=actions+[('☑ ' if r['path'] in selected else '☐ ')+row_label(r) for r in visible]
  index=min(index,len(labels)-1)
  r=visible[index-4] if index>=4 else None
  detail=(r['reason'] or 'Branch retained: '+r['branch'])+'  |  '+r['path'] if r else root
  screen.draw('Claude worktrees', 'Oldest activity first · %d shown / %d total · timestamps estimate use'%(len(visible),len(rows)),labels,index,detail,'↑ ↓ Scroll   Space Select   Enter Details / Action   Esc Back')
  index,k=screen.key(index,len(labels))
  if k in (27,ord('q')):return
  if k==ord(' ') and index>=4:
   r=visible[index-4]
   if not r['reason']:
    if r['path'] in selected:selected.remove(r['path'])
    else:selected.add(r['path'])
  if k not in (10,13,curses.KEY_ENTER):continue
  if index==0:days=screen.prompt_days(days)
  elif index==1:
   eligible={r['path'] for r in visible if not r['reason']}
   selected=set() if selected==eligible else eligible
  elif index==2:
   chosen=[r for r in visible if r['path'] in selected]
   if not chosen:screen.message('Nothing selected','Use Space on READY rows, or select all eligible worktrees.');continue
   if review(screen,chosen):
    areas=[dict(id='review-'+str(i),title=r['name'],kind='worktree',roots=[r['path']],description='Reviewed Claude checkout',min_idle_days=days) for i,r in enumerate(chosen)]
    result=screen.wait('Rechecking and removing selected checkouts',lambda:clean(areas,set(selected)),mutating=True)
    reasons=[reason for x in result['results'] for reason in x.get('reasons',[])]
    screen.message('Cleanup complete','%d removed · disk change %s · %s'%(sum(x['completed'] for x in result['results']),ui.amount(result['net_change_bytes']),'; '.join(reasons) or 'Branches retained. Audit saved.'))
    if demo_rows is not None:return
    rows=screen.wait('Refreshing',lambda:scan_repo([p for p in paths if p.exists()]));selected.clear()
  elif index==3 and demo_rows is None:rows=screen.wait('Refreshing',lambda:scan_repo([p for p in paths if p.exists()]))
  else:
   r=visible[index-4]
   information=[
    'Path: '+r['path'],'Branch: '+r['branch'],
    'Last observed activity: '+(time.strftime('%Y-%m-%d %H:%M',time.localtime(r['timestamp'])) if r['timestamp'] else 'unknown'),
    'Evidence: '+r['source'],'Safety: '+(r['reason'] or 'Eligible; checked again before removal'),
    'Removes checkout files. Keeps branch, commits and recovery reference.']
   lines=[line for item in information for line in textwrap.wrap(text(item),max(20,screen.win.getmaxyx()[1]-8))]
   screen.menu(r['name'],'Last activity is an estimate, not a record of every read.',lines+['Back'])

def review(screen,chosen):
 index=0
 while True:
  labels=['Back — keep everything','Delete these %d reviewed checkouts'%len(chosen)]+[row_label(r) for r in chosen]
  r=chosen[index-2] if index>=2 else None
  screen.draw('Review selected worktrees','%s selected · scroll the entire list before confirming'%ui.amount(sum(r['bytes'] or 0 for r in chosen)),labels,index,r['path']+' | '+r['branch'] if r else 'Source checkout files will be removed. Git branches and recovery refs stay.','↑ ↓ Scroll   Enter Inspect / Confirm   Esc Cancel')
  index,k=screen.key(index,len(labels))
  if k==27:return False
  if k in (10,13,curses.KEY_ENTER):
   if index==0:return False
   if index==1:return screen.menu('Confirm removal','Every selected checkout is rechecked. Changed or active ones are kept.',['Cancel','Remove reviewed checkouts'])==1
   screen.message(chosen[index-2]['name'],chosen[index-2]['path']+' | branch '+chosen[index-2]['branch'])

def cache_browser(screen,areas,scan,clean):
 rows=screen.wait('Scanning caches',lambda:scan([a for a in areas if a['kind']!='worktree'],True));index=0;selected=set()
 while True:
  labels=['Review selected caches']+[('☑ ' if i in selected else '☐ ')+r['area']['title']+'  '+ui.amount(r['eligible_bytes'])+' eligible' for i,r in enumerate(rows)]
  r=rows[index-1] if index else None
  screen.draw('Caches', 'Space selects · eligible bytes are estimates',labels,index,(r['area']['description'] if r else 'Select caches, then review their exact paths.'),'↑ ↓ Scroll   Space Select   Enter Review / Details   Esc Back')
  index,k=screen.key(index,len(labels))
  if k==27:return
  if k==ord(' ') and index:
   if index-1 in selected:selected.remove(index-1)
   elif rows[index-1]['eligible_bytes']:selected.add(index-1)
  if k in (10,13,curses.KEY_ENTER):
   if index:
    r=rows[index-1];screen.menu(r['area']['title'],r['area']['description'],[ui.amount(p['bytes'])+' '+p['path'] for p in r.get('paths',[])]+r['reasons']+['Back']);continue
   chosen=[rows[i]['area'] for i in sorted(selected)]
   if not chosen:continue
   fresh=screen.wait('Preparing exact preview',lambda:scan(chosen,True));paths={p['path'] for r in fresh for p in r.get('preview',[])}
   if not paths:screen.message('Nothing eligible','Recent or active candidates were preserved.');continue
   choice=screen.menu('Review cache contents','Only these paths may be cleaned. Regeneration may slow the next launch.',['Cancel','Clean reviewed paths']+sorted(paths))
   if choice==1:
    result=screen.wait('Cleaning reviewed caches',lambda:clean(chosen,paths),mutating=True);screen.message('Complete','Net disk change '+ui.amount(result['net_change_bytes']));return

def launch(areas,scan,clean,demo=False):
 def run(win):
  screen=Screen(win)
  if demo:
   rows=[]
   for name,age,size,reason in [('archived-search',92,3.8,''),('old-table-polish',63,2.4,''),('experiment-notes',45,1.2,'Uncommitted work: 0 staged, 1 unstaged, 0 untracked paths'),('review-flow',32,1.8,''),('local-secrets',24,0.9,'Ignored local data: 2 paths; preserved'),('active-session',18,2.1,'Active process holds this checkout open')]:
    rows.append(dict(path='/demo/project/.claude/worktrees/'+name,name=name,days=age,timestamp=time.time()-age*86400,source='Claude session',branch='feature/'+name,bytes=int(size*engine.GIB),reason=reason))
   tree_browser(screen,'DEMO DATA · synthetic repository',[],lambda *a:dict(results=[],net_change_bytes=0),rows)
   return
  while True:
   choice=screen.menu('Storage workbench','Choose what you want to manage.',['Claude worktrees — choose a repository','Caches — review disposable data','Exit'])
   if choice in (None,2):return
   if choice==1:
    expanded=screen.wait('Discovering cleanup areas',lambda:catalog.expand(areas))
    cache_browser(screen,expanded,scan,clean);continue
   groups=screen.wait('Finding root repositories',lambda:worktrees.repositories(discovery.worktrees()))
   roots=sorted(groups)
   claude={r:[p for p in groups[r] if '.claude/worktrees/' in str(p)] for r in roots}
   roots=[r for r in roots if claude[r]]
   if not roots:screen.message('No Claude worktrees found','Discovery checks registered Git worktrees under .claude/worktrees.');continue
   choice=screen.menu('Choose a root repository','Registered checkouts in .claude/worktrees; creator identity is inferred from location.',[Path(r).name+'  ·  '+str(len(claude[r]))+' Claude worktrees' for r in roots],roots)
   if choice is not None:tree_browser(screen,roots[choice],claude[roots[choice]],clean)
 curses.wrapper(run)
 return 0
