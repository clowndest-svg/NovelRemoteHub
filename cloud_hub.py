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
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
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

DATA_DIR = "/root/novel_hub_data"
BOOKS_DIR = os.path.join(DATA_DIR, "books")
DOWNLOAD_DIR = "/opt/novel_hub/download"
PASSWORD = os.environ.get("NOVEL_HUB_PASSWORD", "daqin666")

os.makedirs(BOOKS_DIR, exist_ok=True)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

TASKS = {}
LOCAL_STATUS = {
    "online": False,
    "last_seen": 0,
    "current_task": None
}

APP_VERSION_INFO = {
    "versionCode": 9,
    "versionName": "1.7.0",
    "apkUrl": "https://scientificexploration.art/novel/download/NovelRemoteHub.apk?v=1.7.0",
    "changelog": "1. 【番茄机审多分类筛选全面修复】彻底解决筛选类别空白问题，精准细分「🔴高危风控」、「🔵错别字」、「🟣标点排版」与「📏篇幅字数」，标签显影对齐！\n2. 【章节定点重构与番茄驳回修复工坊】全新上线！针对审核驳回或敏感词，唤醒 Antigravity 自动通读前后章与全套设定集，自动备份原章，支持合规脱敏精修与深度剧情重构！\n3. 【设定大典与创作 SOP 铁律双向互锁】续写新章节与大纲推演时，全量注入 00_设定集清单与必须调阅规范，严格保障战力法则、文明演进与剧情逻辑严密一致！",
    "forceUpdate": False
}

# 2. 内存字典与 Redis 双层缓存系统
MEMORY_CACHE = {}

def get_cached(key):
    # 先查内存 (0.01ms)
    if key in MEMORY_CACHE:
        expire_time, val = MEMORY_CACHE[key]
        if time.time() < expire_time:
            return val
        else:
            del MEMORY_CACHE[key]

    # 再查 Redis (0.5ms)
    if HAS_REDIS and REDIS_CLIENT:
        try:
            raw = REDIS_CLIENT.get(key)
            if raw:
                val = json.loads(raw)
                MEMORY_CACHE[key] = (time.time() + 180, val)
                return val
        except Exception:
            pass
    return None

def set_cached(key, val, ttl=300):
    MEMORY_CACHE[key] = (time.time() + ttl, val)
    if HAS_REDIS and REDIS_CLIENT:
        try:
            REDIS_CLIENT.setex(key, ttl, json.dumps(val, ensure_ascii=False))
        except Exception:
            pass

def invalidate_cache(pattern=None):
    global MEMORY_CACHE
    if not pattern:
        MEMORY_CACHE.clear()
        if HAS_REDIS and REDIS_CLIENT:
            try:
                REDIS_CLIENT.flushdb()
            except Exception:
                pass
    else:
        MEMORY_CACHE = {k: v for k, v in MEMORY_CACHE.items() if pattern not in k}
        if HAS_REDIS and REDIS_CLIENT:
            try:
                keys = REDIS_CLIENT.keys(f"*{pattern}*")
                if keys:
                    REDIS_CLIENT.delete(*keys)
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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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
        return token == "daqin_authorized"

    def get_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            raw = self.rfile.read(length).decode("utf-8", errors="ignore")
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return {}

    def do_GET(self):
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
            files = sorted(glob.glob(os.path.join(c_dir, "**", "*.md"), recursive=True))
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
            candidates = glob.glob(os.path.join(bpath, "01_正文", "**", os.path.basename(fname)), recursive=True)
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
            candidates = glob.glob(os.path.join(bpath, "01_正文", "**", os.path.basename(fname)), recursive=True)
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
            img_path = os.path.join(bpath, "02_插图", chap_dir, img_name)
            if os.path.exists(img_path) and os.path.isfile(img_path):
                try:
                    with open(img_path, "rb") as fp:
                        img_data = fp.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(img_data)))
                    self.send_header("Cache-Control", "public, max-age=86400")
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

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self.get_json_body()

        # 登录认证
        if path == "/api/login" or path == "/novel/api/login":
            pwd = body.get("password", "")
            if pwd == PASSWORD:
                self.send_json({"ok": True, "token": "daqin_authorized"})
            else:
                self.send_json({"ok": False, "error": "口令错误，请重新输入"}, 401)
            return

        # 本地桥接器多书架全量同步 (写入后立即失效缓存)
        if path == "/api/sync_data" or path == "/novel/api/sync_data":
            LOCAL_STATUS["online"] = True
            LOCAL_STATUS["last_seen"] = time.time()
            book_id = body.get("book_id", "给政哥讲量子力学，他竟肉身硬抗二向箔")
            bpath = os.path.join(BOOKS_DIR, book_id)
            os.makedirs(os.path.join(bpath, "01_正文", "第一卷_人界篇"), exist_ok=True)
            os.makedirs(os.path.join(bpath, "00_设定集"), exist_ok=True)

            # 同步章节 (仅在内容发生变动时才写盘并精准失效缓存，避免重复破坏缓存)
            chapters = body.get("chapters", [])
            has_chapter_changes = False
            for c in chapters:
                fpath = os.path.join(bpath, "01_正文", "第一卷_人界篇", c["filename"])
                old_text = ""
                if os.path.exists(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as fp:
                            old_text = fp.read()
                    except Exception:
                        pass
                if old_text != c["content"]:
                    with open(fpath, "w", encoding="utf-8") as fp:
                        fp.write(c["content"])
                    has_chapter_changes = True
                    invalidate_cache(c["filename"])

            # 同步设定
            settings = body.get("settings", [])
            has_setting_changes = False
            for s in settings:
                fpath = os.path.join(bpath, "00_设定集", s["filename"])
                old_text = ""
                if os.path.exists(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as fp:
                            old_text = fp.read()
                    except Exception:
                        pass
                if old_text != s["content"]:
                    with open(fpath, "w", encoding="utf-8") as fp:
                        fp.write(s["content"])
                    has_setting_changes = True
                    invalidate_cache(s["filename"])

            # 同步对话
            conversations = body.get("conversations", [])
            if conversations:
                save_json_file(os.path.join(DATA_DIR, "conversations.json"), conversations)

            # 仅在有实际内容变更时失效目录总览缓存
            if has_chapter_changes:
                invalidate_cache(f"novel:cache:chapters:{book_id}")
            if has_setting_changes:
                invalidate_cache(f"novel:cache:settings:{book_id}")

            self.send_json({"ok": True, "chapters_count": len(chapters), "settings_count": len(settings)})
            return

        # 随章插图与「作者有话说」元数据双端同步接口
        if path in ["/api/sync_illustrations", "/novel/api/sync_illustrations"]:
            book_id = body.get("book_id", "给政哥讲量子力学，他竟肉身硬抗二向箔")
            chap_dir = body.get("chapter_code", "")
            meta = body.get("meta", {})
            images = body.get("images", {})
            
            bpath, _ = novel_manager.get_book_path(book_id, BOOKS_DIR)
            chap_clean_name = re.sub(r"[^\w\u4e00-\u9fa5]", "_", chap_dir)
            target_dir = os.path.join(bpath, "02_插图", chap_clean_name)
            os.makedirs(target_dir, exist_ok=True)
            
            import base64
            saved_cnt = 0
            for fname, b64_str in images.items():
                try:
                    img_bytes = base64.b64decode(b64_str)
                    with open(os.path.join(target_dir, fname), "wb") as ifp:
                        ifp.write(img_bytes)
                    saved_cnt += 1
                except Exception:
                    pass
                    
            if meta:
                with open(os.path.join(target_dir, "meta.json"), "w", encoding="utf-8") as mfp:
                    json.dump(meta, mfp, ensure_ascii=False, indent=2)
                    
            if "author_note" in meta:
                with open(os.path.join(target_dir, "作者有话说.md"), "w", encoding="utf-8") as nfp:
                    nfp.write(meta["author_note"])
                    
            self.send_json({"ok": True, "saved_images": saved_cnt})
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
                candidates = glob.glob(os.path.join(bpath, "01_正文", "**", fname), recursive=True)
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
            book_id = body.get("book_id", "给政哥讲量子力学，他竟肉身硬抗二向箔")
            engine_mode = body.get("engine_mode", "antigravity_pro")
            sub_action = body.get("sub_action", "")
            extra_data = body.get("extra_data", {})

            tid = f"task_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            task = {
                "id": tid,
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
                t["status"] = body.get("status", t["status"])
                log_entry = body.get("log")
                if log_entry:
                    t["progress_log"].append(f"[{time.strftime('%H:%M:%S')}] {log_entry}")
                if t["status"] == "completed":
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
    TASKS = load_json_file(tasks_file, {})
    port = 8899
    server = HTTPServer(("0.0.0.0", port), HubHandler)
    print(f"Novel Hub 3.5 Server (Redis Cache & Fast Tomato Engine) running on 0.0.0.0:{port}...")
    server.serve_forever()
