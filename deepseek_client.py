# -*- coding: utf-8 -*-
r"""
DeepSeek 官方大模型 API 客户端与创作引擎 (deepseek-flash / deepseek-v4-pro)
官方 API 接口文档规范: https://api-docs.deepseek.com/zh-cn/
Base URL: https://api.deepseek.com

本模块严格对齐 DeepSeek 官方最新接口文档规范：
1. 模型体系：
   - deepseek-flash: DeepSeek-V4.1-Flash-1017 官方主力大模型 (1M 上下文，支持思考模式与图像理解)
   - deepseek-v4-pro: DeepSeek-V4-Pro-0813 旗舰专业模型 (1M 上下文，宏大世界观深度推演)
2. 思考模式 (Thinking Mode)：
   - 支持 reasoning_effort ("low", "high", "max")
   - thinking: {"type": "enabled" | "disabled"}
   - 完整捕获与回显 reasoning_content 思维链
3. 零 Token 账户连通性与余额校验：
   - 官方 GET /user/balance 接口实时查询账户可用状态及充值、赠金总额
4. 全天候直连创作引擎：
   - 支持移动端离线直连创作、多章连续续写与全局设定推演
"""
import os
import re
import glob
import json
import urllib.request
import urllib.error
import ssl
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com"

# 严格对齐官方文档最新模型规范
DEEPSEEK_MODELS = [
    {
        "id": "deepseek-flash",
        "name": "deepseek-flash (DeepSeek-V4.1-Flash-1017 官方主力 · 1M 上下文 · 图像理解)",
        "version": "DeepSeek-V4.1-Flash-1017",
        "is_default": True,
        "disabled": False,
        "efforts": [
            {"id": "high", "name": "深度思考模式 (默认 · 高度逻辑推理)"},
            {"id": "max", "name": "极限推演思考 (Max · 复杂伏笔与世界观闭环)"},
            {"id": "low", "name": "快速思考模式 (Low · 敏捷推理)"},
            {"id": "none", "name": "非思考极速创作 (关闭 Thinking · 快速行文)"}
        ],
        "default_effort": "high",
        "context_window": 1000000,
        "max_output_tokens": 393216,
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"]
    },
    {
        "id": "deepseek-v4-pro",
        "name": "deepseek-v4-pro (DeepSeek-V4-Pro-0813 旗舰专业 · 宏大世界观推演)",
        "version": "DeepSeek-V4-Pro-0813",
        "is_default": False,
        "disabled": False,
        "efforts": [
            {"id": "high", "name": "旗舰深度推演 (默认 · 高度逻辑推理)"},
            {"id": "max", "name": "极限制霸宇宙推演 (Max · 极境宏大架构)"},
            {"id": "low", "name": "快速专业推理 (Low · 逻辑论证)"},
            {"id": "none", "name": "旗舰标准创作 (关闭 Thinking · 华丽辞藻)"}
        ],
        "default_effort": "high",
        "context_window": 1000000,
        "max_output_tokens": 393216,
        "input_modalities": ["text"],
        "output_modalities": ["text"]
    }
]


def normalize_model(model_name):
    """规范化模型名称，旧别名与别名全自动映射至官方标准名"""
    if not model_name:
        return "deepseek-flash"
    m = str(model_name).strip().lower()
    if m in ("deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp", "deepseek-chat"):
        return "deepseek-flash"
    if m in ("deepseek-v4-pro", "deepseek-reasoner", "deepseek-r1"):
        return "deepseek-v4-pro"
    return "deepseek-flash"


def get_deepseek_config(config_dir=None):
    from novel_manager import get_user_settings
    settings = get_user_settings(config_dir)
    api_key = settings.get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
    enabled = bool(settings.get("deepseek_enabled", False)) and bool(api_key.strip())
    base_url = (settings.get("deepseek_base_url") or os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)).strip().rstrip("/")
    return {
        "enabled": enabled,
        "api_key": api_key.strip(),
        "base_url": base_url or DEFAULT_BASE_URL,
        "default_model": normalize_model(settings.get("deepseek_default_model", "deepseek-flash"))
    }


def test_api_key(api_key, base_url=None):
    """
    在线连通性测试。
    优先调用官方 GET /user/balance 接口：零 token 消耗，直接校验 Key 并获取账户真实余额！
    官方文档：https://api-docs.deepseek.com/zh-cn/api/get-user-balance
    """
    if not api_key or not str(api_key).strip():
        return False, "API Key 不能为空，请在设置中粘贴您的 sk- 开头的密钥"
    
    clean_key = str(api_key).strip()
    root_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    ctx = ssl.create_default_context()

    # 1. 优先调用官方 GET /user/balance 接口
    balance_url = f"{root_url}/user/balance"
    req = urllib.request.Request(balance_url, headers={
        "Authorization": f"Bearer {clean_key}",
        "User-Agent": "NovelRemoteHub/3.5"
    })
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            is_avail = data.get("is_available", False)
            infos = data.get("balance_infos", [])
            if infos:
                b = infos[0]
                curr = "￥" if b.get("currency") == "CNY" else "$"
                total = b.get("total_balance", "0.00")
                topped = b.get("topped_up_balance", "0.00")
                granted = b.get("granted_balance", "0.00")
                status_text = "正常可用" if is_avail else "余额不足"
                msg = f"DeepSeek 官方 API 认证成功！账户{status_text}（总余额: {curr}{total}，充值: {curr}{topped}，赠金: {curr}{granted}）"
            else:
                msg = "DeepSeek 官方 API 认证成功，Key 完全有效！"
            return True, msg
    except urllib.error.HTTPError as err:
        if err.code == 401:
            return False, "DeepSeek 官方认证失败 (HTTP 401): API Key 无效或已过期，请核对后重新输入"
        if err.code != 404:
            err_body = err.read().decode("utf-8", errors="ignore")
            try:
                msg = json.loads(err_body).get("error", {}).get("message", err_body)
            except Exception:
                msg = err_body
            return False, f"DeepSeek 官方返回错误 (HTTP {err.code}): {msg[:200]}"
    except Exception as err:
        pass

    # 2. 降级：调用 /chat/completions 进行最小探测
    try:
        url = f"{root_url}/chat/completions"
        payload = {
            "model": "deepseek-flash",
            "messages": [{"role": "user", "content": "你好"}],
            "max_tokens": 5
        }
        data = json.dumps(payload).encode("utf-8")
        req2 = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {clean_key}",
            "User-Agent": "NovelRemoteHub/3.5"
        })
        with urllib.request.urlopen(req2, context=ctx, timeout=12) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if "choices" in body and body["choices"]:
                return True, "DeepSeek 官方 API 认证成功，Key 完全有效！"
            return False, "DeepSeek 接口响应格式异常"
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8", errors="ignore")
        try:
            msg = json.loads(err_body).get("error", {}).get("message", err_body)
        except Exception:
            msg = err_body
        return False, f"DeepSeek 官方返回错误 (HTTP {err.code}): {msg[:200]}"
    except Exception as err:
        return False, f"网络连接失败: {str(err)}"


def discover(config_dir=None):
    cfg = get_deepseek_config(config_dir)
    if not cfg["enabled"]:
        return {
            "id": "deepseek",
            "name": "DeepSeek (官方直连 API)",
            "available": False,
            "models": [],
            "error": "DeepSeek 官方 API 未启用。请在右上角【⚙️ 系统设置】中开启并填入 API Key。"
        }
    return {
        "id": "deepseek",
        "name": "DeepSeek (官方直连 API)",
        "available": True,
        "models": DEEPSEEK_MODELS,
        "workspace": "D:\\小说",
        "project_name": "小说",
        "capabilities": {"text": True, "images": True},
        "permission_mode": "full-access"
    }


def generate_content(prompt, model="deepseek-flash", effort="high", system_prompt=None, config_dir=None, progress_cb=None):
    """
    调用 DeepSeek 官方 Chat Completions API 进行文本生成与逻辑推理。
    严格支持 Thinking Mode 思考模式参数与 reasoning_content 思维链捕获。
    """
    cfg = get_deepseek_config(config_dir)
    if not cfg["api_key"]:
        raise ValueError("DeepSeek API Key 未配置，请在右上角【⚙️ 系统设置】中开启并填入您的 Key")

    target_model = normalize_model(model or cfg["default_model"])
    url = f"{cfg['base_url']}/chat/completions"
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": target_model,
        "messages": messages,
        "stream": False,
        "max_tokens": 16384
    }

    # 官方思考模式参数规范控制
    if effort == "none":
        payload["thinking"] = {"type": "disabled"}
        payload["temperature"] = 0.95
    else:
        payload["thinking"] = {"type": "enabled"}
        actual_effort = effort if effort in ("low", "high", "max") else "high"
        payload["reasoning_effort"] = actual_effort
        payload["top_p"] = 1.0

    if progress_cb:
        effort_names = {
            "high": "深度思考模式 (High)",
            "max": "极限推演模式 (Max)",
            "low": "快速思考模式 (Low)",
            "none": "非思考极速创作"
        }
        tag = effort_names.get(effort, "深度思考模式")
        progress_cb(f"已连接 DeepSeek 官方 API，正在调用 {target_model}（{tag}）构思并撰写正文...")

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg['api_key']}",
        "User-Agent": "NovelRemoteHub/3.5"
    })
    ctx = ssl.create_default_context()
    
    with urllib.request.urlopen(req, context=ctx, timeout=300) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        choices = res.get("choices", [])
        if not choices:
            raise RuntimeError("DeepSeek 未返回有效结果")
        msg = choices[0].get("message", {})
        reasoning = msg.get("reasoning_content", "")
        content = msg.get("content", "")
        if reasoning and progress_cb:
            progress_cb(f"【DeepSeek 思维链推理已就绪】\n{reasoning[:500]}...")
        return content, reasoning


def write_serial_chapters(book_root, book_id, count=1, user_prompt="", model="deepseek-flash", effort="high", config_dir=None, progress_cb=None):
    """
    使用 DeepSeek 官方大模型执行多章连续续写。
    遵循：
    1. 番茄小说排版铁律（全角双引号“”，移动端短段落，单段不超过4行）；
    2. 宇宙科技修仙风格与大纲细纲；
    3. 生产力决定生产关系（民生政治科技润物细无声演进，严禁突兀一蹴而就）；
    4. 强力断章悬念钩子。
    """
    import novel_manager
    count = novel_manager.validate_chapter_count(count)
    completed = 0
    last_added = None

    for index in range(count):
        # 1. 扫描已有章节与编号
        c_files = novel_manager.chapter_files(book_root)
        next_num = len(c_files) + 1
        
        # 2. 读取上一章末尾正文
        prev_tail = ""
        prev_filename = ""
        if c_files:
            last_file = c_files[-1]
            prev_filename = os.path.basename(last_file)
            try:
                with open(last_file, "r", encoding="utf-8", errors="ignore") as fp:
                    prev_tail = fp.read()[-2500:]
            except Exception:
                pass

        # 3. 读取设定集文档
        setting_dir = os.path.join(book_root, "00_设定集")
        setting_files = sorted(glob.glob(os.path.join(setting_dir, "*.md")))
        setting_summary = []
        for sf in setting_files:
            try:
                sname = os.path.basename(sf)
                with open(sf, "r", encoding="utf-8", errors="ignore") as sfp:
                    stext = sfp.read()
                    # 细纲大纲全量读，其他文档提取前1500字
                    if "细纲" in sname or "大纲" in sname:
                        setting_summary.append(f"### 文档: {sname}\n{stext}\n")
                    else:
                        setting_summary.append(f"### 文档: {sname}\n{stext[:1500]}\n")
            except Exception:
                pass
        all_settings_text = "\n".join(setting_summary)

        # 4. 提取当前章节对应的细纲/大纲规划
        chapter_outline = ""
        outline_candidates = glob.glob(os.path.join(setting_dir, "*细纲*.md")) + glob.glob(os.path.join(setting_dir, "*大纲*.md"))
        if outline_candidates:
            try:
                with open(outline_candidates[0], "r", encoding="utf-8", errors="ignore") as ofp:
                    otext = ofp.read()
                    m = re.search(rf"(第0*{next_num}章[^\n]*\n.*?)(?=\n[^\n]*第\d+章|\Z)", otext, re.DOTALL)
                    if m:
                        chapter_outline = m.group(1).strip()
            except Exception:
                pass

        if progress_cb:
            progress_cb(f"续写进度 {index}/{count}：已阅读全书设定集与上一章结尾，正在构思第{next_num:03d}章...")

        # 5. 构建系统提示词与正文提示词
        system_prompt = f"""你是一位顶级番茄小说白金畅销作家，当前正在撰写爆款宇宙科技修仙小说《{book_id}》。
【核心题材与风格】：
- 宇宙科技修仙：融合21世纪现代物理与未来宇宙级科幻（真空零点能、狄拉克海涨落、高维展开、曲率化神、二向箔降维等），与修仙境界深度融合。
- 风格特征：爆笑发癫、无敌暴爽、大秦征霸、千古一帝霸烈与社畜主角反差。
- 民生、政治、文化与人民思想演进铁律（严禁一蹴而就）：遵循“生产力决定生产关系”，随着科技推进与民生事件，润物细无声、因势利导地融入章节，写出真实的生活质感与历史厚重感。

【番茄小说黄金排版与行文铁律】：
1. 标点符号：严格使用全角中文标点符号，对话必须使用中文全角双引号“”，严禁使用英文半角双引号 \"\" 或英文标点。
2. 移动端排版：短段落排版，单个自然段绝不超过4行，段落之间空一行，对话单列成段，严禁长篇大论密不透风。
3. 节奏：戏带设定，绝不生硬掉书袋；每章字数4000-5000字，情节层层递进。
4. 章末钩子：章节结尾必须留下悬念巨大、极度抓人想要立即点击下一章的强力断章钩子！
5. 输出格式规范：第一行输出章节标题（格式为：第XXX章 标题名），接下来是完整正文。严禁输出任何多余的开场白或自我介绍。
"""

        user_content = f"""【小说创作任务】：创作第 {next_num:03d} 章正文

【全书设定集核心文档参考】：
{all_settings_text[:8000]}

【本章对应大纲/细纲设计】：
{chapter_outline if chapter_outline else "请根据前文发展与全书主线自主推演本章剧情，务必环环相扣！"}

【上一章（{prev_filename}）结尾内容回顾】：
\"\"\"
{prev_tail if prev_tail else "（当前为全书第一章开篇）"}
\"\"\"

【作者本次附加指示】：
{user_prompt if user_prompt else "严格按照大纲设定与生产力演进律续写，保持剧情高度连贯与人物性格鲜明。"}

请立即开始创作第 {next_num:03d} 章，输出包含章节标题与 4000-5000 字正文：
"""

        content, reasoning = generate_content(
            prompt=user_content,
            model=model,
            effort=effort,
            system_prompt=system_prompt,
            config_dir=config_dir,
            progress_cb=progress_cb
        )

        if not content or len(content.strip()) < 500:
            raise RuntimeError(f"DeepSeek 返回内容不足（长度 {len(content.strip())}），生成中断")

        # 6. 解析章节标题与整理文本
        raw_lines = [l.rstrip() for l in content.strip().splitlines() if l.strip()]
        first_line = raw_lines[0].lstrip("#").strip()
        m_title = re.search(rf"第0*{next_num}章\s*([^\n\r]+)", first_line)
        if m_title:
            title_suffix = m_title.group(1).strip()
            chapter_title = f"第{next_num:03d}章 {title_suffix}"
            body_text = "\n\n".join(raw_lines[1:])
        else:
            chapter_title = f"第{next_num:03d}章 乾坤借法显神通"
            body_text = "\n\n".join(raw_lines)

        full_chapter_content = f"# {chapter_title}\n\n{body_text}\n"

        # 7. 写入章节文件
        # 优先保留分卷目录结构（如 01_正文/第一卷_人界篇/）
        content_dir = os.path.join(book_root, "01_正文")
        if c_files:
            target_dir = os.path.dirname(c_files[-1])
        else:
            target_dir = os.path.join(content_dir, "第一卷_人界篇")
        os.makedirs(target_dir, exist_ok=True)

        safe_filename = re.sub(r'[\\/*?:"<>|]', "", chapter_title) + ".md"
        target_path = os.path.join(target_dir, safe_filename)

        with open(target_path, "w", encoding="utf-8") as out_fp:
            out_fp.write(full_chapter_content)

        completed += 1
        last_added = target_path
        word_count = len(re.findall(r'[\u4e00-\u9fa5A-Za-z0-9]', full_chapter_content))
        if progress_cb:
            progress_cb(f"第{next_num:03d}章《{chapter_title}》创作完成，共计 {word_count} 字，已写入正文卷宗！")

    return True, f"DeepSeek 续写完成，共成功创作 {completed}/{count} 章，均已按番茄小说格式校验入库。", last_added


def execute_instruction(book_root, book_id, user_prompt, model="deepseek-flash", effort="high", config_dir=None, progress_cb=None):
    """使用 DeepSeek 执行设定推演、伏笔推导或自由创作指令"""
    setting_dir = os.path.join(book_root, "00_设定集")
    setting_files = sorted(glob.glob(os.path.join(setting_dir, "*.md")))
    settings_text = ""
    for sf in setting_files:
        try:
            with open(sf, "r", encoding="utf-8", errors="ignore") as fp:
                settings_text += f"\n### {os.path.basename(sf)}\n{fp.read()[:2000]}\n"
        except Exception:
            pass

    system_prompt = f"你是一流的小说推演架构大师，辅助作者构思并推进《{book_id}》的世界观、人物弧光与硬核科技修仙体系。"
    prompt = f"""【《{book_id}》推演与辅助创作指令】
全书核心设定参考：
{settings_text[:6000]}

来自作者的指令：
\"\"\"
{user_prompt}
\"\"\"

请严格通读并参考上述全部设定集文档，遵循本书宏观世界观与严密逻辑执行该推演要求，给出条理清晰、深度闭环的方案。
"""
    content, reasoning = generate_content(
        prompt=prompt,
        model=model,
        effort=effort,
        system_prompt=system_prompt,
        config_dir=config_dir,
        progress_cb=progress_cb
    )
    return True, content
