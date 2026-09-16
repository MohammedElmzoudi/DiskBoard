"""Capture real interactive CLI output for docs. --demo never touches user files."""
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
out=root
out.mkdir(parents=True,exist_ok=True)
master,slave=pty.openpty()
fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',36,112,0,0))
env=os.environ.copy();env['TERM']='xterm-256color';env.pop('NO_COLOR',None)
process=subprocess.Popen(['/usr/bin/python3','-B',str(root/'diskpick.py'),'--demo'],cwd=root,
                         stdin=slave,stdout=slave,stderr=slave,env=env)
os.close(slave)

def read_prompt():
    data=b''
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        if select.select([master],[],[],0.1)[0]:
            data+=os.read(master,65536)
            if b'diskpick \xe2\x80\xba ' in data:return data
    raise RuntimeError('CLI did not reach its input prompt')

try:
    first=read_prompt()
    (out/'picker.ansi').write_bytes(first)
    os.write(master,b'1 4, 2\n')
    second=read_prompt()
    assert b'simulated 8.32 GiB recovery' in second
    (out/'result.ansi').write_bytes(second)
    os.write(master,b'q\n')
    process.wait(timeout=5)
    assert process.returncode==0
    print('Captured real PTY input/output: selection 1 4, 2; demo-only recovery 8.32 GiB.')
finally:
    if process.poll() is None:process.terminate();process.wait(timeout=5)
    os.close(master)
