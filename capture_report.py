"""Exercise and capture the real report browser with synthetic data only."""
import fcntl,os,pty,select,struct,subprocess,termios,time
from pathlib import Path
root=Path(__file__).resolve().parent
master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',34,112,0,0))
env=os.environ.copy();env['TERM']='xterm-256color'
p=subprocess.Popen(['/usr/bin/python3','-B',str(root/'diskpick.py'),'--report-demo'],stdin=slave,stdout=slave,stderr=slave,env=env);os.close(slave)
def read(seconds=.6):
 data=b'';deadline=time.monotonic()+seconds
 while time.monotonic()<deadline:
  if select.select([master],[],[],.05)[0]:
   try:data+=os.read(master,65536)
   except OSError:break
 return data
try:
 raw=read(1)
 assert b'Agent disk report' in raw and b'DEMO' in raw
 assert b'\x1b[37m' not in raw and b'\x1b[40m' not in raw
 # Four moves reach a group. Space selects its CHECK items.
 os.write(master,b'\x1bOB'*5+b' ');raw+=read()
 assert '☑'.encode() in raw
 (root/'report.ansi').write_bytes(raw)
 os.write(master,b'\x1bOB\r');raw+=read()
 assert b'The build command creates these files again.' in raw
 (root/'report-detail.ansi').write_bytes(raw)
 os.write(master,b'\x1b');read();os.write(master,b'q');read();p.wait(timeout=3)
 assert p.returncode==0
 print('Verified report group selection, item details, and theme defaults; demo only.')
finally:
 if p.poll() is None:p.kill();p.wait(timeout=3)
 os.close(master)
