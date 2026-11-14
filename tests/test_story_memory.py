import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import novel_manager
import story_memory as memory
from storage import atomic_write, atomic_create
from writing_fixtures import response


class StoryMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / '作品'
        atomic_write(self.root / '00_设定集/规则.md', '主角不能飞行。旧约定在码头兑现。')
        atomic_write(self.root / '01_正文/第一卷/第001章 起点.md', '# 第001章 起点\n\n主角在码头留下约定。')
        self.preferences = {'writing_context_chars': 64000, 'writing_context_mode': 'layered'}
        self.requests = []

    def generate(self, prompt):
        self.requests.append(prompt)
        return response(prompt)

    def write(self, count=1, generate=None):
        return memory.write_chapters(self.root, count, '承接码头约定', self.preferences,
            generate or self.generate, lambda message: None)

    def test_pipeline_plan_retrieval_audit_publish_and_memory(self):
        ok, _, target = self.write(2)
        self.assertTrue(ok)
        self.assertIn('第003章', target)
        stages = [request.splitlines()[0] for request in self.requests]
        self.assertEqual(stages.count('【阶段 本章计划】'), 2)
        self.assertEqual(stages.count('【阶段 一致性审核】'), 2)
        self.assertTrue(memory.status(self.root)['ready'])
        reviews = list((self.root / '.story-memory/reviews').glob('*.json'))
        self.assertEqual(len(reviews), 2)
        self.assertTrue(all(json.loads(path.read_text(encoding='utf-8'))['status'] == 'published' for path in reviews))
        self.assertTrue(memory.status(self.root)['last_review']['references'])

    def test_ten_chapters_share_one_batch_and_audit_automatically(self):
        self.write(10)
        reviews = memory.status(self.root)['reviews']
        self.assertEqual(len(reviews), 10)
        self.assertEqual(len({record['batch'] for record in reviews}), 1)
        self.assertTrue(all(record['batch_count'] == 10 and record['status'] == 'published' for record in reviews))
        self.assertEqual(sum('【阶段 一致性审核】' in request for request in self.requests), 10)

    def test_other_books_never_enter_context_or_memory(self):
        other = self.root.parent / '另一部小说'
        atomic_write(other / '00_设定集/规则.md', '另书设定绝密标记')
        atomic_write(other / '01_正文/第001章.md', '另书正文绝密标记')
        self.write()
        self.assertTrue(all('绝密标记' not in request for request in self.requests))
        self.assertFalse((other / '.story-memory').exists())
        self.assertNotEqual(memory.fingerprint(memory.sources(other)), memory.status(self.root)['revision'])

    def test_unchanged_memory_cached_and_old_edit_invalidates_chain(self):
        docs = memory.sources(self.root)
        memory.ensure_memory(self.root, docs, self.generate, 64000, lambda message: None)
        first = len(self.requests)
        memory.ensure_memory(self.root, docs, self.generate, 64000, lambda message: None)
        self.assertEqual(len(self.requests), first)
        atomic_write(self.root / '00_设定集/规则.md', '新版规则')
        self.assertFalse(memory.status(self.root)['ready'])
        memory.ensure_memory(self.root, memory.sources(self.root), self.generate, 64000, lambda message: None)
        self.assertEqual(len(self.requests), first * 2)

    def test_rejected_draft_never_published(self):
        def reject(prompt):
            if '【阶段 一致性审核】' in prompt:
                return json.dumps({'passed': False, 'issues': ['违反不能飞行设定']})
            return self.generate(prompt)
        with self.assertRaisesRegex(RuntimeError, '审核未通过'):
            self.write(generate=reject)
        self.assertEqual(len(novel_manager.chapter_files(str(self.root))), 1)
        self.assertTrue(list((self.root / '.generation-drafts').glob('*.md')))
        self.assertTrue(any('【阶段 修订草稿】' in request for request in self.requests))

    def test_memory_update_failure_keeps_approved_chapter_as_draft(self):
        drafting = False
        def fail(prompt):
            nonlocal drafting
            if '【阶段 生成草稿】' in prompt:
                drafting = True
            if drafting and '【阶段 更新记忆】' in prompt:
                raise RuntimeError('额度不足')
            return self.generate(prompt)
        with self.assertRaisesRegex(RuntimeError, '额度不足'):
            self.write(generate=fail)
        self.assertEqual(len(novel_manager.chapter_files(str(self.root))), 1)
        self.assertTrue(list((self.root / '.generation-drafts').glob('*.md')))
        # Retry reuses valid old checkpoints and publishes exactly one chapter.
        self.write()
        self.assertEqual(len(novel_manager.chapter_files(str(self.root))), 2)
        self.assertTrue(memory.status(self.root)['ready'])

    def test_concurrent_edit_preserves_source_and_draft(self):
        def change(prompt):
            if '【阶段 生成草稿】' in prompt:
                atomic_write(self.root / '00_设定集/规则.md', '作者的新规则')
            return self.generate(prompt)
        with self.assertRaisesRegex(RuntimeError, '保留'):
            self.write(generate=change)
        self.assertEqual(len(novel_manager.chapter_files(str(self.root))), 1)
        self.assertEqual((self.root / '00_设定集/规则.md').read_text(encoding='utf-8'), '作者的新规则')

    def test_source_references_are_book_scoped(self):
        def invalid(prompt):
            if '【阶段 本章计划】' in prompt:
                return json.dumps({'plan': '借用别书', 'sources': ['../另一本书/章节.md']})
            return self.generate(prompt)
        with self.assertRaisesRegex(ValueError, '引用无效'):
            self.write(generate=invalid)
        self.assertEqual(len(novel_manager.chapter_files(str(self.root))), 1)

    def test_required_historical_source_is_loaded_in_full(self):
        atomic_write(self.root / '01_正文/第一卷/第000章 旧约.md', '最早原文开始' + '追溯内容' * 1900 + '最早原文结尾')
        required = '01_正文/第一卷/第000章 旧约.md'
        def retrieve(prompt):
            if '【阶段 本章计划】' in prompt:
                return json.dumps({'plan': '回收旧约，核对旧约原文', 'sources': [required]})
            return self.generate(prompt)
        self.write(generate=retrieve)
        draft = next(p for p in self.requests if '【阶段 生成草稿】' in p)
        self.assertIn('最早原文开始', draft)
        self.assertIn('最早原文结尾', draft)

    def test_budget_never_silently_truncates_required_source(self):
        atomic_write(self.root / '01_正文/第一卷/第000章 旧约.md', '旧约' * 40000)
        required = '01_正文/第一卷/第000章 旧约.md'
        def retrieve(prompt):
            if '【阶段 本章计划】' in prompt:
                return json.dumps({'plan': '核对旧约', 'sources': [required]})
            return self.generate(prompt)
        with self.assertRaisesRegex(ValueError, '超出预算'):
            self.write(generate=retrieve)
        self.assertFalse(any('【阶段 生成草稿】' in p for p in self.requests))

    def test_open_threads_cannot_be_removed_by_omission(self):
        calls = 0
        def extract(prompt):
            nonlocal calls
            calls += 1
            record = json.loads(response(prompt))
            if calls > 1:
                record['threads'] = []
            return json.dumps(record)
        index = memory.ensure_memory(self.root, memory.sources(self.root), extract, 64000, lambda message: None)
        self.assertIn('开篇约定', index['state']['threads'])
        self.assertEqual(index['open_threads'], 1)

    def test_atomic_create_preserves_existing_chapter(self):
        target = self.root / '01_正文/既有章.md'
        atomic_create(target, '原章')
        with self.assertRaises(FileExistsError):
            atomic_create(target, '重试章')
        self.assertEqual(target.read_text(encoding='utf-8'), '原章')

    def test_full_mode_loads_all_source_text_and_budget_preflight(self):
        self.preferences['writing_context_mode'] = 'full'
        atomic_write(self.root / '01_正文/第一卷/第000章 旧事.md', '全文复核专属标记')
        self.write()
        draft = next(p for p in self.requests if '【阶段 生成草稿】' in p)
        self.assertIn('全文复核专属标记', draft)
        self.assertIn('手动全文复核资料', draft)

    def test_settings_validate_mode_and_budget(self):
        for data in ({'writing_context_chars': True}, {'writing_context_chars': 1}, {'writing_context_mode': 'unknown'}):
            with self.assertRaises(ValueError):
                novel_manager.save_user_settings(data, str(self.root))


if __name__ == '__main__':
    unittest.main()
