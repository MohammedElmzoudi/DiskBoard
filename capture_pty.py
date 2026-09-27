"""Exercise the real arrow/space worktree UI with synthetic data only."""
import fcntl
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import termios
import time
root=Path(__file__).resolve().parent
from capture_list import demo_capture, persistence_flow, cleanup_flow
demo_capture()
persistence_flow()
cleanup_flow()
from capture_storage import demo_capture as capture_storage, fixture_flow
capture_storage()
fixture_flow()
master,slave=pty.openpty()
fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',34,112,0,0))
env=os.environ.copy();env['TERM']='xterm-256color'
code="""import curses,time
from diskpick_tui import Screen,tree_browser
rows=[dict(path='/demo/project/.claude/worktrees/old-search',name='old-search',days=92,timestamp=time.time()-92*86400,source='Demo',branch='feature/search',bytes=4096,reason='')]
curses.wrapper(lambda win:tree_browser(Screen(win),'DEMO DATA',[],lambda *args:None,rows))
"""
p=subprocess.Popen(['/usr/bin/python3','-B','-c',code],cwd=root,stdin=slave,stdout=slave,stderr=slave,env=env)
os.close(slave)
def read():
 data=b'';deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  if select.select([master],[],[],0.2)[0]:data+=os.read(master,65536)
  elif data:return data
 raise RuntimeError('TUI did not render')
try:
 first=read();assert b'Claude worktrees' in first and b'DEMO' in first
 assert b'\x1b[37m' not in first and b'\x1b[40m' not in first, 'UI must inherit terminal colors'
 # Four arrows select the first worktree; Space marks it, then review.
 os.write(master,b'\x1bOB'*4+b' '+b'\x1bOA'*2+b'\r')
 second=read();assert b'Review selected worktrees' in second,second
 os.write(master,b'\x1b');read();os.write(master,b'q')
 deadline=time.monotonic()+5
 while p.poll() is None and time.monotonic()<deadline:
  if select.select([master],[],[],0.1)[0]:
   try:os.read(master,65536)
   except OSError:break
 p.wait(timeout=2);assert p.returncode==0
 print('Passed saved-list PTY acceptance plus the retained worktree picker and review; synthetic data only.')
finally:
 if p.poll() is None:p.kill();p.wait(timeout=5)
 os.close(master)
