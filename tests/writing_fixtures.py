"""Isolated upstream responses for the multi-stage writing protocol."""
import json
import re


def response(prompt):
    if '【阶段 批量更新记忆】' in prompt:
        keys = re.findall(r'【片段 id=([a-f0-9]+)】', prompt)
        records = [dict(json.loads(response('【阶段 更新记忆】')), id=key) for key in keys]
        return json.dumps({'records': records}, ensure_ascii=False)
    if '【阶段 更新记忆】' in prompt:
        return json.dumps({'summary': '原文事件与线索', 'synopsis': '故事按原文继续发展',
            'characters': {'主角': '位置与认知见本章'},
            'threads': [{'id': '开篇约定', 'status': 'open', 'detail': '原文约定尚未兑现'}]}, ensure_ascii=False)
    if '【阶段 本章计划】' in prompt:
        return json.dumps({'plan': '承接前章，核对人物位置与认知，继续主线', 'sources': []}, ensure_ascii=False)
    if '【阶段 一致性审核】' in prompt:
        return json.dumps(dict(json.loads(response('【阶段 更新记忆】')), passed=True, issues=[]), ensure_ascii=False)
    if '【分章大纲自动推演与补全指令】' in prompt or prompt.startswith('【《') and '分章大纲' in prompt:
        m = re.search(r'第 (\d+) 至 第 (\d+) 章', prompt)
        start = int(m.group(1)) if m else 1
        end = int(m.group(2)) if m else start
        outlines = []
        for c in range(start, end + 1):
            outlines.append(f"### 第{c:03d}章 规划章节{c}\n- **时间与核心场景**：码头旧地\n- **核心剧情推进与主要冲突**：追查当年隐秘线索与各方势力冲突\n- **情绪爽点与反差伏笔**：主角从容破局展现惊人推演能力\n- **章末强力断章悬念钩子**：暗中窥伺之人终于现身！\n")
        return "\n".join(outlines)
    number = int(re.search(r'本轮只创作第(\d+)章', prompt).group(1))
    target = re.search(r'目标(\d+)-(\d+)字', prompt)
    length = int(target.group(1)) if target else 1120
    body = ('正文承接前情，继续追查线索。' * (length // 14 + 1))[:length]
    return f'# 第{number:03d}章 延续\n\n' + body


def staged_generation(draft):
    def generate(prompt, *args, **kwargs):
        if '【阶段 生成草稿】' in prompt or '【阶段 修订草稿】' in prompt:
            return draft(prompt, *args, **kwargs)
        return response(prompt), ''
    return generate
