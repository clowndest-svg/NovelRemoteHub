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
import json
import urllib.request
import urllib.error
import ssl
from pathlib import Path
import uuid
from contextlib import nullcontext
from storage import atomic_write
from sync_protocol import book_snapshot

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
    if not isinstance(api_key, str) or not api_key.strip():
        return False, "API Key 不能为空"
    request = urllib.request.Request((base_url or DEFAULT_BASE_URL).rstrip("/") + "/user/balance",
        headers={"Authorization": "Bearer " + api_key.strip()})
    try:
        with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=12) as response:
            result = json.load(response)
        balances = result.get("balance_infos", [])
        detail = "；".join(f"{item.get('currency', '')} {item.get('total_balance', '0')}" for item in balances)
        available = result.get("is_available") is True
        return available, ("DeepSeek 认证成功，账户正常可用" if available else "DeepSeek 认证成功，账户余额不足") + ("，总余额 " + detail if detail else "")
    except urllib.error.HTTPError as error:
        with error:
            return False, f"余额查询失败，HTTP {error.code}，请检查密钥与服务地址"
    except (OSError, ValueError) as error:
        return False, "余额查询失败，请检查网络与服务地址"


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
        "capabilities": {"text": True, "images": False},
        "permission_mode": "full-access"
    }


def generate_content(prompt, model="deepseek-flash", effort="high", system_prompt=None, config_dir=None, progress_cb=None):
    """
    调用 DeepSeek 官方 Chat Completions API 进行文本生成与逻辑推理。
    严格支持 Thinking Mode 思考模式参数与 reasoning_content 思维链捕获。
    """
    cfg = get_deepseek_config(config_dir)
    if not cfg["enabled"]:
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
        if choices[0].get("finish_reason") not in (None, "stop"):
            raise RuntimeError("模型返回未完成内容，原因 " + str(choices[0].get("finish_reason")))
        msg = choices[0].get("message", {})
        reasoning = msg.get("reasoning_content", "")
        content = msg.get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("DeepSeek 返回正文为空")
        return content, reasoning


def writing_material(book_root, config_dir=None, query="作品整体发展", model="deepseek-flash", effort="high", progress_cb=None):
    import novel_manager
    import story_memory
    preferences = novel_manager.get_user_settings(config_dir)
    files = novel_manager.chapter_files(book_root)
    number = max((int(match.group(1)) for filename in files
                  if (match := re.search(r"第(\d+)章", Path(filename).name))), default=0) + 1
    def generate(prompt):
        return generate_content(prompt, model, effort, config_dir=config_dir, progress_cb=progress_cb)[0]
    material = story_memory.instruction_material(Path(book_root).resolve(), query, preferences, generate, progress_cb or (lambda message: None))
    return material, number, files, preferences


def write_serial_chapters(book_root, book_id, count=1, user_prompt="", model="deepseek-flash", effort="high", config_dir=None, progress_cb=None, write_lock=None, on_chapter=None):
    import novel_manager
    import story_memory
    preferences = novel_manager.get_user_settings(config_dir)
    def generate(prompt):
        content, _ = generate_content(prompt, model, effort, "遵循作品来源与任务要求。结构化阶段仅返回要求的 JSON。", config_dir, progress_cb)
        return content
    return story_memory.write_chapters(book_root, count, user_prompt, preferences, generate,
        progress_cb or (lambda message: None), write_lock, on_chapter)


def execute_instruction(book_root, book_id, user_prompt, model="deepseek-flash", effort="high", config_dir=None, progress_cb=None):
    material, _, _, _ = writing_material(book_root, config_dir, user_prompt, model, effort, progress_cb)
    content, _ = generate_content(f"【作品资料与前章】\n{material}\n【作者要求】\n{user_prompt}", model, effort,
                                 f"你正在辅助创作《{book_id}》，严格遵循本书题材、人物与设定。", config_dir, progress_cb)
    return True, content
