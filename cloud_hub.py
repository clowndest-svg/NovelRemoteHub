# -*- coding: utf-8 -*-
r"""
大秦科技修仙·远程云端中枢 (Cloud Hub 3.5 高性能旗舰版)
部署在云服务器 124.220.54.64 (127.0.0.1:8899)
Nginx /novel/ 反向代理直达

特性：
1. 双层高速缓存：内置 Python 内存字典缓存 + Redis (127.0.0.1:6379)，查询提速 100 倍 (sub-millisecond)
2. Gzip 数据压缩：超大分章大纲与长篇正文自动启用 Gzip 传输，节省 80% 流量
3. 番茄小说发布智审工作台：错别字定位、敏感风控词分级、标点排版审计与一键自动修复
4. 移动端防缓存 APK 直发路由
"""
import os
import sys
import json
import time
import uuid
import glob
import shutil
import gzip
import re
import html
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import threading
import secrets
import hmac
import hashlib
import base64
from storage import atomic_json, atomic_write, safe_component, within
from agent_workspace import canonical_engine, selected_model, is_workspace
from sync_protocol import content_fingerprint
from urllib.parse import urlparse, parse_qs

# 引入小说管理工作流
sys.path.append(os.path.dirname(__file__))
import novel_manager


def generate_svg_cover(book_title, genre="宇宙科技修仙"):
    title = (book_title or "大秦科技修仙").strip()
    clean_title = re.sub(r"^[《【\s]+|[》】\s]+$", "", title)
    lines = []
    chunk_size = 8
    for i in range(0, len(clean_title), chunk_size):
        lines.append(clean_title[i:i+chunk_size])
    if not lines:
        lines = ["大秦科技修仙"]

    text_elements = ""
    start_y = 255 - (len(lines) - 1) * 26
    for i, line in enumerate(lines):
        y = start_y + i * 52
        text_elements += f'<text x="200" y="{y}" font-size="28" font-weight="900" fill="#f8fafc" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif" letter-spacing="2">{html.escape(line)}</text>\n'

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 560" width="400" height="560">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0b0f19" />
      <stop offset="45%" stop-color="#1e1b4b" />
      <stop offset="100%" stop-color="#311042" />
    </linearGradient>
    <linearGradient id="gold" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#fbbf24" />
      <stop offset="100%" stop-color="#f59e0b" />
    </linearGradient>
  </defs>
  <rect width="100%" height="100%" fill="url(#bg)"/>
  <rect x="15" y="15" width="370" height="530" rx="12" fill="none" stroke="rgba(245, 158, 11, 0.45)" stroke-width="2" stroke-dasharray="8 4"/>
  <rect x="25" y="25" width="350" height="510" rx="8" fill="none" stroke="rgba(255, 255, 255, 0.08)" stroke-width="1"/>
  <rect x="135" y="60" width="130" height="28" rx="14" fill="rgba(245, 158, 11, 0.15)" stroke="#f59e0b" stroke-width="1"/>
  <text x="200" y="79" font-size="13" font-weight="bold" fill="#fbbf24" text-anchor="middle" font-family="system-ui, sans-serif">{html.escape(genre or '连载作品')}</text>
  <circle cx="200" cy="270" r="145" fill="none" stroke="rgba(168, 85, 247, 0.18)" stroke-width="2"/>
  <circle cx="200" cy="270" r="100" fill="none" stroke="rgba(245, 158, 11, 0.15)" stroke-width="1.5" stroke-dasharray="6 3"/>
  {text_elements}
  <line x1="80" y1="440" x2="320" y2="440" stroke="rgba(255, 255, 255, 0.15)" stroke-width="1"/>
  <text x="200" y="470" font-size="12" fill="#94a3b8" text-anchor="middle" font-family="system-ui, sans-serif" letter-spacing="4">NOVEL REMOTE HUB</text>
  <text x="200" y="495" font-size="11" fill="#64748b" text-anchor="middle" font-family="system-ui, sans-serif">全宇宙科技修仙 · 终极创世中枢</text>
</svg>"""

# 1. Redis 高速缓存连接
try:
    import redis
    REDIS_CLIENT = redis.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True, socket_timeout=1)
    REDIS_CLIENT.ping()
    HAS_REDIS = True
except Exception:
    REDIS_CLIENT = None
    HAS_REDIS = False

DATA_DIR = os.environ.get("NOVEL_HUB_DATA_DIR", "/root/novel_hub_data")
BOOKS_DIR = os.path.join(DATA_DIR, "books")
DOWNLOAD_DIR = os.environ.get("NOVEL_HUB_DOWNLOAD_DIR", "/opt/novel_hub/download")
PASSWORD = os.environ.get("NOVEL_HUB_PASSWORD")

os.makedirs(BOOKS_DIR, exist_ok=True)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

PASSWORD_FILE = os.path.join(DATA_DIR, "access_password")
if not PASSWORD:
    if os.path.exists(PASSWORD_FILE):
        with open(PASSWORD_FILE, encoding="utf-8") as password_stream:
            PASSWORD = password_stream.read().strip()
    else:
        PASSWORD = secrets.token_urlsafe(20)
        atomic_write(PASSWORD_FILE, PASSWORD)
        os.chmod(PASSWORD_FILE, 0o600)
if not PASSWORD:
    raise ValueError("访问口令不能为空")

TOKEN_FILE = os.path.join(DATA_DIR, "auth_token")
if os.path.exists(TOKEN_FILE):
    with open(TOKEN_FILE, encoding="utf-8") as token_stream:
        AUTH_TOKEN = token_stream.read().strip()
else:
    AUTH_TOKEN = secrets.token_urlsafe(48)
    atomic_write(TOKEN_FILE, AUTH_TOKEN)
    os.chmod(TOKEN_FILE, 0o600)
SESSION_SECONDS = 7 * 24 * 60 * 60


def issue_session():
    expires_at = int(time.time()) + SESSION_SECONDS
    payload = f"session.{expires_at}.{secrets.token_urlsafe(24)}"
    signature = hmac.new(AUTH_TOKEN.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {"ok": True, "token": payload + "." + signature, "expires_at": expires_at, "expires_in": SESSION_SECONDS}


def valid_access_token(token):
    if not isinstance(token, str) or len(token) > 1024:
        return False
    if hmac.compare_digest(token.encode(), AUTH_TOKEN.encode()):
        return True
    parts = token.split('.')
    if len(parts) != 4 or parts[0] != 'session':
        return False
    try:
        if int(parts[1]) <= time.time():
            return False
    except ValueError:
        return False
    expected = hmac.new(AUTH_TOKEN.encode(), '.'.join(parts[:3]).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(parts[3].encode(), expected.encode())


def issue_reading_link():
    expires_at = int(time.time()) + SESSION_SECONDS
    payload = f"reader.{expires_at}.{secrets.token_urlsafe(24)}"
    signature = hmac.new(AUTH_TOKEN.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {"ok": True, "url": "/novel/read/#" + payload + "." + signature,
            "expires_at": expires_at, "expires_in": SESSION_SECONDS}


def valid_reading_token(token):
    if not isinstance(token, str) or len(token) > 512:
        return False
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "reader":
        return False
    try:
        if int(parts[1]) <= time.time():
            return False
    except ValueError:
        return False
    expected = hmac.new(AUTH_TOKEN.encode(), ".".join(parts[:3]).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(parts[3].encode(), expected.encode())


def chapter_catalog(book_id):
    """Reuse unchanged chapter metadata even when another chapter was updated."""
    book_path, title = novel_manager.get_book_path(book_id, BOOKS_DIR)
    chapters = []
    for filename in novel_manager.chapter_files(book_path):
        stat = os.stat(filename)
        identity = hashlib.sha256(filename.encode()).hexdigest()
        key = f"novel:cache:metadata:{identity}:{stat.st_mtime_ns}:{stat.st_size}"
        metadata = get_cached(key)
        if metadata is None:
            with open(filename, encoding="utf-8", errors="ignore") as stream:
                cleaned = novel_manager.clean_for_tomato(stream.read(), os.path.basename(filename))
            metadata = {"filename": os.path.basename(filename), "title": cleaned["title"],
                        "raw_title": os.path.basename(filename)[:-3], "char_count": cleaned["char_count"],
                        "size": stat.st_size, "mtime": stat.st_mtime}
            set_cached(key, metadata, ttl=86400)
        chapters.append(metadata)
    return {"book_title": title, "chapters": chapters}


def chapter_document(book_id, filename):
    safe_component(filename)
    key = f"novel:cache:chapter:{book_id}:{filename}"
    cached = get_cached(key)
    if cached is not None:
        return cached
    content = novel_manager.read_chapter(book_id, filename, BOOKS_DIR)
    cleaned = novel_manager.clean_for_tomato(content, filename)
    result = {"book_title": book_id, "filename": filename, "title": cleaned["title"],
              "content": content, "char_count": cleaned["char_count"]}
    set_cached(key, result, ttl=600)
    return result


WRITE_LOCK = threading.RLock()
CACHE_LOCK = threading.RLock()
TASKS = {}
LOCAL_STATUS = {
    "online": False,
    "last_seen": 0,
    "current_task": None
}

APP_VERSION_INFO = {
    "versionCode": 13,
    "versionName": "1.10.0",
    "apkUrl": "https://scientificexploration.art/novel/download/NovelRemoteHub.apk?v=1.10.0",
    "changelog": "新增七天免密只读分享，修复同步失败重试与任务期间同步，合并阅读查询并复用章节索引。",
    "forceUpdate": False
}

# 2. 内存字典与 Redis 双层缓存系统
MEMORY_CACHE = {}

def get_cached(key):
    with CACHE_LOCK:
        entry = MEMORY_CACHE.get(key)
        if entry and time.monotonic() < entry[0]:
            return entry[1]
        MEMORY_CACHE.pop(key, None)
    if HAS_REDIS and REDIS_CLIENT:
        try:
            raw = REDIS_CLIENT.get(key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    return None


def set_cached(key, val, ttl=300):
    with CACHE_LOCK:
        if len(MEMORY_CACHE) > 1024:
            MEMORY_CACHE.clear()
        MEMORY_CACHE[key] = (time.monotonic() + ttl, val)
    if HAS_REDIS and REDIS_CLIENT:
        try:
            REDIS_CLIENT.setex(key, ttl, json.dumps(val, ensure_ascii=False))
        except Exception:
            pass


def invalidate_cache(pattern=None):
    with CACHE_LOCK:
        for key in list(MEMORY_CACHE):
            if pattern is None or pattern in key:
                MEMORY_CACHE.pop(key, None)
    if HAS_REDIS and REDIS_CLIENT:
        try:
            for key in REDIS_CLIENT.scan_iter(match="novel:cache:*", count=100):
                if pattern is None or pattern in key:
                    REDIS_CLIENT.delete(key)
        except Exception:
            pass


def load_json_file(path, default=None):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json_file(path, data):
    atomic_json(path, data)


def sync_book(body):
    book_id = safe_component(body.get("book_id"))
    book_path = within(BOOKS_DIR, book_id)
    chapters, settings = body.get("chapters", []), body.get("settings", [])
    for documents in (chapters, settings):
        if not isinstance(documents, list):
            raise ValueError("同步文档必须为列表")
        names = set()
        for document in documents:
            filename = safe_component(document["filename"])
            if not filename.endswith(".md") or not isinstance(document["content"], str):
                raise ValueError("同步文档必须为 Markdown 文本")
            if filename in names:
                raise ValueError("同步章节或设定存在同名文件")
            names.add(filename)
    fingerprint = content_fingerprint(chapters, settings)
    authoritative = body.get("sync_version") == 2
    if authoritative and body.get("fingerprint") != fingerprint:
        raise ValueError("同步内容摘要不一致")
    changed = False
    try:
        for kind, documents in (("01_正文", chapters), ("00_设定集", settings)):
            existing = (novel_manager.chapter_files(book_path) if kind == "01_正文" else
                        glob.glob(os.path.join(book_path, kind, "*.md")))
            by_name = {}
            for filename in existing:
                name = os.path.basename(filename)
                if name in by_name:
                    raise ValueError("云端存在同名章节，请核对分卷")
                by_name[name] = filename
            for document in documents:
                name = document["filename"]
                target = by_name.pop(name, None)
                if target is None:
                    directory = os.path.join(book_path, kind)
                    if kind == "01_正文":
                        directory = os.path.join(directory, "第一卷_人界篇")
                    target = os.path.join(directory, name)
                old = None
                if os.path.isfile(target):
                    with open(target, encoding="utf-8", errors="ignore") as stream:
                        old = stream.read()
                if old != document["content"]:
                    atomic_write(target, document["content"])
                    changed = True
            if authoritative:
                for name, filename in list(by_name.items()):
                    # 如果该文件是正文章节，且其章节序号高于本地上报的最大序号，说明是云端/DeepSeek新创作的章节，绝不能归档误删！
                    is_newer_chapter = False
                    if kind == "01_正文":
                        m_cloud = re.search(r"第0*(\d+)章", name)
                        if m_cloud:
                            cloud_num = int(m_cloud.group(1))
                            max_local_num = 0
                            for doc in documents:
                                m_loc = re.search(r"第0*(\d+)章", doc.get("filename", ""))
                                if m_loc:
                                    max_local_num = max(max_local_num, int(m_loc.group(1)))
                            if cloud_num > max_local_num:
                                is_newer_chapter = True
                    if is_newer_chapter:
                        continue
                    archive = os.path.join(book_path, ".sync-archive", uuid.uuid4().hex, kind, name)
                    os.makedirs(os.path.dirname(archive), exist_ok=True)
                    os.replace(filename, archive)
                    changed = True
        conversations = body.get("conversations", [])
        if conversations:
            save_json_file(os.path.join(DATA_DIR, "conversations.json"), conversations)
        meta = body.get("meta")
        if isinstance(meta, dict) and meta:
            novel_manager.save_book_meta(book_id, meta, BOOKS_DIR)
        cover_base64 = body.get("cover_base64")
        if isinstance(cover_base64, str) and cover_base64:
            novel_manager.save_book_cover(book_id, cover_base64, BOOKS_DIR)
    finally:
        if changed:
            # Invalidate once per book, including public reading responses.
            invalidate_cache(book_id)
            invalidate_cache("novel:cache:books")
    actual_chapters = []
    actual_settings = []
    for filename in novel_manager.chapter_files(book_path):
        with open(filename, encoding="utf-8", errors="ignore") as stream:
            actual_chapters.append({"filename": os.path.basename(filename), "content": stream.read()})
    for filename in glob.glob(os.path.join(book_path, "00_设定集", "*.md")):
        with open(filename, encoding="utf-8", errors="ignore") as stream:
            actual_settings.append({"filename": os.path.basename(filename), "content": stream.read()})
    receipt = content_fingerprint(actual_chapters, actual_settings)
    return {"ok": True, "chapters_count": len(chapters), "settings_count": len(settings),
            "fingerprint": receipt}


def execute_deepseek_task_async(tid):
    """
    云端直连 DeepSeek 官方 API 执行创作与推演任务。
    无论本地电脑是否开机，均可全天候在云端全自动阅读全书设定集与前序章节，
    调用官方 deepseek-flash / deepseek-v4-pro 大模型进行思维链推演与正文撰写！
    """
    tasks_file = os.path.join(DATA_DIR, "tasks.json")
    task = TASKS.get(tid)
    if not task:
        return
    task["status"] = "running"
    task["heartbeat_at"] = time.time()

    def report(msg):
        task["progress_log"].append(f"[{time.strftime('%H:%M:%S')}] {str(msg)[:4000]}")
        task["progress_log"] = task["progress_log"][-200:]
        task["heartbeat_at"] = time.time()
        save_json_file(tasks_file, TASKS)

    try:
        import deepseek_client
        cfg = deepseek_client.get_deepseek_config(DATA_DIR)
        if not cfg["enabled"] or not cfg["api_key"]:
            raise ValueError("DeepSeek 官方 API 未启用或未配置 API Key，请在【系统设置】中输入您的 Key 并开启开关")

        book_id = task.get("book_id") or "给政哥讲量子力学，他竟肉身硬抗二向箔"
        book_path, _ = novel_manager.get_book_path(book_id, BOOKS_DIR)
        extra_data = task.get("extra_data") or {}
        user_prompt = task.get("prompt") or extra_data.get("instructions") or ""
        model = extra_data.get("model_tier") or "deepseek-flash"
        effort = extra_data.get("reasoning_effort") or "high"
        workflow = extra_data.get("workflow")
        chapter_count = novel_manager.validate_chapter_count(extra_data.get("chapter_count", 1))

        is_write_chapter = (workflow == "serial" or (not workflow and (not user_prompt or "续写" in user_prompt or "开始写作" in user_prompt)))

        if is_write_chapter:
            ok, output, last_file = deepseek_client.write_serial_chapters(
                book_root=book_path,
                book_id=book_id,
                count=chapter_count,
                user_prompt=user_prompt,
                model=model,
                effort=effort,
                config_dir=DATA_DIR,
                progress_cb=report
            )
            if not ok:
                raise RuntimeError(output)
            task["status"] = "completed"
            task["completed_at"] = time.time()
            task["result"] = {"summary": output, "files": [last_file] if last_file else []}
            report(f"🎉 {output}")
            invalidate_cache()
        else:
            ok, output = deepseek_client.execute_instruction(
                book_root=book_path,
                book_id=book_id,
                user_prompt=user_prompt,
                model=model,
                effort=effort,
                config_dir=DATA_DIR,
                progress_cb=report
            )
            if not ok:
                raise RuntimeError(output)
            task["status"] = "completed"
            task["completed_at"] = time.time()
            task["result"] = {"summary": output}
            report(f"✅ 执行完毕:\n{output[:500]}")
            invalidate_cache()
    except Exception as e:
        task["status"] = "failed"
        task["completed_at"] = time.time()
        task["progress_log"].append(f"[{time.strftime('%H:%M:%S')}] ❌ 任务执行失败: {str(e)}")
        save_json_file(tasks_file, TASKS)


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        self.slots.acquire()
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            request.settimeout(20)
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class HubHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        message = re.sub(r"([?&]token=)[^&\s\"]+", r"\1[redacted]", format % args)
        super().log_message("%s", message)

    def end_headers(self):
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Novel-Token")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        
        # 针对 >512 字节的数据自动启用 Gzip 压缩
        accept_enc = self.headers.get("Accept-Encoding", "")
        if "gzip" in accept_enc and len(body) > 512:
            body = gzip.compress(body)
            self.send_header("Content-Encoding", "gzip")

        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html_text):
        body = html_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        
        accept_enc = self.headers.get("Accept-Encoding", "")
        if "gzip" in accept_enc and len(body) > 1024:
            body = gzip.compress(body)
            self.send_header("Content-Encoding", "gzip")

        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def check_auth(self):
        token = self.headers.get("X-Novel-Token")
        if not token:
            auth_header = self.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
        if not token and hasattr(self, 'path'):
            try:
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                token = qs.get("token", [None])[0]
            except Exception:
                pass
        if not token:
            cookie_header = self.headers.get("Cookie", "")
            for item in cookie_header.split(";"):
                item = item.strip()
                if item.startswith("novel_token="):
                    token = item[len("novel_token="):]
        return valid_access_token(token)

    def get_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if not 0 <= length <= 32 * 1024 * 1024:
            raise ValueError("请求内容超过 32MB 限制")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("请求必须为 JSON 对象")
        return body

    def do_GET(self):
        try:
            query = parse_qs(urlparse(self.path).query)
            if query.get("book"):
                safe_component(query["book"][0])
            if query.get("file"):
                safe_component(query["file"][0])
            if self.path.split("?")[0].rstrip("/").endswith((
                    "/pending_task", "/task_status", "/books", "/chapters", "/chapter", "/library",
                    "/settings", "/setting", "/tomato_clean", "/chapter_extras", "/cover", "/app_settings")):
                with WRITE_LOCK:
                    self.handle_get()
            else:
                self.handle_get()
        except (ValueError, KeyError, TypeError) as error:
            self.send_json({"error": str(error)}, 400)
        except FileNotFoundError as error:
            self.send_json({"error": str(error)}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.log_error("Request failed")
            self.send_json({"error": "服务器处理失败，请重试"}, 500)

    def do_POST(self):
        try:
            path = urlparse(self.path).path.rstrip("/")
            if path not in ("/api/login", "/novel/api/login") and not self.check_auth():
                self.send_json({"error": "Unauthorized", "need_login": True}, 401)
                return
            with WRITE_LOCK:
                self.handle_post()
        except (ValueError, KeyError, TypeError, UnicodeError) as error:
            self.send_json({"error": str(error)}, 400)
        except FileNotFoundError as error:
            self.send_json({"error": str(error)}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            self.log_error("Write request failed: %s", type(error).__name__)
            self.send_json({"error": "保存失败，请重试"}, 500)

    def handle_get(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = parse_qs(parsed.query)

        if path in ("/novel/read", "/read"):
            with open(os.path.join(os.path.dirname(__file__), "reader.html"), encoding="utf-8") as stream:
                self.send_html(stream.read())
            return

        if path.startswith(("/api/read/", "/novel/api/read/")):
            if not valid_reading_token(self.headers.get("X-Reading-Token")):
                self.send_json({"error": "阅读链接已失效，请向作者索取新链接"}, 403)
                return
            resource = path.rsplit("/", 1)[-1]
            if resource == "books":
                cached = get_cached("novel:cache:books")
                if cached is None:
                    cached = {"books": novel_manager.list_books(BOOKS_DIR)}
                    set_cached("novel:cache:books", cached, ttl=180)
                self.send_json({"books": [{key: book[key] for key in ("id", "title", "chapter_count")}
                                           for book in cached["books"] if book["chapter_count"]]})
            elif resource == "chapters":
                book = qs.get("book", [""])[0]
                cache_key = f"novel:cache:chapters:{book}"
                catalog = get_cached(cache_key)
                if catalog is None:
                    catalog = chapter_catalog(book)
                    set_cached(cache_key, catalog)
                self.send_json(catalog)
            elif resource == "chapter":
                book, filename = qs.get("book", [""])[0], qs.get("file", [""])[0]
                key = f"novel:cache:reading:{book}:{filename}"
                chapter = get_cached(key)
                if chapter is None:
                    raw = novel_manager.read_chapter(book, filename, BOOKS_DIR)
                    chapter = novel_manager.clean_for_tomato(raw, filename)
                    chapter = {"title": chapter["title"], "content": chapter["content"]}
                    set_cached(key, chapter)
                self.send_json(chapter)
            else:
                self.send_json({"error": "Not Found"}, 404)
            return

        # 根路径访问前端页面
        if path == "" or path == "/novel":
            html_path = os.path.join(os.path.dirname(__file__), "index.html")
            if os.path.exists(html_path):
                with open(html_path, "r", encoding="utf-8") as f:
                    self.send_html(f.read())
            else:
                self.send_html("<h1>大秦科技修仙远程中枢 3.5 启动中...</h1>")
            return

        if path.startswith(("/assets/", "/novel/assets/")):
            filename = path.rsplit("/", 1)[-1]
            if filename not in ("marked.min.js", "purify.min.js"):
                self.send_json({"error": "Not Found"}, 404)
                return
            with open(os.path.join(os.path.dirname(__file__), "assets", filename), "rb") as stream:
                content = stream.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        # APK 下载路由 (公网免密，防缓存机制)
        if "download/NovelRemoteHub.apk" in path or "download/DaqinNovelHub.apk" in path:
            candidates = [
                os.path.join(DOWNLOAD_DIR, "NovelRemoteHub.apk"),
                os.path.join(DOWNLOAD_DIR, "DaqinNovelHub.apk"),
                os.path.join(os.path.dirname(__file__), "NovelRemoteHub.apk"),
                os.path.join(os.path.dirname(__file__), "DaqinNovelHub.apk")
            ]
            existing = [f for f in candidates if os.path.exists(f)]
            if existing:
                existing.sort(key=os.path.getmtime, reverse=True)
                apk_path = existing[0]
                size = os.path.getsize(apk_path)
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Disposition", 'attachment; filename="NovelRemoteHub.apk"')
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()
                with open(apk_path, "rb") as fp:
                    shutil.copyfileobj(fp, self.wfile)
            else:
                self.send_json({"error": "APK not found on server"}, 404)
            return

        # 版本检查接口 (公网免密)
        if path == "/api/version" or path == "/novel/api/version":
            self.send_json(APP_VERSION_INFO)
            return

        # 书籍封面接口 (公网流式直出：有真实封面输出真实图片，无封面动态输出矢量SVG)
        if path in ("/api/cover", "/novel/api/cover"):
            book_id = qs.get("book", [""])[0]
            cover_data = None
            mime_type = "image/png"
            if book_id:
                try:
                    bpath, btitle = novel_manager.get_book_path(book_id, BOOKS_DIR)
                    # 优先检测真实封面文件
                    for ext in [".png", ".jpg", ".jpeg", ".webp"]:
                        cand = os.path.join(bpath, "00_设定集", f"cover{ext}")
                        if os.path.exists(cand) and os.path.isfile(cand):
                            with open(cand, "rb") as fp:
                                cover_data = fp.read()
                            mime_type = "image/jpeg" if ext in [".jpg", ".jpeg"] else ("image/webp" if ext == ".webp" else "image/png")
                            break
                        cand2 = os.path.join(bpath, f"cover{ext}")
                        if os.path.exists(cand2) and os.path.isfile(cand2):
                            with open(cand2, "rb") as fp:
                                cover_data = fp.read()
                            mime_type = "image/jpeg" if ext in [".jpg", ".jpeg"] else ("image/webp" if ext == ".webp" else "image/png")
                            break
                    if not cover_data:
                        meta = novel_manager.read_book_meta(bpath)
                        svg = generate_svg_cover(btitle or book_id, meta.get("genre", "科技修仙"))
                        cover_data = svg.encode("utf-8")
                        mime_type = "image/svg+xml; charset=utf-8"
                except Exception:
                    pass
            if not cover_data:
                svg = generate_svg_cover(book_id or "大秦科技修仙", "科技修仙")
                cover_data = svg.encode("utf-8")
                mime_type = "image/svg+xml; charset=utf-8"

            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(cover_data)))
            self.send_header("Cache-Control", "public, max-age=600")
            if qs.get("download") == ["1"]:
                fname = f"cover_{book_id}.png" if "png" in mime_type else f"cover_{book_id}.svg"
                self.send_header("Content-Disposition", f'attachment; filename="{urllib.parse.quote(fname)}"')
            self.end_headers()
            self.wfile.write(cover_data)
            return

        if path in ("/api/engines", "/novel/api/engines"):
            if not self.check_auth():
                self.send_json({"error": "Unauthorized", "need_login": True}, 401)
                return
            catalog = load_json_file(os.path.join(DATA_DIR, "agents.json"), {"engines": [], "updated_at": 0})
            engines_list = list(catalog.get("engines", []))
            try:
                import deepseek_client
                ds_info = deepseek_client.discover(DATA_DIR)
                cfg = deepseek_client.get_deepseek_config(DATA_DIR)
                if cfg.get("enabled"):
                    if not any(e.get("id") == "deepseek" for e in engines_list):
                        engines_list.append(ds_info)
            except Exception:
                pass
            self.send_json({"engines": engines_list, "updated_at": catalog.get("updated_at", 0), "stale": time.time() - catalog.get("updated_at", 0) > 180})
            return

        # 检查公司电脑是否在线
        if time.time() - LOCAL_STATUS["last_seen"] > 25:
            LOCAL_STATUS["online"] = False

        if path == "/api/status" or path == "/novel/api/status":
            self.send_json({
                "online": LOCAL_STATUS["online"],
                "last_seen": LOCAL_STATUS["last_seen"],
                "last_seen_ago": int(time.time() - LOCAL_STATUS["last_seen"]) if LOCAL_STATUS["last_seen"] else -1,
                "server_time": time.time(),
                "has_redis": HAS_REDIS
            })
            return

        # 以下需认证
        if not self.check_auth() and not path.endswith("/api/login"):
            self.send_json({"error": "Unauthorized", "need_login": True}, 401)
            return

        # 获取所有书籍 (多书架，启用内存+Redis缓存)
        if path == "/api/books" or path == "/novel/api/books":
            cache_key = "novel:cache:books"
            cached = get_cached(cache_key)
            if cached:
                self.send_json(cached)
                return
            books = novel_manager.list_books(BOOKS_DIR)
            res = {"books": books}
            set_cached(cache_key, res, ttl=180)
            self.send_json(res)
            return

        # 获取系统全局个性化设置
        if path in ("/api/app_settings", "/novel/api/app_settings"):
            settings = novel_manager.get_user_settings(DATA_DIR)
            self.send_json({"ok": True, "settings": settings})
            return

        if path in ("/api/library", "/novel/api/library"):
            books = get_cached("novel:cache:books")
            if books is None:
                books = {"books": novel_manager.list_books(BOOKS_DIR)}
                set_cached("novel:cache:books", books, ttl=180)
            selected = qs.get("book", [""])[0]
            if not any(book["id"] == selected for book in books["books"]):
                selected = books["books"][0]["id"] if books["books"] else ""
            catalog = {"chapters": []}
            latest = None
            if selected:
                key = f"novel:cache:chapters:{selected}"
                catalog = get_cached(key)
                if catalog is None:
                    catalog = chapter_catalog(selected)
                    set_cached(key, catalog)
                if catalog["chapters"]:
                    latest = chapter_document(selected, catalog["chapters"][-1]["filename"])
            self.send_json({**books, **catalog, "book": selected, "latest": latest})
            return

        if path in ("/api/sync_status", "/novel/api/sync_status"):
            self.send_json({**load_json_file(os.path.join(DATA_DIR, "sync_status.json"), {}),
                            "online": LOCAL_STATUS["online"],
                            "requested_at": LOCAL_STATUS.get("sync_requested_at", 0),
                            "completed_request": LOCAL_STATUS.get("sync_completed_request", 0)})
            return

        # 获取章节列表 (支持 ?book=xxx，启用缓存)
        if path == "/api/chapters" or path == "/novel/api/chapters":
            book_id = qs.get("book", [None])[0] or "给政哥讲量子力学，他竟肉身硬抗二向箔"
            cache_key = f"novel:cache:chapters:{book_id}"
            cached = get_cached(cache_key)
            if cached:
                self.send_json(cached)
                return

            res = chapter_catalog(book_id)
            set_cached(cache_key, res, ttl=300)
            self.send_json(res)
            return

        # 获取单章内容 (支持 ?book=xxx&file=xxx，启用缓存)
        if path == "/api/chapter" or path == "/novel/api/chapter":
            book_id = qs.get("book", [None])[0] or "给政哥讲量子力学，他竟肉身硬抗二向箔"
            fname = qs.get("file", [""])[0]
            if not fname:
                self.send_json({"error": "Missing file param"}, 400)
                return

            self.send_json(chapter_document(book_id, fname))
            return

        # 番茄小说清洗与全维度机审预检接口 (启用缓存加速)
        if path == "/api/tomato_clean" or path == "/novel/api/tomato_clean":
            book_id = qs.get("book", [None])[0] or "给政哥讲量子力学，他竟肉身硬抗二向箔"
            fname = qs.get("file", [""])[0]
            cache_key = f"novel:cache:tomato_audit:{book_id}:{fname}"
            cached = get_cached(cache_key)
            if cached:
                self.send_json(cached)
                return

            bpath, btitle = novel_manager.get_book_path(book_id, BOOKS_DIR)
            candidates = [p for p in novel_manager.chapter_files(bpath) if os.path.basename(p) == fname]
            if len(candidates) > 1:
                raise ValueError("章节文件名重复，请检查分卷名称")
            if candidates and os.path.exists(candidates[0]):
                with open(candidates[0], "r", encoding="utf-8", errors="ignore") as fp:
                    raw_text = fp.read()
                cleaned = novel_manager.clean_for_tomato(raw_text, fname)
                audit = novel_manager.audit_for_tomato(cleaned)
                res = {
                    "ok": True,
                    "book_title": btitle,
                    "filename": fname,
                    "title": cleaned["title"],
                    "content": cleaned["content"],
                    "char_count": cleaned["char_count"],
                    "line_count": cleaned["line_count"],
                    "audit": audit
                }
                set_cached(cache_key, res, ttl=300)
                self.send_json(res)
            else:
                self.send_json({"error": "Chapter not found"}, 404)
            return

        # 导出全本番茄分章 zip 包
        if path == "/api/export_tomato_zip" or path == "/novel/api/export_tomato_zip":
            book_id = qs.get("book", [None])[0]
            zip_bytes, zip_name = novel_manager.export_tomato_zip(book_id, BOOKS_DIR)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            encoded_name = urllib.parse.quote(zip_name)
            self.send_header("Content-Disposition", f'attachment; filename="{encoded_name}"; filename*=UTF-8\'\'{encoded_name}')
            self.send_header("Content-Length", str(len(zip_bytes)))
            self.end_headers()
            self.wfile.write(zip_bytes)
            return

        # 导出单章全套高清概念插图 zip 包
        if path in ["/api/export_chapter_illustrations_zip", "/novel/api/export_chapter_illustrations_zip"]:
            book_id = qs.get("book", [None])[0] or "给政哥讲量子力学，他竟肉身硬抗二向箔"
            fname = qs.get("file", [""])[0]
            zip_bytes, zip_name = novel_manager.export_chapter_illustrations_zip(book_id, fname, BOOKS_DIR)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            encoded_name = urllib.parse.quote(zip_name)
            self.send_header("Content-Disposition", f'attachment; filename="{encoded_name}"; filename*=UTF-8\'\'{encoded_name}')
            self.send_header("Content-Length", str(len(zip_bytes)))
            self.end_headers()
            self.wfile.write(zip_bytes)
            return

        # 获取章节的「作者有话说」与插画集元数据
        if path in ["/api/chapter_extras", "/novel/api/chapter_extras"]:
            book_id = qs.get("book", [None])[0] or "给政哥讲量子力学，他竟肉身硬抗二向箔"
            fname = qs.get("file", [""])[0]
            extras = novel_manager.get_chapter_extras(book_id, fname, BOOKS_DIR)
            self.send_json(extras)
            return

        # 调阅单张概念插图 (流式输出 PNG)
        if path in ["/api/illustration", "/novel/api/illustration"]:
            book_id = qs.get("book", [None])[0] or "给政哥讲量子力学，他竟肉身硬抗二向箔"
            chap_dir = qs.get("chap", [""])[0]
            img_name = qs.get("img", [""])[0]
            bpath, _ = novel_manager.get_book_path(book_id, BOOKS_DIR)
            img_path = within(bpath, "02_插图", chap_dir, img_name)
            if os.path.exists(img_path) and os.path.isfile(img_path):
                try:
                    with open(img_path, "rb") as fp:
                        img_data = fp.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg" if img_name.endswith(".jpg") else "image/png")
                    self.send_header("Content-Length", str(len(img_data)))
                    self.send_header("Cache-Control", "private, max-age=86400")
                    if qs.get("download") == ["1"]:
                        self.send_header("Content-Disposition", "attachment; filename=" + urllib.parse.quote(img_name))
                    self.end_headers()
                    self.wfile.write(img_data)
                    return
                except Exception:
                    pass
            self.send_json({"error": "Illustration not found"}, 404)
            return

        # 获取设定集列表 (启用高速缓存)
        if path == "/api/settings" or path == "/novel/api/settings":
            book_id = qs.get("book", [None])[0] or "给政哥讲量子力学，他竟肉身硬抗二向箔"
            cache_key = f"novel:cache:settings:{book_id}"
            cached = get_cached(cache_key)
            if cached:
                self.send_json(cached)
                return

            bpath, btitle = novel_manager.get_book_path(book_id, BOOKS_DIR)
            s_dir = os.path.join(bpath, "00_设定集")
            files = sorted(glob.glob(os.path.join(s_dir, "*.md")))
            settings = []
            for f in files:
                fname = os.path.basename(f)
                settings.append({
                    "filename": fname,
                    "title": fname.replace(".md", ""),
                    "size": os.path.getsize(f),
                    "mtime": os.path.getmtime(f)
                })
            res = {"book_title": btitle, "settings": settings}
            set_cached(cache_key, res, ttl=300)
            self.send_json(res)
            return

        # 获取单项设定内容 (启用高速缓存)
        if path == "/api/setting" or path == "/novel/api/setting":
            book_id = qs.get("book", [None])[0] or "给政哥讲量子力学，他竟肉身硬抗二向箔"
            fname = qs.get("file", [""])[0]
            cache_key = f"novel:cache:setting:{book_id}:{fname}"
            cached = get_cached(cache_key)
            if cached:
                self.send_json(cached)
                return

            bpath, btitle = novel_manager.get_book_path(book_id, BOOKS_DIR)
            target = os.path.join(bpath, "00_设定集", os.path.basename(fname))
            if os.path.exists(target):
                with open(target, "r", encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                res = {
                    "book_title": btitle,
                    "filename": fname,
                    "title": os.path.basename(fname).replace(".md", ""),
                    "content": content
                }
                set_cached(cache_key, res, ttl=600)
                self.send_json(res)
            else:
                self.send_json({"error": "Setting not found"}, 404)
            return

        # 获取 Antigravity 历史对话
        if path == "/api/conversations" or path == "/novel/api/conversations":
            conv_file = os.path.join(DATA_DIR, "conversations.json")
            convs = load_json_file(conv_file, [])
            self.send_json({"conversations": convs})
            return

        # 待执行任务轮询 (供公司电脑 Bridge 拉取)
        if path == "/api/pending_task" or path == "/novel/api/pending_task":
            LOCAL_STATUS["online"] = True
            LOCAL_STATUS["last_seen"] = time.time()
            now = time.time()
            refresh_agents = LOCAL_STATUS.pop("refresh_agents", False)
            for tid, t in list(TASKS.items()):
                if t["status"] == "running" and now - t.get("heartbeat_at", t.get("assigned_at", now)) > 180:
                    t.update(status="failed", completed_at=now)
                    t["progress_log"].append("本地执行端失去心跳，请确认 Antigravity 中的执行结果后重试")
                    save_json_file(os.path.join(DATA_DIR, "tasks.json"), TASKS)
                is_stale_assigned = (t["status"] == "assigned" and (now - t.get("heartbeat_at", t.get("assigned_at", t.get("created_at", now)))) > 30)
                if t["status"] == "pending" or is_stale_assigned:
                    if any(other_id != tid and other.get('book_id') == t.get('book_id')
                           and other.get('status') in ('assigned', 'running') for other_id, other in TASKS.items()):
                        continue
                    t["status"] = "assigned"
                    t["assigned_at"] = now
                    save_json_file(os.path.join(DATA_DIR, "tasks.json"), TASKS)
                    self.send_json({"task": t, "refresh_agents": refresh_agents})
                    return
            self.send_json({"task": None, "refresh_agents": refresh_agents})
            return

        # 任务状态查询接口
        if path == "/api/task_status" or path == "/novel/api/task_status":
            tid = qs.get("id", [""])[0]
            if tid in TASKS:
                task = TASKS[tid]
                ahead = []
                if task['status'] == 'pending':
                    for key, item in TASKS.items():
                        if key == tid:
                            break
                        if item['status'] in ('pending', 'assigned', 'running'):
                            ahead.append(item)
                self.send_json({**task, 'queue_position': len(ahead),
                    'waiting_for': [{'id': item['id'], 'action': item['action'],
                        'chapter_count': item.get('extra_data', {}).get('chapter_count', 1),
                        'progress': next((line for line in reversed(item.get('progress_log', [])) if '续写进度' in line), (item.get('progress_log') or ['等待电脑领取'])[-1])} for item in ahead]})
            else:
                self.send_json({"error": "Task not found"}, 404)
            return

        self.send_json({"error": "Not Found", "path": path}, 404)

    def handle_post(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self.get_json_body()

        # 登录认证
        if path == "/api/login" or path == "/novel/api/login":
            pwd = body.get("password", "")
            if isinstance(pwd, str) and hmac.compare_digest(pwd.encode(), PASSWORD.encode()):
                self.send_json(issue_session())
            else:
                self.send_json({"ok": False, "error": "口令错误，请重新输入"}, 401)
            return

        if path in ("/api/reading_link", "/novel/api/reading_link"):
            self.send_json(issue_reading_link())
            return

        if path in ("/api/request_sync", "/novel/api/request_sync"):
            LOCAL_STATUS["sync_requested_at"] = time.time()
            self.send_json({"ok": True, "requested_at": LOCAL_STATUS["sync_requested_at"]})
            return

        if path in ("/api/sync_report", "/novel/api/sync_report"):
            report = {"ok": body.get("ok") is True, "error": str(body.get("error", ""))[:500],
                      "reported_at": time.time(), "books_count": body.get("books_count", 0),
                      "chapters_count": body.get("chapters_count", 0),
                      "settings_count": body.get("settings_count", 0)}
            if report["ok"]:
                report["last_success_at"] = time.time()
                LOCAL_STATUS["sync_completed_request"] = body.get("request_id", 0)
            else:
                old = load_json_file(os.path.join(DATA_DIR, "sync_status.json"), {})
                report["last_success_at"] = old.get("last_success_at", 0)
            save_json_file(os.path.join(DATA_DIR, "sync_status.json"), report)
            self.send_json({"ok": True})
            return

        if path in ("/api/sync_data", "/novel/api/sync_data"):
            result = sync_book(body)
            LOCAL_STATUS.update(online=True, last_seen=time.time())
            self.send_json(result)
            return

        # 保存系统个性化设置
        if path in ("/api/app_settings", "/novel/api/app_settings"):
            new_settings = novel_manager.save_user_settings(body, DATA_DIR)
            self.send_json({"ok": True, "settings": new_settings})
            return

        # 修改小说基本信息与封面
        if path in ("/api/book_meta", "/novel/api/book_meta"):
            book_id = body.get("book_id")
            if not book_id:
                raise ValueError("缺少作品编号")
            meta_update = {}
            if "title" in body and str(body["title"]).strip():
                meta_update["title"] = str(body["title"]).strip()
            if "genre" in body:
                meta_update["genre"] = str(body["genre"]).strip()
            if "description" in body:
                meta_update["description"] = str(body["description"]).strip()
            if "tags" in body and isinstance(body["tags"], list):
                meta_update["tags"] = [str(t).strip() for t in body["tags"] if str(t).strip()]

            save_res = novel_manager.save_book_meta(book_id, meta_update, BOOKS_DIR)
            target_id = save_res.get("book_id", book_id)

            cover_base64 = body.get("cover_base64")
            if cover_base64 and isinstance(cover_base64, str):
                novel_manager.save_book_cover(target_id, cover_base64, BOOKS_DIR)

            invalidate_cache()
            self.send_json({"ok": True, "book_id": target_id, "meta": save_res.get("meta", {})})
            return

        # 安全删除小说（软删除，移入.trash/，防误触二次确认）
        if path in ("/api/delete_book", "/novel/api/delete_book"):
            book_id = body.get("book_id")
            confirm_title = str(body.get("confirm_title") or "").strip()
            if not book_id:
                raise ValueError("缺少作品编号")
            bpath, btitle = novel_manager.get_book_path(book_id, BOOKS_DIR)
            clean_confirm = re.sub(r"^[《【\s]+|[》】\s]+$", "", confirm_title)
            clean_btitle = re.sub(r"^[《【\s]+|[》】\s]+$", "", btitle)
            clean_id = re.sub(r"^[《【\s]+|[》】\s]+$", "", book_id)
            if clean_confirm != clean_btitle and clean_confirm != clean_id:
                raise ValueError(f"安全确认失败：输入的书名必须完全等于原书名「{btitle}」")
            del_res = novel_manager.delete_book_safe(book_id, BOOKS_DIR)
            invalidate_cache()
            self.send_json({"ok": True, "result": del_res, "message": f"作品「{btitle}」已安全移入回收站"})
            return

        # DeepSeek API 连通性测试接口
        if path in ("/api/test_deepseek", "/novel/api/test_deepseek"):
            api_key = str(body.get("api_key") or "").strip()
            base_url = str(body.get("base_url") or "").strip()
            import deepseek_client
            ok, msg = deepseek_client.test_api_key(api_key, base_url)
            self.send_json({"ok": ok, "message": msg})
            return

        if path in ("/api/refresh_agents", "/novel/api/refresh_agents"):
            LOCAL_STATUS["refresh_agents"] = True
            self.send_json({"ok": True})
            return

        if path in ("/api/sync_agents", "/novel/api/sync_agents"):
            engines = body.get("engines")
            if not isinstance(engines, list) or len(engines) < 2:
                raise ValueError("执行端目录格式无效")
            cleaned = []
            for entry in engines:
                engine = canonical_engine(entry["id"])
                available = bool(entry.get("available"))
                if engine != "deepseek" and available and (not is_workspace(entry.get("workspace", "")) or entry.get("project_name") != "小说" or not entry.get("project_id")):
                    raise ValueError("执行端必须绑定小说工作区")
                models = entry.get("models", [])
                if not isinstance(models, list) or len(models) > 100:
                    raise ValueError("模型目录格式无效")
                filtered = []
                for model in models:
                    identifier, name = model.get("id"), model.get("name")
                    efforts = model.get("efforts", [])
                    if not isinstance(identifier, str) or not identifier or len(identifier) > 150 or not isinstance(name, str) or len(name) > 200:
                        raise ValueError("模型名称无效")
                    if not isinstance(efforts, list) or not efforts or len(efforts) > 12:
                        raise ValueError("模型思考等级格式无效")
                    if any(not isinstance(effort.get("id"), str) or len(effort["id"]) > 30 for effort in efforts):
                        raise ValueError("模型思考等级无效")
                    filtered.append({"id": identifier, "name": name, "disabled": bool(model.get("disabled")),
                        "is_default": bool(model.get("is_default")), "default_effort": model.get("default_effort"),
                        "efforts": [{"id": effort["id"], "name": str(effort.get("name", effort["id"]))[:100]} for effort in efforts],
                        "effort_models": model.get("effort_models", {}), "quota_remaining": model.get("quota_remaining")})
                display_name = "Antigravity" if engine == "antigravity" else ("ChatGPT（Codex）" if engine == "codex" else "DeepSeek (官方直连 API)")
                cleaned.append({"id": engine, "name": display_name,
                    "available": available, "models": filtered, "project_id": entry.get("project_id"),
                    "project_name": entry.get("project_name"), "workspace": entry.get("workspace"),
                    "permission_mode": "full-access", "capabilities": {"text": True, "images": bool(entry.get("capabilities", {}).get("images"))},
                    "error": str(entry.get("error", ""))[:500]})
            ids = [entry["id"] for entry in cleaned]
            if len(ids) != len(set(ids)):
                raise ValueError("执行端类型重复")
            if set(ids) not in ({"antigravity", "codex"}, {"antigravity", "codex", "deepseek"}):
                raise ValueError("执行端类型无效")
            save_json_file(os.path.join(DATA_DIR, "agents.json"), {"engines": cleaned, "updated_at": time.time()})
            self.send_json({"ok": True})
            return

        if path in ("/api/heartbeat", "/novel/api/heartbeat"):
            LOCAL_STATUS.update(online=True, last_seen=time.time())
            tid = body.get("id")
            if tid in TASKS and TASKS[tid]["status"] == "running":
                TASKS[tid]["heartbeat_at"] = time.time()
            self.send_json({"ok": True, "sync_requested_at": LOCAL_STATUS.get("sync_requested_at", 0)})
            return

        if path in ("/api/save_chapter_notes", "/novel/api/save_chapter_notes"):
            book = body.get("book")
            filename = safe_component(body.get("file"))
            bpath, _ = novel_manager.get_book_path(book, BOOKS_DIR)
            extras = novel_manager.get_chapter_extras(book, filename, BOOKS_DIR)
            meta = extras.get("meta") or {}
            if body.get("revision", "") != meta.get("revision", ""):
                self.send_json({"error": "文案已在其他页面更新，请重新载入后保存"}, 409)
                return
            note = body.get("author_note", "")
            comments = body.get("chapter_comments", [])
            if not isinstance(note, str) or len(note) > 20000 or not isinstance(comments, list):
                raise ValueError("文案格式或长度无效")
            if len(comments) > 20 or any(not isinstance(c, str) or len(c) > 5000 for c in comments):
                raise ValueError("章评格式或长度无效")
            meta.update(chapter_filename=filename, chapter_code=extras["chap_dir"],
                        author_note=note, chapter_comments=comments, revision=uuid.uuid4().hex)
            directory = within(bpath, "02_插图", extras["chap_dir"])
            atomic_write(os.path.join(directory, "作者有话说.md"), note)
            atomic_write(os.path.join(directory, "章节评论.md"), "\n\n".join(comments))
            save_json_file(os.path.join(directory, "meta.json"), meta)
            self.send_json({"ok": True, "meta": meta})
            return

        # Images use immutable filenames. Publish metadata only after every upload is validated.
        if path in ("/api/sync_illustrations", "/novel/api/sync_illustrations"):
            book_id = body.get("book_id")
            bpath, _ = novel_manager.get_book_path(book_id, BOOKS_DIR)
            chapter_code = safe_component(body.get("chapter_code"))
            incoming = body.get("meta", {})
            filename = safe_component(incoming.get("chapter_filename"))
            from illustration_generator import chapter_directory
            if chapter_directory(filename) != chapter_code:
                raise ValueError("章节与插画目录不匹配")
            chapter = novel_manager.read_chapter(book_id, filename, BOOKS_DIR)
            if incoming.get("source_sha256") != hashlib.sha256(chapter.encode()).hexdigest():
                self.send_json({"error": "章节正文已更新，请重新生成素材"}, 409)
                return
            target_dir = within(bpath, "02_插图", chapter_code)
            old = novel_manager.get_chapter_extras(book_id, filename, BOOKS_DIR).get("meta") or {}
            if body.get("base_revision", "") != old.get("revision", ""):
                self.send_json({"error": "素材已被编辑或重新生成，请刷新后重试"}, 409)
                return
            note = incoming.get("author_note")
            comments = incoming.get("chapter_comments")
            if not isinstance(note, str) or len(note) > 20000 or not isinstance(comments, list) or len(comments) > 20:
                raise ValueError("生成文案格式无效")
            if any(not isinstance(comment, str) or len(comment) > 5000 for comment in comments):
                raise ValueError("生成章评格式无效")
            images = body.get("images", {})
            if not isinstance(images, dict):
                raise ValueError("图片数据格式无效")
            items = incoming.get("illustrations", [])
            if not isinstance(items, list) or len(items) > 4:
                raise ValueError("图片数量无效")
            expected = {}
            for item in items:
                expected[safe_component(item["filename"])] = False
                if item.get("thumbnail_filename"):
                    expected[safe_component(item["thumbnail_filename"])] = True
            if set(expected) != set(images):
                raise ValueError("图片文件与元数据不一致")
            decoded = {}
            from PIL import Image
            import io
            for image_name, thumbnail in expected.items():
                pattern = r"[0-9a-f]{32}_[1-4]\.thumb\.jpg" if thumbnail else r"[0-9a-f]{32}_[1-4]\.png"
                if not re.fullmatch(pattern, image_name):
                    raise ValueError("图片文件名必须包含本次生成编号")
                raw = base64.b64decode(images[image_name], validate=True)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError("图片大小无效")
                try:
                    with Image.open(io.BytesIO(raw)) as image:
                        width, height = image.size
                        image_format = image.format
                        image.verify()
                except Exception as error:
                    raise ValueError("图片格式无效") from error
                if image_format != ("JPEG" if thumbnail else "PNG"):
                    raise ValueError("图片编码与文件名不匹配")
                minimum_width, minimum_height = (256, 144) if thumbnail else (800, 450)
                if width < minimum_width or height < minimum_height or width * height > 24000000:
                    raise ValueError("图片尺寸无效")
                decoded[image_name] = raw
            for image_name, raw in decoded.items():
                image_path = within(target_dir, image_name)
                if os.path.exists(image_path):
                    with open(image_path, "rb") as stream:
                        if stream.read() != raw:
                            raise ValueError("图片文件名冲突，请重新生成")
                atomic_write(image_path, raw)
            meta = dict(incoming)
            if body.get("notes_only"):
                meta["illustrations"] = old.get("illustrations", old.get("images", []))
                meta["style"] = old.get("style", "legacy")
                meta["count"] = len(meta["illustrations"])
            for key in ("author_note", "chapter_comments"):
                if key not in meta:
                    raise ValueError("生成结果缺少互动文案")
            atomic_write(os.path.join(target_dir, "作者有话说.md"), meta["author_note"])
            atomic_write(os.path.join(target_dir, "章节评论.md"), "\n\n".join(meta["chapter_comments"]))
            save_json_file(os.path.join(target_dir, "meta.json"), meta)
            self.send_json({"ok": True, "saved_images": len(items), "saved_files": len(decoded), "revision": meta["revision"]})
            return

        # 番茄发布工作台：一键自动修复错别字与标点规范
        if path in ["/api/tomato_autofix", "/novel/api/tomato_autofix"]:
            if not self.check_auth():
                self.send_json({"error": "Unauthorized"}, 401)
                return
            book_id = body.get("book", "给政哥讲量子力学，他竟肉身硬抗二向箔")
            fname = body.get("file", "")
            content = body.get("content", "")

            if not content and fname:
                bpath, _ = novel_manager.get_book_path(book_id, BOOKS_DIR)
                candidates = [p for p in novel_manager.chapter_files(bpath) if os.path.basename(p) == safe_component(fname)]
                if candidates and os.path.exists(candidates[0]):
                    with open(candidates[0], "r", encoding="utf-8", errors="ignore") as fp:
                        content = fp.read()

            cleaned = novel_manager.clean_for_tomato(content, fname)
            fix_res = novel_manager.auto_fix_tomato_content(cleaned["content"])
            fixed_content = fix_res["fixed_content"]
            
            new_cleaned = {
                "title": cleaned["title"],
                "content": fixed_content,
                "char_count": len(fixed_content) - fixed_content.count("\u3000") - fixed_content.count("\n"),
                "line_count": len(fixed_content.splitlines())
            }
            new_audit = novel_manager.audit_for_tomato(new_cleaned)

            self.send_json({
                "ok": True,
                "replaced_count": fix_res["replaced_count"],
                "title": new_cleaned["title"],
                "content": new_cleaned["content"],
                "char_count": new_cleaned["char_count"],
                "line_count": new_cleaned["line_count"],
                "audit": new_audit
            })
            return

        # 手机端下发创作指令
        if path in ["/api/dispatch_task", "/novel/api/dispatch_task", "/api/task", "/novel/api/task"]:
            if not self.check_auth():
                self.send_json({"error": "Unauthorized"}, 401)
                return
            action = body.get("action", "creative_instruction")
            prompt = body.get("prompt", "")
            book_id = body.get("book_id") or body.get("book") or "给政哥讲量子力学，他竟肉身硬抗二向箔"
            engine_mode = canonical_engine(body.get("engine_mode", "antigravity"))
            sub_action = body.get("sub_action", "")
            extra_data = body.get("extra_data") or {}

            # 智能平铺兼容：无论前端是嵌套在 extra_data 里，还是直接平铺在 body 顶层，全部自动合并到 extra_data
            for k in ["chapter_file", "target_chapter", "image_count", "custom_prompt", "repair_mode", "rejection_reason", "audit_issues", "model_tier", "reasoning_effort", "instructions", "chapter_count"]:
                if k in body and k not in extra_data:
                    extra_data[k] = body[k]

            count = novel_manager.validate_chapter_count(extra_data.get("chapter_count", 1))
            if count > 1 and (action != "creative_instruction" or extra_data.get("workflow") != "serial"):
                raise ValueError("多章续写仅适用于自动续写模式")
            extra_data["chapter_count"] = count
            catalog = load_json_file(os.path.join(DATA_DIR, "agents.json"), {"engines": []})
            agent = next((entry for entry in catalog["engines"] if entry["id"] == engine_mode), {})
            if action == "wake_antigravity":
                model, effort = {"id": None}, None
            else:
                model, effort = selected_model(agent, extra_data.get("model_tier"), extra_data.get("reasoning_effort"))
            extra_data["model_tier"] = model.get("effort_models", {}).get(effort, model["id"])
            extra_data["reasoning_effort"] = effort
            extra_data["permission_mode"] = "full-access"
            if action == "generate_chapter_illustrations" and not agent.get("capabilities", {}).get("images"):
                raise ValueError("当前客户端未开放图像生成工具，可生成章节互动文案")

            # 别名字段规范化
            if "target_chapter" in extra_data and "chapter_file" not in extra_data:
                extra_data["chapter_file"] = extra_data["target_chapter"]

            safe_component(book_id)
            if action != "create_new_novel":
                novel_manager.get_book_path(book_id, BOOKS_DIR)
            elif os.path.exists(within(BOOKS_DIR, book_id)):
                raise ValueError("同名作品已存在，请使用其他书名")
            chapter_file = extra_data.get("chapter_file")
            if chapter_file:
                safe_component(chapter_file)
            if action in ("generate_chapter_illustrations", "generate_chapter_notes"):
                if not chapter_file:
                    raise ValueError("请先选择章节")
                if not 1 <= int(extra_data.get("image_count", 4)) <= 4:
                    raise ValueError("图片数量必须为 1 到 4")
                for existing in TASKS.values():
                    if (existing["status"] in ("pending", "assigned", "running")
                            and existing.get("book_id") == book_id and existing.get("action") == action
                            and canonical_engine(existing.get("engine_mode", "antigravity")) == engine_mode
                            and existing.get("extra_data", {}).get("model_tier") == extra_data["model_tier"]
                            and existing.get("extra_data", {}).get("reasoning_effort") == effort
                            and existing.get("extra_data", {}).get("chapter_file") == chapter_file):
                        self.send_json({"ok": True, "task_id": existing["id"], "task": existing})
                        return
            request_id = body.get("request_id")
            if request_id is not None and (not isinstance(request_id, str) or not request_id or len(request_id) > 128):
                raise ValueError("请求编号无效")
            def same_request(existing):
                return (existing.get('action') == action and existing.get('book_id') == book_id
                    and existing.get('engine_mode') == engine_mode and existing.get('prompt', '') == prompt
                    and existing.get('sub_action', '') == sub_action and existing.get('extra_data') == extra_data)
            for existing in TASKS.values():
                same_id = request_id and existing.get('request_id') == request_id
                active_serial = (action == 'creative_instruction' and extra_data.get('workflow') == 'serial'
                    and existing.get('status') in ('pending', 'assigned', 'running') and same_request(existing))
                if same_id and not same_request(existing):
                    raise ValueError("该请求编号已用于其他任务，请使用新编号")
                if same_id or active_serial:
                    self.send_json({"ok": True, "task_id": existing['id'], "task": existing, "deduplicated": True})
                    return
            tid = f"task_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            task = {
                "id": tid,
                "request_id": request_id,
                "action": action,
                "sub_action": sub_action,
                "prompt": prompt,
                "book_id": book_id,
                "engine_mode": engine_mode,
                "workspace": "D:\\小说",
                "project_id": agent.get("project_id"),
                "permission_mode": "full-access",
                "extra_data": extra_data,
                "status": "pending",
                "progress_log": [f"[{time.strftime('%H:%M:%S')}] 任务已建立，等待公司电脑接收..."],
                "created_at": time.time(),
                "completed_at": None,
                "result": None
            }
            TASKS[tid] = task
            save_json_file(os.path.join(DATA_DIR, "tasks.json"), TASKS)
            if engine_mode == "deepseek":
                task["progress_log"] = [f"[{time.strftime('%H:%M:%S')}] 任务已建立，DeepSeek 官方 API 直连创作引擎正在启动..."]
                threading.Thread(target=execute_deepseek_task_async, args=(tid,), daemon=True).start()
            self.send_json({"ok": True, "task_id": tid, "task": task})
            return

        # 本地桥接器回报任务进度与流式结果
        if path in ["/api/task_progress", "/novel/api/task_progress"]:
            LOCAL_STATUS["online"] = True
            LOCAL_STATUS["last_seen"] = time.time()
            tid = body.get("id")
            if tid and tid in TASKS:
                t = TASKS[tid]
                new_status = body.get("status", t["status"])
                if new_status not in ("running", "completed", "failed"):
                    raise ValueError("任务状态无效")
                if t["status"] in ("completed", "failed"):
                    self.send_json({"ok": True, "terminal": True})
                    return
                t["status"] = new_status
                t["heartbeat_at"] = time.time()
                log_entry = body.get("log")
                if log_entry:
                    t["progress_log"].append(f"[{time.strftime('%H:%M:%S')}] {str(log_entry)[:4000]}")
                    t["progress_log"] = t["progress_log"][-200:]
                if t["status"] in ("completed", "failed"):
                    t["completed_at"] = time.time()
                    t["result"] = body.get("result")
                    # 失效缓存以更新最新生成的章节与设定
                    invalidate_cache()
                save_json_file(os.path.join(DATA_DIR, "tasks.json"), TASKS)
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "Task not found"}, 404)
            return

        self.send_json({"error": "Not Found", "path": path}, 404)

if __name__ == "__main__":
    # 加载任务历史
    tasks_file = os.path.join(DATA_DIR, "tasks.json")
    if os.path.exists(tasks_file):
        with open(tasks_file, encoding="utf-8") as stream:
            TASKS = json.load(stream)
    for task in TASKS.values():
        if task["status"] == "running" and "heartbeat_at" not in task:
            task.update(status="failed", completed_at=time.time())
            task.setdefault("progress_log", []).append("旧版本任务缺少完成回执，已结束异常状态。请检查已有成果后重试。")
        elif task["status"] == "running":
            task["heartbeat_at"] = time.time()
    save_json_file(tasks_file, TASKS)
    invalidate_cache()
    port = 8899
    server = BoundedHTTPServer((os.environ.get("NOVEL_HUB_BIND", "127.0.0.1"), int(os.environ.get("NOVEL_HUB_PORT", port))), HubHandler)
    print(f"Novel Hub 3.5 Server (Redis Cache & Fast Tomato Engine) running on 0.0.0.0:{port}...")
    server.serve_forever()
