# -*- coding: utf-8 -*-
r"""
NovelRemoteHub · 智笔云枢 本地执行与多书架同步桥接器 (Local Agent Bridge 3.0)
运行在公司 Windows 电脑上 (D:\RuanJian\NovelRemoteHub)
严格遵守 C 盘零占用铁律

核心特性：
1. 默认采用 Google 官方最新 Gemini 3.8 Flash High (主力 · 量大管饱)
2. 智能容灾降级：遭遇模型额度上限 (Quota/429) 自动熔断切换至备用模型，保证创作不中断
3. 代理自愈重试：遭遇 Clash Verge 代理网络波动握手超时自动 3 次重试与健康诊断
4. 客户端常驻守护：电脑端 Antigravity 意外退出或重启可远程自动拉起唤醒
5. 多书架实时同步与大秦小说规范全自动化
"""
import os
import sys
import time
import json
import glob
import re
import urllib.request
import urllib.parse
import ssl
import subprocess
import ctypes
import socket
import threading
import uuid
import base64
from pathlib import Path
from storage import atomic_json, atomic_write, within, safe_component
import agent_client
import desktop_agents
from agent_workspace import canonical_engine
import story_memory
import novel_manager
import deepseek_client
from sync_protocol import content_fingerprint, book_snapshot, digest, cover_fingerprint
import library_sync
import http.client

# 确保工作目录锁定为当前项目根目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 确保在 pythonw.exe 无控制台环境下运行时，stdout/stderr 自动安全重定向至 daemon.log，避免 NoneType.write 异常崩溃
LOG_WRITE_LOCK = threading.Lock()


class SafeDaemonLogStream:
    def __init__(self, log_path, orig_stream=None):
        self.log_path = log_path
        self.orig_stream = orig_stream
    def write(self, text):
        if not text:
            return
        try:
            with LOG_WRITE_LOCK:
                path = Path(self.log_path)
                if path.exists() and path.stat().st_size >= 8 * 1024 * 1024:
                    for number in (2, 1):
                        old = Path(str(path) + f'.{number}')
                        if old.exists():
                            os.replace(old, str(path) + f'.{number + 1}')
                    os.replace(path, str(path) + '.1')
                with open(path, "a", encoding="utf-8", errors="replace") as f:
                    f.write(text)
        except Exception:
            pass
        if self.orig_stream:
            try:
                self.orig_stream.write(text)
                self.orig_stream.flush()
            except Exception:
                pass
    def flush(self):
        if self.orig_stream:
            try:
                self.orig_stream.flush()
            except Exception:
                pass

LOG_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daemon.log")
sys.stdout = SafeDaemonLogStream(LOG_FILE_PATH, sys.stdout)
sys.stderr = SafeDaemonLogStream(LOG_FILE_PATH, sys.stderr)

import traceback
def global_exception_handler(exc_type, exc_value, exc_traceback):
    err = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    try:
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] [CRITICAL FATAL] 未捕获异常崩溃:\n{err}\n")
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_traceback)

sys.excepthook = global_exception_handler

# 目录与常量配置 (支持 D:\小说 下多书架并行管理)
BOOKS_ROOT = r"D:\小说"
DEFAULT_BOOK = "给政哥讲量子力学，他竟肉身硬抗二向箔"
AGENTAPI_BAT = r"C:\Users\23793\.gemini\antigravity\bin\agentapi.bat"
ANTIGRAVITY_EXE = r"C:\Users\23793\AppData\Local\Programs\antigravity\Antigravity.exe"

CLOUD_SERVER_URL = "https://scientificexploration.art/novel/api"
CONFIG_PATH = Path(__file__).with_name(".bridge-config.json")
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
TOKEN = os.environ.get("NOVEL_HUB_TOKEN") or CONFIG.get("token", "")
ACTIVE_TASK = None
SYNC_LOCK = threading.Lock()
SYNC_REQUEST = threading.Event()
SYNC_REQUEST_ID = 0
SYNC_RECEIPTS = {}
SYNC_REPORT_PENDING = False
SYNC_LAST_ERROR = ""
SYNC_CONFLICT = False
BRIDGE_PREFERENCES = {}
TASK_REPORTS_PATH = Path(__file__).with_name('.bridge-task-reports.json')
ACTIVE_JOB_PATH = Path(__file__).with_name('.bridge-active-task.json')
TASK_REPORT_LOCK = threading.RLock()


class TaskCancelled(RuntimeError):
    pass


def active_jobs():
    data = story_memory.load_json(ACTIVE_JOB_PATH, {})
    if data.get('id'):
        return {data['id']: data}
    return data.get('tasks', {})


def remember_task(task):
    with TASK_REPORT_LOCK:
        jobs = active_jobs()
        jobs[task['id']] = task
        atomic_json(ACTIVE_JOB_PATH, {'tasks': jobs})


def forget_task(task_id):
    with TASK_REPORT_LOCK:
        jobs = active_jobs()
        jobs.pop(task_id, None)
        if jobs:
            atomic_json(ACTIVE_JOB_PATH, {'tasks': jobs})
        else:
            ACTIVE_JOB_PATH.unlink(missing_ok=True)

# 独立直连并校验服务器证书。
SSL_CTX = ssl.create_default_context()


class CloudHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        if self.host == 'scientificexploration.art':
            # Keep hostname verification and SNI while bypassing Clash Fake-IP DNS.
            self._create_connection = lambda address, timeout, source_address: socket.create_connection(
                (CONFIG.get('server_ip', '124.220.54.64'), address[1]), timeout, source_address)
        super().connect()


class CloudHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(CloudHTTPSConnection, request, context=self._context)


CLOUD_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), CloudHTTPSHandler(context=SSL_CTX))


# 0. 单实例互斥守护 (端口锁)
_LOCK_SOCKET = None
def ensure_single_instance(port=27960):
    global _LOCK_SOCKET
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(("127.0.0.1", port))
        s.listen(1)
        _LOCK_SOCKET = s
        return True
    except OSError:
        s.close()
        print("[Daemon] 另一个 NovelRemoteHub 本地守护进程已经在运行中，当前进程退出避免重复。")
        sys.exit(0)

# 1. Windows 系统防休眠设置
def prevent_sleep():
    try:
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_AWAYMODE_REQUIRED = 0x00000040
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)
        print("[Power] 已激活 Windows 防休眠守护 (系统与网络保持常驻)")
    except Exception as e:
        print(f"[Power] 防休眠设置警告: {e}")

# 2. HTTP 请求助手
def api_post(endpoint, data):
    url = f"{CLOUD_SERVER_URL}/{endpoint.lstrip('/')}"
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "X-Novel-Token": TOKEN
    })
    timeout = 120 if (endpoint == "sync_data" or len(body) > 50 * 1024) else 30
    for attempt in range(3):
        try:
            with CLOUD_OPENER.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            with error:
                try:
                    result = json.load(error)
                except (ValueError, OSError):
                    result = {'error': f'HTTP {error.code}'}
            print(f"[api_post error] {endpoint}: HTTP {error.code} {result.get('error', '')}")
            if error.code < 500 and error.code != 429:
                return result
        except (OSError, ValueError, http.client.HTTPException) as error:
            print(f"[api_post error] {endpoint}: {error}")
        if attempt < 2:
            time.sleep(attempt + 1)
    return None


def report_task(payload):
    """Persist terminal reports until the cloud acknowledges them, including across restarts."""
    payload = dict(payload)
    payload.setdefault('event_id', uuid.uuid4().hex)
    with TASK_REPORT_LOCK:
        reports = story_memory.load_json(TASK_REPORTS_PATH, {})
        terminal = payload.get('status') in ('completed', 'failed')
        if terminal:
            reports[payload['id']] = payload
            atomic_json(TASK_REPORTS_PATH, reports)
        result = api_post('task_progress', payload)
        if not terminal and result and (result.get('cancel_requested') or result.get('terminal')):
            raise TaskCancelled('任务已终止，停止当前创作流程')
        if terminal and result and result.get('ok'):
            reports.pop(payload['id'], None)
            atomic_json(TASK_REPORTS_PATH, reports)
            forget_task(payload['id'])
        return result


def flush_task_reports():
    with TASK_REPORT_LOCK:
        for payload in story_memory.load_json(TASK_REPORTS_PATH, {}).values():
            result = report_task(payload)
            if not result or not result.get('ok'):
                return False
    return True

def api_get(endpoint, silent=False):
    url = f"{CLOUD_SERVER_URL}/{endpoint.lstrip('/')}"
    req = urllib.request.Request(url, headers={
        "X-Novel-Token": TOKEN
    })
    timeout = 30 if ("sync_document" in endpoint or "manifest" in endpoint) else 15
    try:
        with CLOUD_OPENER.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        if not silent:
            print(f"[api_get error] {endpoint}: {e}")
        return None

# 3. Antigravity 进程健康检测与自动唤醒拉起
def is_process_running(proc_name):
    try:
        res = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {proc_name}"], capture_output=True, text=True, errors="ignore")
        return proc_name.lower() in res.stdout.lower()
    except Exception:
        return False

def ensure_antigravity_running(progress_cb=None):
    try:
        has_gui = is_process_running("Antigravity.exe")
        has_server = is_process_running("language_server.exe")

        if has_gui and has_server:
            return True, "Antigravity 客户端及后台语言服务均在线活跃！"

        # 如果两者都不在，或者客户端界面未打开，自动静默拉起客户端
        if not has_gui:
            if progress_cb:
                progress_cb("⚡ 检测到电脑端 Antigravity 处于完全关闭状态，正在自动拉起唤醒客户端...")
            print("[Antigravity] 检测到客户端完全关闭，正在自动拉起 Antigravity.exe...")
            if os.path.exists(ANTIGRAVITY_EXE):
                subprocess.Popen([ANTIGRAVITY_EXE], cwd=os.path.dirname(ANTIGRAVITY_EXE), close_fds=True)
            else:
                return False, f"未找到 Antigravity 可执行路径: {ANTIGRAVITY_EXE}"

        # 智能轮询等待核心引擎 language_server.exe 启动并就绪 (最多等待 15 秒)
        for i in range(15):
            time.sleep(1)
            if is_process_running("language_server.exe"):
                if progress_cb:
                    progress_cb("✅ Antigravity 核心引擎已就绪，正在接入高速创作管道...")
                time.sleep(2)  # 给 IPC 管道建立留足缓冲
                return True, "已成功自动拉起并唤醒本电脑 Antigravity 客户端及后台语言服务！"
            if progress_cb and (i + 1) % 3 == 0:
                progress_cb(f"正在等待 Antigravity 客户端完成启动初始化 ({i+1}/15秒)...")

        return False, "Antigravity 语言服务在等待时间内尚未就绪，请稍后重试"
    except Exception as e:
        return False, f"进程拉起与检测异常: {e}"

# 4. 检测本地代理 (Clash Verge) 连通性
def check_proxy_health():
    # 测试 Clash 常见本地端口 7890 / 7897
    ports = [7890, 7897]
    for p in ports:
        try:
            with socket.create_connection(("127.0.0.1", p), timeout=1.5):
                return True, f"Clash Verge 本地代理端口 {p} 连通正常"
        except Exception:
            continue
    return False, "未检测到本地代理监听端口 (7890/7897)，请确保 Clash Verge 已开启"

# 5. 扫描本地多书架数据
def collect_book_data(book_dir_name):
    book_path = within(BOOKS_ROOT, book_dir_name)
    chapters = []
    c_files = novel_manager.chapter_files(book_path)
    for f in c_files:
        with open(f, "r", encoding="utf-8-sig") as fp:
            chapters.append({
                "filename": os.path.basename(f),
                "content": fp.read()
            })

    settings = []
    s_files = sorted(glob.glob(os.path.join(book_path, "00_设定集", "*.md")))
    for f in s_files:
        with open(f, "r", encoding="utf-8-sig") as fp:
            settings.append({
                "filename": os.path.basename(f),
                "content": fp.read()
            })
    return chapters, settings

def collect_book_meta_and_cover(book_dir_name):
    book_path = within(BOOKS_ROOT, book_dir_name)
    meta = novel_manager.read_book_meta(book_path, book_dir_name)
    cover_base64 = None
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        cov = os.path.join(book_path, "00_设定集", f"cover{ext}")
        if os.path.exists(cov) and os.path.isfile(cov):
            try:
                with open(cov, "rb") as cfp:
                    cover_base64 = base64.b64encode(cfp.read()).decode("ascii")
                break
            except Exception:
                pass
    return meta, cover_base64

def collect_all_conversations():
    conversations = []
    conversation_id = CONFIG.get("conversation_id")
    logs_candidates = []
    if conversation_id:
        safe_component(conversation_id)
        path = agent_client.BRAIN / conversation_id / ".system_generated/logs/transcript.jsonl"
        if path.is_file():
            logs_candidates = [str(path)]
    if logs_candidates:
        logs_candidates.sort(key=os.path.getmtime, reverse=True)
        active_log = logs_candidates[0]
        try:
            with open(active_log, "r", encoding="utf-8", errors="ignore") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        src = d.get("source")
                        stype = d.get("type")
                        content = d.get("content", "")
                        thinking = d.get("thinking", "")
                        tool_calls = d.get("tool_calls", [])
                        created_at = d.get("created_at")

                        if src == "USER_EXPLICIT" or stype == "USER_INPUT":
                            conversations.append({
                                "role": "user",
                                "time": created_at,
                                "text": content
                            })
                        elif src == "MODEL" and (content or thinking or tool_calls):
                            conversations.append({
                                "role": "assistant",
                                "time": created_at,
                                "text": content[:1500] if content else "",
                                "thinking": thinking[:200] if thinking else "",
                                "tools": [t.get("name") for t in tool_calls] if tool_calls else []
                            })
                    except Exception:
                        continue
        except Exception:
            pass
    return conversations[-50:]

# 6. 全量同步多书架至云端
def sync_all_books_to_cloud(force=True, only_book=None):
    with SYNC_LOCK:
        return _sync_all_books_to_cloud(force, only_book)


def _sync_all_books_to_cloud(force, only_book=None):
    global SYNC_REPORT_PENDING, SYNC_LAST_ERROR, SYNC_CONFLICT
    SYNC_CONFLICT = False
    total_chapters = 0
    total_settings = 0
    convs = collect_all_conversations()
    all_ok = True
    books_count = 0
    attempted = False
    request_id = SYNC_REQUEST_ID
    error = ""
    book_errors = {}
    try:
        os.stat(BOOKS_ROOT)
        manifest = api_get("sync_manifest", silent=True)
        if manifest is None or "books" not in manifest:
            raise RuntimeError("无法获取云端同步清单，将自动重试")
        if only_book:
            manifest = {**manifest, 'books': {key: value for key, value in manifest['books'].items() if key == only_book},
                        'deleted': {key: value for key, value in manifest.get('deleted', {}).items() if key == only_book}}
        BRIDGE_PREFERENCES.update(manifest.get("preferences", {}))
        state_path = Path(BOOKS_ROOT) / ".hub-sync-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        library_sync.reconcile(BOOKS_ROOT, manifest, state, lambda endpoint: api_get(endpoint, silent=True), errors=book_errors)
        with os.scandir(BOOKS_ROOT) as directory:
            entries = list(directory)
        for entry in entries:
            if entry.is_dir() and not entry.name.startswith(".") and (only_book is None or entry.name == only_book):
                book_id = entry.name
                if book_id in book_errors:
                    continue
                try:
                    if any(story_memory.load_json(path, {}).get('status') != 'committed'
                           for path in Path(entry.path, '.story-memory/commits').glob('*.json')):
                        raise RuntimeError('章节提交正在进行，稍后重试同步')
                    chapters, settings = collect_book_data(book_id)
                    meta, cover_base64 = collect_book_meta_and_cover(book_id)
                    fingerprint = content_fingerprint(chapters, settings)
                    snapshot = book_snapshot(entry.path)
                    document_hashes = {"chapters": {item["filename"]: digest(item["content"].encode("utf-8")) for item in chapters},
                                       "settings": {item["filename"]: digest(item["content"].encode("utf-8")) for item in settings}}
                    meta_hashes = {key: meta[key] for key in ("title", "genre", "author", "description", "tags") if key in meta}
                    if (document_hashes["chapters"] != snapshot["chapters"] or document_hashes["settings"] != snapshot["settings"]
                            or meta_hashes != snapshot["meta"] or (cover_fingerprint(cover_base64) if cover_base64 else "") != snapshot["cover_hash"]):
                        raise RuntimeError("本地文件读取期间发生修改，将重新读取后同步")
                    total_chapters += len(chapters)
                    total_settings += len(settings)
                    books_count += 1
                    if not force and SYNC_RECEIPTS.get(book_id) == snapshot["revision"]:
                        continue
                    attempted = True
                    payload = {
                        "book_id": book_id,
                        "chapters": chapters,
                        "settings": settings,
                        "meta": meta,
                        "cover_base64": cover_base64,
                        "conversations": convs,
                        "writing_memory": story_memory.status(entry.path),
                        "sync_version": 3,
                        "expected_revision": manifest["books"].get(book_id, {}).get("revision"),
                        "fingerprint": fingerprint
                    }
                    res = api_post("sync_data", payload)
                    if res and res.get("ok") and res.get("fingerprint") == fingerprint:
                        SYNC_RECEIPTS[book_id] = snapshot["revision"]
                        state[book_id] = snapshot
                        atomic_json(state_path, state)
                    else:
                        all_ok = False
                        SYNC_RECEIPTS.pop(book_id, None)
                        error = (res or {}).get('error') or f"《{book_id}》同步未确认，将自动重试"
                except (OSError, ValueError, RuntimeError) as failure:
                    book_errors[book_id] = {'error': str(failure), 'conflict': isinstance(failure, ValueError)}
        if book_errors:
            all_ok = False
            SYNC_CONFLICT = any(item['conflict'] for item in book_errors.values())
            error = '。'.join(f'《{book}》{item["error"]}' for book, item in book_errors.items())
    except Exception as failure:
        all_ok = False
        error = str(failure)
        SYNC_CONFLICT = isinstance(failure, ValueError)
    if only_book is None and (attempted or force or not all_ok or SYNC_REPORT_PENDING):
        report = api_post("sync_report", {"ok": all_ok, "error": error, "request_id": request_id,
                          "books_count": books_count, "chapters_count": total_chapters,
                          "settings_count": total_settings, "book_errors": book_errors})
        SYNC_REPORT_PENDING = not report or not report.get("ok")
        if SYNC_REPORT_PENDING:
            all_ok = False
    if error:
        print(f"[Sync] {error}")
    SYNC_LAST_ERROR = error or ("同步状态回执未确认" if not all_ok else "")
    return all_ok, total_chapters, total_settings


def wait_for_cloud_sync(progress, book_id=None):
    attempt = 0
    while True:
        if sync_all_books_to_cloud(force=False, only_book=book_id)[0]:
            return
        if SYNC_CONFLICT:
            raise RuntimeError(SYNC_LAST_ERROR)
        attempt += 1
        progress(f'正在重试云端同步（第{attempt}次），已保存成果，连接恢复后继续。{SYNC_LAST_ERROR}')
        SYNC_REQUEST.set()
        time.sleep(min(attempt * 5, 30))


def sync_loop():
    last_reconcile = 0
    while True:
        try:
            requested = SYNC_REQUEST.is_set()
            if requested:
                SYNC_REQUEST.clear()
            force = requested or time.monotonic() - last_reconcile >= 60
            ok, _, _ = sync_all_books_to_cloud(force=force)
            if ok and force:
                last_reconcile = time.monotonic()
            elif not ok and requested:
                SYNC_REQUEST.set()
        except Exception as error:
            print(f"[Sync] 同步检查失败，将自动重试: {error}")
        time.sleep(5)

# 7. 在所选桌面端执行并校验任务成果回执
def execute_agentapi(prompt, title="【手机遥控】任务执行", engine_mode="antigravity_pro",
                     model_tier=None, progress_cb=None, reasoning_effort=None, session=None):
    engine_mode = canonical_engine(engine_mode)
    if engine_mode == "antigravity":
        ready, message = ensure_antigravity_running(progress_cb)
        if not ready:
            return False, message
    receipt = Path(BOOKS_ROOT) / ".hub-results" / (uuid.uuid4().hex + ".json")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    prompt += f"""\n完成所有操作后，最后一步将 UTF-8 JSON 回执写入 {receipt}。
结构为 {{"summary":"实际完成内容","files":["实际写入文件的绝对路径"]}}。
如果执行失败，写入 {{"error":"具体失败原因"}}。回执必须在全部操作完成后才写入。
必须使用 json.dumps、JSON.stringify 或 ConvertTo-Json 正确序列化回执，再读取并解析确认合法，避免文本引号造成无效 JSON。
"""
    try:
        if session is not None and session.get('id'):
            if engine_mode == 'antigravity' and session.get('receipt'):
                desktop_agents.wait_for_receipt(session['receipt'], session['id'], progress_cb, engine_mode)
            conversation = desktop_agents.continue_conversation(session['id'], prompt, model_tier, engine_mode, reasoning_effort)
            action = '续接'
        else:
            conversation = desktop_agents.new_conversation(prompt, title, model_tier, engine_mode, reasoning_effort)
            if session is not None:
                session['id'] = conversation
            action = '建立'
        if session is not None:
            session['receipt'] = str(receipt)
        if progress_cb:
            progress_cb(f"已在小说工作区{action} {engine_mode} 会话 {conversation}，完全访问权限已校验")
        result = desktop_agents.wait_for_receipt(receipt, conversation, progress_cb, engine_mode)
        if not isinstance(result.get("summary"), str) or not result["summary"].strip() or not isinstance(result.get("files"), list):
            raise ValueError("执行端完成回执缺少有效总结或成果列表")
        for filename in result["files"]:
            target = Path(filename).resolve()
            if not target.is_relative_to(Path(BOOKS_ROOT).resolve()) or not target.is_file():
                raise ValueError("AI 回执中包含无效成果路径")
        return True, f"会话 {conversation} 已返回完成回执。\n{result.get('summary', '')}"
    except Exception as error:
        return False, str(error)


def reconcile_stage_filesystem_changes(root, before_sources, before_fp, progress=None):
    """检查执行端在调用期间是否引起了工作区文件变更。
    若已有历史正文或设定集被修改/删除，坚决熔断抛出 RuntimeError。
    若已有文件完好，仅产生执行端违规提前写入的正文草稿文件，将其安全隔离，恢复干净工作区指纹，避免任务中断。
    """
    after_sources = story_memory.sources(root)
    if story_memory.fingerprint(after_sources) == before_fp:
        return True

    # 1. 检查已有源文件是否被篡改或删除
    for name, text in before_sources.items():
        if name not in after_sources or story_memory.digest(after_sources[name]) != story_memory.digest(text):
            raise RuntimeError('历史正文发生变化或设定被执行端修改，已停止任务')

    # 2. 检查多出的新文件
    new_files = [name for name in after_sources if name not in before_sources]
    if new_files and all(name.startswith('01_正文/') for name in new_files):
        quarantine_dir = Path(root) / '.story-memory' / 'quarantine'
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        import gc
        gc.collect()
        for name in new_files:
            src_p = Path(root) / name
            dst_p = quarantine_dir / (src_p.name + '.' + uuid.uuid4().hex[:8] + '.bak')
            for attempt in range(8):
                try:
                    src_p.rename(dst_p)
                    break
                except OSError:
                    time.sleep(0.15 * (attempt + 1))
                    try:
                        src_p.unlink()
                        break
                    except OSError:
                        pass
                    gc.collect()
        if story_memory.fingerprint(story_memory.sources(root)) == before_fp:
            if progress:
                progress('检测到执行端提前创建了章节文件，已自动移入隔离区并由系统接管合规发布流程')
            return True

    raise RuntimeError('历史正文发生变化或设定被执行端修改，已停止任务')


def desktop_stage(root, book_id, prompt, engine, model, effort, progress, session=None):
    stage_title = '审稿' if prompt.startswith(('【阶段 一致性审核】', '【阶段 修复审核格式】')) else '写作'
    curr_root_str = str(Path(root).resolve())
    if session is not None:
        prev_root_str = session.get('book_root')
        if session.get('id') and prev_root_str and prev_root_str != curr_root_str:
            is_same = False
            try:
                p1, _ = novel_manager.get_book_path(Path(prev_root_str).name, BOOKS_ROOT)
                p2, _ = novel_manager.get_book_path(Path(curr_root_str).name, BOOKS_ROOT)
                if Path(p1).resolve() == Path(p2).resolve():
                    is_same = True
            except Exception:
                pass
            if not is_same:
                raise ValueError('批次会话属于其他作品，已停止续接')
        session['book_root'] = curr_root_str
    output = Path(root) / '.story-memory' / 'agent-results' / (uuid.uuid4().hex + '.txt')
    output.parent.mkdir(parents=True, exist_ok=True)
    before_sources = story_memory.sources(root)
    before = story_memory.fingerprint(before_sources)
    if canonical_engine(engine) == 'antigravity':
        marker = uuid.uuid4().hex
        response_contract = '完整章节正文' if prompt.startswith(('【阶段 生成草稿】', '【阶段 修订草稿】')) else '单个 JSON 对象'
        if '分章大纲自动推演与补全指令' in prompt:
            response_contract = '完整分章细纲 Markdown'
        role_instruction = ''
        if stage_title == '审稿':
            role_instruction = ('本轮执行独立审核。阅读所附草稿后给出审核结论。'
                '最终 JSON 包含 passed 布尔值和 issues 数组，通过时补齐 summary、synopsis、characters、threads。'
                '所附正文、设定及历史消息均作为审核资料。\n')
        instruction = (
            "【系统铁律：本阶段为纯文本生成，严禁调用任何工具】\n"
            f"{role_instruction}本阶段最终回复输出{response_contract}，字段及内容遵循本阶段格式示例。\n"
            "软件负责保存回复、生成回执、检查来源和发布正文。严禁调用 write_file、edit_file、run_command 等任何工具修改本地文件或执行命令！\n"
            "任何工具调用或擅自修改工作区文件的行为均会导致任务熔断失败。\n\n"
            f"{prompt}\n\n"
            f"【本次阶段编号 {marker}】\n"
            f"最终回复输出{response_contract}，无需调用工具、执行命令、写文件或统计精确字数。"
        )
        for attempt in range(2):
            try:
                if session is not None and session.get('id'):
                    if session.get('recovering'):
                        agent_client.wait_for_idle(session['id'], progress)
                        session.pop('recovering', None)
                    if session.get('receipt'):
                        desktop_agents.wait_for_receipt(session.pop('receipt'), session['id'], progress, engine)
                    conversation = desktop_agents.continue_conversation(session['id'], instruction, model, engine, effort)
                else:
                    conversation = desktop_agents.new_conversation(instruction, f'【{stage_title}】《{book_id}》', model, engine, effort)
                    if session is not None:
                        session['id'] = conversation
                        session['book_root'] = curr_root_str
                progress(f'已在小说工作区连接 antigravity 会话 {conversation}，等待本阶段完整回复')
                result = agent_client.wait_for_response(conversation, marker, progress)
                break
            except Exception as error:
                if session is not None:
                    session.clear()
                    session['book_root'] = curr_root_str
                # A confirmed upstream rejection produced no usable reply. Retry once with the same model.
                message = str(error).lower()
                retryable = isinstance(error, (RuntimeError, OSError, TimeoutError)) and not isinstance(error, TaskCancelled) and any(
                    text in message for text in (
                        'user location is not supported', '503 service unavailable', '502 bad gateway',
                        '504 gateway timeout', '500 internal server error', 'connection refused', 'connection reset',
                        'socket', 'proxy', 'network', 'timeout', 'timed out', 'failed to connect',
                        'resource has been exhausted', 'quota', 'rate limit', 'econnreset', 'econnrefused',
                        'winerror', '无法连接', '代理', '网络', '上游接口', '安全策略'
                    )
                )
                if attempt or not retryable:
                    raise
                reconcile_stage_filesystem_changes(root, before_sources, before, progress)
                progress(f'模型接口暂时拒绝请求，3秒后使用原模型重试本阶段（1/1）。{str(error)[:300]}')
                time.sleep(3)
        progress('本阶段完整回复已接收，正在核对成果')
        atomic_write(output, result)
        reconcile_stage_filesystem_changes(root, before_sources, before, progress)
        return result
    instruction = (
        "【系统铁律：严禁调用非指定工具】\n"
        f"当前作品根目录 `{root}`。本阶段完整输入已在消息中提供。\n"
        f"本次为分阶段创作。只将本阶段要求的 JSON 或正文写入 `{output}`，然后返回完成回执。\n"
        "输出与回执的父目录均已建立。直接使用一次脚本写入结果和本次新回执，再读取这两个文件校验。\n"
        "两个文件校验通过后立即结束本阶段并回复完成。\n"
        "文件访问范围限定为本次指定输出和新回执，其他阶段的暂存文件与旧回执均与本任务无关。\n"
        "作品正式正文、设定与记忆索引由软件管理，保持这些文件原样。\n\n"
        f"{prompt}"
    )
    ok, result = execute_agentapi(instruction, title=f"【{stage_title}】《{book_id}》", engine_mode=engine,
        model_tier=model, reasoning_effort=effort, progress_cb=progress, session=session)
    reconcile_stage_filesystem_changes(root, before_sources, before, progress)
    if not ok:
        raise RuntimeError(result)
    if not output.is_file():
        raise RuntimeError('执行端未保存本阶段结果')
    return output.read_text(encoding='utf-8-sig')


def execute_serial_chapters(book_id, count, user_prompt, engine, model, effort, progress, sessions=None, task_id=None, auto_ensure_outline=None, preferences_snapshot=None):
    count = novel_manager.validate_chapter_count(count)
    completed = 0
    root, _ = novel_manager.get_book_path(book_id, BOOKS_ROOT)
    book_meta = novel_manager.read_book_meta(root, book_id)
    n_type = book_meta.get('novel_type') or book_meta.get('scale_type') or 'million_long'
    task_prefs = dict(preferences_snapshot if preferences_snapshot is not None else BRIDGE_PREFERENCES)
    if not task_prefs.get('default_chapter_words'):
        if n_type == 'short_story':
            task_prefs['default_chapter_words'] = '2000-3000'
        elif n_type == 'mid_short':
            task_prefs['default_chapter_words'] = '2500-3500'
        else:
            task_prefs['default_chapter_words'] = BRIDGE_PREFERENCES.get('default_chapter_words') or '4000-5000'
    story_memory.recover_commits(root)
    checkpoint = Path(root) / '.story-memory/tasks' / (safe_component(task_id) + '.json') if task_id else None
    state = story_memory.load_json(checkpoint, {}) if checkpoint else {}
    state.setdefault('preferences', task_prefs)
    if 'default_chapter_words' not in state['preferences'] or not state['preferences']['default_chapter_words']:
        state['preferences']['default_chapter_words'] = task_prefs['default_chapter_words']
    batch_id = state.get('batch_id') or uuid.uuid4().hex
    sessions = sessions if sessions is not None else state.get('sessions', {'writer': {}, 'reviewer': {}})
    if state.get('sessions'):
        for session in sessions.values():
            if session.get('id'):
                session['recovering'] = True
    state.update(batch_id=batch_id, sessions=sessions, count=count)
    if checkpoint:
        atomic_json(checkpoint, state)
        for review_path in (Path(root) / '.story-memory/reviews').glob('*.json'):
            review = story_memory.load_json(review_path, {})
            if review.get('batch') != batch_id or review.get('status') not in ('approved', 'published'):
                continue
            target = Path(review['target']).resolve()
            if review.get('status') == 'approved' and not target.exists():
                continue
            if not target.is_relative_to(Path(root).resolve()) or not target.is_file():
                return False, '已保存章节缺失，恢复任务前请核对原稿'
            if story_memory.digest(target.read_text(encoding='utf-8-sig')) != review.get('target_hash'):
                return False, '已保存章节发生修改，恢复任务前请核对原稿'
            completed += 1
    recovered = completed
    def generate(prompt):
        role = 'reviewer' if prompt.startswith(('【阶段 一致性审核】', '【阶段 修复审核格式】')) else 'writer'
        try:
            return desktop_stage(root, book_id, prompt, engine, model, effort, progress, sessions[role])
        except Exception:
            if sessions and role in sessions and isinstance(sessions[role], dict):
                sessions[role].clear()
            raise
        finally:
            if checkpoint:
                atomic_json(checkpoint, state)
    def saved(filename):
        nonlocal completed
        completed += 1
        if checkpoint:
            atomic_json(checkpoint, state)
        wait_for_cloud_sync(progress, book_id)
    try:
        if recovered:
            progress(f'正在恢复原任务，已保存 {recovered}/{count} 章，核对同步后继续剩余章节')
            wait_for_cloud_sync(progress, book_id)
        remaining = count - recovered
        if remaining < 0:
            raise ValueError('任务已保存章数超过请求数量，请核对任务记录')
        def batch_progress(message):
            if recovered:
                message = re.sub(r'续写进度 (\d+)/\d+', lambda m: f'续写进度 {recovered + int(m.group(1))}/{count}', message)
            progress(message)
        if remaining:
            prefs = dict(state['preferences'])
            prefs.setdefault('writing_context_chars', 64000)
            if auto_ensure_outline is not None:
                prefs['auto_ensure_outline'] = auto_ensure_outline
            elif 'auto_ensure_outline' not in prefs:
                prefs['auto_ensure_outline'] = False
            story_memory.write_chapters(root, remaining, user_prompt, prefs, generate, batch_progress,
                                        on_chapter=saved, batch_id=batch_id, batch_count=count)
        return True, f'已完成 {completed} 章续写'
    except Exception as error:
        return False, f"连续续写已停止，已完成 {completed}/{count} 章。{error}"


def sync_agent_catalog():
    while True:
        try:
            api_post("sync_agents", desktop_agents.catalog())
        except Exception as error:
            print(f"[Agents] 模型列表同步失败: {error}")
        time.sleep(60)


ACTIVE_WORKERS = {}
WORKERS_LOCK = threading.Lock()
MAX_CONCURRENT_BOOKS = 4
SCHEDULER_AT = 0
BRIDGE_PHASE = 'initial_sync'

def heartbeat_loop():
    global SYNC_REQUEST_ID
    while True:
        with WORKERS_LOCK:
            tids = [info["task_id"] for info in ACTIVE_WORKERS.values()]
        payload = {"ids": tids, "capacity": MAX_CONCURRENT_BOOKS}
        health = {'pid': os.getpid(), 'scheduler_at': SCHEDULER_AT, 'phase': BRIDGE_PHASE,
                  'active_count': len(tids), 'heartbeat_at': time.time()}
        payload['health'] = health
        try:
            atomic_json(Path(__file__).with_name('.bridge-health.json'), health)
        except OSError as error:
            print(f'[Health] 运行状态写入失败 {error}')
        if tids:
            payload["id"] = tids[0]
        elif ACTIVE_TASK:
            payload["id"] = ACTIVE_TASK
        result = api_post("heartbeat", payload)
        requested = result.get("sync_requested_at", 0) if result else 0
        if requested > SYNC_REQUEST_ID:
            SYNC_REQUEST_ID = requested
            SYNC_REQUEST.set()
        time.sleep(10)


def process_single_task(task):
    global ACTIVE_TASK
    book_id = safe_component(task.get("book_id", DEFAULT_BOOK))
    tid = task.get("id")
    try:
        tid = task["id"]
        ACTIVE_TASK = tid
        action = task.get("action", "creative_instruction")
        sub_action = task.get("sub_action", "")
        extra_data = task.get("extra_data", {})
        user_prompt = task.get("prompt", "")
        book_id = safe_component(task.get("book_id", DEFAULT_BOOK))
        engine = canonical_engine(task.get("engine_mode", "antigravity_pro"))
        model_tier = extra_data.get("model_tier")
        reasoning_effort = extra_data.get("reasoning_effort")

        if action == "create_new_novel":
            book_title = safe_component(book_id)
            book_root = os.path.join(BOOKS_ROOT, book_title)
        else:
            book_root, _ = novel_manager.get_book_path(book_id, BOOKS_ROOT)

        print(f"\n[Task] 捕获指令! ID={tid}, Action={action}, 作品={book_id}, 模型={model_tier}")

        report_task({'id': tid, 'status': 'running', 'log': '电脑已领取任务，正在核对云端资料'})

        # 等待对账确认，短暂断网期间保留当前任务。
        wait_for_cloud_sync(lambda msg: report_task({'id': tid, 'status': 'running', 'log': msg}), book_id)

        # 进度更新回调闭包
        def report_progress(msg):
            report_task({"id": tid, "status": "running", "log": msg})

        # A. 远程唤醒/健康检查指令
        if action == "wake_antigravity":
            report_progress(f"正在检测与唤醒所选桌面端 {engine}…")
            if engine == "codex":
                desktop_agents.codex_client.CLIENT.wake()
                ensure_ok, ensure_msg = True, "ChatGPT（Codex）小说工作区已连接"
            else:
                ensure_ok, ensure_msg = ensure_antigravity_running(progress_cb=report_progress)
            if not ensure_ok:
                raise RuntimeError(ensure_msg)
            api_post("sync_agents", desktop_agents.catalog())
            proxy_ok, proxy_msg = check_proxy_health()
            log_text = f"✅ 电脑端健康检测完毕：\n1. 引擎状态：{ensure_msg}\n2. 网络代理：{proxy_msg}"
            report_task({"id": tid, "status": "completed", "log": log_text})
            time.sleep(1)
            return

        # B. 构思创建全新小说架构
        elif action == "create_new_novel":
            book_title = safe_component(book_id)
            display_title = extra_data.get("title") or book_id
            genre = extra_data.get("genre", "科幻修真 / 网文")
            concept = extra_data.get("concept", user_prompt)
            novel_type = extra_data.get("novel_type") or extra_data.get("scale_type") or "million_long"
            book_root = os.path.join(BOOKS_ROOT, book_title)

            report_progress(f"正在为全新作品《{book_title}》初始化工程目录结构...")
            os.makedirs(os.path.join(book_root, "00_设定集"), exist_ok=True)
            if novel_type == "short_story":
                os.makedirs(os.path.join(book_root, "01_正文"), exist_ok=True)
            elif novel_type == "mid_short":
                os.makedirs(os.path.join(book_root, "01_正文", "第一卷_开端篇"), exist_ok=True)
            else:
                os.makedirs(os.path.join(book_root, "01_正文", "第一卷_人界篇"), exist_ok=True)

            novel_manager.save_book_meta(book_title, {
                "title": display_title,
                "genre": genre,
                "novel_type": novel_type
            }, BOOKS_ROOT, rename=False)

            type_label = "百万长篇巨制 (1000+章)" if novel_type == "million_long" else ("中短篇精品 (几十万字)" if novel_type == "mid_short" else "爆款短故事 (极速反转)")

            if novel_type == "short_story":
                prompt = f"""【爆款短故事架构与设定工程创建指令 · 《{book_title}》】
目标工程目录：`D:\\小说\\{book_title}`
题材分类：{genre}
创作体量模式：爆款短故事 / 极速反转脑洞（1~5万字 / 3~10章）
核心设定与作者构思：
\"\"\"
{concept}
\"\"\"

你现在是白金级爆款短故事/脑洞剧本总架构师。请为《{book_title}》设计高能反转与强情绪张力的短篇架构，并调用 write_file 完整写入以下设定文件：
1. `D:\\小说\\{book_title}\\00_设定集\\01_故事核心设定与爆款脑洞.md`（包含黄金三秒开篇冲突hook、核心高概念脑洞、极致情绪基调与受众爆爽点）；
2. `D:\\小说\\{book_title}\\00_设定集\\02_人物小传与隐秘暗线.md`（包含主角隐匿身份、核心反派嘴脸动机、关键证物与反转钥匙）；
3. `D:\\小说\\{book_title}\\00_设定集\\03_全篇高能反转细纲.md`（包含黄金开局、压抑铺垫、暗线试探、初次撕裂、惊天三重高能反转与终极反杀大结局的 3~10 章分章推进细纲）；
4. `D:\\小说\\{book_title}\\00_设定集\\00_反转伏笔与关键线索清单.md`（包含关键证据链、言行疑点矩阵与终局反转回收清单）。

请直接调用 write_file 将以上文档全部写入落盘，确保开局抓人、反转震撼、节奏紧凑、绝无废话！
"""
            elif novel_type == "mid_short":
                prompt = f"""【中短篇精品小说架构与分卷总谱工程创建指令 · 《{book_title}》】
目标工程目录：`D:\\小说\\{book_title}`
题材分类：{genre}
创作体量模式：中短篇精品连载（几十万字 / 50~200章 · 快节奏爽文）
核心设定与作者构思：
\"\"\"
{concept}
\"\"\"

你现在是白金级网文总架构师。请为《{book_title}》构建紧凑高爽度、强闭环的中短篇小说架构，并调用 write_file 完整写入以下设定文件：
1. `D:\\小说\\{book_title}\\00_设定集\\01_核心设定与爽点机制.md`（包含核心金手指/系统规则、能力升级路线、战力天花板控制与快节奏爽点设计）；
2. `D:\\小说\\{book_title}\\00_设定集\\02_人物角色与冲突阵营.md`（包含主角核心动机、主要反派势力、盟友阵营与阶梯式冲突推进）；
3. `D:\\小说\\{book_title}\\00_设定集\\03_全书紧凑大纲与首期细纲.md`（包含全书三幕式结构规划、分卷高潮闭环、前15章每章紧凑剧情推演与反转卡点）；
4. `D:\\小说\\{book_title}\\00_设定集\\00_主线进展与核心伏笔账本.md`（包含核心人物初始属性、当前主线推进度与关键伏笔跟踪）。

请直接调用 write_file 将以上文档全部写入落盘，确保主线单刀直入、节奏如离弦之箭、爽感密集！
"""
            else:
                prompt = f"""【全新长篇小说架构与总谱工程创建指令 · 《{book_title}》】
目标工程目录：`D:\\小说\\{book_title}`
题材分类：{genre}
创作体量模式：百万级长篇巨制（1000+章 / 200万字+ · 史诗世界观）
核心设定与作者构思：
\"\"\"
{concept}
\"\"\"

你现在是白金级网文总架构师。请为《{book_title}》完成全套世界观与分卷分章构想，并调用 write_file 完整写入以下设定文件：
1. `D:\\小说\\{book_title}\\00_设定集\\01_世界观与题材规则.md`（包含本书题材、世界背景、核心规则与故事边界）；
2. `D:\\小说\\{book_title}\\00_设定集\\02_核心人物与关系阵营.md`（包含主角身世、人物目标、核心矛盾与关系变化）；
3. `D:\\小说\\{book_title}\\00_设定集\\03_全书主线与首卷分章细纲.md`（包含全书主线伏笔脉络、各卷高潮、前30章每章剧情推演与章末反转钩子）；
4. `D:\\小说\\{book_title}\\00_设定集\\00_全书前情剧情与伏笔线索总账本.md`（包含核心人物初始状态与位置、世界观底座基准线、当前核心悬念与开篇主线伏笔）。

请直接调用 write_file 将以上文档全部写入落盘，确保内容详实充沛、设定自洽、爽点与悬念密集！
"""
            task_title = f"【手机遥控】新建作品架构《{book_title}》（{type_label}）"

        # C. 设定集演进 (新增、润色扩展、一致性排查)
        elif action == "evolve_settings":
            target_doc = extra_data.get("target_doc", "")
            if target_doc:
                safe_component(target_doc)
            doc_path = os.path.join(book_root, "00_设定集", target_doc) if target_doc else ""

            report_progress(f"正在分析《{book_id}》设定集，准备执行【{sub_action}】...")

            existing_content = ""
            if doc_path and os.path.exists(doc_path):
                with open(doc_path, "r", encoding="utf-8", errors="ignore") as fp:
                    existing_content = fp.read()

            prompt = f"""【《{book_id}》设定大典升级演进指令 · 操作模式：{sub_action}】
当前小说工程目录：`D:\\小说\\{book_id}`
设定集目录：`D:\\小说\\{book_id}\\00_设定集`
目标设定文档：{target_doc if target_doc else "根据需求新建或更新对应设定文件"}

【既有设定文档参考截选】：
\"\"\"
{existing_content[:3000] if existing_content else "(新建或全书排查)"}
\"\"\"

【作者具体演化要求】：
\"\"\"
{user_prompt}
\"\"\"

请严格遵循本书宏观世界观与既有创作规范：
1. 深入推演细节，丰富规则、人物、法宝、势力或逻辑推导；
2. 直接调用 write_file 或 edit_file 更新落盘到 `D:\\小说\\{book_id}\\00_设定集\\` 对应文件；
3. 完成后返回更新总结！
"""
            task_title = f"【手机遥控】《{book_id}》设定演进"

        # D. 番茄发布专属：为章节绘制概念插图与生成作者有话说
        elif action in ("generate_chapter_illustrations", "generate_chapter_notes"):
            filename = safe_component(extra_data.get("chapter_file") or extra_data.get("target_chapter"))
            image_count = int(extra_data.get("image_count", 4))
            notes_only = action == "generate_chapter_notes"
            bpath, _ = novel_manager.get_book_path(book_id, BOOKS_ROOT)
            chapter_text = novel_manager.read_chapter(book_id, filename, BOOKS_ROOT)
            query = urllib.parse.urlencode({"book": book_id, "file": filename})
            previous = api_get("chapter_extras?" + query)
            if not previous or not previous.get("ok"):
                raise RuntimeError("读取云端章节素材版本失败，请重试")
            base_revision = (previous.get("meta") or {}).get("revision", "")
            import illustration_generator
            if engine == "antigravity":
                ready, message = ensure_antigravity_running(report_progress)
                if not ready:
                    raise RuntimeError(message)
            meta_res = illustration_generator.generate_chapter_visual_bundle(
                bpath, filename, chapter_text, image_count,
                extra_data.get("custom_prompt", user_prompt), report_progress,
                notes_only=notes_only, model=model_tier, engine=engine, reasoning_effort=reasoning_effort)
            import base64
            illu_dir = Path(bpath) / "02_插图" / meta_res["chapter_code"]
            filenames = [item[key] for item in meta_res["illustrations"]
                         for key in ("filename", "thumbnail_filename") if item.get(key)]
            images = {name: base64.b64encode((illu_dir / name).read_bytes()).decode() for name in filenames}
            report_progress("素材已生成，正在验证云端保存回执")
            uploaded = api_post("sync_illustrations", {
                "book_id": book_id, "chapter_code": meta_res["chapter_code"],
                "meta": meta_res, "images": images, "notes_only": notes_only,
                "base_revision": base_revision})
            if not uploaded or not uploaded.get("ok") or uploaded.get("saved_images") != len(meta_res["illustrations"]):
                raise RuntimeError((uploaded or {}).get("error") or "云端未确认完整保存，本地生成文件已保留，可重试同步")
            latest = api_get("chapter_extras?" + query)
            if latest and latest.get("meta"):
                atomic_json(illu_dir / "meta.json", latest["meta"])
            report_task({"id": tid, "status": "completed",
                "log": "章节文案已生成并保存" if notes_only else f"已生成并同步 {len(meta_res['illustrations'])} 张二维水墨场景插画与互动文案",
                "result": {"conversation_id": meta_res["conversation_id"]}})
            return

        # E. 番茄驳回/存量章节定点重塑修复
        elif action == "repair_chapter":
            chapter_filename = extra_data.get("chapter_file", "")
            rejection_reason = extra_data.get("rejection_reason", user_prompt)
            repair_mode = extra_data.get("repair_mode", "polish")
            audit_issues = extra_data.get("audit_issues", [])

            report_progress(f"正在调阅《{book_id}》【{chapter_filename}】，准备进行定点重塑修复...")

            bpath = book_root
            all_c_files = novel_manager.chapter_files(bpath)
            
            target_file_path = None
            target_idx = -1
            for idx_c, cf in enumerate(all_c_files):
                if os.path.basename(cf) == os.path.basename(chapter_filename):
                    target_file_path = cf
                    target_idx = idx_c
                    break

            if not target_file_path or not os.path.exists(target_file_path):
                report_task({"id": tid, "status": "failed", "log": f"未找到对应章节正文文件: {chapter_filename}"})
                return

            # 自动安全备份原章节文件
            backup_dir = os.path.join(bpath, "01_正文", ".backup")
            os.makedirs(backup_dir, exist_ok=True)
            backup_filename = f"{os.path.basename(target_file_path)}.bak_{int(time.time())}"
            backup_path = os.path.join(backup_dir, backup_filename)
            with open(target_file_path, "r", encoding="utf-8", errors="ignore") as rfp:
                orig_content = rfp.read()
            with open(backup_path, "w", encoding="utf-8") as wfp:
                wfp.write(orig_content)
            report_progress(f"原章节已安全备份至: .backup/{backup_filename}")

            # 确定前一章与后一章，保证上下文承接
            prev_chapter_path = all_c_files[target_idx - 1] if target_idx > 0 else None
            next_chapter_path = all_c_files[target_idx + 1] if target_idx < len(all_c_files) - 1 else None

            # 收集全套设定集清单
            setting_files = sorted(glob.glob(os.path.join(bpath, "00_设定集", "*.md")))
            setting_file_list_str = "\n".join([f"  * `{os.path.basename(sf)}`" for sf in setting_files])

            # 格式化机审问题清单
            issues_summary_lines = []
            for iss in audit_issues[:15]:
                cat = iss.get("category_name") or iss.get("category") or "机审项"
                msg = iss.get("msg") or ""
                sug = iss.get("suggestion") or ""
                issues_summary_lines.append(f"- [{cat}] {msg} {f'（建议：{sug}）' if sug else ''}")
            issues_summary_str = "\n".join(issues_summary_lines) if issues_summary_lines else "（暂无具体机审问题，按作者要求重构）"

            rep_meta = novel_manager.read_book_meta(bpath)
            rep_type = extra_data.get("novel_type") or extra_data.get("scale_type") or rep_meta.get("novel_type", "million_long")
            type_polish_note = "【中短篇快节奏精修】：剔除注水旁白，紧盯单主线与打脸爽感" if rep_type == "mid_short" else ("【爆款短故事精修】：拉满情绪压迫感，强化反转钥匙与隐秘证据链" if rep_type == "short_story" else "【长篇巨制精修】：兼顾世界观深度与伏笔呼应，黄金留存率排版")

            mode_desc = "【🛡️ 合规脱敏精修】：保持原章节剧情大骨架不变，精准替换、重写涉敏涉嫌违规词句（如涉暴动、敏感冲突等），使用文雅合规的修仙科幻隐喻艺术化表达，优化不顺对白。" if repair_mode == "polish" else "【⚡ 深度剧情重构】：结合前后章节主线与细纲，全盘重写本章高潮冲突与悬念反转，解决逻辑违和或节奏拖沓问题，彻底重塑读者爽感与完读率。"

            prompt = f"""【《{book_id}》番茄小说章节定点重构与驳回修复指令 · {os.path.basename(target_file_path)}】
当前小说工程工作区：`D:\\小说\\{book_id}`
作品体量模式：{rep_type}（{type_polish_note}）
目标修复章节文件：`{target_file_path}`
前置章节参考：`{prev_chapter_path if prev_chapter_path else "（此章为第一章）"}`
后置章节参考：`{next_chapter_path if next_chapter_path else "（此章为目前最新章）"}`
原文件安全备份已落盘至：`{backup_path}`

【平台审核驳回意见 / 作者修改指令】：
\"\"\"
{rejection_reason}
\"\"\"

【机审预检问题清单】：
{issues_summary_str}

【重塑执行模式】：
{mode_desc}

【必须执行的工作流 SOP（铁律执行）】：
1. 步骤1：调用 read_file 读取本章原正文（路径：`{target_file_path}`）{f'以及上一章全文（路径：`{prev_chapter_path}`）' if prev_chapter_path else ''}，吃透前文承接与人物言行质感；
2. 步骤2：检索并查阅 `D:\\小说\\{book_id}\\00_设定集\\` 目录下全部设定：
{setting_file_list_str}
   确保重写后的情节、人物关系和能力体系严密自洽，润物细无声展现生产力跃迁；
3. 步骤3：遵循番茄小说行文规范（中文全角双引号“”、移动端短段落排版、戏带设定、每章必留抓人点击下一章的强力断章钩子）；
4. 步骤4：将重塑后的完整正文直接调用 write_file 覆盖写入目标章节路径：
   `{target_file_path}`！
"""
            task_title = f"【手机遥控】重构修复《{book_id}》{os.path.basename(target_file_path)}"

        # F. 正文创作与章节续写精修 / 大纲总谱推演
        else:
            c_files = novel_manager.chapter_files(book_root)
            next_chapter_num = max((int(m.group(1)) for f in c_files if (m := re.search(r"第(\d+)章", os.path.basename(f)))), default=0) + 1
            pad_num = f"{next_chapter_num:03d}"

            prev_chapter_file = c_files[-1] if c_files else None
            prev_chapter_tail = ""
            if prev_chapter_file:
                with open(prev_chapter_file, "r", encoding="utf-8", errors="ignore") as fp:
                    prev_chapter_tail = fp.read()[-2000:]

            # 收集全套设定集清单
            setting_files = sorted(glob.glob(os.path.join(book_root, "00_设定集", "*.md")))
            setting_file_list_str = "\n".join([f"  * `{os.path.basename(sf)}`" for sf in setting_files])

            outline_candidates = glob.glob(os.path.join(book_root, "00_设定集", "*细纲*.md")) + glob.glob(os.path.join(book_root, "00_设定集", "*大纲*.md"))
            chapter_outline = ""
            if outline_candidates:
                with open(outline_candidates[0], "r", encoding="utf-8", errors="ignore") as fp:
                    outline_text = fp.read()
                    pattern = rf"(第0*{next_chapter_num}章[^\n]*\n.*?)(?=\n[^\n]*第\d+章|\Z)"
                    m = re.search(pattern, outline_text, re.DOTALL)
                    if m:
                        chapter_outline = m.group(1).strip()

            workflow = extra_data.get("workflow")
            is_write_chapter = workflow == "serial" or (not workflow and (not user_prompt or "续写" in user_prompt or "开始写作" in user_prompt))
            if workflow == "polish" and prev_chapter_file:
                backup = Path(book_root) / "01_正文/.backup" / (Path(prev_chapter_file).name + ".bak_" + uuid.uuid4().hex)
                atomic_write(backup, Path(prev_chapter_file).read_bytes())
                user_prompt += f"\n本次只精修现有章节 {prev_chapter_file}，原稿已备份到 {backup}。"

            if is_write_chapter:
                b_meta = novel_manager.read_book_meta(book_root, book_id)
                n_type = extra_data.get("novel_type") or extra_data.get("scale_type") or b_meta.get("novel_type", "million_long")
                if n_type == "short_story":
                    words_range = extra_data.get("default_chapter_words") or "2000-3000"
                elif n_type == "mid_short":
                    words_range = extra_data.get("default_chapter_words") or "2500-3500"
                else:
                    words_range = (extra_data.get("default_chapter_words")
                                   or (task.get("preferences") or {}).get("default_chapter_words")
                                   or "4000-5000")
                task_preferences = dict(task.get("preferences") or {})
                task_preferences["default_chapter_words"] = words_range
                if engine == "deepseek":
                    ok, output, last_file = deepseek_client.write_serial_chapters(
                        book_root=book_root, book_id=book_id, count=extra_data.get("chapter_count", 1),
                        user_prompt=user_prompt, model=model_tier, effort=reasoning_effort, progress_cb=report_progress,
                        auto_ensure_outline=extra_data.get("auto_ensure_outline", False), preferences_snapshot=task_preferences
                    )
                    if ok:
                        sync_all_books_to_cloud()
                    report_task({"id": tid, "status": "completed" if ok else "failed", "log": output})
                    return
                ok, output = execute_serial_chapters(book_id, extra_data.get("chapter_count", 1), user_prompt,
                    engine, model_tier, reasoning_effort, report_progress, task_id=tid,
                    auto_ensure_outline=extra_data.get("auto_ensure_outline", False), preferences_snapshot=task_preferences)
                report_task({"id": tid, "status": "completed" if ok else "failed", "log": output})
                return
            else:
                if engine == "deepseek":
                    ok, output = deepseek_client.execute_instruction(
                        book_root=book_root, book_id=book_id, user_prompt=user_prompt,
                        model=model_tier, effort=reasoning_effort, progress_cb=report_progress
                    )
                    if ok:
                        sync_all_books_to_cloud()
                    report_task({"id": tid, "status": "completed" if ok else "failed", "log": output})
                    return
                book_meta_data = novel_manager.read_book_meta(book_root)
                current_novel_type = extra_data.get("novel_type") or extra_data.get("scale_type") or book_meta_data.get("novel_type", "million_long")
                type_outline_guide = ""
                if workflow == "outline":
                    if current_novel_type == "short_story":
                        type_outline_guide = "\n【爆款短故事推演核心原则】：紧扣黄金开篇冲突与三重惊天大反转，严密核验证据链与反转钥匙，确保终局绝地反杀意料之外又情理之中！"
                    elif current_novel_type == "mid_short":
                        type_outline_guide = "\n【中短篇精品推演核心原则】：紧扣单主线与三幕式紧凑大纲，推演后续 10~15 章快节奏升级与核心高潮卡点，拒绝注水拖沓！"
                    else:
                        type_outline_guide = "\n【百万长篇推演核心原则】：兼顾宏大世界观徐徐展开、多线交织、长线伏笔暗埋与人物多阶段深度成长。"

                prompt = f"""【《{book_id}》小说辅助构思与推演指令】
当前小说工程工作区：`D:\\小说\\{book_id}`
作品体量模式：{current_novel_type}
设定集目录：`D:\\小说\\{book_id}\\00_设定集`
现有全套设定文档：
{setting_file_list_str}

来自作者的指令：
\"\"\"
{user_prompt}
\"\"\"{type_outline_guide}

请严格通读并参考上述全部设定集文档，遵循本书宏观世界观与严密逻辑执行该推演要求，并在必要时直接调用 write_file 或 edit_file 更新对应文档！
"""
                task_title = f"【手机遥控】《{book_id}》智能推演"

        report_progress(f"正在连接 {engine}（模型 {model_tier}，思考等级 {reasoning_effort}），准备在小说工作区执行…")
        if action in ("creative_instruction", "repair_chapter", "evolve_settings"):
            root = Path(book_root).resolve()
            def stage_generate(request):
                return desktop_stage(root, book_id, request, engine, model_tier, reasoning_effort, report_progress)
            material = story_memory.instruction_material(root, prompt, BRIDGE_PREFERENCES, stage_generate, report_progress)
            prompt += "\n【当前作品记忆及历史溯源资料】\n" + material
        ok, output = execute_agentapi(prompt, title=task_title, engine_mode=engine, model_tier=model_tier, progress_cb=report_progress, reasoning_effort=reasoning_effort)
        if ok and action == "create_new_novel" and not any(path.read_text(encoding="utf-8-sig").strip() for path in Path(book_root, "00_设定集").glob("*.md")):
            ok, output = False, "新书任务未保存有效设定文档，请检查执行结果后重试"
        
        if ok:
            wait_for_cloud_sync(report_progress, book_id)
            report_task({
                "id": tid, 
                "status": "completed", 
                "log": f"{output.strip()}\n\n成果已校验并同步至云端。"
            })
            print(f"[Task] 成功执行任务: {task_title}")
            # 立即全量同步最新生成的文件
        else:
            report_task({
                "id": tid, 
                "status": "failed", 
                "log": output
            })


    except Exception as e:
        print(f"[Task Worker Error 《{book_id}》] {e}")
        if tid:
            report_task({"id": tid, "status": "failed", "log": str(e)})
    finally:
        with WORKERS_LOCK:
            ACTIVE_WORKERS.pop(book_id, None)
            if ACTIVE_TASK == tid:
                ACTIVE_TASK = next((w["task_id"] for w in ACTIVE_WORKERS.values()), None)


# 8. 多书并行任务调度主循环
def start_task_worker(task):
    global ACTIVE_TASK
    book = safe_component(task.get('book_id', DEFAULT_BOOK))
    with WORKERS_LOCK:
        if book in ACTIVE_WORKERS or len(ACTIVE_WORKERS) >= MAX_CONCURRENT_BOOKS:
            return False
        remember_task(task)
        thread = threading.Thread(target=process_single_task, args=(task,), daemon=True, name=f'Worker-{book}')
        ACTIVE_WORKERS[book] = {'thread': thread, 'task_id': task['id'], 'task': task, 'started_at': time.time()}
        ACTIVE_TASK = task['id']
        try:
            thread.start()
        except Exception:
            ACTIVE_WORKERS.pop(book, None)
            raise
        return True


def recover_active_tasks():
    with TASK_REPORT_LOCK:
        saved = list(active_jobs().values())
    for task in saved:
        with WORKERS_LOCK:
            if task.get('book_id') in ACTIVE_WORKERS:
                continue
        current = api_get('task_status?id=' + urllib.parse.quote(task['id']))
        if current is None:
            return False
        if current.get('status') not in ('pending', 'assigned', 'running'):
            forget_task(task['id'])
            continue
        # Serial writing has chapter/review checkpoints and supports restart recovery.
        if current.get('extra_data', {}).get('workflow') == 'serial':
            if not start_task_worker(current):
                return False
        else:
            report_task({'id': task['id'], 'status': 'failed',
                         'log': '本地执行端重启中断了任务，请核对桌面端已有成果后重试'})
    return True


def run_bridge_loop():
    global ACTIVE_TASK, SCHEDULER_AT, BRIDGE_PHASE
    ensure_single_instance()
    for book in Path(BOOKS_ROOT).iterdir():
        if book.is_dir() and not book.name.startswith('.'):
            try:
                story_memory.recover_commits(book)
            except (OSError, ValueError, RuntimeError, KeyError) as error:
                print(f'[Recovery] 《{book.name}》等待核对 {error}')
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=sync_agent_catalog, daemon=True).start()
    prevent_sleep()
    ok, c_cnt, s_cnt = sync_all_books_to_cloud()
    threading.Thread(target=sync_loop, daemon=True).start()
    print(f"[Sync] 初始同步完成: 多书架章节总数={c_cnt}, 设定文档={s_cnt}")

    print("\n" + "="*50)
    print("🚀 NovelRemoteHub · 智笔云枢 多书并行本地执行端 (Multi-Worker Bridge) 已就绪！")
    print(f"📡 监听云端中枢: {CLOUD_SERVER_URL}")
    print("🤖 执行端: Antigravity / ChatGPT（Codex），支持多部小说同时并发创作")
    print(f"📚 多书架工作区: {BOOKS_ROOT}")
    print("="*50 + "\n")
    BRIDGE_PHASE = 'ready'

    while True:
        SCHEDULER_AT = time.time()
        try:
            if not flush_task_reports():
                time.sleep(5)
                continue
            if not recover_active_tasks():
                time.sleep(5)
                continue
            # 1. 清理已退出的 Worker 线程
            with WORKERS_LOCK:
                dead_books = [bid for bid, info in ACTIVE_WORKERS.items() if not info["thread"].is_alive()]
                for bid in dead_books:
                    del ACTIVE_WORKERS[bid]
                running_count = len(ACTIVE_WORKERS)

            # 2. 如果当前并行任务数未达上限，拉取下一部小说的待处理任务
            if running_count < MAX_CONCURRENT_BOOKS:
                data = api_get("pending_task")
                if data and data.get("refresh_agents"):
                    api_post("sync_agents", desktop_agents.catalog())
                task = data.get("task") if data else None
                if task:
                    start_task_worker(task)
        except Exception as e:
            print(f"[Loop Exception] {e}")
        time.sleep(2)


if __name__ == "__main__":
    run_bridge_loop()
