"""Workspace identity shared by the two desktop integrations."""
import ntpath
from urllib.parse import unquote, urlparse

WORKSPACE_ROOT = r'D:\小说'
WORKSPACE_NAME = '小说'


def normalized_path(value):
    value = unquote(str(value))
    if value.startswith('file:'):
        value = urlparse(value).path
        if len(value) > 2 and value[0] == '/' and value[2] == ':':
            value = value[1:]
    return ntpath.normcase(ntpath.normpath(value))


def is_workspace(value):
    return normalized_path(value) == normalized_path(WORKSPACE_ROOT)


def choose_project(projects, roots):
    matches = [project for project in projects if project.get('name') == WORKSPACE_NAME
               and any(is_workspace(path) for path in roots(project))]
    if len(matches) != 1:
        raise RuntimeError('请在桌面客户端保留唯一的“小说”项目，并绑定 D:\\小说')
    return matches[0]


def canonical_engine(value):
    if value in ('antigravity', 'antigravity_pro', 'sop_engine', 'gemini_pro_direct'):
        return 'antigravity'
    if value in ('codex', 'chatgpt'):
        return 'codex'
    if value in ('deepseek', 'ds', 'deepseek_chat', 'deepseek_reasoner'):
        return 'deepseek'
    raise ValueError('未知执行类型，请重新选择')


def selected_model(catalog, model_id=None, effort=None):
    models = [model for model in catalog.get('models', []) if not model.get('disabled')]
    if not catalog.get('available') or not models:
        raise ValueError(catalog.get('error') or '当前类型尚未连接，请刷新模型列表')
    model = next((item for item in models if item['id'] == model_id), None) if model_id else next((item for item in models if item.get('is_default')), models[0])
    if not model:
        raise ValueError('所选模型已不可用，请刷新模型列表')
    effort = effort or model.get('default_effort', 'default')
    if effort not in [item['id'] for item in model.get('efforts', [])]:
        raise ValueError('所选模型不支持此思考等级，请重新选择')
    return model, effort
