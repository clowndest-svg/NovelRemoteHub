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
    from local_bridge import execute_agentapi
    return execute_agentapi(prompt, title)


def dispatch_engine(engine_mode, prompt, title):
    if engine_mode == "antigravity_pro" or engine_mode == "antigravity" or engine_mode == "sop_engine":
        return run_antigravity_engine(prompt, title)
    else:
        return run_antigravity_engine(prompt, title)
