"""Regression coverage for UI contracts, bidirectional sync and cloud writing."""
import base64
import io
import json
import os
import re
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ.setdefault('NOVEL_HUB_DATA_DIR', str(Path('.test-data/audit-suite').resolve()))
os.environ.setdefault('NOVEL_HUB_DOWNLOAD_DIR', str(Path('.test-data/download').resolve()))
import cloud_hub as hub
import deepseek_client as deepseek
import library_sync
import novel_manager as manager
from storage import atomic_write, atomic_json
from sync_protocol import book_snapshot, cover_fingerprint


class AuditRegressions(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir='.test-data')
        self.root = Path(self.temporary.name)
        self.books = self.root / 'books'
        self.local = self.root / 'local'
        self.local.mkdir()
        self.book = '审查测试'
        self.book_path = self.books / self.book
        self.filename = '第003章_旧事.md'
        atomic_write(self.book_path / '01_正文/第一卷' / self.filename, '# 第003章 旧事\n\n正文')
        atomic_write(self.book_path / '00_设定集/世界观.md', '这是一部悬疑小说。' + '设定' * 5000 + '文档末尾约束')
        self.patches = [patch.object(hub, 'DATA_DIR', str(self.root)), patch.object(hub, 'BOOKS_DIR', str(self.books)),
                        patch.object(hub, 'HAS_REDIS', False), patch.object(hub, 'TASKS', {})]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.temporary.cleanup)
        hub.MEMORY_CACHE.clear()
        manager.save_user_settings({'deepseek_enabled': True, 'deepseek_api_key': 'sk-test-only'}, str(self.root))

    def manifest(self):
        return {'books': {self.book: book_snapshot(self.book_path)}, 'deleted': {}}

    def get_document(self, endpoint):
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(endpoint).query)
        name = query.get('file', [''])[0]
        if query['kind'][0] == 'chapters':
            return {'content': manager.read_chapter(self.book, name, str(self.books))}
        if query['kind'][0] == 'settings':
            return {'content': (self.book_path / '00_设定集' / name).read_text(encoding='utf-8')}
        return {'cover_base64': base64.b64encode((self.book_path / '00_设定集/cover.png').read_bytes()).decode()}

    def pull(self, state):
        library_sync.reconcile(self.local, self.manifest(), state, self.get_document)

    def test_cloud_new_chapter_and_complete_settings_download(self):
        self.pull({})
        local = self.local / self.book
        self.assertEqual(manager.read_chapter(self.book, self.filename, str(self.local)), '# 第003章 旧事\n\n正文')
        self.assertTrue((local / '00_设定集/世界观.md').read_text(encoding='utf-8').endswith('文档末尾约束'))
        state = {self.book: book_snapshot(local)}
        atomic_write(self.book_path / '01_正文/第一卷/第004章_新章.md', '新正文')
        self.pull(state)
        self.assertEqual(len(manager.chapter_files(str(local))), 2)

    def test_local_delete_does_not_redownload(self):
        self.pull({})
        local = self.local / self.book
        state = {self.book: book_snapshot(local)}
        Path(manager.chapter_files(str(local))[0]).unlink()
        self.pull(state)
        self.assertEqual(manager.chapter_files(str(local)), [])

    def test_remote_delete_archives_local_file(self):
        self.pull({})
        local = self.local / self.book
        state = {self.book: book_snapshot(local)}
        Path(manager.chapter_files(str(self.book_path))[0]).unlink()
        self.pull(state)
        self.assertEqual(manager.chapter_files(str(local)), [])
        self.assertTrue(list((local / '.sync-archive').rglob(self.filename)))

    def test_concurrent_content_changes_preserve_both(self):
        self.pull({})
        local = self.local / self.book
        state = {self.book: book_snapshot(local)}
        atomic_write(manager.chapter_files(str(local))[0], '本地修改')
        atomic_write(manager.chapter_files(str(self.book_path))[0], '云端修改')
        with self.assertRaisesRegex(ValueError, '均已修改'):
            self.pull(state)
        self.assertEqual(manager.read_chapter(self.book, self.filename, str(self.local)), '本地修改')
        self.assertEqual(manager.read_chapter(self.book, self.filename, str(self.books)), '云端修改')

    def test_first_sync_with_different_existing_content_preserves_both(self):
        atomic_write(self.local / self.book / '01_正文/第一卷' / self.filename, '首次对账本地稿')
        with self.assertRaisesRegex(ValueError, '保留两端'):
            self.pull({})
        self.assertEqual(manager.read_chapter(self.book, self.filename, str(self.local)), '首次对账本地稿')

    def test_download_does_not_overwrite_edits_made_during_request(self):
        self.pull({})
        local = self.local / self.book
        state = {self.book: book_snapshot(local)}
        atomic_write(manager.chapter_files(str(self.book_path))[0], '云端修订')
        def changing_get(endpoint):
            result = self.get_document(endpoint)
            atomic_write(manager.chapter_files(str(local))[0], '请求期间本地修订')
            return result
        with self.assertRaisesRegex(ValueError, '下载期间修改'):
            library_sync.reconcile(self.local, self.manifest(), state, changing_get)
        self.assertEqual(manager.read_chapter(self.book, self.filename, str(self.local)), '请求期间本地修订')

    def test_concurrent_same_new_filename_is_conflict(self):
        self.pull({})
        local = self.local / self.book
        state = {self.book: book_snapshot(local)}
        atomic_write(local / '01_正文/第一卷_人界篇/第004章_同名.md', '本地新增')
        atomic_write(self.book_path / '01_正文/第一卷/第004章_同名.md', '云端新增')
        with self.assertRaisesRegex(ValueError, '均已修改'):
            self.pull(state)

    def test_cloud_title_change_keeps_identity_and_syncs_metadata(self):
        self.pull({})
        state = {self.book: book_snapshot(self.local / self.book)}
        result = manager.save_book_meta(self.book, {'title': '修改后的书名'}, str(self.books), rename=False)
        self.assertEqual(result['book_id'], self.book)
        self.pull(state)
        self.assertTrue((self.local / self.book).exists())
        self.assertEqual(manager.read_book_meta(str(self.local / self.book))['title'], '修改后的书名')

    def test_book_tombstone_archives_local_and_rejects_stale_upload(self):
        self.pull({})
        library_sync.reconcile(self.local, {'books': {}, 'deleted': {self.book: time.time()}}, {}, self.get_document)
        self.assertFalse((self.local / self.book).exists())
        self.assertTrue(list((self.local / '.trash').rglob(self.filename)))
        atomic_json(self.root / 'book_tombstones.json', {self.book: time.time()})
        with self.assertRaisesRegex(ValueError, '删除'):
            hub.sync_book({'book_id': self.book})

    def test_optimistic_revision_rejects_stale_upload_before_changes(self):
        snapshot = book_snapshot(self.book_path)
        atomic_write(self.book_path / '00_设定集/新增.md', '云端新设定')
        with self.assertRaisesRegex(ValueError, '云端已更新'):
            hub.sync_book({'book_id': self.book, 'sync_version': 3, 'expected_revision': snapshot['revision']})
        self.assertTrue((self.book_path / '00_设定集/新增.md').exists())

    def test_failed_rename_and_bad_cover_preserve_saved_data(self):
        other = self.books / '同名作品'
        other.mkdir()
        before = manager.read_book_meta(str(self.book_path))
        with self.assertRaises(FileExistsError):
            manager.save_book_meta(self.book, {'title': '同名作品'}, str(self.books))
        self.assertEqual(manager.read_book_meta(str(self.book_path)), before)
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (20, 30), 'navy').save(buffer, 'PNG')
        cover = manager.save_book_cover(self.book, buffer.getvalue(), root_dir=str(self.books))
        previous = Path(cover).read_bytes()
        with self.assertRaises(ValueError):
            manager.save_book_cover(self.book, b'bad image', root_dir=str(self.books))
        self.assertEqual(Path(cover).read_bytes(), previous)

    def test_cover_receipt_uses_pixels_across_png_encoders(self):
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo
        image = Image.new('RGB', (21, 37), 'navy')
        first, second = io.BytesIO(), io.BytesIO()
        image.save(first, 'PNG', compress_level=0)
        info = PngInfo()
        info.add_text('encoder', 'different-platform')
        image.save(second, 'PNG', compress_level=9, pnginfo=info)
        self.assertNotEqual(first.getvalue(), second.getvalue())
        self.assertEqual(cover_fingerprint(first.getvalue()), cover_fingerprint(second.getvalue()))

    def test_cover_version_changes_after_upload(self):
        from PIL import Image
        before = manager.list_books(str(self.books))[0]['cover_version']
        buffer = io.BytesIO()
        Image.new('RGB', (15, 20), 'navy').save(buffer, 'PNG')
        manager.save_book_cover(self.book, buffer.getvalue(), root_dir=str(self.books))
        self.assertNotEqual(before, manager.list_books(str(self.books))[0]['cover_version'])

    def test_deepseek_full_context_generic_genre_and_max_chapter_number(self):
        def generate(prompt, *args, **kwargs):
            self.assertIn('文档末尾约束', prompt)
            self.assertIn('第004章', prompt)
            self.assertNotIn('嬴政', prompt)
            return '# 第004章 新线索\n\n' + '悬疑正文。' * 120, ''
        with patch.object(deepseek, 'generate_content', side_effect=generate):
            ok, _, filename = deepseek.write_serial_chapters(str(self.book_path), self.book, config_dir=str(self.root))
        self.assertTrue(ok)
        self.assertIn('第004章', filename)
        self.assertEqual(len(manager.chapter_files(str(self.book_path))), 2)

    def test_changed_context_does_not_overwrite_and_keeps_generation_draft(self):
        def generate(*args, **kwargs):
            atomic_write(self.book_path / '00_设定集/新增.md', '生成期间修改')
            return '# 第004章 新线索\n\n' + '正文。' * 200, ''
        with patch.object(deepseek, 'generate_content', side_effect=generate), self.assertRaisesRegex(RuntimeError, '保留'):
            deepseek.write_serial_chapters(str(self.book_path), self.book, config_dir=str(self.root))
        self.assertEqual(len(manager.chapter_files(str(self.book_path))), 1)
        self.assertTrue(list((self.book_path / '.generation-drafts').glob('*.md')))

    def test_api_probe_never_falls_back_to_paid_generation(self):
        error = urllib.error.HTTPError('https://api.deepseek.com/user/balance', 404, 'missing', {}, io.BytesIO())
        with patch.object(deepseek.urllib.request, 'urlopen', side_effect=error) as request:
            self.assertFalse(deepseek.test_api_key('sk-test-only')[0])
        self.assertEqual(request.call_count, 1)

    def test_truncated_api_output_is_rejected(self):
        response = MagicMock()
        response.read.return_value = json.dumps({'choices': [{'finish_reason': 'length', 'message': {'content': '截断正文'}}]}).encode()
        with patch.object(deepseek.urllib.request, 'urlopen') as request:
            request.return_value.__enter__.return_value = response
            with self.assertRaisesRegex(RuntimeError, '未完成'):
                deepseek.generate_content('请求', config_dir=str(self.root))

    def test_cloud_outline_task_persists_real_file_and_terminal_state(self):
        hub.TASKS['test-job'] = {'id': 'test-job', 'book_id': self.book, 'status': 'assigned', 'action': 'creative_instruction',
            'engine_mode': 'deepseek', 'extra_data': {'workflow': 'outline'}, 'prompt': '推演大纲', 'progress_log': []}
        with patch.object(deepseek, 'execute_instruction', return_value=(True, '这是本书后续大纲')):
            hub.execute_deepseek_task_async('test-job')
        task = hub.TASKS['test-job']
        self.assertEqual(task['status'], 'completed')
        self.assertEqual(Path(task['result']['files'][0]).read_text(encoding='utf-8'), '这是本书后续大纲')
        self.assertEqual(json.loads((self.root / 'tasks.json').read_text(encoding='utf-8'))['test-job']['status'], 'completed')

    def test_cloud_notes_task_updates_extras(self):
        hub.TASKS['notes'] = {'id': 'notes', 'book_id': self.book, 'status': 'assigned', 'action': 'generate_chapter_notes',
            'engine_mode': 'deepseek', 'extra_data': {'chapter_file': self.filename}, 'prompt': '', 'progress_log': []}
        output = json.dumps({'author_note': '谢谢阅读', 'chapter_comments': ['真相是什么？']}, ensure_ascii=False)
        with patch.object(deepseek, 'execute_instruction', return_value=(True, output)):
            hub.execute_deepseek_task_async('notes')
        self.assertEqual(hub.TASKS['notes']['status'], 'completed')
        self.assertEqual(manager.get_chapter_extras(self.book, self.filename, str(self.books))['meta']['author_note'], '谢谢阅读')

    def test_cloud_new_novel_creates_settings_in_its_own_book(self):
        hub.TASKS['new'] = {'id':'new', 'book_id':'另一部小说', 'status':'assigned', 'action':'create_new_novel',
            'engine_mode':'deepseek', 'extra_data':{'title':'另一部小说', 'genre':'都市悬疑'}, 'prompt':'创建新书', 'progress_log':[]}
        with patch.object(deepseek, 'execute_instruction', return_value=(True, '独立的新书世界观、人物与细纲')):
            hub.execute_deepseek_task_async('new')
        self.assertEqual(hub.TASKS['new']['status'], 'completed')
        self.assertEqual(manager.read_book_meta(str(self.books / '另一部小说'))['genre'], '都市悬疑')
        self.assertTrue((self.books / '另一部小说/00_设定集/世界观人物与首卷大纲.md').exists())
        self.assertEqual(len(manager.chapter_files(str(self.book_path))), 1)

    def test_cloud_polish_keeps_backup_and_rejects_wrong_chapter_number(self):
        task = {'id':'polish', 'book_id':self.book, 'status':'assigned', 'action':'creative_instruction',
            'engine_mode':'deepseek', 'extra_data':{'workflow':'polish'}, 'prompt':'精修', 'progress_log':[]}
        hub.TASKS['polish'] = task
        with patch.object(deepseek, 'execute_instruction', return_value=(True, '# 第003章 旧事\n\n修订正文')):
            hub.execute_deepseek_task_async('polish')
        self.assertEqual(task['status'], 'completed')
        self.assertTrue(list((self.book_path / '01_正文/.backup').glob('*.bak_*')))
        task['status'] = 'assigned'
        with patch.object(deepseek, 'execute_instruction', return_value=(True, '# 第004章 错误编号\n\n错误正文')):
            hub.execute_deepseek_task_async('polish')
        self.assertEqual(task['status'], 'failed')
        self.assertIn('修订正文', manager.read_chapter(self.book, self.filename, str(self.books)))

    def test_cloud_queue_waits_for_same_book_local_task(self):
        hub.TASKS.update({'first': {'book_id': self.book, 'status': 'running', 'engine_mode': 'codex'},
                          'second': {'book_id': self.book, 'status': 'pending', 'engine_mode': 'deepseek'}})
        with patch.object(hub.threading.Thread, 'start') as start:
            hub.start_ready_cloud_tasks()
            start.assert_not_called()
            hub.TASKS['first']['status'] = 'completed'
            hub.start_ready_cloud_tasks()
            self.assertEqual(hub.TASKS['second']['status'], 'assigned')
            self.assertEqual(start.call_count, 1)

    def test_frontend_existing_controls_and_api_contracts(self):
        html = Path('index.html').read_text(encoding='utf-8')
        for value in ('edit-cover-file', 'edit-cover-preview-img', 'edit-cover-placeholder-tip', '/edit_book_meta`', '/reading_tokens`', 'action: "init_novel"', 'setAuthToken(', 'closeAccessLoginModal(', 'renderTomatoAuditPanel('):
            self.assertNotIn(value, html)
        for workflow in ('serial', 'polish', 'outline', 'freeform'):
            self.assertIn('id="wf-tab-' + workflow + '"', html)
        self.assertEqual(len(re.findall(r'function showToast\(', html)), 1)


if __name__ == '__main__':
    unittest.main()
