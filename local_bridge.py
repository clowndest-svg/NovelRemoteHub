# -*- coding: utf-8 -*-
r"""
NovelRemoteHub · 智笔云枢 本地执行与多书架同步桥接器 (Local Agent Bridge 2.3)
运行在公司 Windows 电脑上 (D:\RuanJian\NovelRemoteHub)
严格遵守 C 盘零占用铁律
支持：
1. 创作下一章与正文精修
2. 一键构思新书、全套世界观架构与分卷细纲总谱生成
3. 设定大典智谱演化：新增设定文档、深度扩展与正文逻辑冲突排查
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

# 目录与常量配置 (支持 D:\小说 下多书架并行管理)
BOOKS_ROOT = r"D:\小说"
DEFAULT_BOOK = "给政哥讲量子力学，他竟肉身硬抗二向箔"
BRAIN_CONV_ID = "be7c9c60-1b07-4a49-8927-f9c527d4f9f4"
AGENTAPI_BAT = r"C:\Users\23793\.gemini\antigravity\bin\agentapi.bat"

CLOUD_SERVER_URL = "https://124.220.54.64/novel/api"
TOKEN = "daqin_authorized"

# 忽略 SSL 证书校验 (因为直连 IP)
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

# 3. 扫描本地多书架数据
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
        except Exception as e:
            pass
    return conversations[-50:]

# 4. 全量同步多书架至云端
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

# 5. 执行 Antigravity 唤醒 (优先指定 --model=pro 激发顶级会员算力)
def execute_agentapi(prompt, title="【手机遥控】任务执行", engine_mode="antigravity_pro"):
    cmd = [AGENTAPI_BAT, "new-conversation", "--model=pro", f"--title={title}", prompt]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=45)
        if proc.returncode == 0:
            return True, f"成功启动 Antigravity 专属创作会话 (Gemini Pro)！\n{proc.stdout}"
    except Exception as e:
        print(f"[Antigravity] --model=pro 调用异常: {e}")

    # Fallback
    cmd_fallback = [AGENTAPI_BAT, "new-conversation", f"--title={title}", prompt]
    try:
        proc = subprocess.run(cmd_fallback, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=45)
        if proc.returncode == 0:
            return True, f"成功启动 Antigravity 专属创作会话！\n{proc.stdout}"
        return False, proc.stderr or proc.stdout
    except Exception as e:
        return False, str(e)

# 6. 任务调度主循环
def run_bridge_loop():
    prevent_sleep()
    ok, c_cnt, s_cnt = sync_all_books_to_cloud()
    print(f"[Sync] 初始同步完成: 多书架章节总数={c_cnt}, 设定文档={s_cnt}")

    print("\n" + "="*50)
    print("🚀 NovelRemoteHub · 智笔云枢 本地执行端 (Local Bridge 2.3) 已就绪！")
    print(f"📡 监听云端中枢: {CLOUD_SERVER_URL}")
    print(f"🤖 引擎调度: Gemini Pro (Antigravity 智能体)")
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

                print(f"\n[Task] 捕获指令! ID={tid}, Action={action}, 作品={book_id}")

                # 分流处理 3 大类指令
                if action == "create_new_novel":
                    # A. 构思创建全新小说架构
                    book_title = extra_data.get("title") or book_id
                    genre = extra_data.get("genre", "科幻修真 / 网文")
                    concept = extra_data.get("concept", user_prompt)
                    book_root = os.path.join(BOOKS_ROOT, book_title)

                    api_post("task_progress", {"id": tid, "status": "running", "log": f"正在为全新作品《{book_title}》初始化工程目录结构..."})
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

                elif action == "evolve_settings":
                    # B. 设定集演进 (新增、润色扩展、一致性排查)
                    book_root = os.path.join(BOOKS_ROOT, book_id)
                    target_doc = extra_data.get("target_doc", "")
                    doc_path = os.path.join(book_root, "00_设定集", target_doc) if target_doc else ""

                    api_post("task_progress", {"id": tid, "status": "running", "log": f"正在分析《{book_id}》设定集，准备执行【{sub_action}】..."})

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

                else:
                    # C. 正文创作与章节精修
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

                api_post("task_progress", {"id": tid, "status": "running", "log": f"已组装指令提示词，正在唤醒 Antigravity 智能体 (Gemini Pro)..."})
                
                ok, output = execute_agentapi(prompt, title=task_title, engine_mode=engine)
                if ok:
                    api_post("task_progress", {
                        "id": tid, 
                        "status": "completed", 
                        "log": f"✅ Antigravity (Gemini Pro) 任务执行完毕！落盘文件已自动同步至云端！\n{output.strip()}"
                    })
                    print(f"[Task] 成功执行任务: {task_title}")
                    # 立即全量同步
                    sync_all_books_to_cloud()
                else:
                    api_post("task_progress", {
                        "id": tid, 
                        "status": "failed", 
                        "log": f"❌ Antigravity 执行调用返回错误: {output}"
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
            pass

        time.sleep(3)

if __name__ == "__main__":
    run_bridge_loop()
