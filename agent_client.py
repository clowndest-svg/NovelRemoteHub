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
            hub = json.loads(path.read_text(encoding="utf-8-sig"))
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


def continue_conversation(conversation_id, prompt, model=None, effort=None):
    catalog = discover()
    chosen, effort = selected_model(catalog, model, effort)
    actual_id = chosen.get("effort_models", {}).get(effort, chosen["id"])
    chosen = next(item for item in catalog["models"] if item["id"] == actual_id)
    metadata = rpc("GetConversationMetadata", {"conversationId": conversation_id}).get("metadata", {})
    summary = rpc("GetAllCascadeTrajectories").get("trajectorySummaries", {}).get(conversation_id, {})
    if metadata.get("projectId") != catalog["project_id"] or summary.get("annotations", {}).get("permissionPreset") != "AGENT_PERMISSION_PRESET_TURBO":
        raise RuntimeError("续接会话的小说工作区或权限校验失败")
    rpc("SendUserCascadeMessage", {"cascadeId": conversation_id, "items": [{"text": prompt}],
        "cascadeConfig": {"plannerConfig": {"requestedModel": {"model": chosen["model_enum"]}}}})
    return conversation_id


def read_receipt(path):
    receipt = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(receipt, dict):
        raise ValueError('执行端完成回执必须为 JSON 对象')
    if receipt.get('error'):
        raise RuntimeError(str(receipt['error']))
    return receipt


SAFETY_FILTER_KEYWORDS = (
    "blocked by gemini's filters",
    "blocked by gemini’s filters",
    "trigger by mistake on safe coding",
    "safety policy",
    "content policy violation",
    "violates our safety guidelines",
    "violates our content policy",
    "i cannot fulfill this request due to safety",
    "harmful content",
)

NETWORK_AND_SERVER_ERROR_KEYWORDS = (
    "user location is not supported",
    "503 service unavailable",
    "502 bad gateway",
    "504 gateway timeout",
    "500 internal server error",
    "connection refused",
    "connection reset",
    "socket hang up",
    "proxy error",
    "network error",
    "failed to connect",
    "econnrefused",
    "econnreset",
    "etimedout",
    "ehostunreach",
    "resource has been exhausted",
    "quota exceeded",
    "rate limit exceeded",
    "an internal error has occurred",
    "error communicating with",
    "无法连接到服务器",
    "代理网络连接失败",
    "网络请求超时",
    "套接字操作",
)


def wait_for_idle(conversation_id, progress=None, timeout=1200):
    """Wait for the pre-restart turn before resending a checkpointed stage."""
    deadline = time.monotonic() + timeout
    next_report = 0
    while time.monotonic() < deadline:
        trajectory = rpc('GetCascadeTrajectory', {'cascadeId': conversation_id}, timeout=10)
        if trajectory.get('status') == 'CASCADE_RUN_STATUS_IDLE':
            return
        if progress and time.monotonic() >= next_report:
            progress(f'正在恢复会话 {conversation_id}，等待重启前的生成结束后续接')
            next_report = time.monotonic() + 15
        time.sleep(2)
    raise TimeoutError('重启前的 Antigravity 会话仍在运行，请核对桌面端进度')


def wait_for_response(conversation_id, marker, progress=None, timeout=1200):
    """Read the final reply belonging to this stage, excluding earlier conversation turns."""
    deadline = time.monotonic() + timeout
    next_report = 0
    last_activity = None
    disconnected_since = None
    connection_error = None
    while time.monotonic() < deadline:
        try:
            trajectory = rpc('GetCascadeTrajectory', {'cascadeId': conversation_id}, timeout=10)
            disconnected_since = None
            connection_error = None
        except (OSError, RuntimeError, ValueError) as error:
            disconnected_since = disconnected_since if disconnected_since is not None else time.monotonic()
            connection_error = str(error)
            if time.monotonic() - disconnected_since >= 60:
                raise RuntimeError(f'Antigravity 本地接口持续无法连接，已停止本阶段。{error}') from error
            trajectory = {}
        steps = trajectory.get('trajectory', {}).get('steps', [])
        boundary = next((i for i in range(len(steps) - 1, -1, -1)
            if steps[i].get('type') == 'CORTEX_STEP_TYPE_USER_INPUT' and any(
                marker in item.get('text', '') for item in steps[i].get('userInput', {}).get('items', []))), None)
        if boundary is not None and steps[boundary + 1:]:
            current = steps[boundary + 1:]
            last = current[-1]
            activity = (len(current), last.get('status'), last.get('metadata', {}).get('completedAt'))
            if activity != last_activity:
                deadline = time.monotonic() + timeout
                last_activity = activity
            if trajectory.get('status') == 'CASCADE_RUN_STATUS_IDLE':
                if last.get('type') == 'CORTEX_STEP_TYPE_ERROR_MESSAGE':
                    error = last.get('errorMessage', {}).get('error', {})
                    message = error.get('modelErrorMessage') or error.get('shortError') or error.get('userErrorMessage') or '执行端已终止任务'
                    raise RuntimeError('Antigravity 执行失败 ' + str(message)[:1000])
                response = last.get('plannerResponse', {}).get('response')
                if isinstance(response, str) and response.strip() and not last.get('plannerResponse', {}).get('toolCalls'):
                    res_str = response.strip()
                    lower = res_str.lower()
                    if any(kw in lower for kw in SAFETY_FILTER_KEYWORDS):
                        raise RuntimeError(f'Antigravity 生成被安全策略拦截: {res_str[:300]}')
                    if len(res_str) < 500 and any(kw in lower for kw in NETWORK_AND_SERVER_ERROR_KEYWORDS):
                        raise RuntimeError(f'Antigravity 上游接口或网络代理临时错误: {res_str[:300]}')
                    return res_str
                raise RuntimeError('Antigravity 本阶段已结束，但未返回完整的正文或 JSON')
        if progress and time.monotonic() >= next_report:
            progress(f'Antigravity 本地接口连接中断，正在重连。{connection_error[:300]}' if connection_error else
                     f'Antigravity 会话 {conversation_id} 正在生成本阶段文本…')
            next_report = time.monotonic() + 15
        time.sleep(2)
    raise TimeoutError(f'Antigravity 阶段长时间未返回新进展，会话 {conversation_id}')


def wait_for_receipt(path, conversation_id, progress=None, timeout=1200):
    """A task-specific receipt is written last, after the requested files exist."""
    deadline = time.monotonic() + timeout
    next_report = 0
    next_status = 0
    invalid_receipt = False
    idle_since = None
    recovery_sent = False
    receipt = None
    last_activity = None
    while time.monotonic() < deadline:
        if Path(path).is_file():
            try:
                receipt = read_receipt(path)
                invalid_receipt = False
            except (json.JSONDecodeError, OSError):
                invalid_receipt = True
        if time.monotonic() >= next_status:
            next_status = time.monotonic() + 10
            try:
                trajectory = rpc("GetCascadeTrajectory", {"cascadeId": conversation_id}, timeout=5)
            except (RuntimeError, OSError, ValueError):
                trajectory = {}
            idle = trajectory.get("status") == "CASCADE_RUN_STATUS_IDLE"
            if invalid_receipt and idle:
                try:
                    receipt = read_receipt(path)
                except json.JSONDecodeError as error:
                    raise ValueError("执行端已结束，但完成回执不是有效 JSON，请核对本轮成果") from error
            steps = trajectory.get("trajectory", {}).get("steps", [])
            if steps:
                last = steps[-1]
                activity = (trajectory.get('numTotalSteps', len(steps)), last.get('status'),
                            last.get('metadata', {}).get('completedAt'))
                if activity != last_activity:
                    deadline = time.monotonic() + timeout
                    last_activity = activity
            if (idle and steps
                    and steps[-1].get("type") == "CORTEX_STEP_TYPE_ERROR_MESSAGE"):
                error = steps[-1].get("errorMessage", {}).get("error", {})
                message = error.get("modelErrorMessage") or error.get("shortError") or error.get("userErrorMessage") or "执行端已终止任务"
                raise RuntimeError("Antigravity 执行失败 " + str(message)[:1000])
            if idle and receipt is not None:
                # The agent can keep editing after writing its receipt. Never start the next turn early.
                return read_receipt(path)
            if idle and steps and steps[-1].get("type") == "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
                idle_since = idle_since if idle_since is not None else time.monotonic()
                if time.monotonic() - idle_since >= 15:
                    if recovery_sent:
                        raise RuntimeError("Agent 已结束但仍未保存成果回执，已停止等待，请核对本轮文件")
                    rpc("SendUserCascadeMessage", {"cascadeId": conversation_id, "items": [{"text":
                        f"上一轮尚未完成文件保存和成果回执。请核对原任务，补齐本轮指定成果文件，并用 JSON 序列化 API 写入回执 {path}。已存在的成果先核对，只补齐原任务，禁止新增下一章或扩大任务范围。"}]})
                    recovery_sent = True
                    idle_since = None
                    if progress:
                        progress("Agent 已停止回复但缺少成果回执，正在原会话补齐保存与回执（一次）")
            else:
                idle_since = None
        if progress and time.monotonic() >= next_report:
            progress(f"Antigravity 会话 {conversation_id} 正在执行，等待成果校验…")
            next_report = time.monotonic() + 15
        time.sleep(2)
    raise TimeoutError(f"等待成果超时，会话 {conversation_id} 可在 Antigravity 中继续查看")
