"""Regression tests for isolated storage, HTTP, tasks and chapter materials."""
import base64
import concurrent.futures
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ['NOVEL_HUB_DATA_DIR'] = str(Path('.test-data/unittest').resolve())
os.environ['NOVEL_HUB_DOWNLOAD_DIR'] = str(Path('.test-data/download').resolve())
import cloud_hub as hub
import illustration_generator as generator
import novel_manager as manager
from storage import atomic_json, atomic_write, safe_component, within


class RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(dir='.test-data')
        cls.root = Path(cls.temporary.name).resolve()
        hub.BOOKS_DIR = str(cls.root / 'books')
        hub.DATA_DIR = str(cls.root)
        hub.HAS_REDIS = False
        hub.HubHandler.log_message = lambda *args: None
        cls.server = hub.BoundedHTTPServer(('127.0.0.1', 0), hub.HubHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temporary.cleanup()

    def setUp(self):
        hub.TASKS.clear()
        hub.MEMORY_CACHE.clear()
        self.book = '测试作品'
        self.file = '第013章_观微.md'
        self.text = '# 章节\n\n顾见微将铜制显微镜放在木桌上。\n\n淳于越在殿前凝视水滴。'
        self.path = Path(hub.BOOKS_DIR) / self.book
        atomic_write(self.path / '01_正文/第一卷' / self.file, self.text)
        atomic_write(self.path / '00_设定集/世界观.md', '设定')
        (self.path / '02_插图').mkdir(exist_ok=True)
        import shutil
        for directory in (self.path / '02_插图').iterdir():
            if directory.is_dir():
                shutil.rmtree(directory)
        self.query = urllib.parse.urlencode({'book': self.book, 'file': self.file})

    def request(self, endpoint, data=None, auth=True):
        headers = {'X-Novel-Token': hub.AUTH_TOKEN} if auth else {}
        raw = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        req = urllib.request.Request(self.url + endpoint, data=raw, headers=headers)
        try:
            response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read()
            return response.status, json.loads(body) if 'json' in response.headers.get('Content-Type', '') else body

    def extras(self):
        return self.request('/api/chapter_extras?' + self.query)[1]

    def notes(self, revision='', **kwargs):
        return self.request('/api/save_chapter_notes', dict(book=self.book, file=self.file,
            revision=revision, author_note='谢谢追读。', chapter_comments=['你如何看待本章的实验？'], **kwargs))

    def dispatch(self, **kwargs):
        payload = dict(action='generate_chapter_notes', book_id=self.book, chapter_file=self.file)
        payload.update(kwargs)
        return self.request('/api/dispatch_task', payload)

    def image_payload(self):
        from PIL import Image
        image = io.BytesIO()
        Image.new('RGB', (1280, 720), 'white').save(image, 'PNG')
        filename = 'a' * 32 + '_1.png'
        meta = dict(chapter_filename=self.file, chapter_code='第013章',
            source_sha256=hashlib.sha256(self.text.encode()).hexdigest(),
            illustrations=[{'filename': filename}], author_note='本章观微。',
            chapter_comments=['水滴里有什么？'], revision='r1', style='2d-ink')
        return dict(book_id=self.book, chapter_code='第013章', meta=meta,
                    images={filename: base64.b64encode(image.getvalue()).decode()}, base_revision='')

    def test_get_requires_auth(self):
        self.assertEqual(self.request('/api/books', auth=False)[0], 401)

    def test_sync_requires_auth(self):
        self.assertEqual(self.request('/api/sync_data', {}, auth=False)[0], 401)

    def test_progress_requires_auth(self):
        self.assertEqual(self.request('/api/task_progress', {}, auth=False)[0], 401)

    def test_images_sync_requires_auth(self):
        self.assertEqual(self.request('/api/sync_illustrations', {}, auth=False)[0], 401)

    def test_static_public_and_local(self):
        self.assertEqual(self.request('/assets/marked.min.js', auth=False)[0], 200)
        self.assertEqual(self.request('/assets/unknown.js', auth=False)[0], 404)

    def test_login_issues_random_token(self):
        status, data = self.request('/api/login', {'password': hub.PASSWORD}, auth=False)
        self.assertEqual(status, 200)
        self.assertEqual(data['token'], hub.AUTH_TOKEN)
        self.assertNotEqual(data['token'], 'daqin_authorized')

    def test_public_old_token_rejected(self):
        self.assertFalse(hub.AUTH_TOKEN == 'daqin_authorized')

    def test_unknown_book_no_fallback(self):
        self.assertEqual(self.request('/api/chapters?book=unknown')[0], 404)

    def test_book_traversal_rejected(self):
        self.assertEqual(self.request('/api/books?book=..%2F..')[0], 400)

    def test_image_traversal_rejected(self):
        query = urllib.parse.urlencode(dict(book=self.book, chap='..', img='meta.json'))
        self.assertEqual(self.request('/api/illustration?' + query)[0], 400)

    def test_sync_filename_traversal_rejected(self):
        data = dict(book_id=self.book, chapters=[dict(filename='../x', content='x')])
        self.assertEqual(self.request('/api/sync_data', data)[0], 400)

    def test_chapter_read_and_audit(self):
        status, data = self.request('/api/tomato_clean?' + self.query)
        self.assertEqual(status, 200)
        self.assertEqual(data['audit']['score'], 85)
        self.assertIsInstance(data['audit']['dialogue_ratio'], (float, int))

    def test_notes_without_illustrations(self):
        self.assertEqual(self.notes()[0], 200)
        self.assertEqual(self.extras()['meta']['chapter_comments'], ['你如何看待本章的实验？'])

    def test_notes_revision_conflict(self):
        self.notes()
        self.assertEqual(self.notes()[0], 409)

    def test_notes_save_new_revision(self):
        _, data = self.notes()
        self.assertEqual(self.notes(data['meta']['revision'])[0], 200)

    def test_notes_missing_chapter(self):
        self.assertEqual(self.request('/api/save_chapter_notes', dict(book=self.book, file='missing.md'))[0], 404)

    def test_notes_bad_comments(self):
        self.assertEqual(self.request('/api/save_chapter_notes', dict(book=self.book, file=self.file, chapter_comments='bad'))[0], 400)

    def test_notes_export_contains_separate_fields(self):
        self.notes()
        status, raw = self.request('/api/export_chapter_illustrations_zip?' + self.query)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertIn('作者有话说.txt', archive.namelist())
            self.assertIn('章节评论.txt', archive.namelist())

    def test_export_excludes_unreferenced_files(self):
        self.notes()
        atomic_write(self.path/'02_插图/第013章/old.png', b'unreferenced')
        _, raw = self.request('/api/export_chapter_illustrations_zip?' + self.query)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertNotIn('old.png', archive.namelist())

    def test_upload_images(self):
        self.assertEqual(self.request('/api/sync_illustrations', self.image_payload())[0], 200)
        self.assertEqual(self.extras()['meta']['style'], '2d-ink')

    def test_upload_wrong_source_preserves_old(self):
        self.notes()
        payload = self.image_payload()
        payload['meta']['source_sha256'] = 'stale'
        self.assertEqual(self.request('/api/sync_illustrations', payload)[0], 409)
        self.assertEqual(self.extras()['meta']['author_note'], '谢谢追读。')

    def test_upload_revision_conflict(self):
        self.notes()
        self.assertEqual(self.request('/api/sync_illustrations', self.image_payload())[0], 409)

    def test_upload_invalid_image(self):
        payload = self.image_payload()
        payload['images'][next(iter(payload['images']))] = base64.b64encode(b'invalid').decode()
        self.assertEqual(self.request('/api/sync_illustrations', payload)[0], 400)
        self.assertFalse(self.extras()['has_extras'])

    def test_upload_count_mismatch(self):
        payload = self.image_payload()
        payload['images'] = {}
        self.assertEqual(self.request('/api/sync_illustrations', payload)[0], 400)

    def test_duplicate_material_tasks(self):
        first = self.dispatch()[1]
        self.assertEqual(first['task_id'], self.dispatch()[1]['task_id'])

    def test_invalid_image_count(self):
        self.assertEqual(self.dispatch(image_count=7)[0], 400)

    def test_concurrent_task_claim(self):
        self.dispatch()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.request('/api/pending_task')[1], range(8)))
        self.assertEqual(sum(bool(result['task']) for result in results), 1)

    def test_terminal_task_cannot_regress(self):
        tid = self.dispatch()[1]['task_id']
        self.request('/api/task_progress', {'id': tid, 'status': 'completed'})
        self.request('/api/task_progress', {'id': tid, 'status': 'running'})
        self.assertEqual(self.request('/api/task_status?id=' + tid)[1]['status'], 'completed')

    def test_task_exception_state_validation(self):
        tid = self.dispatch()[1]['task_id']
        self.assertEqual(self.request('/api/task_progress', {'id': tid, 'status': 'bad'})[0], 400)

    def test_cache_invalidation_isolates_redis(self):
        class FakeRedis:
            def __init__(self): self.keys = {'novel:cache:books': 'x', 'other:important': 'keep'}
            def scan_iter(self, **kwargs): return [key for key in self.keys if key.startswith('novel:cache:')]
            def delete(self, key): self.keys.pop(key, None)
        redis = FakeRedis()
        with patch.object(hub, 'HAS_REDIS', True), patch.object(hub, 'REDIS_CLIENT', redis):
            hub.invalidate_cache()
        self.assertEqual(redis.keys, {'other:important': 'keep'})

    def test_sync_invalidates_book_counts(self):
        self.request('/api/books')
        payload = dict(book_id=self.book, chapters=[dict(filename='第014章_续.md', content='新章节')])
        self.assertEqual(self.request('/api/sync_data', payload)[0], 200)
        books = self.request('/api/books')[1]['books']
        self.assertGreaterEqual(books[0]['chapter_count'], 2)
        (self.path/'01_正文/第一卷_人界篇/第014章_续.md').unlink()

    def test_parallel_queries(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            codes = list(executor.map(lambda _: self.request('/api/tomato_clean?' + self.query)[0], range(36)))
        self.assertEqual(codes, [200]*36)

    def test_no_wrong_typo_changes(self):
        result = manager.auto_fix_tomato_content('甘拜下风，叹为观止，账单，制订计划。')
        self.assertEqual(result['replaced_count'], 0)

    def test_longest_typo_replacement(self):
        self.assertEqual(manager.auto_fix_tomato_content('即然如此')['fixed_content'], '既然如此')

    def test_atomic_json_valid(self):
        path = self.root/'atomic.json'
        atomic_json(path, {'text': '水墨'})
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'text': '水墨'})

    def test_path_components(self):
        for value in ('..', '../x', 'a/b', 'a\\b', '', '*'):
            with self.assertRaises(ValueError): safe_component(value)

    def test_generator_failure_preserves_metadata(self):
        self.notes()
        with patch('agent_client.new_conversation', return_value='id'), patch('agent_client.wait_for_receipt', return_value={'error': 'cannot generate'}):
            with self.assertRaises(ValueError):
                generator.generate_chapter_visual_bundle(str(self.path), self.file, self.text)
        self.assertEqual(self.extras()['meta']['author_note'], '谢谢追读。')

    def test_notes_generation_preserves_images(self):
        payload = self.image_payload()
        self.request('/api/sync_illustrations', payload)
        payload['base_revision'] = 'r1'
        payload['notes_only'] = True
        payload['images'] = {}
        payload['meta']['illustrations'] = []
        payload['meta']['revision'] = 'r2'
        payload['meta']['author_note'] = '新文案'
        self.assertEqual(self.request('/api/sync_illustrations', payload)[0], 200)
        meta = self.extras()['meta']
        self.assertEqual(len(meta['illustrations']), 1)
        self.assertEqual(meta['author_note'], '新文案')
        self.assertEqual(meta['style'], '2d-ink')

    def test_sync_validates_entire_batch_before_writes(self):
        payload = dict(book_id=self.book, chapters=[dict(filename='第777章_合法.md', content='x'), dict(filename='../bad.md', content='x')])
        self.assertEqual(self.request('/api/sync_data', payload)[0], 400)
        self.assertFalse(list(self.path.rglob('第777章_合法.md')))

    def test_image_filename_collision_preserves_previous(self):
        payload = self.image_payload()
        self.request('/api/sync_illustrations', payload)
        payload['base_revision'] = 'r1'
        raw = base64.b64decode(next(iter(payload['images'].values()))) + b'changed'
        payload['images'][next(iter(payload['images']))] = base64.b64encode(raw).decode()
        self.assertEqual(self.request('/api/sync_illustrations', payload)[0], 400)

    def test_image_download_header(self):
        payload = self.image_payload()
        self.request('/api/sync_illustrations', payload)
        query = urllib.parse.urlencode(dict(book=self.book, chap='第013章', img=payload['meta']['illustrations'][0]['filename'], download='1'))
        req = urllib.request.Request(self.url + '/api/illustration?' + query, headers={'X-Novel-Token': hub.AUTH_TOKEN})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req) as response:
            self.assertIn('attachment', response.headers['Content-Disposition'])

    def test_legacy_metadata_still_readable(self):
        atomic_json(self.path/'02_插图/第013章/meta.json', dict(chapter_filename=self.file, images=[], author_note='旧文案'))
        self.assertEqual(self.extras()['meta']['author_note'], '旧文案')

    def test_request_id_deduplicates_creative_task(self):
        first = self.dispatch(action='creative_instruction', request_id='unique')[1]
        second = self.dispatch(action='creative_instruction', request_id='unique')[1]
        self.assertEqual(first['task_id'], second['task_id'])

    def test_antigravity_prompt_uses_executable_and_preserves_newlines(self):
        import agent_client
        completed = type('Completed', (), dict(returncode=0, stdout='{"response":{"conversationId":"test"}}', stderr=''))()
        with patch('agent_client.daemon_environment', return_value={}), patch('agent_client.subprocess.run', return_value=completed) as run:
            agent_client.new_conversation('第一行\n第二行设定', '测试')
            arguments = run.call_args.args[0]
            self.assertTrue(arguments[0].endswith('language_server.exe'))
            self.assertEqual(arguments[-1], '第一行\n第二行设定')

    def test_antigravity_zero_exit_with_error_is_failure(self):
        import agent_client
        completed = type('Completed', (), dict(returncode=0, stdout='{"error":"quota"}', stderr=''))()
        with patch('agent_client.daemon_environment', return_value={}), patch('agent_client.subprocess.run', return_value=completed):
            with self.assertRaises(RuntimeError): agent_client.new_conversation('prompt', 'title')


if __name__ == '__main__':
    unittest.main()
