"""Run a single hidden supervisor that restarts the local bridge after exit."""
import socket
import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BRIDGE_PORT = 27960
SUPERVISOR_PORT = 27961


def acquire_port(port):
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            lock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        lock.bind(('127.0.0.1', port))
        lock.listen(1)
        return lock
    except OSError:
        lock.close()
        return None


def launch_hidden(script, *arguments):
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    executable = pythonw if pythonw.exists() else Path(sys.executable)
    command = [str(executable), str(ROOT / script), *arguments]
    flags = getattr(subprocess, 'DETACHED_PROCESS', 0)
    try:
        return subprocess.Popen(command, cwd=ROOT, close_fds=True,
                                creationflags=flags | getattr(subprocess, 'CREATE_BREAKAWAY_FROM_JOB', 0))
    except OSError:
        return subprocess.Popen(command, cwd=ROOT, close_fds=True, creationflags=flags)


def supervise():
    supervisor_lock = acquire_port(SUPERVISOR_PORT)
    if supervisor_lock is None:
        return
    child = None
    try:
        while True:
            try:
                health_path = ROOT / '.bridge-health.json'
                if child is not None and child.poll() is None and health_path.exists():
                    try:
                        health = json.loads(health_path.read_text(encoding='utf-8'))
                        now = time.time()
                        if (health.get('pid') == child.pid and health.get('phase') == 'ready'
                                and health.get('active_count') == 0 and now - health.get('heartbeat_at', 0) < 30
                                and now - health.get('scheduler_at', now) > 300):
                            child.terminate()
                            child.wait(timeout=10)
                    except (ValueError, OSError, subprocess.TimeoutExpired):
                        pass
                # Adopt an existing bridge without interrupting its current task.
                if child is None or child.poll() is not None:
                    probe = acquire_port(BRIDGE_PORT)
                    if probe is not None:
                        probe.close()
                        child = launch_hidden('local_bridge.py')
                        with (ROOT / 'daemon.log').open('a', encoding='utf-8') as stream:
                            stream.write(f'\n[{time.strftime("%Y-%m-%d %H:%M:%S")}] [Supervisor] 启动本地桥接进程 PID={child.pid}\n')
            except OSError as error:
                with (ROOT / 'daemon.log').open('a', encoding='utf-8') as stream:
                    stream.write(f'[Supervisor] 启动失败，稍后重试 {error}\n')
            time.sleep(5)
    finally:
        supervisor_lock.close()


if __name__ == '__main__':
    if '--status' in sys.argv:
        for label, port in [('桥接进程', BRIDGE_PORT), ('自动恢复监督进程', SUPERVISOR_PORT)]:
            probe = acquire_port(port)
            print(f'{label} ' + ('已停止' if probe is not None else '运行中'))
            if probe is not None:
                probe.close()
    elif '--supervise' in sys.argv:
        supervise()
    else:
        process = launch_hidden('launch_daemon.py', '--supervise')
        print(f'Launched NovelRemoteHub supervisor with PID {process.pid}')
