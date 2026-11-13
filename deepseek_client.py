# -*- coding: utf-8 -*-
r"""
DeepSeek 官方 API 客户端与创作引擎 (DeepSeek V3 / R1)
支持官方 OpenAI 兼容接口: https://api.deepseek.com
提供：
1. 模型发现与动态目录注入 (deepseek-chat / deepseek-reasoner)
2. 深度推理思维链 (reasoning_content) 实时捕获与进度上报
3. API Key 连通性在线检测
4. 离线/免电脑直连云端独立创作
"""
import os
import json
import urllib.request
import urllib.error
import ssl

DEFAULT_BASE_URL = "https://api.deepseek.com"

DEEPSEEK_MODELS = [
    {
        "id": "deepseek-chat",
        "name": "DeepSeek-V3 (极速满血版 671B · 文学润色)",
        "is_default": True,
        "disabled": False,
        "efforts": [{"id": "medium", "name": "标准创作"}],
        "default_effort": "medium"
    },
    {
        "id": "deepseek-reasoner",
        "name": "DeepSeek-R1 (深度思维链推理 · 伏笔与战力统筹)",
        "is_default": False,
        "disabled": False,
        "efforts": [{"id": "high", "name": "深度推演"}],
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
        "default_model": settings.get("deepseek_default_model", "deepseek-chat")
    }


def test_api_key(api_key, base_url=None):
    if not api_key or not api_key.strip():
        return False, "API Key 不能为空"
    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/chat/completions"
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "Ping"}],
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
            if "choices" in body:
                return True, "DeepSeek 官方 API 连通正常，Key 有效！"
            return False, "响应格式异常"
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8", errors="ignore")
        try:
            err_json = json.loads(err_body)
            msg = err_json.get("error", {}).get("message", err_body)
        except Exception:
            msg = err_body
        return False, f"DeepSeek 接口报错 (HTTP {err.code}): {msg[:200]}"
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
            "error": "DeepSeek 官方 API 未启用。请在系统设置中填入 API Key 并开启开关。"
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


def generate_content(prompt, model="deepseek-chat", system_prompt=None, config_dir=None, progress_cb=None):
    cfg = get_deepseek_config(config_dir)
    if not cfg["api_key"]:
        raise ValueError("DeepSeek API Key 未配置，请在【系统设置】中输入您的 Key")
    
    url = f"{cfg['base_url']}/chat/completions"
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model or cfg["default_model"],
        "messages": messages,
        "temperature": 0.85 if "reasoner" not in (model or "") else None,
        "stream": False
    }
    # 移除 None 属性
    payload = {k: v for k, v in payload.items() if v is not None}

    if progress_cb:
        progress_cb(f"已连接 DeepSeek 官方 API，正在调用 {model} 执行深度创作推演...")

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg['api_key']}"
    })
    ctx = ssl.create_default_context()
    
    with urllib.request.urlopen(req, context=ctx, timeout=180) as resp:
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
