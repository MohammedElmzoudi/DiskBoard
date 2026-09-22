"""Capture the real setup confirmation at a small pane height; no settings writes."""
import fcntl,os,pty,select,struct,subprocess,termios,time
from pathlib import Path
root=Path(__file__).resolve().parent
master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',14,112,0,0))
env=os.environ.copy();env['TERM']='xterm-256color'
code="import curses; from pathlib import Path; import diskpick_setup as s; from diskpick_tui import Screen; s.CONFIG=Path('/demo/config/diskpick/tools.json'); result=curses.wrapper(lambda w:s.confirm(Screen(w),'codex','/demo/Applications/Codex.app/Contents/Resources/codex')); assert result is None"
p=subprocess.Popen(['/usr/bin/python3','-B','-c',code],cwd=root,stdin=slave,stdout=slave,stderr=slave,env=env);os.close(slave)
def read(seconds=.7):
 data=b'';end=time.monotonic()+seconds
 while time.monotonic()<end:
  if select.select([master],[],[],.05)[0]:
   try:data+=os.read(master,65536)
   except OSError:break
 return data
try:
 raw=read()
 assert b'Yes' in raw and b'No' in raw and b'Use once' in raw
 (root/'setup.ansi').write_bytes(raw)
 os.write(master,b'\x1bOB\r');read();p.wait(timeout=3);assert p.returncode==0
 print('Confirmed Yes, No and Use once are visible in a 14-row pane; No writes nothing.')
finally:
 if p.poll() is None:p.kill();p.wait(timeout=3)
 os.close(master)
