"""Launch the local bridge independently from the calling console."""
import subprocess
import sys
from pathlib import Path

pythonw = Path(sys.executable).with_name("pythonw.exe")
script = Path(__file__).with_name("local_bridge.py")
try:
    flags = subprocess.CREATE_BREAKAWAY_FROM_JOB | subprocess.DETACHED_PROCESS
    process = subprocess.Popen([str(pythonw), str(script)], cwd=script.parent,
                               creationflags=flags, close_fds=True)
except OSError:
    flags = subprocess.DETACHED_PROCESS
    process = subprocess.Popen([str(pythonw), str(script)], cwd=script.parent,
                               creationflags=flags, close_fds=True)
print(f"Launched NovelRemoteHub daemon with PID {process.pid}")
