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
master,slave=pty.openpty()
fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',34,112,0,0))
env=os.environ.copy();env['TERM']='xterm-256color'
p=subprocess.Popen(['/usr/bin/python3','-B',str(root/'diskpick.py'),'--demo'],stdin=slave,stdout=slave,stderr=slave,env=env)
os.close(slave)
def read():
 data=b'';deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  if select.select([master],[],[],0.2)[0]:data+=os.read(master,65536)
  elif data:return data
 raise RuntimeError('TUI did not render')
try:
 first=read();assert b'Claude worktrees' in first
 assert b'\x1b[37m' not in first and b'\x1b[40m' not in first, 'UI must inherit terminal colors'
 (root/'picker.ansi').write_bytes(first)
 # Four arrows select the first worktree; Space marks it, then review.
 os.write(master,b'\x1bOB'*4+b' '+b'\x1bOA'*2+b'\r')
 second=read();assert b'Review selected worktrees' in second,second
 (root/'result.ansi').write_bytes(first+second)
 os.write(master,b'\x1b');read();os.write(master,b'q')
 deadline=time.monotonic()+5
 while p.poll() is None and time.monotonic()<deadline:
  if select.select([master],[],[],0.1)[0]:
   try:os.read(master,65536)
   except OSError:break
 p.wait(timeout=2);assert p.returncode==0
 print('Captured actual worktree picker and selected-only review; synthetic data, no deletion.')
finally:
 if p.poll() is None:p.kill();p.wait(timeout=5)
 os.close(master)
