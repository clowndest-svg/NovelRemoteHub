"""Desktop agent discovery and execution, scoped to the 小说 workspace."""
import time

import agent_client
import codex_client
import deepseek_client
from agent_workspace import canonical_engine


def catalog():
    engines = []
    for identifier, name, discover in [('antigravity', 'Antigravity', agent_client.discover),
                                       ('codex', 'ChatGPT（Codex）', codex_client.CLIENT.discover)]:
        try:
            engines.append(discover())
        except Exception as error:
            engines.append({'id': identifier, 'name': name, 'available': False,
                            'models': [], 'error': str(error)[:500]})
    try:
        ds = deepseek_client.discover()
        if ds.get("available"):
            engines.append(ds)
    except Exception:
        pass
    return {'engines': engines, 'updated_at': time.time()}


def new_conversation(prompt, title, model=None, engine='antigravity', effort=None):
    engine = canonical_engine(engine)
    if engine == 'codex':
        return codex_client.CLIENT.new_conversation(prompt, title, model, effort)
    return agent_client.new_conversation(prompt, title, model, effort)


def continue_conversation(conversation, prompt, model=None, engine='antigravity', effort=None):
    if canonical_engine(engine) == 'codex':
        return codex_client.CLIENT.continue_conversation(conversation, prompt, model, effort)
    return agent_client.continue_conversation(conversation, prompt, model, effort)


def wait_for_receipt(path, conversation, progress=None, engine='antigravity', timeout=1200):
    if canonical_engine(engine) == 'codex':
        codex_client.CLIENT.wait(conversation, progress, timeout)
        return agent_client.wait_for_receipt(path, conversation, None, timeout=5)
    return agent_client.wait_for_receipt(path, conversation, progress, timeout)
