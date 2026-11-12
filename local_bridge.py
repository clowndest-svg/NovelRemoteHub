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
from pathlib import Path
from storage import atomic_json, atomic_write, within, safe_component
import agent_client
import novel_manager

# 确保在 pythonw.exe 无控制台环境下运行时，stdout/stderr 自动安全重定向至 daemon.log，避免 NoneType.write 异常崩溃
class SafeDaemonLogStream:
    def __init__(self, log_path, orig_stream=None):
        self.log_path = log_path
        self.orig_stream = orig_stream
    def write(self, text):
        if not text:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8", errors="replace") as f:
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

# 忽略 SSL 证书校验 (安全直连)
SSL_CTX = ssl.create_default_context()


# 0. 单实例互斥守护 (端口锁)
_LOCK_SOCKET = None
def ensure_single_instance():
    global _LOCK_SOCKET
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 27960))
        s.listen(1)
        _LOCK_SOCKET = s
        return True
    except Exception:
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
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=60 if len(body) > 1024 * 1024 else 15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return None

def api_get(endpoint):
    url = f"{CLOUD_SERVER_URL}/{endpoint.lstrip('/')}"
    req = urllib.request.Request(url, headers={
        "X-Novel-Token": TOKEN
    })
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
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
        with open(f, "r", encoding="utf-8", errors="ignore") as fp:
            chapters.append({
                "filename": os.path.basename(f),
                "content": fp.read()
            })

    settings = []
    s_files = sorted(glob.glob(os.path.join(book_path, "00_设定集", "*.md")))
    for f in s_files:
        with open(f, "r", encoding="utf-8", errors="ignore") as fp:
            settings.append({
                "filename": os.path.basename(f),
                "content": fp.read()
            })
    return chapters, settings

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
def sync_all_books_to_cloud():
    total_chapters = 0
    total_settings = 0
    convs = collect_all_conversations()
    all_ok = True

    if os.path.exists(BOOKS_ROOT):
        for entry in os.scandir(BOOKS_ROOT):
            if entry.is_dir() and not entry.name.startswith("."):
                book_id = entry.name
                chapters, settings = collect_book_data(book_id)
                payload = {
                    "book_id": book_id,
                    "chapters": chapters,
                    "settings": settings,
                    "conversations": convs
                }
                res = api_post("sync_data", payload)
                if res and res.get("ok"):
                    total_chapters += len(chapters)
                    total_settings += len(settings)
                else:
                    all_ok = False

    return all_ok, total_chapters, total_settings

# 7. 执行 Antigravity 唤醒 (具备网络代理自愈重试 + 额度熔断自动降级机制)
def execute_agentapi(prompt, title="【手机遥控】任务执行", engine_mode="antigravity_pro",
                     model_tier="gemini-3.8-flash-high", progress_cb=None):
    ready, message = ensure_antigravity_running(progress_cb)
    if not ready:
        return False, message
    receipt = Path(BOOKS_ROOT) / ".hub-results" / (uuid.uuid4().hex + ".json")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    prompt += f"""\n完成所有操作后，最后一步将 UTF-8 JSON 回执写入 {receipt}。
结构为 {{"summary":"实际完成内容","files":["实际写入文件的绝对路径"]}}。
如果执行失败，写入 {{"error":"具体失败原因"}}。回执必须在全部操作完成后才写入。
"""
    models = [model_tier, "flash"] if model_tier != "flash" else ["flash", "pro"]
    try:
        conversation = None
        for model in models:
            try:
                conversation = agent_client.new_conversation(prompt, title, model)
                break
            except RuntimeError as error:
                if not any(word in str(error).lower() for word in ("quota", "429", "resource_exhausted", "capacity")):
                    raise
                if progress_cb:
                    progress_cb("所选模型额度不足，尝试备用模型")
        if not conversation:
            raise RuntimeError("所选模型与备用模型均不可用")
        result = agent_client.wait_for_receipt(receipt, conversation, progress_cb)
        for filename in result.get("files", []):
            target = Path(filename).resolve()
            if not target.is_relative_to(Path(BOOKS_ROOT).resolve()) or not target.is_file():
                raise ValueError("AI 回执中包含无效成果路径")
        return True, f"会话 {conversation} 已返回完成回执。\n{result.get('summary', '')}"
    except Exception as error:
        return False, str(error)


def heartbeat_loop():
    while True:
        api_post("heartbeat", {"id": ACTIVE_TASK})
        time.sleep(10)


# 8. 任务调度主循环
def run_bridge_loop():
    global ACTIVE_TASK
    ensure_single_instance()
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    prevent_sleep()
    ensure_antigravity_running()
    ok, c_cnt, s_cnt = sync_all_books_to_cloud()
    print(f"[Sync] 初始同步完成: 多书架章节总数={c_cnt}, 设定文档={s_cnt}")

    print("\n" + "="*50)
    print("🚀 NovelRemoteHub · 智笔云枢 本地执行端 (Local Bridge 3.0) 已就绪！")
    print(f"📡 监听云端中枢: {CLOUD_SERVER_URL}")
    print(f"🤖 主力模型: Gemini 3.8 Flash High (量大管饱 · 自动熔断与代理自愈)")
    print(f"📚 多书架工作区: {BOOKS_ROOT}")
    print("="*50 + "\n")

    last_sync_time = time.time()
    last_file_count = 0
    last_sync_mtime = 0
    if os.path.exists(BOOKS_ROOT):
        md_files = glob.glob(os.path.join(BOOKS_ROOT, "**", "*.md"), recursive=True)
        last_file_count = len(md_files)
        last_sync_mtime = max((os.path.getmtime(f) for f in md_files), default=0)

    while True:
        try:
            # 1. 轮询云端任务
            data = api_get("pending_task")
            if data and data.get("task"):
                task = data["task"]
                tid = task["id"]
                ACTIVE_TASK = tid
                action = task.get("action", "creative_instruction")
                sub_action = task.get("sub_action", "")
                extra_data = task.get("extra_data", {})
                user_prompt = task.get("prompt", "")
                book_id = safe_component(task.get("book_id", DEFAULT_BOOK))
                engine = task.get("engine_mode", "antigravity_pro")
                model_tier = extra_data.get("model_tier", "gemini-3.8-flash-high") if extra_data else "gemini-3.8-flash-high"

                print(f"\n[Task] 捕获指令! ID={tid}, Action={action}, 作品={book_id}, 模型={model_tier}")

                # 进度更新回调闭包
                def report_progress(msg):
                    api_post("task_progress", {"id": tid, "status": "running", "log": msg})

                # A. 远程唤醒/健康检查指令
                if action == "wake_antigravity":
                    report_progress("正在对电脑端 Antigravity 客户端及后台语言服务进行健康检查与唤醒...")
                    ensure_ok, ensure_msg = ensure_antigravity_running(progress_cb=report_progress)
                    proxy_ok, proxy_msg = check_proxy_health()
                    log_text = f"✅ 电脑端健康检测完毕：\n1. 引擎状态：{ensure_msg}\n2. 网络代理：{proxy_msg}"
                    api_post("task_progress", {"id": tid, "status": "completed", "log": log_text})
                    time.sleep(1)
                    continue

                # B. 构思创建全新小说架构
                elif action == "create_new_novel":
                    book_title = safe_component(extra_data.get("title") or book_id)
                    genre = extra_data.get("genre", "科幻修真 / 网文")
                    concept = extra_data.get("concept", user_prompt)
                    book_root = os.path.join(BOOKS_ROOT, book_title)

                    report_progress(f"正在为全新作品《{book_title}》初始化工程目录结构...")
                    os.makedirs(os.path.join(book_root, "00_设定集"), exist_ok=True)
                    os.makedirs(os.path.join(book_root, "01_正文", "第一卷_人界篇"), exist_ok=True)

                    prompt = f"""【全新小说架构与总谱工程创建指令 · 《{book_title}》】
目标工程目录：`D:\\小说\\{book_title}`
题材分类：{genre}
核心设定与作者构思：
\"\"\"
{concept}
\"\"\"

你现在是白金级网文总架构师。请为《{book_title}》完成全套世界观与分卷分章构想，并调用 write_file 完整写入以下设定文件：
1. `D:\\小说\\{book_title}\\00_设定集\\01_世界观核心设定与科技修炼体系.md`（包含力量境界体系、登天能级梯次、核心法则、社会生产力演化）；
2. `D:\\小说\\{book_title}\\00_设定集\\02_核心人物小传与各方阵营势力.md`（包含主角身世金手指、核心反派、朝廷/宗门势力架构）；
3. `D:\\小说\\{book_title}\\00_设定集\\03_全书分卷主线总谱与前30章分章细纲.md`（包含全书主线伏笔脉络、各卷高潮、前30章每章剧情推演与章末反转钩子）。

请直接调用 write_file 将以上文档全部写入落盘，确保内容详实充沛、设定自洽、爽点与悬念密集！
"""
                    task_title = f"【手机遥控】新建作品架构《{book_title}》"

                # C. 设定集演进 (新增、润色扩展、一致性排查)
                elif action == "evolve_settings":
                    book_root = os.path.join(BOOKS_ROOT, book_id)
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
                    ready, message = ensure_antigravity_running(report_progress)
                    if not ready:
                        raise RuntimeError(message)
                    meta_res = illustration_generator.generate_chapter_visual_bundle(
                        bpath, filename, chapter_text, image_count,
                        extra_data.get("custom_prompt", user_prompt), report_progress,
                        notes_only=notes_only, model=model_tier)
                    import base64
                    illu_dir = Path(bpath) / "02_插图" / meta_res["chapter_code"]
                    images = {item["filename"]: base64.b64encode((illu_dir / item["filename"]).read_bytes()).decode()
                              for item in meta_res["illustrations"]}
                    report_progress("素材已生成，正在验证云端保存回执")
                    uploaded = api_post("sync_illustrations", {
                        "book_id": book_id, "chapter_code": meta_res["chapter_code"],
                        "meta": meta_res, "images": images, "notes_only": notes_only,
                        "base_revision": base_revision})
                    if not uploaded or not uploaded.get("ok") or uploaded.get("saved_images") != len(images):
                        raise RuntimeError((uploaded or {}).get("error") or "云端未确认完整保存，本地生成文件已保留，可重试同步")
                    latest = api_get("chapter_extras?" + query)
                    if latest and latest.get("meta"):
                        atomic_json(illu_dir / "meta.json", latest["meta"])
                    api_post("task_progress", {"id": tid, "status": "completed",
                        "log": "章节文案已生成并保存" if notes_only else f"已生成并同步 {len(images)} 张二维水墨场景插画与互动文案",
                        "result": {"conversation_id": meta_res["conversation_id"]}})
                    continue

                # E. 番茄驳回/存量章节定点重塑修复
                elif action == "repair_chapter":
                    chapter_filename = extra_data.get("chapter_file", "")
                    rejection_reason = extra_data.get("rejection_reason", user_prompt)
                    repair_mode = extra_data.get("repair_mode", "polish")
                    audit_issues = extra_data.get("audit_issues", [])

                    report_progress(f"正在调阅《{book_id}》【{chapter_filename}】，准备进行定点重塑修复...")

                    bpath = os.path.join(BOOKS_ROOT, book_id)
                    all_c_files = novel_manager.chapter_files(bpath)
                    
                    target_file_path = None
                    target_idx = -1
                    for idx_c, cf in enumerate(all_c_files):
                        if os.path.basename(cf) == os.path.basename(chapter_filename):
                            target_file_path = cf
                            target_idx = idx_c
                            break

                    if not target_file_path or not os.path.exists(target_file_path):
                        api_post("task_progress", {"id": tid, "status": "failed", "log": f"未找到对应章节正文文件: {chapter_filename}"})
                        continue

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

                    mode_desc = "【🛡️ 合规脱敏精修】：保持原章节剧情大骨架不变，精准替换、重写涉敏涉嫌违规词句（如涉暴动、敏感冲突等），使用文雅合规的修仙科幻隐喻艺术化表达，优化不顺对白。" if repair_mode == "polish" else "【⚡ 深度剧情重构】：结合前后章节主线与细纲，全盘重写本章高潮冲突与悬念反转，解决逻辑违和或节奏拖沓问题，彻底重塑读者爽感与完读率。"

                    prompt = f"""【《{book_id}》番茄小说章节定点重构与驳回修复指令 · {os.path.basename(target_file_path)}】
当前小说工程工作区：`D:\\小说\\{book_id}`
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
                    book_root = os.path.join(BOOKS_ROOT, book_id)
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
                        prompt = f"""【《{book_id}》小说新章节创作指令 · 第{pad_num}章】
你正在为番茄小说作品《{book_id}》创作全新正文第{pad_num}章。
当前小说工程工作区：`D:\\小说\\{book_id}`
上一章正文文件路径：`{prev_chapter_file if prev_chapter_file else "（首章）"}`

【全套设定集文档清单（必须严格对照，杜绝任何设定冲突）】：
{setting_file_list_str}

【上一章末尾衔接剧情与悬念钩子】：
\"\"\"
{prev_chapter_tail}
\"\"\"

【本章分章细纲规划】：
\"\"\"
{chapter_outline if chapter_outline else f"第{pad_num}章按全书细纲与主线总谱剧情推进，承接上一章伏笔"}
\"\"\"

【作者指令附加要求】：
\"\"\"
{user_prompt if user_prompt else "按大纲极致推演，强化剧情反差张力，章末设置引人点击下一章的王炸悬念钩子"}
\"\"\"

【必须执行的工作流 SOP（铁律执行）】：
1. 步骤1：调用 read_file 读取上一章正文（路径：`{prev_chapter_file}`），确保剧情节奏、人物台词对白与章末悬念钩子无缝衔接，逻辑严密！
2. 步骤2：检索并查阅 `D:\\小说\\{book_id}\\00_设定集\\` 目录下全部设定文档，并读取本书根目录存在的 GEMINI.md，确保严格遵循本书既有世界观、人物设定和创作规范；
3. 步骤3：遵循番茄小说行文规范（中文全角双引号“”、移动端短段落排版、戏带设定、每章必留抓人点击下一章的强力断章钩子）；
4. 步骤4：篇幅要求：4000-5000字，情节饱满，节奏极度紧凑；
5. 步骤5：创作完成后直接调用 write_file 写入对应文件路径：
   `D:\\小说\\{book_id}\\01_正文\\第一卷_人界篇\\第{pad_num}章_xxxx.md`！
"""
                        task_title = f"【手机遥控】《{book_id}》创作第{pad_num}章"
                    else:
                        prompt = f"""【《{book_id}》小说辅助构思与推演指令】
当前小说工程工作区：`D:\\小说\\{book_id}`
设定集目录：`D:\\小说\\{book_id}\\00_设定集`
现有全套设定文档：
{setting_file_list_str}

来自作者的指令：
\"\"\"
{user_prompt}
\"\"\"

请严格通读并参考上述全部设定集文档，遵循本书宏观世界观与严密逻辑执行该推演要求，并在必要时直接调用 write_file 或 edit_file 更新对应文档！
"""
                        task_title = f"【手机遥控】《{book_id}》智能推演"

                report_progress(f"正在唤醒 Antigravity (模型: {model_tier})，开启智能创作会话...")
                ok, output = execute_agentapi(prompt, title=task_title, engine_mode=engine, model_tier=model_tier, progress_cb=report_progress)
                
                if ok:
                    synced, _, _ = sync_all_books_to_cloud()
                    if not synced:
                        raise RuntimeError("AI 已执行完成，但云端同步失败，请检查网络后重试同步")
                    api_post("task_progress", {
                        "id": tid, 
                        "status": "completed", 
                        "log": f"{output.strip()}\n\n🎉 成果已就绪，正在自动同步至多端云枢！"
                    })
                    print(f"[Task] 成功执行任务: {task_title}")
                    # 立即全量同步最新生成的文件
                else:
                    api_post("task_progress", {
                        "id": tid, 
                        "status": "failed", 
                        "log": output
                    })

            # 2. 定时检查本地是否有新文件或内容变动 (只在真正有变动时才同步，避免频繁无意义刷空缓存)
            if time.time() - last_sync_time > 15:
                if os.path.exists(BOOKS_ROOT):
                    current_md_files = glob.glob(os.path.join(BOOKS_ROOT, "**", "*.md"), recursive=True)
                    current_count = len(current_md_files)
                    current_max_mtime = max((os.path.getmtime(f) for f in current_md_files), default=0)
                    if current_count != last_file_count or current_max_mtime > last_sync_mtime:
                        print(f"[Sync] 检测到文件数量或修改时间变化 ({last_file_count} -> {current_count})，立即自动同步至云端！")
                        sync_all_books_to_cloud()
                        last_file_count = current_count
                        last_sync_mtime = current_max_mtime
                last_sync_time = time.time()

        except Exception as e:
            print(f"[Loop Exception] {e}")
            if ACTIVE_TASK:
                api_post("task_progress", {"id": ACTIVE_TASK, "status": "failed", "log": str(e)})
        finally:
            ACTIVE_TASK = None

        time.sleep(3)

if __name__ == "__main__":
    run_bridge_loop()
