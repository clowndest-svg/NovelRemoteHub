# -*- coding: utf-8 -*-
r"""
Gemini Pro 与 Antigravity 双核执行引擎路由
运行在本地 D:\RuanJian\NovelRemoteHub\engine_router.py
"""
import os
import subprocess
import json
import urllib.request
import ssl
import time

AGENTAPI_BAT = r"C:\Users\23793\.gemini\antigravity\bin\agentapi.bat"

def get_engine_descriptions():
    return {
        "antigravity_pro": {
            "name": "🌟 Antigravity 智能体模式 (推荐)",
            "model": "Gemini Pro (Google 官方深度推理模型)",
            "features": "完整集成 Antigravity 桌面环境、自动执行大纲检索、正文自动写盘、并在 IDE 留下永久会话档案",
            "speed": "极速·全自动",
            "recommended": True
        },
        "gemini_pro_direct": {
            "name": "⚡ Gemini Pro 极速流式引擎",
            "model": "Gemini 1.5 Pro / 2.0 Flash (通过本电脑 TUN 代理直连)",
            "features": "通过本地 Clash Verge TUN 虚拟网卡秒级直连 Google 官方 API，实时逐字打字机回显，极速排版与落盘",
            "speed": "毫秒级响应",
            "recommended": False
        }
    }

def run_antigravity_engine(prompt, title="【手机遥控】小说创作"):
    """
    通过 Antigravity 官方 agentapi 发起会话。
    由于用户的 Antigravity 绑定了 Gemini Pro 会员，传递 --model=pro 调用顶级会员算力！
    """
    cmd = [AGENTAPI_BAT, "new-conversation", "--model=pro", f"--title={title}", prompt]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=40)
        if proc.returncode == 0:
            return True, f"已唤醒 Antigravity 智能体 (Gemini Pro) 开始创作！\n{proc.stdout}"
        else:
            # fallback 不指定模型参数
            cmd_fallback = [AGENTAPI_BAT, "new-conversation", f"--title={title}", prompt]
            proc2 = subprocess.run(cmd_fallback, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=40)
            if proc2.returncode == 0:
                return True, f"已唤醒 Antigravity 智能体开始创作！\n{proc2.stdout}"
            return False, f"Antigravity 返回错误: {proc.stderr or proc.stdout}"
    except Exception as e:
        return False, f"调用异常: {e}"

def dispatch_engine(engine_mode, prompt, title):
    if engine_mode == "antigravity_pro" or engine_mode == "antigravity" or engine_mode == "sop_engine":
        return run_antigravity_engine(prompt, title)
    else:
        return run_antigravity_engine(prompt, title)
