# -*- coding: utf-8 -*-
r"""
DeepSeek 官方 API 客户端与创作引擎 (DeepSeek V4.1-Flash / V4-Pro)
官方 API 文档规范: https://api-docs.deepseek.com/zh-cn/
Base URL: https://api.deepseek.com
提供：
1. 官方规范最新模型架构与目录 (deepseek-flash / deepseek-v4-pro / deepseek-chat / deepseek-reasoner)
2. 思考模式 (Thinking Mode) 深度集成，完整捕获 reasoning_content 思维链
3. 官方 API Key 连通性在线检测
4. 手机端全天候离线免电脑直连创作
"""
import os
import json
import urllib.request
import urllib.error
import ssl

DEFAULT_BASE_URL = "https://api.deepseek.com"

# 严格对齐官方文档最新模型规范
DEEPSEEK_MODELS = [
    {
        "id": "deepseek-flash",
        "name": "deepseek-flash (DeepSeek-V4.1-Flash 官方主力 · 1M 上下文)",
        "is_default": True,
        "disabled": False,
        "efforts": [
            {"id": "high", "name": "深度思考模式 (Thinking 开启)"},
            {"id": "none", "name": "极速非思考模式 (Thinking 关闭)"}
        ],
        "default_effort": "high"
    },
    {
        "id": "deepseek-v4-pro",
        "name": "deepseek-v4-pro (DeepSeek-V4-Pro 旗舰专业 · 宏大架构世界观)",
        "is_default": False,
        "disabled": False,
        "efforts": [
            {"id": "high", "name": "深度思考模式 (Thinking 开启)"},
            {"id": "none", "name": "标准创作模式 (Thinking 关闭)"}
        ],
        "default_effort": "high"
    },
    {
        "id": "deepseek-chat",
        "name": "deepseek-chat (通用对话兼容模型)",
        "is_default": False,
        "disabled": False,
        "efforts": [
            {"id": "none", "name": "标准对话"}
        ],
        "default_effort": "none"
    },
    {
        "id": "deepseek-reasoner",
        "name": "deepseek-reasoner (深度逻辑推理经典模型)",
        "is_default": False,
        "disabled": False,
        "efforts": [
            {"id": "high", "name": "深度推理链"}
        ],
        "default_effort": "high"
    }
]


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
        "default_model": settings.get("deepseek_default_model", "deepseek-flash")
    }


def test_api_key(api_key, base_url=None):
    if not api_key or not api_key.strip():
        return False, "API Key 不能为空，请在设置中粘贴您的 sk- 开头的密钥"
    
    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/chat/completions"
    payload = {
        "model": "deepseek-flash",
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 5
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key.strip()}"
    })
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if "choices" in body and body["choices"]:
                return True, "DeepSeek 官方 API 认证成功，Key 完全有效！"
            return False, "DeepSeek 接口响应格式异常"
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8", errors="ignore")
        try:
            err_json = json.loads(err_body)
            msg = err_json.get("error", {}).get("message", err_body)
        except Exception:
            msg = err_body
        return False, f"DeepSeek 官方返回错误 (HTTP {err.code}): {msg[:250]}"
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
        "capabilities": {"text": True, "images": False},
        "permission_mode": "full-access"
    }


def generate_content(prompt, model="deepseek-flash", effort=None, system_prompt=None, config_dir=None, progress_cb=None):
    cfg = get_deepseek_config(config_dir)
    if not cfg["api_key"]:
        raise ValueError("DeepSeek API Key 未配置，请在【系统设置】中输入您的 Key")
    
    url = f"{cfg['base_url']}/chat/completions"
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    target_model = model or cfg["default_model"] or "deepseek-flash"

    payload = {
        "model": target_model,
        "messages": messages,
        "stream": False
    }

    # 官方文档 Thinking Mode 思考模式参数控制
    is_reasoning_model = (target_model in ("deepseek-reasoner",))
    is_thinking_enabled = (effort == "high" or is_reasoning_model or effort is None)

    if target_model in ("deepseek-flash", "deepseek-v4-pro"):
        if is_thinking_enabled and effort != "none":
            payload["thinking"] = {"type": "enabled"}
            payload["temperature"] = 1.0
        else:
            payload["thinking"] = {"type": "disabled"}
            payload["temperature"] = 1.0

    if progress_cb:
        thinking_tag = "思考模式 (Thinking)" if is_thinking_enabled else "极速创作模式"
        progress_cb(f"已连接 DeepSeek 官方 API，正在调用 {target_model}（{thinking_tag}）构思并撰写正文...")

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg['api_key']}"
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
            progress_cb(f"【DeepSeek 思维链推理已就绪】\n{reasoning[:300]}...")
        return content, reasoning
