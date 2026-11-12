"""Discover the current Antigravity daemon and invoke its installed agentapi."""
import json
import os
import socket
import subprocess
import time
from pathlib import Path

AGENTAPI = Path.home() / "AppData/Local/Programs/antigravity/resources/bin/language_server.exe"
BRAIN = Path.home() / ".gemini/antigravity/brain"


def daemon_environment():
    candidates = sorted((Path.home() / ".gemini/antigravity/daemon").glob("hub_*.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            hub = json.loads(path.read_text(encoding="utf-8"))
            with socket.create_connection(("127.0.0.1", int(hub["httpPort"])), timeout=1):
                env = os.environ.copy()
                env["ANTIGRAVITY_LS_ADDRESS"] = f"127.0.0.1:{hub['httpPort']}"
                env["ANTIGRAVITY_CSRF_TOKEN"] = hub["csrfToken"]
                return env
        except (OSError, ValueError, KeyError):
            continue
    raise RuntimeError("Antigravity 语言服务尚未就绪，请启动客户端后重试")


def invoke(arguments, timeout=90):
    result = subprocess.run([str(AGENTAPI), "agentapi", *arguments], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout,
                            env=daemon_environment(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = result.stdout.strip()
    try:
        payload = json.loads(output)
    except ValueError:
        payload = {}
    if result.returncode or payload.get("error"):
        raise RuntimeError(str(payload.get("error") or result.stderr or output)[:1500])
    return output, payload


def new_conversation(prompt, title, model="gemini-3.8-flash-high"):
    output, payload = invoke(["new-conversation", f"--model={model}", f"--title={title}", prompt])
    response = payload.get("response", payload)
    conversation_id = response.get("conversationId") or payload.get("conversationId")
    if not conversation_id:
        import re
        match = re.search(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", output)
        conversation_id = match.group(0) if match else None
    if not conversation_id:
        raise RuntimeError("Antigravity 未返回会话编号，无法确认任务已接收")
    return conversation_id


def wait_for_receipt(path, conversation_id, progress=None, timeout=1200):
    """A task-specific receipt is written last, after the requested files exist."""
    deadline = time.monotonic() + timeout
    next_report = 0
    while time.monotonic() < deadline:
        if Path(path).is_file():
            try:
                receipt = json.loads(Path(path).read_text(encoding="utf-8"))
                if receipt.get("error"):
                    raise RuntimeError(str(receipt["error"]))
                return receipt
            except json.JSONDecodeError:
                pass
        if progress and time.monotonic() >= next_report:
            progress(f"Antigravity 会话 {conversation_id} 正在执行，等待成果校验…")
            next_report = time.monotonic() + 15
        time.sleep(2)
    raise TimeoutError(f"等待成果超时，会话 {conversation_id} 可在 Antigravity 中继续查看")
