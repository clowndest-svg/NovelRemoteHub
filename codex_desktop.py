"""Keep Codex's desktop sidebar assignment in sync with app-server project identity."""
import json
import os
import shutil
import subprocess
from pathlib import Path



def call_desktop(tool, arguments, caller_thread):
    candidates = []
    if os.environ.get('CODEX_APP_TOOLS_PIPE_PATH'):
        candidates.append(os.environ['CODEX_APP_TOOLS_PIPE_PATH'])
    if os.name == 'nt':
        candidates.extend('\\\\.\\pipe\\' + name for name in os.listdir('\\\\.\\pipe\\')
                          if name.startswith('codex-app-tools-'))
    node = shutil.which('node')
    if not node:
        executable = shutil.which('codex')
        node = str(Path(executable).parent / 'cua_node/bin/node.exe') if executable else None
    if not node:
        raise RuntimeError('未找到桌面项目接口所需的 Node 运行时')
    errors = []
    for pipe in dict.fromkeys(candidates):
        result = subprocess.run([node, str(Path(__file__).with_name('desktop_app_bridge.mjs'))],
            input=json.dumps({'pipe': pipe, 'tool': tool, 'arguments': arguments, 'threadId': caller_thread}),
            capture_output=True, text=True, encoding='utf-8', timeout=20,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode == 1:
            raise RuntimeError(result.stderr.strip()[:500] or 'Codex 桌面操作失败')
        if result.returncode:
            errors.append(result.stderr.strip()[:200])
            continue
        text = json.loads(result.stdout)['text']
        try:
            return json.loads(text)
        except ValueError:
            return {'message': text}
    raise RuntimeError('Codex 桌面项目接口尚未连接，请打开桌面端。' + (' '.join(errors))[:300])


def require_full_access():
    home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    state = json.loads((home / '.codex-global-state.json').read_text(encoding='utf-8'))
    choices = [value.get('permission-selection-by-host-id:local') for value in state.values()
               if isinstance(value, dict) and 'permission-selection-by-host-id:local' in value]
    if not choices or any(choice != {'kind': 'agent-mode', 'agentMode': 'full-access'} for choice in choices):
        raise RuntimeError('请将 Codex 桌面端的本地权限设置为完全访问，软件会在创建前核验该设置')
