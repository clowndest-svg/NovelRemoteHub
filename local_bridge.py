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

# 目录与常量配置 (支持 D:\小说 下多书架并行管理)
BOOKS_ROOT = r"D:\小说"
DEFAULT_BOOK = "给政哥讲量子力学，他竟肉身硬抗二向箔"
AGENTAPI_BAT = r"C:\Users\23793\.gemini\antigravity\bin\agentapi.bat"
ANTIGRAVITY_EXE = r"C:\Users\23793\AppData\Local\Programs\antigravity\Antigravity.exe"

CLOUD_SERVER_URL = "https://scientificexploration.art/novel/api"
TOKEN = "daqin_authorized"

# 忽略 SSL 证书校验 (安全直连)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

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
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=10) as resp:
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
def ensure_antigravity_running():
    try:
        res = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Antigravity.exe"], capture_output=True, text=True, errors="ignore")
        is_running = "Antigravity.exe" in res.stdout
        if not is_running:
            # 进一步检测后台 language_server
            res_ls = subprocess.run(["tasklist", "/FI", "IMAGENAME eq language_server.exe"], capture_output=True, text=True, errors="ignore")
            if "language_server.exe" in res_ls.stdout:
                return True, "Antigravity 后台语言服务 (language_server) 在线活跃中！"

            print("[Antigravity] 检测到客户端未处于运行状态，正在自动拉起唤醒...")
            if os.path.exists(ANTIGRAVITY_EXE):
                subprocess.Popen([ANTIGRAVITY_EXE], close_fds=True)
                time.sleep(4)
                return True, "已成功拉起并唤醒本电脑 Antigravity 客户端！"
        return True, "Antigravity 客户端在线正常！"
    except Exception as e:
        return False, f"进程检测异常: {e}"

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
    book_path = os.path.join(BOOKS_ROOT, book_dir_name)
    chapters = []
    c_files = sorted(glob.glob(os.path.join(book_path, "01_正文", "**", "*.md"), recursive=True))
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
    logs_candidates = glob.glob(r"C:\Users\23793\.gemini\antigravity\brain\*\.system_generated\logs\transcript.jsonl")
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

    return True, total_chapters, total_settings

# 7. 执行 Antigravity 唤醒 (具备网络代理自愈重试 + 额度熔断自动降级机制)
def execute_agentapi(prompt, title="【手机遥控】任务执行", engine_mode="antigravity_pro", model_tier="gemini-3.8-flash-high", progress_cb=None):
    # 先做健康检测
    ensure_antigravity_running()

    # 规范化模型选择 (默认主力: gemini-3.8-flash-high)
    model_map = {
        "gemini-3.8-flash-high": {"flag": "--model=gemini-3.8-flash-high", "display": "Gemini 3.8 Flash High (主力 · 量大管饱)", "fallback": "flash"},
        "claude-sonnet-4-6": {"flag": "--model=claude-sonnet-4-6", "display": "Claude Sonnet 4.6 (深度构思)", "fallback": "gemini-3.8-flash-high"},
        "flash": {"flag": "--model=flash", "display": "Gemini Flash (标准极速)", "fallback": "pro"},
        "pro": {"flag": "--model=pro", "display": "Gemini Pro (高维推理)", "fallback": "gemini-3.8-flash-high"}
    }

    tier_key = model_tier if model_tier in model_map else "gemini-3.8-flash-high"
    selected = model_map[tier_key]
    primary_flag = selected["flag"]
    primary_display = selected["display"]
    fallback_tier = selected["fallback"]

    # 判定错误类型辅助函数
    def is_quota_error(text):
        lower = text.lower()
        return any(k in lower for k in ["quota", "rate limit", "resource exhausted", "429", "capacity", "exhausted", "resource_exhausted"])

    def is_network_error(text):
        lower = text.lower()
        return any(k in lower for k in ["timeout", "timed out", "connect", "connection refused", "proxy", "socket", "winerror 10061", "eof", "dial tcp", "reset by peer"])

    # 循环尝试执行 (最多重试 3 次，并在遇到额度问题时降级)
    max_retries = 3
    current_flag = primary_flag
    current_display = primary_display
    has_fallback = False

    for attempt in range(1, max_retries + 1):
        cmd = [AGENTAPI_BAT, "new-conversation", current_flag, f"--title={title}", prompt]
        print(f"[Antigravity] 尝试调用 (第 {attempt} 次): {current_flag} -> {title}")
        
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=60)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            combined = f"{stdout}\n{stderr}"

            if proc.returncode == 0:
                success_msg = f"✅ 成功唤醒 Antigravity 专属创作会话 [{current_display}]！"
                if has_fallback:
                    success_msg += "\n(注：已自动熔断切换至备用模型平滑完成)"
                return True, f"{success_msg}\n{stdout}"

            # 如果失败，分析错误根因
            print(f"[Antigravity] 执行返回非零码 ({proc.returncode}): {stderr.strip()[:200]}")

            # 1. 额度耗尽 (Quota / Rate Limit) -> 自动熔断切换备用模型
            if is_quota_error(combined) and not has_fallback:
                fb_info = model_map.get(fallback_tier, model_map["flash"])
                current_flag = fb_info["flag"]
                current_display = fb_info["display"]
                has_fallback = True
                msg = f"⚠️ 检测到 {primary_display} 额度达到上限，已自动熔断切换至备用模型 [{current_display}] 继续重试..."
                print(f"[Antigravity Fallback] {msg}")
                if progress_cb:
                    progress_cb(msg)
                time.sleep(2)
                continue

            # 2. 网络代理超时 (Proxy / Network Failure) -> 等待并重试
            if is_network_error(combined):
                proxy_ok, proxy_msg = check_proxy_health()
                msg = f"⚠️ 本电脑网络代理握手超时 ({proxy_msg})，正在进行第 {attempt}/{max_retries} 次自动重试..."
                print(f"[Antigravity Retry] {msg}")
                if progress_cb:
                    progress_cb(msg)
                time.sleep(3)
                continue

            # 其他常规错误，短暂重试
            time.sleep(2)

        except subprocess.TimeoutExpired:
            msg = f"⚠️ 进程响应超时 (60秒)，正在尝试第 {attempt}/{max_retries} 次重试..."
            print(f"[Antigravity Timeout] {msg}")
            if progress_cb:
                progress_cb(msg)
            time.sleep(2)
        except Exception as e:
            msg = f"⚠️ 调用异常 ({e})，正在重试..."
            print(f"[Antigravity Exception] {msg}")
            if progress_cb:
                progress_cb(msg)
            time.sleep(2)

    # 如果所有重试都失败，最后尝试一次无参数极简唤醒保底
    cmd_minimal = [AGENTAPI_BAT, "new-conversation", f"--title={title}", prompt]
    try:
        proc = subprocess.run(cmd_minimal, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=45)
        if proc.returncode == 0:
            return True, f"✅ 成功唤醒 Antigravity 默认极简会话！\n{proc.stdout}"
        return False, f"❌ 调用失败：{proc.stderr or proc.stdout}\n[诊断建议] 请检查办公室电脑 Clash Verge TUN 代理节点与网络连接。"
    except Exception as e:
        return False, f"❌ 执行异常: {e}\n[诊断建议] 请检查办公室电脑 Clash Verge TUN 代理节点状态。"

# 8. 任务调度主循环
def run_bridge_loop():
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
    if os.path.exists(BOOKS_ROOT):
        last_file_count = len(glob.glob(os.path.join(BOOKS_ROOT, "**", "*.md"), recursive=True))

    while True:
        try:
            # 1. 轮询云端任务
            data = api_get("pending_task")
            if data and data.get("task"):
                task = data["task"]
                tid = task["id"]
                action = task.get("action", "creative_instruction")
                sub_action = task.get("sub_action", "")
                extra_data = task.get("extra_data", {})
                user_prompt = task.get("prompt", "")
                book_id = task.get("book_id", DEFAULT_BOOK)
                engine = task.get("engine_mode", "antigravity_pro")
                model_tier = extra_data.get("model_tier", "gemini-3.8-flash-high") if extra_data else "gemini-3.8-flash-high"

                print(f"\n[Task] 捕获指令! ID={tid}, Action={action}, 作品={book_id}, 模型={model_tier}")

                # 进度更新回调闭包
                def report_progress(msg):
                    api_post("task_progress", {"id": tid, "status": "running", "log": msg})

                # A. 远程唤醒/健康检查指令
                if action == "wake_antigravity":
                    report_progress("正在对电脑端 Antigravity 客户端及后台语言服务进行健康检查与唤醒...")
                    ensure_ok, ensure_msg = ensure_antigravity_running()
                    proxy_ok, proxy_msg = check_proxy_health()
                    log_text = f"✅ 电脑端健康检测完毕：\n1. 引擎状态：{ensure_msg}\n2. 网络代理：{proxy_msg}"
                    api_post("task_progress", {"id": tid, "status": "completed", "log": log_text})
                    time.sleep(1)
                    continue

                # B. 构思创建全新小说架构
                elif action == "create_new_novel":
                    book_title = extra_data.get("title") or book_id
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

请严格遵循本书宏观世界观逻辑与科技修仙/生产力演化原则：
1. 深入推演细节，丰富规则、人物、法宝、势力或逻辑推导；
2. 直接调用 write_file 或 edit_file 更新落盘到 `D:\\小说\\{book_id}\\00_设定集\\` 对应文件；
3. 完成后返回更新总结！
"""
                    task_title = f"【手机遥控】《{book_id}》设定演进"

                # D. 正文创作与章节续写精修
                else:
                    book_root = os.path.join(BOOKS_ROOT, book_id)
                    c_files = sorted(glob.glob(os.path.join(book_root, "01_正文", "**", "*.md"), recursive=True))
                    next_chapter_num = len(c_files) + 1
                    pad_num = f"{next_chapter_num:03d}"

                    prev_chapter_tail = ""
                    if c_files:
                        with open(c_files[-1], "r", encoding="utf-8", errors="ignore") as fp:
                            prev_chapter_tail = fp.read()[-1500:]

                    outline_candidates = glob.glob(os.path.join(book_root, "00_设定集", "*细纲*.md")) + glob.glob(os.path.join(book_root, "00_设定集", "*大纲*.md"))
                    chapter_outline = ""
                    if outline_candidates:
                        with open(outline_candidates[0], "r", encoding="utf-8", errors="ignore") as fp:
                            outline_text = fp.read()
                            m = re.search(rf"(第{pad_num}章[^\n]*\n(?:(?!\n###|\n##第|\n\* \*\*第).)*)", outline_text, re.DOTALL)
                            if m:
                                chapter_outline = m.group(1).strip()

                    is_write_chapter = any(kw in user_prompt for kw in ["第", "章", "创作", "续写", "开始写作"]) or not user_prompt

                    if is_write_chapter:
                        prompt = f"""【《{book_id}》小说创作指令 · 第{pad_num}章】
你正在为番茄小说作品《{book_id}》创作正文。
请严格遵循全局创作铁律：
1. 题材核心：遵循本书宇宙设定与主线总谱，将硬核设定与情节高潮深度融合；
2. 生产力决定生产关系铁律：民生政治文化润物细无声，展现真实质感与震撼；
3. 番茄小说排版规范：移动端短段落黄金排版，对话严格使用中文全角双引号“”，每段1-3行，戏带设定，绝不通篇说明文；
4. 篇幅要求：4000-5000字，字数充沛，情节饱满，节奏极度紧凑。

【上一章末尾衔接钩子】：
\"\"\"
{prev_chapter_tail}
\"\"\"

【本章分章细纲规划】：
{chapter_outline if chapter_outline else f"第{pad_num}章按总谱剧情推进，承接上一章伏笔"}

【指令附加要求】：
{user_prompt if user_prompt else "按大纲极致推演，强化剧情反差张力，章末设置引人点击下一章的王炸悬念钩子"}

请直接开始创作第{pad_num}章完整正文，并在完成后调用 write_file 写入对应文件路径：
`D:\\小说\\{book_id}\\01_正文\\第一卷_人界篇\\第{pad_num}章_xxxx.md`！
"""
                    else:
                        prompt = f"""【《{book_id}》小说辅助指令】
当前小说工程工作区：`D:\\小说\\{book_id}`
来自作者的指令：
\"\"\"
{user_prompt}
\"\"\"
请严格按照本书宇宙设定集与大纲体系执行该要求，并在必要时直接修改或创建对应文件。
"""
                    task_title = f"【手机遥控】《{book_id}》创作第{pad_num}章"

                report_progress(f"正在唤醒 Antigravity (模型: {model_tier})，开启智能创作会话...")
                ok, output = execute_agentapi(prompt, title=task_title, engine_mode=engine, model_tier=model_tier, progress_cb=report_progress)
                
                if ok:
                    api_post("task_progress", {
                        "id": tid, 
                        "status": "completed", 
                        "log": f"{output.strip()}\n\n🎉 成果已就绪，正在自动同步至多端云枢！"
                    })
                    print(f"[Task] 成功执行任务: {task_title}")
                    # 立即全量同步最新生成的文件
                    sync_all_books_to_cloud()
                else:
                    api_post("task_progress", {
                        "id": tid, 
                        "status": "failed", 
                        "log": output
                    })

            # 2. 定时检查本地是否有新文件生成并同步
            if time.time() - last_sync_time > 10:
                current_count = 0
                if os.path.exists(BOOKS_ROOT):
                    current_count = len(glob.glob(os.path.join(BOOKS_ROOT, "**", "*.md"), recursive=True))
                if current_count != last_file_count:
                    print(f"[Sync] 检测到文件变化 ({last_file_count} -> {current_count})，立即自动同步至云端！")
                    sync_all_books_to_cloud()
                    last_file_count = current_count
                else:
                    sync_all_books_to_cloud()
                last_sync_time = time.time()

        except Exception as e:
            print(f"[Loop Exception] {e}")

        time.sleep(3)

if __name__ == "__main__":
    run_bridge_loop()
