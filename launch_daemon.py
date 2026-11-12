import subprocess
import sys
import os

pythonw = r"C:\Python314\pythonw.exe"
script = r"D:\RuanJian\NovelRemoteHub\local_bridge.py"

# CREATE_BREAKAWAY_FROM_JOB = 0x01000000, DETACHED_PROCESS = 0x00000008
flags = 0x01000000 | 0x00000008

proc = subprocess.Popen([pythonw, script], creationflags=flags, close_fds=True)
print(f"Launched detached NovelRemoteHub Daemon with PID: {proc.pid}")
