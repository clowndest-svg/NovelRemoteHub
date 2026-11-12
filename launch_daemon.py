# -*- coding: utf-8 -*-
import subprocess
import sys
import os

# 真正脱离控制台、独立在后台常驻
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

script_path = os.path.join(os.path.dirname(__file__), "local_bridge.py")
log_path = os.path.join(os.path.dirname(__file__), "bridge.log")

with open(log_path, "a", encoding="utf-8") as log_file:
    p = subprocess.Popen(
        [sys.executable, script_path],
        stdout=log_file,
        stderr=log_file,
        stdin=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
        close_fds=True
    )
print(f"Daemon launched successfully with PID: {p.pid}")
