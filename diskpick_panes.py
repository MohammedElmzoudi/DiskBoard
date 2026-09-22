"""Isolated tmux workspace: real terminals, no terminal emulation or API proxy."""
import curses
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import uuid
import diskpick_setup as setup

SOCKET='diskpick-workbench'
ROOT=Path(__file__).resolve().parent


def run(tmux,*args,socket=SOCKET):
    result=subprocess.run([tmux,'-L',socket,'-f','/dev/null',*args],capture_output=True,text=True)
    if result.returncode:raise RuntimeError(result.stderr.strip() or 'tmux command failed')
    return result.stdout.strip()


def choose():
    from diskpick_tui import Screen
    def menu(win):
        s=Screen(win)
        options=['Claude CLI','Codex CLI','Workbench only','Cancel']
        details=['Start Claude in the DiskBoard source folder. Uses your existing login and permissions.',
                 'Start Codex in the DiskBoard source folder. Uses your existing login and permissions.',
                 'Open the existing full-height storage workbench.','Return to your terminal.']
        while True:
            index=s.menu('Choose your agent','Top: storage workbench  ·  Bottom: agent terminal  ·  Shift+↑/↓ switches focus',options,details)
            if index is None or index==3:return None
            if index==2:return 'none'
            agent=('claude','codex')[index]
            if setup.ensure(s,agent):return agent
    return curses.wrapper(menu)


def create(tmux,agent_command,top_command,root=ROOT,socket=SOCKET,session=None,width=None,height=None):
    """Commands are argument lists; only shlex.join produces shell command strings."""
    session=session or 'workspace-'+uuid.uuid4().hex[:10]
    dimensions=[]
    if width:dimensions+=['-x',str(width)]
    if height:dimensions+=['-y',str(height)]
    def call(*args):return run(tmux,*args,socket=socket)
    # Configure the top terminal before launching any agent.
    top=call('new-session','-d','-P','-F','#{pane_id}','-s',session,'-c',str(root),*dimensions,shlex.join(['/usr/bin/env','DISKPICK_TMUX='+tmux,*top_command]))
    try:
        call('set-option','-t',session,'status-style','fg=default,bg=default')
        call('set-option','-t',session,'status-left',' DiskBoard ')
        call('set-option','-t',session,'status-right','Shift+↑/↓ switch | Ctrl+b r reload top | Ctrl+b d detach ')
        call('set-option','-t',session,'status-right-length','95')
        call('set-option','-t',session,'mouse','on')
        call('set-option','-t',session,'history-limit','10000')
        call('set-option','-t',session,'destroy-unattached','off')
        call('set-window-option','-t',session,'remain-on-exit','on')
        call('set-window-option','-t',session,'pane-border-status','top')
        call('set-window-option','-t',session,'pane-border-format',' #{pane_title} ')
        call('set-window-option','-t',session,'pane-border-style','fg=default,bg=default')
        call('set-window-option','-t',session,'pane-active-border-style','fg=default,bg=default,bold')
        call('select-pane','-t',top,'-T','DiskBoard · storage workbench')
        # These bindings belong only to the dedicated diskpick server.
        call('bind-key','-n','S-Up','select-pane','-U')
        call('bind-key','-n','S-Down','select-pane','-D')
        call('bind-key','r','respawn-pane','-k','-t',':.0')
        bottom=call('split-window','-v','-p','45','-P','-F','#{pane_id}','-t',top,'-c',str(root),shlex.join(agent_command))
        call('set-option','-t',session,'@diskpick-agent-pane',bottom)
        call('select-pane','-t',bottom,'-T',Path(agent_command[0]).name+' · DiskBoard source')
        call('select-pane','-t',top)
        return session,top,bottom
    except Exception as exc:
        # Never kill a possibly started agent on setup/attachment failure.
        raise RuntimeError(str(exc)+'; retained workspace: diskboard --resume '+session) from exc


def launch(agent=None,config=None,resume=None):
    if not resume:
        agent=agent or choose()
        if agent is None:return 0
        if agent=='none':return None
        executable=setup.resolve(agent) or setup.prompt_tool(agent)
        if not executable:return 0
    tmux=setup.resolve('tmux') or setup.prompt_tool('tmux')
    if not tmux:return None
    if resume:
        if not re.fullmatch(r'workspace-[a-zA-Z0-9-]+',resume):raise ValueError('Invalid workspace name')
        session=resume
        try:run(tmux,'has-session','-t',session)
        except RuntimeError as exc:raise ValueError(str(exc)) from exc
    else:
        top=[sys.executable,'-B',str(ROOT/'diskpick.py'),'--workbench']
        if config:top+=['--config',str(config.expanduser().resolve())]
        try:session,_,_=create(tmux,[executable],top)
        except RuntimeError as exc:raise ValueError(str(exc)) from exc
    print('Shift+↑ / Shift+↓ switches panes. Agent working folder: '+str(ROOT))
    print('Detach without stopping the agent: Ctrl+b, then d. Resume: diskboard --resume '+session,flush=True)
    # Clearing TMUX only for this child allows a separate, nested diskpick client.
    # It does not alter the parent shell or any other tmux server.
    import os
    env=os.environ.copy();env.pop('TMUX',None)
    result=subprocess.run([tmux,'-L',SOCKET,'attach-session','-t',session],env=env)
    print('Workspace retained. Resume with: diskboard --resume '+session)
    return result.returncode
