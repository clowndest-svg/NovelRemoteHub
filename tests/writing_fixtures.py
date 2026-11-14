"""Isolated upstream responses for the multi-stage writing protocol."""
import json
import re


def response(prompt):
    if '【阶段 更新记忆】' in prompt:
        return json.dumps({'summary': '原文事件与线索', 'synopsis': '故事按原文继续发展',
            'characters': {'主角': '位置与认知见本章'},
            'threads': [{'id': '开篇约定', 'status': 'open', 'detail': '原文约定尚未兑现'}]}, ensure_ascii=False)
    if '【阶段 本章计划】' in prompt:
        return json.dumps({'plan': '承接前章，核对人物位置与认知，继续主线', 'sources': []}, ensure_ascii=False)
    if '【阶段 一致性审核】' in prompt:
        return json.dumps({'passed': True, 'issues': []})
    number = int(re.search(r'本轮只创作第(\d+)章', prompt).group(1))
    return f'# 第{number:03d}章 延续\n\n' + '正文承接前情，继续追查线索。' * 80


def staged_generation(draft):
    def generate(prompt, *args, **kwargs):
        if '【阶段 生成草稿】' in prompt or '【阶段 修订草稿】' in prompt:
            return draft(prompt, *args, **kwargs)
        return response(prompt), ''
    return generate
