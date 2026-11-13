"""Discover the current Antigravity daemon and invoke its installed agentapi."""
import json
import os
import socket
import subprocess
import time
from pathlib import Path
import re
import urllib.request
from agent_workspace import WORKSPACE_ROOT, choose_project, selected_model

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


def rpc(method, params=None, timeout=30):
    env = daemon_environment()
    request = urllib.request.Request(
        "http://" + env["ANTIGRAVITY_LS_ADDRESS"] + "/exa.language_server_pb.LanguageServerService/" + method,
        data=json.dumps(params or {}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-codeium-csrf-token": env["ANTIGRAVITY_CSRF_TOKEN"]})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        with error:
            try:
                message = json.load(error).get("message", str(error.code))
            except ValueError:
                message = f"HTTP {error.code}"
        raise RuntimeError("Antigravity 接口错误 " + message) from error


def discover():
    ids = rpc("ListProjects").get("projectIds", [])
    projects = rpc("ReadProjects", {"ids": ids}).get("projects", [])
    project = choose_project(projects, lambda item: [resource.get("folderUri", "")
        for resource in item.get("projectResources", {}).get("resources", [])])
    configs = rpc("GetUserStatus").get("userStatus", {}).get("cascadeModelConfigData", {}).get("clientModelConfigs", [])
    models = []
    for config in configs:
        identifier = config.get("modelId")
        model_enum = config.get("modelOrAlias", {}).get("model")
        if not identifier or not model_enum:
            continue
        variant = re.search(r"-(high|medium|low)$", identifier)
        effort = variant.group(1) if variant else "default"
        models.append({"id": identifier, "name": config.get("label", identifier), "disabled": bool(config.get("disabled")),
                       "model_enum": model_enum, "default_effort": effort,
                       "efforts": [{"id": effort, "name": effort if variant else "模型内置思考"}],
                       "is_default": not models, "quota_remaining": config.get("quotaInfo", {}).get("remainingFraction")})
    for model in models:
        variant = re.match(r"^(.*)-(high|medium|low)$", model["id"])
        if variant:
            family = variant.group(1)
            choices = {other["default_effort"]: other["id"] for other in models
                       if not other["disabled"] and re.match(r"^(.*)-(high|medium|low)$", other["id"])
                       and other["id"].rsplit("-", 1)[0] == family}
            model["effort_models"] = choices
            model["efforts"] = [{"id": effort, "name": effort} for effort in choices]
    if not any(not model["disabled"] for model in models):
        raise RuntimeError("Antigravity 尚未返回可用模型")
    return {"id": "antigravity", "name": "Antigravity", "available": True,
            "project_id": project["id"], "workspace": WORKSPACE_ROOT, "project_name": "小说",
            "models": models, "capabilities": {"images": True, "text": True}, "permission_mode": "full-access"}


def new_conversation(prompt, title, model="gemini-3.8-flash-high", effort=None):
    catalog = discover()
    chosen, effort = selected_model(catalog, model, effort)
    actual_id = chosen.get("effort_models", {}).get(effort, chosen["id"])
    chosen = next(item for item in catalog["models"] if item["id"] == actual_id)
    project_id = catalog["project_id"]
    # Allocate in the target project, verify it, and set permissions before sending the task.
    response = rpc("StartCascade", {"source": "CORTEX_TRAJECTORY_SOURCE_AGENT_API",
        "requestedModel": chosen["model_enum"], "workspaceUris": ["file:///" + urllib.parse.quote(WORKSPACE_ROOT.replace("\\", "/"), safe="/:")],
        "projectEnvConfig": {"projectId": project_id}})
    conversation_id = response["cascadeId"]
    metadata = rpc("GetConversationMetadata", {"conversationId": conversation_id}).get("metadata", {})
    if metadata.get("projectId") != project_id:
        raise RuntimeError("会话工作区校验失败，任务尚未发送")
    rpc("UpdateConversationAnnotations", {"cascadeId": conversation_id,
        "annotations": {"title": title, "permissionPreset": "AGENT_PERMISSION_PRESET_TURBO"}, "mergeAnnotations": True})
    summary = rpc("GetAllCascadeTrajectories").get("trajectorySummaries", {}).get(conversation_id, {})
    if summary.get("annotations", {}).get("permissionPreset") != "AGENT_PERMISSION_PRESET_TURBO":
        raise RuntimeError("会话完全访问权限校验失败，任务尚未发送")
    rpc("SendUserCascadeMessage", {"cascadeId": conversation_id, "items": [{"text": prompt}],
        "cascadeConfig": {"plannerConfig": {"requestedModel": {"model": chosen["model_enum"]}}}})
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
