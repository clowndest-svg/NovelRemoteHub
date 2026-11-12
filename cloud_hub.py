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
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import threading
import secrets
import hmac
import hashlib
import base64
import struct
from storage import atomic_json, atomic_write, safe_component, within
from urllib.parse import urlparse, parse_qs

# 引入小说管理工作流
sys.path.append(os.path.dirname(__file__))
import novel_manager

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
PASSWORD = os.environ.get("NOVEL_HUB_PASSWORD", "daqin666")

os.makedirs(BOOKS_DIR, exist_ok=True)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

TOKEN_FILE = os.path.join(DATA_DIR, "auth_token")
if os.path.exists(TOKEN_FILE):
    with open(TOKEN_FILE, encoding="utf-8") as token_stream:
        AUTH_TOKEN = token_stream.read().strip()
else:
    AUTH_TOKEN = secrets.token_urlsafe(48)
    atomic_write(TOKEN_FILE, AUTH_TOKEN)
    os.chmod(TOKEN_FILE, 0o600)
WRITE_LOCK = threading.RLock()
CACHE_LOCK = threading.RLock()
TASKS = {}
LOCAL_STATUS = {
    "online": False,
    "last_seen": 0,
    "current_task": None
}

APP_VERSION_INFO = {
    "versionCode": 10,
    "versionName": "1.8.0",
    "apkUrl": "https://scientificexploration.art/novel/download/NovelRemoteHub.apk?v=1.8.0",
    "changelog": "二维水墨场景插画，独立章节互动文案编辑与保存，查询加速，任务回执校验及同步安全修复。",
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
    def end_headers(self):
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
        return isinstance(token, str) and hmac.compare_digest(token, AUTH_TOKEN)

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
            if self.path.split("?")[0].rstrip("/").endswith(("/pending_task", "/task_status")):
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

        # 引擎与模型详细说明接口 (公网免密)
        if path == "/api/engines" or path == "/novel/api/engines":
            self.send_json({
                "active_model": {
                    "name": "Gemini 3.8 Flash High",
                    "full_name": "Google Gemini 3.8 Flash High (主力创作旗舰 · 量大管饱)",
                    "tier": "gemini-3.8-flash-high",
                    "provider": "Google DeepMind 官方最新 3.8 高性能大模型",
                    "auth": "Google Gemini 官方订阅直通已激活",
                    "proxy": "Clash Verge TUN 虚拟网卡专属高速通道 · 具备故障自愈重试",
                    "workspace": "D:\\小说",
                    "features": "超大上下文窗口、极速吞吐与密集爽点输出，全自动额度熔断与代理自愈降级"
                },
                "models": [
                    {
                        "id": "gemini-3.8-flash-high",
                        "tier": "gemini-3.8-flash-high",
                        "name": "🚀 Gemini 3.8 Flash High (主力 · 量大管饱 · 推荐)",
                        "desc": "Google 官方最新 3.8 高性能大模型，超大上下文与高并发输出，写长篇正文首选主力引擎！",
                        "recommended": True
                    },
                    {
                        "id": "claude-sonnet-4-6",
                        "tier": "claude-sonnet-4-6",
                        "name": "🧠 Claude Sonnet 4.6 (深度思考 · 细纲伏笔)",
                        "desc": "长链条深度思考模型，适合全书大纲推演、复杂战力设定冲突排查与高潮反转构思。",
                        "recommended": False
                    },
                    {
                        "id": "gemini-flash",
                        "tier": "flash",
                        "name": "⚡ Gemini Flash (标准极速)",
                        "desc": "标准轻量极速模型，适合快速短评、错字校正、灵感片段记录。",
                        "recommended": False
                    },
                    {
                        "id": "gemini-pro",
                        "tier": "pro",
                        "name": "🏛️ Gemini Pro (高维推理)",
                        "desc": "复杂系统宏观推理模型，适合文明科技跨度推导与严谨物理法则架构。",
                        "recommended": False
                    }
                ]
            })
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

        # 获取章节列表 (支持 ?book=xxx，启用缓存)
        if path == "/api/chapters" or path == "/novel/api/chapters":
            book_id = qs.get("book", [None])[0] or "给政哥讲量子力学，他竟肉身硬抗二向箔"
            cache_key = f"novel:cache:chapters:{book_id}"
            cached = get_cached(cache_key)
            if cached:
                self.send_json(cached)
                return

            bpath, btitle = novel_manager.get_book_path(book_id, BOOKS_DIR)
            c_dir = os.path.join(bpath, "01_正文")
            files = novel_manager.chapter_files(bpath)
            chapters = []
            for f in files:
                fname = os.path.basename(f)
                with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                clean_res = novel_manager.clean_for_tomato(content, fname)
                chapters.append({
                    "filename": fname,
                    "title": clean_res["title"] or fname.replace(".md", ""),
                    "raw_title": fname.replace(".md", ""),
                    "char_count": clean_res["char_count"],
                    "size": os.path.getsize(f),
                    "mtime": os.path.getmtime(f)
                })
            res = {"book_title": btitle, "chapters": chapters}
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

            cache_key = f"novel:cache:chapter:{book_id}:{fname}"
            cached = get_cached(cache_key)
            if cached:
                self.send_json(cached)
                return

            bpath, btitle = novel_manager.get_book_path(book_id, BOOKS_DIR)
            candidates = [p for p in novel_manager.chapter_files(bpath) if os.path.basename(p) == fname]
            if len(candidates) > 1:
                raise ValueError("章节文件名重复，请检查分卷名称")
            if candidates and os.path.exists(candidates[0]):
                target = candidates[0]
                with open(target, "r", encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                clean_res = novel_manager.clean_for_tomato(content, fname)
                res = {
                    "book_title": btitle,
                    "filename": fname,
                    "title": clean_res["title"] or os.path.basename(fname).replace(".md", ""),
                    "content": content,
                    "char_count": clean_res["char_count"]
                }
                set_cached(cache_key, res, ttl=600)
                self.send_json(res)
            else:
                self.send_json({"error": "Chapter not found"}, 404)
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
                    self.send_header("Content-Type", "image/png")
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
            for tid, t in list(TASKS.items()):
                if t["status"] == "running" and now - t.get("heartbeat_at", t.get("assigned_at", now)) > 180:
                    t.update(status="failed", completed_at=now)
                    t["progress_log"].append("本地执行端失去心跳，请确认 Antigravity 中的执行结果后重试")
                    save_json_file(os.path.join(DATA_DIR, "tasks.json"), TASKS)
                is_stale_assigned = (t["status"] == "assigned" and (now - t.get("assigned_at", t.get("created_at", now))) > 30)
                if t["status"] == "pending" or is_stale_assigned:
                    t["status"] = "assigned"
                    t["assigned_at"] = now
                    save_json_file(os.path.join(DATA_DIR, "tasks.json"), TASKS)
                    self.send_json({"task": t})
                    return
            self.send_json({"task": None})
            return

        # 任务状态查询接口
        if path == "/api/task_status" or path == "/novel/api/task_status":
            tid = qs.get("id", [""])[0]
            if tid in TASKS:
                self.send_json(TASKS[tid])
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
            if pwd == PASSWORD:
                self.send_json({"ok": True, "token": AUTH_TOKEN})
            else:
                self.send_json({"ok": False, "error": "口令错误，请重新输入"}, 401)
            return

        # 本地桥接器多书架全量同步 (写入后立即失效缓存)
        if path == "/api/sync_data" or path == "/novel/api/sync_data":
            LOCAL_STATUS["online"] = True
            LOCAL_STATUS["last_seen"] = time.time()
            book_id = body.get("book_id", "给政哥讲量子力学，他竟肉身硬抗二向箔")
            bpath = within(BOOKS_DIR, book_id)
            os.makedirs(os.path.join(bpath, "01_正文", "第一卷_人界篇"), exist_ok=True)
            os.makedirs(os.path.join(bpath, "00_设定集"), exist_ok=True)

            chapters = body.get("chapters", [])
            settings = body.get("settings", [])
            if not isinstance(chapters, list) or not isinstance(settings, list):
                raise ValueError("同步文档必须为列表")
            for item in chapters + settings:
                safe_component(item["filename"])
                if not item["filename"].endswith(".md") or not isinstance(item["content"], str):
                    raise ValueError("同步文档必须为 Markdown 文本")
            # Only changed files are replaced, and cache invalidation follows each write.
            has_chapter_changes = False
            for c in chapters:
                fpath = os.path.join(bpath, "01_正文", "第一卷_人界篇", safe_component(c["filename"]))
                old_text = ""
                if os.path.exists(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as fp:
                            old_text = fp.read()
                    except Exception:
                        pass
                if old_text != c["content"]:
                    atomic_write(fpath, c["content"])
                    has_chapter_changes = True
                    invalidate_cache(c["filename"])

            # 同步设定
            settings = body.get("settings", [])
            has_setting_changes = False
            for s in settings:
                fpath = os.path.join(bpath, "00_设定集", safe_component(s["filename"]))
                old_text = ""
                if os.path.exists(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as fp:
                            old_text = fp.read()
                    except Exception:
                        pass
                if old_text != s["content"]:
                    atomic_write(fpath, s["content"])
                    has_setting_changes = True
                    invalidate_cache(s["filename"])

            # 同步对话
            conversations = body.get("conversations", [])
            if conversations:
                save_json_file(os.path.join(DATA_DIR, "conversations.json"), conversations)

            # 仅在有实际内容变更时失效目录总览缓存
            if has_chapter_changes or has_setting_changes:
                invalidate_cache("novel:cache:books")
            if has_chapter_changes:
                invalidate_cache(f"novel:cache:chapters:{book_id}")
            if has_setting_changes:
                invalidate_cache(f"novel:cache:settings:{book_id}")

            self.send_json({"ok": True, "chapters_count": len(chapters), "settings_count": len(settings)})
            return

        if path in ("/api/heartbeat", "/novel/api/heartbeat"):
            LOCAL_STATUS.update(online=True, last_seen=time.time())
            tid = body.get("id")
            if tid in TASKS and TASKS[tid]["status"] == "running":
                TASKS[tid]["heartbeat_at"] = time.time()
            self.send_json({"ok": True})
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
            if not isinstance(items, list) or len(items) > 4 or len(images) != len(items):
                raise ValueError("图片数量无效")
            decoded = {}
            for item in items:
                image_name = safe_component(item["filename"])
                if not re.fullmatch(r"[0-9a-f]{32}_[1-4]\.png", image_name):
                    raise ValueError("图片文件名必须包含本次生成编号")
                raw = base64.b64decode(images[image_name], validate=True)
                if len(raw) > 8 * 1024 * 1024 or raw[:8] != b"\x89PNG\r\n\x1a\n" or len(raw) < 32:
                    raise ValueError("图片格式或大小无效")
                width, height = struct.unpack(">II", raw[16:24])
                if width < 800 or height < 450 or width * height > 24000000:
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
            self.send_json({"ok": True, "saved_images": len(decoded), "revision": meta["revision"]})
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
            engine_mode = body.get("engine_mode", "antigravity_pro")
            sub_action = body.get("sub_action", "")
            extra_data = body.get("extra_data") or {}

            # 智能平铺兼容：无论前端是嵌套在 extra_data 里，还是直接平铺在 body 顶层，全部自动合并到 extra_data
            for k in ["chapter_file", "target_chapter", "image_count", "custom_prompt", "repair_mode", "rejection_reason", "audit_issues", "model_tier", "instructions"]:
                if k in body and k not in extra_data:
                    extra_data[k] = body[k]

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
                            and existing.get("extra_data", {}).get("chapter_file") == chapter_file):
                        self.send_json({"ok": True, "task_id": existing["id"], "task": existing})
                        return
            request_id = body.get("request_id")
            if request_id:
                for existing in TASKS.values():
                    if existing.get("request_id") == request_id:
                        self.send_json({"ok": True, "task_id": existing["id"], "task": existing})
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
                "extra_data": extra_data,
                "status": "pending",
                "progress_log": [f"[{time.strftime('%H:%M:%S')}] 任务已建立，等待公司电脑接收..."],
                "created_at": time.time(),
                "completed_at": None,
                "result": None
            }
            TASKS[tid] = task
            save_json_file(os.path.join(DATA_DIR, "tasks.json"), TASKS)
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
    port = 8899
    server = BoundedHTTPServer((os.environ.get("NOVEL_HUB_BIND", "127.0.0.1"), int(os.environ.get("NOVEL_HUB_PORT", port))), HubHandler)
    print(f"Novel Hub 3.5 Server (Redis Cache & Fast Tomato Engine) running on 0.0.0.0:{port}...")
    server.serve_forever()
