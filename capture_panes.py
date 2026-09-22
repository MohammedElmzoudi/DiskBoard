"""Capture actual tmux UI and test Shift-arrow focus with a labelled agent stand-in."""
import fcntl,os,pty,select,shutil,signal,struct,subprocess,sys,termios,time,uuid
from pathlib import Path
import diskpick_panes as panes
root=Path(__file__).resolve().parent;tmux=shutil.which('tmux');socket='diskpick-proof-'+uuid.uuid4().hex
master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',42,112,0,0))
client=None
try:
 agent=[sys.executable,'-u','-c',"print('AGENT TERMINAL — DEMO STAND-IN (no AI request sent)'); print('Working folder: diskpick source repository'); print('Paste the scan prompt here to make a new disk report.'); input('agent > ')"]
 session,top,bottom=panes.create(tmux,agent,[sys.executable,'-B',str(root/'diskpick.py'),'--report-demo'],root,socket,session='workspace-proof',width=112,height=42)
 panes.run(tmux,'select-pane','-t',bottom,'-T','Codex or Claude CLI · demo stand-in',socket=socket)
 env=os.environ.copy();env.pop('TMUX',None);env['TERM']='xterm-256color'
 client=subprocess.Popen([tmux,'-L',socket,'attach-session','-t',session],stdin=slave,stdout=slave,stderr=slave,env=env);os.close(slave)
 def read(seconds=1):
  data=b'';deadline=time.monotonic()+seconds
  while time.monotonic()<deadline:
   if select.select([master],[],[],0.05)[0]:
    try:data+=os.read(master,65536)
    except OSError:break
  return data
 raw=read(2)
 os.write(master,b'\x1b[1;2B');raw+=read(0.5)
 active=panes.run(tmux,'display-message','-p','-t',session,'#{pane_id}',socket=socket)
 assert active==bottom,(active,bottom)
 os.write(master,b'\x1b[1;2A');raw+=read(0.5)
 assert panes.run(tmux,'display-message','-p','-t',session,'#{pane_id}',socket=socket)==top
 # Force a full redraw to obtain an independently renderable screen.
 client_name=panes.run(tmux,'list-clients','-t',session,'-F','#{client_name}',socket=socket)
 panes.run(tmux,'refresh-client','-t',client_name,socket=socket);raw+=read(0.5)
 (root/'panes.ansi').write_bytes(raw)
 os.write(master,b'\x02d');read(0.5);client.wait(timeout=3)
 assert client.returncode==0
 assert panes.run(tmux,'has-session','-t',session,socket=socket)==''
 print('Verified real Shift+Down/Up pane switching and detach preservation; no real agent launched.')
finally:
 if client and client.poll() is None:client.kill();client.wait(timeout=3)
 os.close(master)
 subprocess.run([tmux,'-L',socket,'kill-server'],capture_output=True)
