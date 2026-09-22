"""Agent report browser with local, exact-path cleanup review."""
import curses
import json
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import textwrap
import diskpick_catalog as catalog
import diskpick_report as report
import diskpick_ui as ui
from diskpick_tui import Screen


def total(items):
    known=sum(i['bytes'] or 0 for i in items)
    return ui.amount(known)+(' + unmeasured' if any(i['bytes'] is None for i in items) else '')

def details(screen,item):
    fields=['Path: '+item['path'],'Agent estimate: '+('Not measured' if item['bytes'] is None else ui.amount(item['bytes'])),
            'Agent assessment: '+item['risk'],item['description'],'Restore: '+item['regeneration'],
            'Evidence: '+item['evidence'],'Local rule: '+(item['area_id'] or 'None; inspect only')]
    screen.menu(item['title'],'Agent advice is not a safety check.',[s for f in fields for s in textwrap.wrap(f,max(20,screen.win.getmaxyx()[1]-8))]+['Back'])

def prepare(items,areas,scan):
    expanded=catalog.expand(areas)
    ids={i['area_id'] for i in items if i['risk']=='rebuildable' and i['area_id']}
    rows=scan([a for a in expanded if a['id'] in ids and a['kind']!='inspect'],True) if ids else []
    chosen={};paths=set();blocked=[]
    for item in items:
        rules,matched=report.matched_paths(item,rows)
        if not matched:
            reasons=[reason for r in rows if r['area']['id']==item['area_id'] for reason in r.get('reasons',[])]
            blocked.append(item['title']+': '+('; '.join(reasons) or 'No eligible path matches a local cleanup rule.'))
        for rule in rules:chosen[rule['id']]=rule
        paths.update(matched)
    return list(chosen.values()),paths,blocked

def cleanup(screen,items,areas,scan,clean):
    rules,paths,blocked=screen.wait('Check selected paths',lambda:prepare(items,areas,scan))
    if not paths:
        screen.message('No files can be removed',' '.join(blocked) or 'No path was selected.');return False
    # Never accept more paths if the inventory changes after this preview.
    labels=['Cancel','Remove only these checked paths']+['REMOVE  '+p for p in sorted(paths)]+['KEEP  '+x for x in blocked]
    choice=screen.menu('Review exact paths','The check will run again. A rebuild can take time.',labels)
    if choice!=1:return False
    if screen.menu('Confirm removal','Files at these paths will be removed. This action has no undo.',['Cancel','Remove checked files'])!=1:return False
    result=screen.wait('Remove checked files',lambda:clean(rules,paths),mutating=True)
    reasons=[reason for row in result['results'] for reason in row.get('reasons',[])]
    screen.message('Cleanup finished','Net free-space change: '+ui.amount(result['net_change_bytes'])+'. '+(' '.join(reasons) or 'The audit file records the result.'))
    return True

def copy_prompt(screen,feed,config=None):
    # Rotate only after successful copy. Old replies cannot fill a new scan.
    token=report.new_token();body=report.prompt(token,config)
    executable=shutil.which('pbcopy') or shutil.which('wl-copy') or shutil.which('xclip')
    if executable:
        cmd=[executable]+(['-selection','clipboard'] if Path(executable).name=='xclip' else [])
        try:
            subprocess.run(cmd,input=body,text=True,check=True,timeout=5)
        except (OSError,subprocess.SubprocessError):
            screen.message('Copy failed','Your clipboard did not accept the prompt. Try again.');return False
    elif feed.pane:
        # No OS clipboard: an explicit paste from tmux works in the lower pane.
        import diskpick_panes
        import diskpick_setup
        proc=subprocess.run([diskpick_setup.resolve('tmux'),'-L',diskpick_panes.SOCKET,'load-buffer','-b','diskpick-prompt','-'],input=body,text=True,capture_output=True,timeout=5)
        if proc.returncode:
            screen.message('Copy failed','The terminal buffer is not available.');return False
        screen.message('Prompt is in the terminal buffer','Press Shift+Down. Press Ctrl+b, then ] to paste the prompt.')
    else:
        screen.message('Clipboard tool not found','Install pbcopy, wl-copy, or xclip. You can also open diskpick with its agent pane.');return False
    feed.token=token;feed.last='';feed.status='Prompt copied. Press Shift+Down, paste it, and send it.'
    return True


def demo_report():
    return report.validate({'version':1,'scan_id':'demo','summary':'Build files use most of the measured space. One folder could not be measured.',
        'groups':[{'title':'Project build files','description':'These folders contain files made by build tools.','items':[
            {'id':'build','title':'Old build output','path':'/demo/project/target','bytes':12*1024**3,'description':'This folder contains files from earlier builds.','regeneration':'The build command creates these files again.','risk':'rebuildable','evidence':'The fixture has no active build process.','area_id':'demo-build'},
            {'id':'source','title':'Project source','path':'/demo/project/src','bytes':2*1024**3,'description':'This folder contains project source files.','regeneration':'Some edits are not saved in Git.','risk':'keep','evidence':'The fixture has unsaved work.','area_id':None}]},
        {'title':'App data','description':'These folders contain data used by apps.','items':[
            {'id':'cache','title':'Test cache','path':'/demo/cache','bytes':3*1024**3,'description':'This folder contains downloaded test files.','regeneration':'The next test downloads these files again.','risk':'review','evidence':'The fixture has no supported cleanup rule.','area_id':None},
            {'id':'unknown','title':'Private app data','path':'/demo/private','bytes':None,'description':'This folder could not be measured.','regeneration':'Restore is not known.','risk':'keep','evidence':'The fixture reports a permission error.','area_id':None}]}]},'demo')


def launch(areas,scan,clean,demo=False,config=None):
    def run(win):
        screen=Screen(win);feed=report.PaneFeed();current=demo_report() if demo else None
        if demo:feed.status='DEMO DATA. No disk scan or deletion is available.'
        index=0;opened=set(range(len(current['groups']))) if demo else set();selected=set();events=queue.Queue(maxsize=1);stop=threading.Event();lock=threading.Lock()
        def worker():
            while not stop.wait(1):
                try:
                    with lock:
                        token=feed.token;value=feed.poll()
                    if value:
                        # Keep the newest report even while a details dialog is open.
                        try:events.get_nowait()
                        except queue.Empty:pass
                        events.put_nowait((token,value,None))
                except queue.Full:pass
                except (ValueError,OSError,subprocess.SubprocessError) as exc:
                    try:events.put_nowait((feed.token,None,str(exc)))
                    except queue.Full:pass
        if not demo:threading.Thread(target=worker,daemon=True).start()
        win.timeout(250)
        try:
            while True:
                try:
                    token,value,error=events.get_nowait()
                    if token==feed.token:
                        if error:feed.status='Report not loaded: '+error
                        elif value:
                            current=value;selected.clear();opened=set(range(len(value['groups'])));index=0
                            feed.status='New report loaded. Check agent estimates before removal.'
                except queue.Empty:pass
                groups=current['groups'] if current else []
                actions=['Copy a new scan prompt','Check and review selected paths (%d)'%len(selected),'Read scan notes','Open local cleanup tools','Check for updates']
                labels=list(actions);rows=[None]*len(actions)
                for gi,group in sorted(enumerate(groups),key=lambda pair:-sum(i['bytes'] or 0 for i in pair[1]['items'])):
                    labels.append(('▾ ' if gi in opened else '▸ ')+group['title']+'  '+total(group['items']));rows.append(('group',gi))
                    if gi in opened:
                        for item in sorted(group['items'],key=lambda x:-(x['bytes'] or 0)):
                            state='CHECK' if item['risk']=='rebuildable' and item['area_id'] else 'KEEP' if item['risk']=='keep' else 'INSPECT'
                            labels.append('  '+('☑ ' if item['id'] in selected else '☐ ')+item['title']+'  '+('Not measured' if item['bytes'] is None else ui.amount(item['bytes']))+'  '+state)
                            rows.append(('item',item))
                index=min(index,len(labels)-1);row=rows[index]
                detail=(row[1]['description'] if row and row[0]=='item' else groups[row[1]]['description'] if row else feed.status)
                free=18*1024**3 if demo else shutil.disk_usage(Path.home()).free
                subtitle='Free '+ui.amount(free)+' · '+(current['summary'] if current else 'Copy the prompt. Paste it in the agent pane. Wait for the report.')
                screen.draw('Agent disk report'+(' · DEMO' if demo else ''),subtitle,labels,index,detail,'↑ ↓ Move  Enter Open  Space Select  Shift+↑/↓ Pane  Esc Exit')
                index,k=screen.key(index,len(labels));row=rows[index]
                if k in (27,ord('q')):return
                if k==ord(' ') and row and row[0]=='group':
                    eligible={i['id'] for i in groups[row[1]]['items'] if i['risk']=='rebuildable' and i['area_id']}
                    if eligible<=selected:selected.difference_update(eligible)
                    else:selected.update(eligible)
                if k==ord(' ') and row and row[0]=='item':
                    item=row[1]
                    if item['risk']=='rebuildable' and item['area_id']:
                        if item['id'] in selected:selected.remove(item['id'])
                        else:selected.add(item['id'])
                    else:feed.status='This path is inspect only. No removal rule is available.'
                if k not in (10,13,curses.KEY_ENTER):continue
                win.timeout(-1)
                try:
                    if row:
                        if row[0]=='group':
                            if row[1] in opened:opened.remove(row[1])
                            else:opened.add(row[1])
                        else:details(screen,row[1])
                    elif index==0:
                        if demo:screen.message('Demo prompt','In the live app, this button copies a fresh scan prompt.')
                        else:
                            with lock:
                                if copy_prompt(screen,feed,config):
                                    current=None;selected.clear();opened.clear()
                    elif index==1:
                        items=[i for g in groups for i in g['items'] if i['id'] in selected]
                        if demo:screen.message('Demo only','This screen cannot remove files.')
                        elif not items:screen.message('No paths selected','Expand a group. Use Space on a CHECK row. DiskBoard will then check that path locally.')
                        elif cleanup(screen,items,areas,scan,clean):
                            current=None;selected.clear();feed.token=report.new_token();feed.status='The report is now stale. Copy a new scan prompt.'
                    elif index==2:
                        screen.message('Scan notes',(current['summary']+' ' if current else '')+feed.status+' CHECK means a local check is still required. INSPECT and KEEP cannot be selected. Sizes come from the agent and can be wrong.')
                    elif index==4:
                        if demo:screen.message('Updates','Demo mode does not check the network.')
                        else:
                            import diskboard_update
                            result=screen.wait('Check for updates',lambda:diskboard_update.run(False))
                            screen.message('DiskBoard updates',result)
                    else:
                        if demo:screen.message('Demo only','Local tools are not available in this demo.')
                        else:
                            # Leave this curses wrapper before opening the original UI.
                            return 'local'
                finally:win.timeout(250)
        finally:stop.set();win.timeout(-1)
    while True:
        result=curses.wrapper(run)
        if result!='local':return 0
        import diskpick_tui
        diskpick_tui.launch(areas,scan,clean)
