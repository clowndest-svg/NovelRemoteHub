import os
import json
import glob

NOVEL_ROOT = r"D:\小说\给政哥讲量子力学，他竟肉身硬抗二向箔"
BRAIN_CONV_ID = "be7c9c60-1b07-4a49-8927-f9c527d4f9f4"
TRANSCRIPT_PATH = rf"C:\Users\23793\.gemini\antigravity\brain\{BRAIN_CONV_ID}\.system_generated\logs\transcript.jsonl"

def get_chapters():
    chapters_dir = os.path.join(NOVEL_ROOT, "01_正文", "第一卷_人界篇")
    if not os.path.exists(chapters_dir):
        return []
    files = glob.glob(os.path.join(chapters_dir, "*.md"))
    files.sort()
    result = []
    for f in files:
        fname = os.path.basename(f)
        size = os.path.getsize(f)
        with open(f, "r", encoding="utf-8", errors="ignore") as fp:
            content = fp.read()
        char_count = len(content)
        title = fname.replace(".md", "")
        result.append({
            "filename": fname,
            "title": title,
            "char_count": char_count,
            "size": size,
            "mtime": os.path.getmtime(f)
        })
    return result

def get_settings():
    settings_dir = os.path.join(NOVEL_ROOT, "00_设定集")
    if not os.path.exists(settings_dir):
        return []
    files = glob.glob(os.path.join(settings_dir, "*.md"))
    files.sort()
    result = []
    for f in files:
        fname = os.path.basename(f)
        result.append({
            "filename": fname,
            "title": fname.replace(".md", ""),
            "size": os.path.getsize(f),
            "mtime": os.path.getmtime(f)
        })
    return result

def get_conversations(limit=30):
    if not os.path.exists(TRANSCRIPT_PATH):
        return []
    history = []
    with open(TRANSCRIPT_PATH, "r", encoding="utf-8", errors="ignore") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                src = data.get("source")
                step_type = data.get("type")
                created_at = data.get("created_at")
                content = data.get("content", "")
                thinking = data.get("thinking", "")
                tool_calls = data.get("tool_calls", [])
                
                # 用户输入
                if src == "USER_EXPLICIT" or step_type == "USER_INPUT":
                    history.append({
                        "role": "user",
                        "time": created_at,
                        "text": content
                    })
                elif src == "MODEL" and (content or thinking or tool_calls):
                    # 如果有内容或者工具调用
                    history.append({
                        "role": "assistant",
                        "time": created_at,
                        "text": content,
                        "thinking": thinking[:300] if thinking else "",
                        "tools": [t.get("name") for t in tool_calls] if tool_calls else []
                    })
            except Exception:
                continue
    return history[-limit:]

if __name__ == "__main__":
    ch = get_chapters()
    st = get_settings()
    cv = get_conversations(5)
    print(f"Chapters count: {len(ch)}")
    for c in ch[:3]:
        print(" -", c["title"], f"({c['char_count']} 字)")
    print(f"Settings count: {len(st)}")
    for s in st[:3]:
        print(" -", s["title"])
    print(f"Recent conversations count: {len(cv)}")