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
            "name": "Antigravity 智能体",
            "model": "按任务所选模型配置执行",
            "features": "通过本地客户端读取设定、执行创作并保存任务成果回执",
            "recommended": True
        },
        "gemini_pro_direct": {
            "name": "兼容旧版任务入口",
            "model": "通过 Antigravity 执行",
            "features": "已有任务继续由本地 Antigravity 客户端承接",
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
