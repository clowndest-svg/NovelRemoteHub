# -*- coding: utf-8 -*-
r"""
通用多书架管理与番茄小说自动化发布工作流引擎
支持本地 (Windows) 与云端 (Linux) 双向通用
"""
import os
import glob
import re
import json
import zipfile
import io
import datetime

# 自动判断当前是在本地 Windows 还是云端 Linux
if os.name == "nt":
    DEFAULT_ROOT = r"D:\小说"
else:
    DEFAULT_ROOT = "/root/novel_hub_data/books"

os.makedirs(DEFAULT_ROOT, exist_ok=True)

def list_books(root_dir=None):
    base = root_dir or DEFAULT_ROOT
    if not os.path.exists(base):
        return []
    books = []
    for entry in os.scandir(base):
        if entry.is_dir():
            bpath = entry.path
            chapters = glob.glob(os.path.join(bpath, "01_正文", "**", "*.md"), recursive=True)
            settings = glob.glob(os.path.join(bpath, "00_设定集", "*.md"))
            books.append({
                "id": entry.name,
                "title": entry.name,
                "path": bpath,
                "chapter_count": len(chapters),
                "settings_count": len(settings),
                "has_outline": os.path.exists(os.path.join(bpath, "00_设定集"))
            })
    return books

def get_book_path(book_id=None, root_dir=None):
    base = root_dir or DEFAULT_ROOT
    books = list_books(base)
    if not book_id:
        if books:
            return books[0]["path"], books[0]["title"]
        def_path = os.path.join(base, "给政哥讲量子力学，他竟肉身硬抗二向箔")
        return def_path, "给政哥讲量子力学，他竟肉身硬抗二向箔"
    
    bpath = os.path.join(base, book_id)
    if os.path.exists(bpath):
        return bpath, book_id
    if books:
        return books[0]["path"], books[0]["title"]
    return os.path.join(base, book_id), book_id

def clean_for_tomato(markdown_text, chapter_filename=""):
    """
    番茄小说发布格式深度清洗器：
    1. 去除 Markdown 标题符号 (#, ##, ### 等)
    2. 去除加粗 (**text** -> text), 斜体, 删除线
    3. 去除引用块符号 (>)
    4. 规范化段落：每段首行缩进两个全角空格 (\u3000\u3000)
    5. 规范化标点：英文双引号自动纠偏为中文全角双引号 “”，英文逗号句号感叹号纠偏
    6. 提取标准番茄规范标题：第XXX章 章节标题 (去掉下划线与多余前缀)
    """
    raw_lines = markdown_text.splitlines()
    clean_lines = []
    
    standard_title = ""
    if chapter_filename:
        base = os.path.basename(chapter_filename).replace(".md", "")
        m = re.match(r"(第\d+章)[_：:\s]*(.*)", base)
        if m:
            standard_title = f"{m.group(1)} {m.group(2)}"
        else:
            standard_title = base.replace("_", " ")

    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        
        # 过滤书名大标题与分卷名
        if line.startswith("# 《") or line.startswith("# "):
            continue
        if line.startswith("## 第一卷") or line.startswith("## "):
            continue
        if line.startswith("### 第") or line.startswith("## 第"):
            clean_t = re.sub(r"^[#\s]+", "", line).replace("：", " ").replace(":", " ").replace("_", " ")
            if not standard_title:
                standard_title = clean_t
            continue
        
        # 清除分割线
        if re.match(r"^[-*_]{3,}$", line):
            continue

        # 清除 Markdown 格式
        text = line
        text = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", text)
        text = re.sub(r"^>\s*", "", text)
        text = re.sub(r"`(.*?)`", r"\1", text)

        # 标点符号规范化：英文双引号成对转为中文全角引号
        parts = text.split('"')
        if len(parts) > 1:
            fixed_text = ""
            for i, part in enumerate(parts):
                fixed_text += part
                if i < len(parts) - 1:
                    fixed_text += "“" if i % 2 == 0 else "”"
            text = fixed_text

        text = text.replace(",", "，").replace("?", "？").replace("!", "！")

        # 首行缩进两全角空格
        text = text.strip()
        if text:
            clean_lines.append("\u3000\u3000" + text)

    clean_content = "\n\n".join(clean_lines)
    pure_chars = len(clean_content) - clean_content.count("\u3000") - clean_content.count("\n")
    
    return {
        "title": standard_title,
        "content": clean_content,
        "char_count": pure_chars,
        "line_count": len(clean_lines)
    }

def audit_for_tomato(cleaned_result):
    """番茄小说发布质量与过审检测"""
    char_count = cleaned_result["char_count"]
    content = cleaned_result["content"]
    title = cleaned_result["title"]
    
    issues = []
    status = "pass"
    
    # 1. 字数判定 (番茄黄金区间：3000 - 5000 字)
    if char_count < 2000:
        issues.append({"level": "danger", "msg": f"当前字数仅 {char_count} 字，低于番茄最低签约与推荐线(2000字)，建议充实剧情！"})
        status = "warn"
    elif char_count < 3000:
        issues.append({"level": "info", "msg": f"当前字数 {char_count} 字，达到基本要求，若充实到 3500+ 字更易上榜。"})
    elif char_count > 6500:
        issues.append({"level": "warn", "msg": f"当前字数 {char_count} 字，单章偏长，可考虑拆分为两章发布。"})

    # 2. 引号闭合检查
    open_quotes = content.count("“")
    close_quotes = content.count("”")
    if open_quotes != close_quotes:
        issues.append({"level": "danger", "msg": f"双引号未完全成对闭合 (前引号 {open_quotes} 个，后引号 {close_quotes} 个)，可能影响读者阅读质感！"})
        status = "warn"

    # 3. 常见敏感词初筛
    sensitive_words = ["文革", "暴动", "迷药", "黄色", "邪教", "国家领导人"]
    found_sensitive = [w for w in sensitive_words if w in content]
    if found_sensitive:
        issues.append({"level": "danger", "msg": f"检测到可能触发番茄机审复核词汇：{', '.join(found_sensitive)}，建议避讳替换！"})
        status = "warn"

    if "_" in title:
        issues.append({"level": "warn", "msg": "章节标题中含有下划线 '_'，番茄后台不允许特殊字符，已自动清洗。"})

    return {
        "status": status,
        "issues": issues,
        "char_count": char_count,
        "ready_to_publish": (status != "danger")
    }

def export_tomato_zip(book_id=None, root_dir=None):
    """打包生成番茄小说批量发布专用的分章 TXT 压缩包"""
    bpath, btitle = get_book_path(book_id, root_dir)
    chapters_dir = os.path.join(bpath, "01_正文")
    files = sorted(glob.glob(os.path.join(chapters_dir, "**", "*.md"), recursive=True))

    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        readme = f"《{btitle}》番茄小说发布规范专用分章包\n生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n总章节数：{len(files)}\n已自动去除Markdown标记，符合每段缩进两格标准。\n"
        zf.writestr("番茄发布说明.txt", readme.encode("utf-8"))

        for f in files:
            with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                raw_text = fp.read()
            cleaned = clean_for_tomato(raw_text, os.path.basename(f))
            fname = f"{cleaned['title']}.txt" if cleaned['title'] else os.path.basename(f).replace(".md", ".txt")
            file_body = f"{cleaned['title']}\n\n{cleaned['content']}\n"
            zf.writestr(fname, file_body.encode("utf-8"))

    mem_zip.seek(0)
    return mem_zip.getvalue(), f"{btitle}_番茄发布包.zip"
