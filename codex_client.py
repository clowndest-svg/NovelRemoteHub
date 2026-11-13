"""Codex desktop app-server transport with project-bound persistent conversations."""
import atexit
import json
import queue
import shutil
import subprocess
import threading
import time
import os
from pathlib import Path
import codex_desktop

from agent_workspace import WORKSPACE_ROOT, choose_project, is_workspace, selected_model


class CodexClient:
    def __init__(self):
        self.process = None
        self.lock = threading.RLock()
        self.pending = {}
        self.next_id = 0
        self.task_turns = {}

    def start(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                return
            executable = shutil.which('codex')
            if not executable:
                raise RuntimeError('未找到 Codex 桌面端执行程序')
            self.process = subprocess.Popen([executable, 'app-server', '--listen', 'stdio://'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', bufsize=1, cwd=WORKSPACE_ROOT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            threading.Thread(target=self._read, args=(self.process,), daemon=True).start()
            self.request('initialize', {'clientInfo': {'name': 'novel_remote_hub', 'title': '智笔云枢', 'version': '1.9.0'},
                                       'capabilities': {'experimentalApi': True}})
            self._send({'method': 'initialized'})

    def _send(self, message):
        with self.lock:
            if not self.process or self.process.poll() is not None:
                raise RuntimeError('Codex 连接已关闭')
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + '\n')
            self.process.stdin.flush()

    def _read(self, process):
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                identifier = message.get('id')
                if 'method' in message and identifier is not None:
                    # Full access is set on our threads. Unexpected interactive requests terminate clearly.
                    self._send({'id': identifier, 'error': {'code': -32601, 'message': '无人值守任务不支持此交互请求'}})
                    continue
                if identifier is not None:
                    recipient = self.pending.get(identifier)
                    if recipient:
                        recipient.put(message)
                    continue
        finally:
            failure = {'error': {'message': 'Codex 进程连接已断开'}}
            if self.process is process:
                for recipient in list(self.pending.values()):
                    recipient.put(failure)

    def request(self, method, params=None, timeout=60):
        with self.lock:
            self.next_id += 1
            identifier = self.next_id
            recipient = queue.Queue()
            self.pending[identifier] = recipient
            self._send({'id': identifier, 'method': method, 'params': params or {}})
        try:
            result = recipient.get(timeout=timeout)
            if result.get('error'):
                raise RuntimeError(str(result['error'].get('message', result['error']))[:1500])
            return result['result']
        except queue.Empty as error:
            raise TimeoutError('Codex 接口响应超时') from error
        finally:
            self.pending.pop(identifier, None)

    def pages(self, method):
        data = []
        cursor = None
        while True:
            result = self.request(method, {'limit': 100, 'cursor': cursor})
            data.extend(result.get('data', []))
            cursor = result.get('nextCursor')
            if not cursor:
                return data

    def discover(self):
        self.start()
        codex_desktop.require_full_access()
        project = choose_project(self.pages('project/list'), lambda item: [root['path'] for root in item.get('roots', [])])
        caller = os.environ.get('CODEX_THREAD_ID')
        if not caller:
            threads = self.request('thread/list', {'cwd': WORKSPACE_ROOT, 'limit': 1, 'currentAccountOnly': True}).get('data', [])
            if not threads:
                raise RuntimeError('Codex 桌面端尚未提供本地会话上下文，请先打开小说项目')
            caller = threads[0]['id']
        desktop_projects = codex_desktop.call_desktop('list_projects', {}, caller).get('projects', [])
        desktop_project = choose_project([{**entry, 'name': entry.get('label')} for entry in desktop_projects],
                                          lambda item: [item.get('path', '')])
        self.caller_thread = caller
        self.desktop_project = desktop_project['projectId']
        models = []
        for item in self.pages('model/list'):
            if item.get('hidden'):
                continue
            efforts = [{'id': entry['reasoningEffort'], 'name': entry['reasoningEffort']}
                       for entry in item.get('supportedReasoningEfforts', [])]
            models.append({'id': item.get('model', item['id']), 'name': item.get('displayName', item['id']),
                           'is_default': item.get('isDefault', False), 'disabled': False,
                           'efforts': efforts, 'default_effort': item.get('defaultReasoningEffort', 'medium')})
        if not models:
            raise RuntimeError('Codex 当前未返回可用模型')
        return {'id': 'codex', 'name': 'ChatGPT（Codex）', 'available': True, 'project_id': desktop_project['projectId'], 'server_project_id': project['id'],
                'workspace': WORKSPACE_ROOT, 'project_name': '小说', 'models': models,
                'capabilities': {'images': True, 'text': True}, 'permission_mode': 'full-access'}

    def wake(self):
        executable = shutil.which('codex')
        desktop = Path(executable).parents[1] / 'ChatGPT.exe' if executable else None
        if not desktop or not desktop.is_file():
            raise RuntimeError('未找到 ChatGPT（Codex）桌面程序')
        subprocess.Popen([str(desktop)], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                return self.discover()
            except (RuntimeError, OSError):
                time.sleep(2)
        raise RuntimeError('ChatGPT（Codex）桌面连接尚未就绪，请检查桌面端登录与小说项目')

    def new_conversation(self, prompt, title, model=None, effort=None):
        catalog = self.discover()
        chosen, effort = selected_model(catalog, model, effort)
        result = codex_desktop.call_desktop('create_thread', {
            # Native creation initializes desktop sidebar membership. Model overrides are
            # applied by the native continuation endpoint after this short initialization.
            'title': title, 'prompt': '小说软件初始化连接。请只回复“初始化完成”，不要操作文件。',
            'target': {'type': 'project', 'projectId': self.desktop_project, 'environment': {'type': 'local'}}
        }, self.caller_thread)
        identifier = result.get('threadId')
        if not identifier:
            raise RuntimeError('Codex 桌面端未返回任务编号')
        self.wait(identifier, timeout=120)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            threads = codex_desktop.call_desktop('list_threads', {'limit': 50}, self.caller_thread).get('threads', [])
            thread = next((entry for entry in threads if entry['id'] == identifier), {})
            if thread.get('projectId') == self.desktop_project and is_workspace(thread.get('cwd', '')):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError('Codex 桌面任务的小说工作区归属校验失败')
        initial = codex_desktop.call_desktop('read_thread', {'threadId': identifier, 'turnLimit': 1, 'includeOutputs': False}, self.caller_thread)
        initial_turn = initial['turns'][0]['id']
        codex_desktop.require_full_access()
        codex_desktop.call_desktop('send_message_to_thread', {
            'threadId': identifier, 'hostId': 'local', 'prompt': prompt,
            'model': chosen['id'], 'thinking': effort
        }, self.caller_thread)
        # Check the execution context written by the desktop's own executor.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            info = self.request('thread/read', {'threadId': identifier}).get('thread', {})
            path = info.get('path')
            if path and Path(path).is_file():
                contexts = []
                with open(path, encoding='utf-8') as stream:
                    for line in stream:
                        try:
                            record = json.loads(line)
                        except ValueError:
                            continue
                        if record.get('type') == 'turn_context':
                            contexts.append(record['payload'])
                if contexts:
                    context = contexts[-1]
                    if context.get('turn_id') == initial_turn:
                        time.sleep(0.5)
                        continue
                    if (context.get('approval_policy') != 'never'
                            or context.get('sandbox_policy', {}).get('type') != 'danger-full-access'
                            or context.get('model') != chosen['id'] or context.get('effort') != effort):
                        raise RuntimeError('Codex 桌面任务权限、模型或思考等级与请求不符')
                    self.task_turns[identifier] = context['turn_id']
                    return identifier
            time.sleep(0.5)
        raise TimeoutError('Codex 桌面任务已建立，执行配置回执尚未就绪，请在小说项目中查看')

    def wait(self, identifier, progress=None, timeout=1200):
        deadline = time.monotonic() + timeout
        next_report = 0
        while time.monotonic() < deadline:
            result = codex_desktop.call_desktop('read_thread', {
                'threadId': identifier, 'hostId': 'local', 'turnLimit': 1, 'includeOutputs': False
            }, self.caller_thread)
            turns = result.get('turns', [])
            expected = self.task_turns.get(identifier)
            latest = turns[0] if turns else {}
            status = latest.get('status') if not expected or latest.get('id') == expected else None
            if status in ('failed', 'interrupted'):
                raise RuntimeError('Codex 桌面任务失败或已中断，请查看小说项目中的会话')
            if status == 'completed':
                return
            thread_status = result.get('thread', {}).get('status', {}).get('type')
            if thread_status in ('systemError', 'needsAttention'):
                raise RuntimeError('Codex 桌面任务需要处理，请查看小说项目中的会话')
            if progress and time.monotonic() >= next_report:
                progress(f'ChatGPT（Codex）小说项目任务 {identifier} 正在执行…')
                next_report = time.monotonic() + 15
            time.sleep(5)
        raise TimeoutError('Codex 桌面任务等待超时，请在小说项目中查看')

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


CLIENT = CodexClient()
atexit.register(CLIENT.close)
