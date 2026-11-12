# -*- coding: utf-8 -*-
r"""
大秦科技修仙·远程云端中枢 (Cloud Hub 2.0)
部署在云服务器 124.220.54.64 (127.0.0.1:8899)
Nginx /novel/ 反向代理直达
内置多书架、番茄小说发布工作流、Redis 缓存与双核引擎调度
"""
import os
import sys
import json
import time
import uuid
import glob
import shutil
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# 尝试引入 Redis
try:
    import redis
    REDIS_CLIENT = redis.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True, socket_timeout=2)
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

# 引入小说管理工作流
sys.path.append(os.path.dirname(__file__))
import novel_manager

TASKS = {}
LOCAL_STATUS = {
    "online": False,
    "last_seen": 0,
    "current_task": None
}

APP_VERSION_INFO = {
    "versionCode": 4,
    "versionName": "1.3.0",
    "apkUrl": "https://scientificexploration.art/novel/download/NovelRemoteHub.apk",
    "changelog": "1. 【全面升级】所有接口、服务与下载链接全量接入官方域名 scientificexploration.art！\n2. 【模型透明化】实时显影底层 AI 模型（Gemini 2.0 Pro / Flash）与会员算力状态！\n3. 【AI指挥中心】重构4大专业网文创作模式：自动续写、单章精修、大纲推演与自由定制！\n4. 【架构增强】强化功能间联动，多书架切换数据严密一致！",
    "forceUpdate": False
}

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
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html_text):
        body = html_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def check_auth(self):
        token = self.headers.get("X-Novel-Token")
        if not token:
            auth_header = self.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()
        return token == "daqin_authorized"

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = parse_qs(parsed.query)

        # 根路径访问前端单页
        if path == "" or path == "/novel" or path == "/novel/index.html":
            index_path = os.path.join(os.path.dirname(__file__), "index.html")
            if os.path.exists(index_path):
                with open(index_path, "r", encoding="utf-8") as f:
                    self.send_html(f.read())
            else:
                self.send_html("<h1>大秦科技修仙远程中枢 2.0 启动中...</h1>")
            return

        # APK 下载路由 (公网免密)
        if (path.endswith("/download/NovelRemoteHub.apk") or path == "/download/NovelRemoteHub.apk" or
            path.endswith("/download/DaqinNovelHub.apk") or path == "/download/DaqinNovelHub.apk"):
            apk_filename = "NovelRemoteHub.apk" if "NovelRemoteHub" in path else "DaqinNovelHub.apk"
            apk_path = os.path.join(DOWNLOAD_DIR, apk_filename)
            if not os.path.exists(apk_path):
                cand = [f for f in [os.path.join(DOWNLOAD_DIR, "NovelRemoteHub.apk"), os.path.join(DOWNLOAD_DIR, "DaqinNovelHub.apk")] if os.path.exists(f)]
                if cand:
                    apk_path = cand[0]
            if os.path.exists(apk_path):
                size = os.path.getsize(apk_path)
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(apk_path)}"')
                self.send_header("Content-Length", str(size))
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
                    "name": "Gemini 2.0 Pro",
                    "full_name": "Google Gemini 2.0 Pro (深度推理旗舰模型)",
                    "tier": "pro",
                    "provider": "Google DeepMind 官方顶级大模型",
                    "auth": "Google Gemini Pro 会员订阅直通已激活",
                    "proxy": "Clash Verge 全局 TUN 虚拟网卡专属高速直连",
                    "workspace": "D:\\小说",
                    "features": "支持超长上下文记忆链、分章细纲精准推演、复杂网文世界观逻辑闭环与正文创作"
                },
                "models": [
                    {
                        "id": "gemini_2_0_pro",
                        "tier": "pro",
                        "name": "🌟 Gemini 2.0 Pro (深度思考 · 推荐)",
                        "desc": "Google 官方旗舰深度推理模型，适合长篇正文创作、大纲总谱推演、设定集构建，具备高严密逻辑与反转能力。",
                        "recommended": True
                    },
                    {
                        "id": "gemini_2_0_flash",
                        "tier": "flash",
                        "name": "⚡ Gemini 2.0 Flash (极速响应)",
                        "desc": "毫秒级响应，适合临时剧情灵感脑洞发散、单段落语法润色、错别字校对。",
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

        # 获取所有书籍 (多书架)
        if path == "/api/books" or path == "/novel/api/books":
            books = novel_manager.list_books(BOOKS_DIR)
            self.send_json({"books": books})
            return

        # 获取章节列表 (支持 ?book=xxx)
        if path == "/api/chapters" or path == "/novel/api/chapters":
            book_id = qs.get("book", [None])[0]
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
            self.send_json({"book_title": btitle, "chapters": chapters})
            return

        # 获取单章内容 (支持 ?book=xxx&file=xxx)
        if path == "/api/chapter" or path == "/novel/api/chapter":
            book_id = qs.get("book", [None])[0]
            fname = qs.get("file", [""])[0]
            if not fname:
                self.send_json({"error": "Missing file param"}, 400)
                return
            bpath, btitle = novel_manager.get_book_path(book_id, BOOKS_DIR)
            # 搜索文件
            candidates = glob.glob(os.path.join(bpath, "01_正文", "**", os.path.basename(fname)), recursive=True)
            if candidates and os.path.exists(candidates[0]):
                target = candidates[0]
                with open(target, "r", encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                clean_res = novel_manager.clean_for_tomato(content, fname)
                self.send_json({
                    "book_title": btitle,
                    "filename": fname,
                    "title": clean_res["title"] or os.path.basename(fname).replace(".md", ""),
                    "content": content,
                    "char_count": clean_res["char_count"]
                })
            else:
                self.send_json({"error": "Chapter not found"}, 404)
            return

        # 番茄小说清洗与发布质检接口
        if path == "/api/tomato_clean" or path == "/novel/api/tomato_clean":
            book_id = qs.get("book", [None])[0]
            fname = qs.get("file", [""])[0]
            bpath, btitle = novel_manager.get_book_path(book_id, BOOKS_DIR)
            candidates = glob.glob(os.path.join(bpath, "01_正文", "**", os.path.basename(fname)), recursive=True)
            if candidates and os.path.exists(candidates[0]):
                with open(candidates[0], "r", encoding="utf-8", errors="ignore") as fp:
                    raw_text = fp.read()
                cleaned = novel_manager.clean_for_tomato(raw_text, fname)
                audit = novel_manager.audit_for_tomato(cleaned)
                self.send_json({
                    "ok": True,
                    "book_title": btitle,
                    "title": cleaned["title"],
                    "content": cleaned["content"],
                    "char_count": cleaned["char_count"],
                    "line_count": cleaned["line_count"],
                    "audit": audit
                })
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

        # 获取设定集列表
        if path == "/api/settings" or path == "/novel/api/settings":
            book_id = qs.get("book", [None])[0]
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
            self.send_json({"book_title": btitle, "settings": settings})
            return

        # 获取单项设定内容
        if path == "/api/setting" or path == "/novel/api/setting":
            book_id = qs.get("book", [None])[0]
            fname = qs.get("file", [""])[0]
            bpath, btitle = novel_manager.get_book_path(book_id, BOOKS_DIR)
            target = os.path.join(bpath, "00_设定集", os.path.basename(fname))
            if os.path.exists(target):
                with open(target, "r", encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                self.send_json({
                    "book_title": btitle,
                    "filename": fname,
                    "title": os.path.basename(fname).replace(".md", ""),
                    "content": content
                })
            else:
                self.send_json({"error": "Setting not found"}, 404)
            return

        # 获取 Antigravity 历史对话
        if path == "/api/conversations" or path == "/novel/api/conversations":
            conv_file = os.path.join(DATA_DIR, "conversations.json")
            data = load_json_file(conv_file, default=[])
            self.send_json({"conversations": data})
            return

        # 本地桥接器获取待办任务 (轮询)
        if path == "/api/pending_task" or path == "/novel/api/pending_task":
            LOCAL_STATUS["online"] = True
            LOCAL_STATUS["last_seen"] = time.time()
            if HAS_REDIS:
                try:
                    REDIS_CLIENT.set("local_pc_online", "1", ex=30)
                except Exception:
                    pass

            for tid, t in TASKS.items():
                if t.get("status") == "pending":
                    t["status"] = "running"
                    self.send_json({"task": t})
                    return
            self.send_json({"task": None})
            return

        # 查询任务状态
        if path == "/api/task_status" or path == "/novel/api/task_status":
            tid = qs.get("id", [""])[0]
            t = TASKS.get(tid)
            if t:
                self.send_json(t)
            else:
                self.send_json({"error": "Task not found"}, 404)
            return

        self.send_json({"error": "Not Found", "path": path}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(length) if length > 0 else b""
        
        try:
            body = json.loads(post_data.decode("utf-8")) if post_data else {}
        except Exception:
            body = {}

        # 登录认证
        if path == "/api/login" or path == "/novel/api/login":
            pwd = body.get("password", "")
            if pwd == PASSWORD:
                self.send_json({"ok": True, "token": "daqin_authorized"})
            else:
                self.send_json({"ok": False, "error": "口令错误，请重新输入"}, 401)
            return

        # 本地桥接器多书架全量同步
        if path == "/api/sync_data" or path == "/novel/api/sync_data":
            LOCAL_STATUS["online"] = True
            LOCAL_STATUS["last_seen"] = time.time()
            book_id = body.get("book_id", "给政哥讲量子力学，他竟肉身硬抗二向箔")
            bpath = os.path.join(BOOKS_DIR, book_id)
            os.makedirs(os.path.join(bpath, "01_正文", "第一卷_人界篇"), exist_ok=True)
            os.makedirs(os.path.join(bpath, "00_设定集"), exist_ok=True)

            # 同步章节
            chapters = body.get("chapters", [])
            for c in chapters:
                fpath = os.path.join(bpath, "01_正文", "第一卷_人界篇", c["filename"])
                with open(fpath, "w", encoding="utf-8") as fp:
                    fp.write(c["content"])

            # 同步设定
            settings = body.get("settings", [])
            for s in settings:
                fpath = os.path.join(bpath, "00_设定集", s["filename"])
                with open(fpath, "w", encoding="utf-8") as fp:
                    fp.write(s["content"])

            # 同步对话
            conversations = body.get("conversations", [])
            if conversations:
                save_json_file(os.path.join(DATA_DIR, "conversations.json"), conversations)

            self.send_json({"ok": True, "chapters_count": len(chapters), "settings_count": len(settings)})
            return

        # 手机端下发创作指令
        if path == "/api/dispatch_task" or path == "/novel/api/dispatch_task":
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
            self.send_json({"ok": True, "task": task})
            return

        # 本地桥接器回报任务进度与流式结果
        if path == "/api/task_progress" or path == "/novel/api/task_progress":
            LOCAL_STATUS["online"] = True
            LOCAL_STATUS["last_seen"] = time.time()
            tid = body.get("id")
            if tid and tid in TASKS:
                t = TASKS[tid]
                if "status" in body:
                    t["status"] = body["status"]
                if "log" in body:
                    t["progress_log"].append(f"[{time.strftime('%H:%M:%S')}] {body['log']}")
                if "result" in body:
                    t["result"] = body["result"]
                if t["status"] in ["completed", "failed"]:
                    t["completed_at"] = time.time()
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "Task not found"}, 404)
            return

        self.send_json({"error": "Not Found", "path": path}, 404)

def run():
    port = 8899
    server = HTTPServer(("0.0.0.0", port), HubHandler)
    print(f"Novel Hub 2.0 Server running on 0.0.0.0:{port}...")
    server.serve_forever()

if __name__ == "__main__":
    run()
