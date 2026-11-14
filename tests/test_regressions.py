"""Regression tests for isolated storage, HTTP, tasks and chapter materials."""
import base64
import concurrent.futures
import hashlib
import io
import json
import os
import re
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
        atomic_json(self.root / 'agents.json', {'updated_at': __import__('time').time(), 'engines': [
            {'id': 'antigravity', 'available': True, 'name': 'Antigravity', 'project_id': 'ag-project',
             'project_name': '小说', 'workspace': 'D:\\小说', 'capabilities': {'images': True},
             'models': [{'id': 'gemini-3.8-flash-high', 'name': 'Gemini', 'is_default': True,
                         'default_effort': 'high', 'efforts': [{'id': 'high', 'name': 'high'}]}]},
            {'id': 'codex', 'available': True, 'name': 'ChatGPT（Codex）', 'project_id': 'codex-project',
             'project_name': '小说', 'workspace': 'D:\\小说', 'capabilities': {'images': False},
             'models': [{'id': 'gpt-test', 'name': 'GPT', 'is_default': True,
                         'default_effort': 'medium', 'efforts': [{'id': 'medium'}, {'id': 'high'}]}]}]})
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

    def test_wake_available_when_client_catalog_is_offline(self):
        atomic_json(self.root / 'agents.json', {'engines': []})
        status, result = self.dispatch(action='wake_antigravity', engine_mode='codex')
        self.assertEqual(status, 200)
        self.assertEqual(hub.TASKS[result['task_id']]['engine_mode'], 'codex')

    def test_verified_codex_image_capability_can_dispatch(self):
        catalog = json.loads((self.root / 'agents.json').read_text())
        catalog['engines'][1]['capabilities']['images'] = True
        self.assertEqual(self.request('/api/sync_agents', catalog)[0], 200)
        self.assertEqual(self.dispatch(action='generate_chapter_illustrations', engine_mode='codex')[0], 200)

    def test_login_handles_unicode_password_input(self):
        self.assertEqual(self.request('/api/login', {'password': '错误口令'}, auth=False)[0], 401)
        with patch('cloud_hub.PASSWORD', '测试口令'):
            status, data = self.request('/api/login', {'password': '测试口令'}, auth=False)
        self.assertEqual(status, 200)
        self.assertTrue(hub.valid_access_token(data['token']))

    def test_login_session_expires_after_seven_days(self):
        with patch('cloud_hub.time.time', return_value=1000):
            session = hub.issue_session()
        self.assertEqual(session['expires_at'], 1000 + 604800)
        with patch('cloud_hub.time.time', return_value=1000 + 604799):
            self.assertTrue(hub.valid_access_token(session['token']))
        with patch('cloud_hub.time.time', return_value=1000 + 604800):
            self.assertFalse(hub.valid_access_token(session['token']))

    def test_signed_session_accepts_cookie_and_rejects_tampering(self):
        session = hub.issue_session()
        request = urllib.request.Request(self.url + '/api/books', headers={'Cookie': 'novel_token=' + session['token']})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request) as response:
            self.assertEqual(response.status, 200)
        token = session['token']
        self.assertFalse(hub.valid_access_token(token[:-1] + ('0' if token[-1] != '0' else '1')))
        self.assertFalse(hub.valid_access_token(token.replace('session.', 'other.', 1)))
        self.assertNotEqual(hub.issue_session()['token'], token)
        self.assertFalse(hub.valid_access_token('无效令牌'))

    def test_serial_dispatch_counts(self):
        status, result = self.dispatch(action='creative_instruction', extra_data={'workflow': 'serial', 'chapter_count': 3})
        self.assertEqual(status, 200)
        self.assertEqual(hub.TASKS[result['task_id']]['extra_data']['chapter_count'], 3)
        for count in [0, 11, True, 1.5, '2']:
            self.assertEqual(self.dispatch(action='creative_instruction', extra_data={'workflow': 'serial', 'chapter_count': count})[0], 400)
        self.assertEqual(self.dispatch(action='creative_instruction', extra_data={'workflow': 'polish', 'chapter_count': 2})[0], 400)

    def test_concurrent_serial_retry_creates_one_task(self):
        def dispatch_once(_):
            return self.dispatch(action='creative_instruction', request_id='concurrent-retry', extra_data={'workflow': 'serial', 'chapter_count': 3})[1]['task_id']
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            identifiers = list(executor.map(dispatch_once, range(4)))
        self.assertEqual(len(set(identifiers)), 1)
        self.assertEqual(len(hub.TASKS), 1)

    def test_serial_active_duplicates_reuse_task(self):
        payload = dict(action='creative_instruction', prompt='连续续写', extra_data={'workflow': 'serial', 'chapter_count': 10})
        first = self.dispatch(**payload)[1]
        second = self.dispatch(**payload)[1]
        self.assertEqual(first['task_id'], second['task_id'])
        self.assertTrue(second['deduplicated'])
        hub.TASKS[first['task_id']]['status'] = 'completed'
        third = self.dispatch(**payload)[1]
        self.assertNotEqual(first['task_id'], third['task_id'])

    def test_request_id_payload_conflict_rejected(self):
        self.dispatch(action='creative_instruction', request_id='retry', prompt='A')
        self.assertEqual(self.dispatch(action='creative_instruction', request_id='retry', prompt='B')[0], 400)
        self.assertEqual(len(hub.TASKS), 1)

    def test_same_book_has_one_active_claim(self):
        first = self.dispatch(action='creative_instruction', extra_data={'workflow': 'serial', 'chapter_count': 10})[1]
        second = self.dispatch(action='creative_instruction', extra_data={'workflow': 'serial', 'chapter_count': 3})[1]
        self.assertEqual(self.request('/api/pending_task')[1]['task']['id'], first['task_id'])
        self.assertIsNone(self.request('/api/pending_task')[1]['task'])
        hub.TASKS[first['task_id']]['status'] = 'completed'
        self.assertEqual(self.request('/api/pending_task')[1]['task']['id'], second['task_id'])

    def test_pending_task_reports_queue_and_active_progress(self):
        _, first = self.dispatch(action='creative_instruction', extra_data={'workflow': 'serial', 'chapter_count': 10})
        _, second = self.dispatch(action='creative_instruction', extra_data={'workflow': 'serial', 'chapter_count': 3})
        hub.TASKS[first['task_id']]['status'] = 'running'
        hub.TASKS[first['task_id']]['progress_log'].extend(['续写进度 2/10', '会话正在执行，等待成果校验'])
        status, result = self.request('/api/task_status?id=' + second['task_id'])
        self.assertEqual(status, 200)
        self.assertEqual(result['queue_position'], 1)
        self.assertEqual(result['waiting_for'][0]['progress'], '续写进度 2/10')

    def test_catalog_requires_auth(self):
        self.assertEqual(self.request('/api/engines', auth=False)[0], 401)

    def test_codex_dispatch_preserves_model_effort_and_permissions(self):
        status, result = self.dispatch(engine_mode='codex', model_tier='gpt-test', reasoning_effort='high')
        self.assertEqual(status, 200)
        task = hub.TASKS[result['task_id']]
        self.assertEqual(task['engine_mode'], 'codex')
        self.assertEqual(task['workspace'], 'D:\\小说')
        self.assertEqual(task['permission_mode'], 'full-access')
        self.assertEqual(task['project_id'], 'codex-project')
        self.assertEqual(task['extra_data']['model_tier'], 'gpt-test')
        self.assertEqual(task['extra_data']['reasoning_effort'], 'high')

    def test_dispatch_rejects_unknown_model_or_effort(self):
        for overrides in [dict(engine_mode='codex', model_tier='missing'),
                          dict(engine_mode='codex', model_tier='gpt-test', reasoning_effort='ultra'),
                          dict(engine_mode='missing')]:
            with self.subTest(overrides=overrides):
                self.assertEqual(self.dispatch(**overrides)[0], 400)
        self.assertFalse(hub.TASKS)

    def test_codex_images_rejected_without_verified_image_capability(self):
        self.assertEqual(self.dispatch(action='generate_chapter_illustrations', engine_mode='codex')[0], 400)

    def test_catalog_rejects_wrong_or_duplicate_workspace(self):
        catalog = json.loads((self.root / 'agents.json').read_text())
        catalog['engines'][0]['workspace'] = 'D:\\other'
        self.assertEqual(self.request('/api/sync_agents', catalog)[0], 400)
        catalog['engines'][0]['workspace'] = 'D:\\小说'
        catalog['engines'][1]['id'] = 'antigravity'
        self.assertEqual(self.request('/api/sync_agents', catalog)[0], 400)

    def test_catalog_sync_and_refresh(self):
        catalog = json.loads((self.root / 'agents.json').read_text())
        self.assertEqual(self.request('/api/sync_agents', catalog)[0], 200)
        self.assertEqual(len(self.request('/api/engines')[1]['engines']), 2)
        self.assertEqual(self.request('/api/refresh_agents', {})[0], 200)
        self.assertTrue(self.request('/api/pending_task')[1]['refresh_agents'])
        self.assertFalse(self.request('/api/pending_task')[1]['refresh_agents'])

    def test_workspace_identity_requires_unique_name_and_root(self):
        from agent_workspace import choose_project, is_workspace
        self.assertTrue(is_workspace('file:///d%3A/%E5%B0%8F%E8%AF%B4'))
        projects = [{'name': '小说', 'path': 'D:\\小说', 'id': 'correct'},
                    {'name': '小说', 'path': 'D:\\other', 'id': 'other'}]
        self.assertEqual(choose_project(projects, lambda entry: [entry['path']])['id'], 'correct')
        with self.assertRaises(RuntimeError):
            choose_project(projects + [projects[0]], lambda entry: [entry['path']])

    def test_html_entry_has_no_store_cache_policy(self):
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(self.url + '/novel/') as response:
            self.assertEqual(response.headers.get('Cache-Control'), 'no-store')

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
        self.assertNotEqual(data['token'], hub.AUTH_TOKEN)
        self.assertTrue(hub.valid_access_token(data['token']))
        self.assertEqual(data['expires_in'], 7 * 24 * 60 * 60)
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

    def reading_request(self, resource, token=None, data=None):
        link = token or hub.issue_reading_link()['url'].split('#')[1]
        raw = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(self.url + '/api/read/' + resource, data=raw,
                                         headers={'X-Reading-Token': link})
        try:
            response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5)
        except urllib.error.HTTPError as failure:
            response = failure
        with response:
            return response.status, json.load(response)

    def test_reading_link_generation_requires_login(self):
        self.assertEqual(self.request('/api/reading_link', {}, auth=False)[0], 401)
        code, link = self.request('/api/reading_link', {})
        self.assertEqual(code, 200)
        self.assertEqual(link['expires_in'], 7 * 24 * 60 * 60)
        self.assertNotEqual(link['url'], self.request('/api/reading_link', {})[1]['url'])
        self.assertEqual(self.request('/novel/read/', auth=False)[0], 200)

    def test_library_contains_matching_catalog_and_latest_content(self):
        code, library = self.request('/api/library?' + self.query)
        self.assertEqual(code, 200)
        self.assertEqual(library['book'], self.book)
        self.assertEqual(library['latest']['filename'], library['chapters'][-1]['filename'])
        self.assertEqual(library['latest']['content'], self.text)
        self.assertEqual(self.request('/api/library', auth=False)[0], 401)

    def test_reading_hierarchy_and_private_files(self):
        code, books = self.reading_request('books')
        self.assertEqual(code, 200)
        self.assertTrue(any(book['id'] == self.book for book in books['books']))
        self.assertTrue(all(set(book) == {'id', 'title', 'chapter_count'} for book in books['books']))
        self.assertEqual(self.reading_request('chapters?' + self.query)[0], 200)
        code, chapter = self.reading_request('chapter?' + self.query)
        self.assertEqual(code, 200)
        self.assertIn('顾见微', chapter['content'])
        self.assertEqual(self.reading_request('settings?' + self.query)[0], 404)
        self.assertEqual(self.reading_request('chapter?' + urllib.parse.urlencode({'book':self.book, 'file':'世界观.md'}))[0], 404)

    def test_reading_token_cannot_be_used_for_admin(self):
        token = hub.issue_reading_link()['url'].split('#')[1]
        for endpoint in ('/api/books', '/api/settings?' + self.query, '/api/conversations'):
            self.assertEqual(self.request(endpoint + ('&' if '?' in endpoint else '?') + 'token=' + token, auth=False)[0], 401)
        for endpoint in ('/api/sync_data', '/api/dispatch_task', '/api/reading_link', '/api/request_sync'):
            request = urllib.request.Request(self.url + endpoint, data=b'{}', headers={'X-Novel-Token': token})
            with self.assertRaises(urllib.error.HTTPError) as result:
                urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request)
            self.assertEqual(result.exception.code, 401)
            result.exception.close()
        self.assertEqual(self.reading_request('chapter?' + self.query, data={})[0], 401)

    def test_reading_expiry_and_tamper_rechecked_on_cache_hits(self):
        with patch.object(hub.time, 'time', return_value=1000):
            link = hub.issue_reading_link()
        token = link['url'].split('#')[1]
        with patch.object(hub.time, 'time', return_value=link['expires_at'] - 1):
            self.assertEqual(self.reading_request('chapter?' + self.query, token)[0], 200)
        with patch.object(hub.time, 'time', return_value=link['expires_at']):
            self.assertEqual(self.reading_request('chapter?' + self.query, token)[0], 403)
        self.assertEqual(self.reading_request('books', token + 'x')[0], 403)
        self.assertEqual(self.reading_request('books', hub.AUTH_TOKEN)[0], 403)
        self.assertFalse(hub.valid_access_token(token))

    def test_sync_v2_receipt_archive_and_public_cache_refresh(self):
        from sync_protocol import content_fingerprint
        chapters = [{'filename': self.file, 'content': '修订后的正文'}]
        settings = [{'filename':'世界观.md', 'content':'设定'}]
        payload = dict(book_id=self.book, chapters=chapters, settings=settings, sync_version=2,
                       fingerprint=content_fingerprint(chapters, settings))
        self.reading_request('chapter?' + self.query)
        code, response = self.request('/api/sync_data', payload)
        self.assertEqual(code, 200)
        self.assertEqual(response['fingerprint'], payload['fingerprint'])
        self.assertIn('修订后的正文', self.reading_request('chapter?' + self.query)[1]['content'])
        chapters = [{'filename':'第014章_改名.md', 'content':'改名正文'}]
        payload.update(chapters=chapters, fingerprint=content_fingerprint(chapters, settings))
        self.assertEqual(self.request('/api/sync_data', payload)[0], 200)
        self.assertEqual(self.reading_request('chapter?' + self.query)[0], 404)
        self.assertEqual(len(manager.chapter_files(self.path)), 1)
        self.assertTrue(list((self.path / '.sync-archive').rglob(self.file)))
        # Restore fixture layout for tests sharing this temporary book.
        for filename in manager.chapter_files(self.path):
            Path(filename).unlink()
        atomic_write(self.path / '01_正文/第一卷' / self.file, self.text)

    def test_sync_v2_rejects_wrong_digest_before_writing(self):
        code, _ = self.request('/api/sync_data', dict(book_id=self.book, chapters=[], settings=[], sync_version=2, fingerprint='wrong'))
        self.assertEqual(code, 400)
        self.assertEqual(manager.read_chapter(self.book, self.file, hub.BOOKS_DIR), self.text)

    def test_read_cache_cannot_cross_a_sync_write(self):
        entered = threading.Event()
        release = threading.Event()
        original = manager.read_chapter
        def paused_read(*args, **kwargs):
            text = original(*args, **kwargs)
            entered.set()
            release.wait(3)
            return text
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            with patch.object(manager, 'read_chapter', side_effect=paused_read):
                reading = executor.submit(self.request, '/api/chapter?' + self.query)
                self.assertTrue(entered.wait(2))
                uploading = executor.submit(self.request, '/api/sync_data', dict(book_id=self.book,
                    chapters=[{'filename':self.file, 'content':'并发更新正文'}]))
                self.assertFalse(uploading.done())
                release.set()
                self.assertEqual(reading.result()[0], 200)
                self.assertEqual(uploading.result()[0], 200)
        self.assertEqual(self.request('/api/chapter?' + self.query)[1]['content'], '并发更新正文')

    def test_sync_request_and_report_status(self):
        requested = self.request('/api/request_sync', {})[1]['requested_at']
        self.assertEqual(self.request('/api/heartbeat', {})[1]['sync_requested_at'], requested)
        self.request('/api/sync_report', {'ok':True, 'request_id':requested, 'chapters_count':1})
        status = self.request('/api/sync_status')[1]
        self.assertEqual(status['completed_request'], requested)
        success = status['last_success_at']
        self.request('/api/sync_report', {'ok':False, 'error':'网络失败'})
        status = self.request('/api/sync_status')[1]
        self.assertFalse(status['ok'])
        self.assertEqual(status['last_success_at'], success)
        self.assertEqual(self.request('/api/sync_status', auth=False)[0], 401)

    def test_bidirectional_sync_http_metadata_delete_and_tombstone(self):
        import local_bridge as bridge
        with tempfile.TemporaryDirectory(dir='.test-data') as directory:
            with patch.object(bridge, 'BOOKS_ROOT', directory), patch.object(bridge, 'CLOUD_SERVER_URL', self.url + '/api'), patch.object(bridge, 'TOKEN', hub.AUTH_TOKEN), patch.object(bridge, 'SYNC_RECEIPTS', {}):
                self.assertTrue(bridge.sync_all_books_to_cloud()[0])
                self.assertEqual(manager.read_chapter(self.book, self.file, directory), self.text)
                self.assertEqual(self.request('/api/book_meta', {'book_id':self.book, 'title':'仅修改显示书名'})[0], 200)
                self.assertTrue(bridge.sync_all_books_to_cloud(False)[0])
                self.assertEqual(manager.read_book_meta(str(Path(directory) / self.book))['title'], '仅修改显示书名')
                Path(manager.chapter_files(str(Path(directory) / self.book))[0]).unlink()
                self.assertTrue(bridge.sync_all_books_to_cloud(False)[0])
                self.assertEqual(manager.chapter_files(self.path), [])
                self.assertEqual(self.request('/api/delete_book', {'book_id':self.book, 'confirm_title':'仅修改显示书名'})[0], 200)
                self.assertTrue(bridge.sync_all_books_to_cloud()[0])
                self.assertFalse((Path(directory) / self.book).exists())
        # Shared fixture is rebuilt for subsequent requests.
        atomic_json(self.root / 'book_tombstones.json', {})
        atomic_write(self.path / '01_正文/第一卷' / self.file, self.text)
        atomic_write(self.path / '00_设定集/世界观.md', '设定')

    def test_deepseek_uses_server_configuration_and_cannot_be_claimed_locally(self):
        import deepseek_client
        catalog = dict(id='deepseek', available=True, models=deepseek_client.DEEPSEEK_MODELS, capabilities={'images':False})
        with patch.object(deepseek_client, 'discover', return_value=catalog), patch.object(hub, 'start_ready_cloud_tasks'):
            code, result = self.dispatch(action='creative_instruction', engine_mode='deepseek',
                extra_data={'workflow':'outline', 'model_tier':'deepseek-flash', 'reasoning_effort':'high'})
            self.assertEqual(code, 200)
            self.assertIsNone(self.request('/api/pending_task')[1]['task'])
            self.assertEqual(hub.TASKS[result['task_id']]['status'], 'pending')
            self.assertEqual(self.dispatch(engine_mode='deepseek', action='generate_chapter_illustrations')[0], 400)

    def test_tomato_replacement_returns_correct_count_and_preview(self):
        code, result = self.request('/api/tomato_autofix', {'book':self.book, 'file':self.file,
            'content':"角色的 isn't 口头禅。", 'wrong_word':"isn't", 'correct_word':'不是'})
        self.assertEqual(code, 200)
        self.assertEqual(result['replaced_count'], 1)
        self.assertIn('不是', result['content'])
        self.assertEqual(manager.read_chapter(self.book, self.file, hub.BOOKS_DIR), self.text)

    def test_cover_requires_owner_and_is_not_cached_after_edit(self):
        self.assertEqual(self.request('/api/cover?' + self.query, auth=False)[0], 401)
        self.assertEqual(self.request('/api/cover?' + self.query)[0], 200)

    def test_new_novel_recreation_gets_new_identity_and_retry_reuses_task(self):
        deleted_id = '已删除同名新书'
        atomic_json(self.root / 'book_tombstones.json', {deleted_id: 1})
        payload = dict(action='create_new_novel', book_id=deleted_id, prompt='创建悬疑新书', request_id='recreate-book')
        code, first = self.dispatch(**payload)
        self.assertEqual(code, 200)
        self.assertNotEqual(first['task']['book_id'], deleted_id)
        self.assertEqual(first['task']['extra_data']['title'], deleted_id)
        self.assertEqual(self.dispatch(**payload)[1]['task_id'], first['task_id'])
        atomic_json(self.root / 'book_tombstones.json', {})

    def test_cover_thumbnail_cache_invalidation_and_original_download(self):
        from PIL import Image
        raw = io.BytesIO()
        Image.effect_noise((1200, 1800), 60).convert('RGB').save(raw, 'PNG')
        manager.save_book_cover(self.book, raw.getvalue(), root_dir=str(self.root / 'books'))
        def get(query='', headers=None):
            request = urllib.request.Request(self.url + '/api/cover?' + self.query + query,
                headers={'X-Novel-Token': hub.AUTH_TOKEN, **(headers or {})})
            return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request)
        with get('&size=thumb&v=first') as image:
            thumb = image.read()
            etag = image.headers['ETag']
            self.assertIn('private', image.headers['Cache-Control'])
            self.assertIn('max-age=86400', image.headers['Cache-Control'])
            self.assertEqual(image.headers['Content-Type'], 'image/jpeg')
        with Image.open(io.BytesIO(thumb)) as resized:
            self.assertLessEqual(resized.width, 320)
            self.assertLessEqual(resized.height, 480)
        self.assertLess(len(thumb), len(raw.getvalue()) // 10)
        with self.assertRaises(urllib.error.HTTPError) as cached:
            get('&size=thumb&v=first', {'If-None-Match': etag})
        self.assertEqual(cached.exception.code, 304)
        cached.exception.close()
        with get('&download=1') as original:
            self.assertEqual(original.read(), (self.path / '00_设定集/cover.png').read_bytes())
        image = io.BytesIO()
        Image.new('RGB', (80, 120), 'red').save(image, 'PNG')
        manager.save_book_cover(self.book, image.getvalue(), root_dir=str(self.root / 'books'))
        with get('&size=thumb&v=second', {'If-None-Match': etag}) as changed:
            self.assertNotEqual(changed.headers['ETag'], etag)
            self.assertNotEqual(changed.read(), thumb)
        self.assertEqual(self.request('/api/cover?' + self.query + '&size=thumb', auth=False)[0], 401)

    def test_memory_status_requires_auth_and_is_book_scoped(self):
        code, denied = self.request('/api/writing_memory?book=' + urllib.parse.quote(self.book), auth=False)
        self.assertEqual(code, 401)
        code, data = self.request('/api/writing_memory?book=' + urllib.parse.quote(self.book))
        self.assertEqual(code, 200)
        self.assertIn('ready', data)
        self.assertEqual(data['sources'], 2)
        self.assertEqual(self.request('/api/writing_memory?book=..')[0], 400)

    def test_cloud_writing_real_http_transport_and_two_chapter_persistence(self):
        import time
        from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
        calls = []
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(payload)
                from writing_fixtures import response
                output = response(payload['messages'][-1]['content'])
                body = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': output}}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        settings_path = self.root / '.user_settings.json'
        old_settings = settings_path.read_bytes() if settings_path.exists() else None
        before = set(manager.chapter_files(self.path))
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        network_patch = patch.object(urllib.request, 'urlopen', side_effect=lambda request, context=None, timeout=300: direct.open(request, timeout=timeout))
        network_patch.start()
        try:
            manager.save_user_settings({'deepseek_enabled':True, 'deepseek_api_key':'sk-local-stub-only',
                'deepseek_base_url':f'http://127.0.0.1:{upstream.server_port}'}, str(self.root))
            code, result = self.dispatch(action='creative_instruction', engine_mode='deepseek',
                extra_data={'workflow':'serial', 'chapter_count':2, 'model_tier':'deepseek-flash', 'reasoning_effort':'none'})
            self.assertEqual(code, 200)
            deadline = time.monotonic() + 10
            while hub.TASKS[result['task_id']]['status'] not in ('completed','failed') and time.monotonic() < deadline:
                self.assertIsNone(self.request('/api/pending_task')[1]['task'])
                time.sleep(.02)
            task = hub.TASKS[result['task_id']]
            self.assertEqual(task['status'], 'completed', task['progress_log'])
            self.assertEqual(len(task['result']['files']), 2)
            self.assertGreater(len(calls), 2)
            self.assertEqual(calls[0]['thinking']['type'], 'disabled')
            drafts = [call['messages'][-1]['content'] for call in calls if '【阶段 生成草稿】' in call['messages'][-1]['content']]
            self.assertEqual(len(drafts), 2)
            self.assertIn('第014章_延续.md', drafts[1])
            self.assertEqual(len(manager.chapter_files(self.path)), len(before) + 2)
        finally:
            network_patch.stop()
            upstream.shutdown()
            upstream.server_close()
            for filename in set(manager.chapter_files(self.path)) - before:
                Path(filename).unlink()
            if old_settings is None:
                settings_path.unlink(missing_ok=True)
            else:
                atomic_write(settings_path, old_settings)
            hub.invalidate_cache()

    def test_catalog_reuses_metadata_after_another_chapter_changes(self):
        hub.chapter_catalog(self.book)
        with patch.object(manager, 'clean_for_tomato', wraps=manager.clean_for_tomato) as clean:
            atomic_write(self.path / '01_正文/第一卷/第014章_新增.md', '新增')
            hub.chapter_catalog(self.book)
            self.assertEqual(clean.call_count, 1)
        (self.path / '01_正文/第一卷/第014章_新增.md').unlink()

    def test_chapter_backups_are_excluded(self):
        atomic_write(self.path / '01_正文/.backup/第000章_备份.md', '备份私稿')
        self.assertFalse(any('备份' in filename for filename in manager.chapter_files(self.path)))

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

    def test_antigravity_native_prompt_preserves_newlines(self):
        import agent_client
        catalog = {'available': True, 'project_id': 'project', 'models': [{'id': 'test', 'model_enum': 'MODEL_TEST', 'default_effort': 'high', 'efforts': [{'id': 'high'}]}]}
        replies = [{'cascadeId': 'conversation'}, {'metadata': {'projectId': 'project'}}, {},
                   {'trajectorySummaries': {'conversation': {'annotations': {'permissionPreset': 'AGENT_PERMISSION_PRESET_TURBO'}}}}, {}]
        with patch('agent_client.discover', return_value=catalog), patch('agent_client.rpc', side_effect=replies) as rpc:
            self.assertEqual(agent_client.new_conversation('第一行\n第二行设定', '测试', 'test'), 'conversation')
            self.assertEqual(rpc.call_args_list[-1].args[1]['items'][0]['text'], '第一行\n第二行设定')
            self.assertEqual(rpc.call_args_list[0].args[1]['projectEnvConfig']['projectId'], 'project')

    def test_antigravity_permissions_checked_before_prompt(self):
        import agent_client
        catalog = {'available': True, 'project_id': 'project', 'models': [{'id': 'test', 'model_enum': 'MODEL_TEST', 'default_effort': 'high', 'efforts': [{'id': 'high'}]}]}
        with patch('agent_client.discover', return_value=catalog), patch('agent_client.rpc', side_effect=[{'cascadeId':'conversation'}, {'metadata': {'projectId': 'project'}}, {}, {'trajectorySummaries': {}}]) as rpc:
            with self.assertRaises(RuntimeError): agent_client.new_conversation('prompt', 'title', 'test')
            self.assertNotIn('SendUserCascadeMessage', [call.args[0] for call in rpc.call_args_list])

    def test_thumbnail_upload_and_content_type(self):
        from PIL import Image
        payload = self.image_payload()
        filename = 'a' * 32 + '_1.thumb.jpg'
        image = io.BytesIO()
        Image.new('RGB', (768, 432), 'white').save(image, 'JPEG')
        payload['meta']['illustrations'][0]['thumbnail_filename'] = filename
        payload['images'][filename] = base64.b64encode(image.getvalue()).decode()
        status, result = self.request('/api/sync_illustrations', payload)
        self.assertEqual(status, 200)
        self.assertEqual(result['saved_images'], 1)
        self.assertEqual(result['saved_files'], 2)
        query = urllib.parse.urlencode(dict(book=self.book, chap='第013章', img=filename))
        req = urllib.request.Request(self.url + '/api/illustration?' + query, headers={'X-Novel-Token': hub.AUTH_TOKEN})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req) as response:
            self.assertEqual(response.headers['Content-Type'], 'image/jpeg')

    def test_missing_thumbnail_rejects_publication(self):
        payload = self.image_payload()
        payload['meta']['illustrations'][0]['thumbnail_filename'] = 'a' * 32 + '_1.thumb.jpg'
        self.assertEqual(self.request('/api/sync_illustrations', payload)[0], 400)
        self.assertFalse(self.extras()['has_extras'])

    def test_corrupt_png_rejected(self):
        payload = self.image_payload()
        filename = next(iter(payload['images']))
        raw = base64.b64decode(payload['images'][filename])
        payload['images'][filename] = base64.b64encode(raw[:32]).decode()
        self.assertEqual(self.request('/api/sync_illustrations', payload)[0], 400)

    def test_bridge_cloud_requests_use_dedicated_opener(self):
        import sys
        streams = sys.stdout, sys.stderr, sys.excepthook
        try:
            import local_bridge
        finally:
            sys.stdout, sys.stderr, sys.excepthook = streams
        with patch.object(local_bridge.CLOUD_OPENER, 'open', side_effect=[io.BytesIO(b'{"ok":true}'), io.BytesIO(b'{"online":true}')]) as opener:
            self.assertTrue(local_bridge.api_post('heartbeat', {})['ok'])
            self.assertTrue(local_bridge.api_get('status')['online'])
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(opener.call_args_list[0].args[0].full_url, local_bridge.CLOUD_SERVER_URL + '/heartbeat')

    def test_creative_receipt_requires_summary_and_files(self):
        import sys
        streams = sys.stdout, sys.stderr, sys.excepthook
        try:
            import local_bridge
        finally:
            sys.stdout, sys.stderr, sys.excepthook = streams
        with patch.object(local_bridge, 'BOOKS_ROOT', str(self.root)), patch.object(local_bridge, 'ensure_antigravity_running', return_value=(True, 'ready')), patch('agent_client.new_conversation', return_value='id'), patch('agent_client.wait_for_receipt', return_value={}):
            ok, message = local_bridge.execute_agentapi('核对设定')
        self.assertFalse(ok)
        self.assertIn('完成回执', message)

    def test_creative_receipt_accepts_verified_summary(self):
        import sys
        streams = sys.stdout, sys.stderr, sys.excepthook
        try:
            import local_bridge
        finally:
            sys.stdout, sys.stderr, sys.excepthook = streams
        with patch.object(local_bridge, 'BOOKS_ROOT', str(self.root)), patch.object(local_bridge, 'ensure_antigravity_running', return_value=(True, 'ready')), patch('agent_client.new_conversation', return_value='id'), patch('agent_client.wait_for_receipt', return_value={'summary': '已核对设定', 'files': []}):
            ok, message = local_bridge.execute_agentapi('核对设定')
        self.assertTrue(ok)
        self.assertIn('已核对设定', message)


class DesktopAgentTests(unittest.TestCase):
    def test_idle_missing_receipt_recovers_once(self):
        import agent_client
        import itertools
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            def rpc(method, *args, **kwargs):
                if method == 'SendUserCascadeMessage':
                    path.write_text('{"summary":"saved","files":[]}', encoding='utf-8')
                    return {}
                return {'status': 'CASCADE_RUN_STATUS_IDLE', 'trajectory': {'steps': [{'type': 'CORTEX_STEP_TYPE_PLANNER_RESPONSE'}]}}
            with patch('agent_client.rpc', side_effect=rpc) as calls, patch('agent_client.time.sleep'), patch('agent_client.time.monotonic', side_effect=itertools.count(0, 5)):
                self.assertEqual(agent_client.wait_for_receipt(path, 'id')['summary'], 'saved')
            self.assertEqual(sum(c.args[0] == 'SendUserCascadeMessage' for c in calls.call_args_list), 1)

    def test_idle_recovery_has_bounded_attempts(self):
        import agent_client
        import itertools
        idle = {'status': 'CASCADE_RUN_STATUS_IDLE', 'trajectory': {'steps': [{'type': 'CORTEX_STEP_TYPE_PLANNER_RESPONSE'}]}}
        with tempfile.TemporaryDirectory() as directory, patch('agent_client.rpc', return_value=idle) as calls, patch('agent_client.time.sleep'), patch('agent_client.time.monotonic', side_effect=itertools.count(0, 5)):
            with self.assertRaisesRegex(RuntimeError, '仍未保存'):
                agent_client.wait_for_receipt(Path(directory) / 'missing.json', 'id')
            self.assertEqual(sum(c.args[0] == 'SendUserCascadeMessage' for c in calls.call_args_list), 1)

    def test_idle_invalid_receipt_fails_without_hanging(self):
        import agent_client
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            path.write_text('{"summary":"invalid"quote"}', encoding='utf-8')
            with patch('agent_client.rpc', return_value={'status': 'CASCADE_RUN_STATUS_IDLE'}):
                with self.assertRaisesRegex(ValueError, '不是有效 JSON'):
                    agent_client.wait_for_receipt(path, 'id', timeout=5)

    def test_receipt_accepts_utf8_bom(self):
        import agent_client
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            path.write_text('{"summary":"done","files":[]}', encoding='utf-8-sig')
            self.assertEqual(agent_client.wait_for_receipt(path, 'id')['summary'], 'done')

    def test_antigravity_reports_terminal_error_without_waiting_for_receipt(self):
        import agent_client
        trajectory = {'status': 'CASCADE_RUN_STATUS_IDLE', 'trajectory': {'steps': [
            {'type': 'CORTEX_STEP_TYPE_ERROR_MESSAGE', 'errorMessage': {'error': {'modelErrorMessage': 'User location is not supported for the API use.'}}}
        ]}}
        with tempfile.TemporaryDirectory() as directory, patch('agent_client.rpc', return_value=trajectory):
            with self.assertRaisesRegex(RuntimeError, 'location is not supported'):
                agent_client.wait_for_receipt(Path(directory) / 'missing.json', 'conversation', timeout=5)

    def test_codex_wait_checks_expected_turn(self):
        from codex_client import CodexClient
        client = CodexClient()
        client.caller_thread = 'caller'
        client.task_turns['task'] = 'actual-task-turn'
        snapshots = [
            {'turns': [{'id': 'initialization', 'status': 'completed'}]},
            {'turns': [{'id': 'actual-task-turn', 'status': 'completed'}]}
        ]
        with patch('codex_desktop.call_desktop', side_effect=snapshots) as call, patch('codex_client.time.sleep'):
            client.wait('task')
        self.assertEqual(call.call_count, 2)

    def test_codex_wait_reports_failed_turn(self):
        from codex_client import CodexClient
        client = CodexClient()
        client.caller_thread = 'caller'
        with patch('codex_desktop.call_desktop', return_value={'turns': [{'status': 'failed'}]}):
            with self.assertRaises(RuntimeError):
                client.wait('task')

    def test_codex_requires_full_access_before_creating(self):
        import codex_desktop
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / '.codex-global-state.json'
            state.write_text(json.dumps({'owner': {'permission-selection-by-host-id:local': {'kind': 'agent-mode', 'agentMode': 'full-access'}}}))
            with patch.dict(os.environ, {'CODEX_HOME': directory}):
                codex_desktop.require_full_access()
                state.write_text(json.dumps({'owner': {'permission-selection-by-host-id:local': {'kind': 'agent-mode', 'agentMode': 'workspace-write'}}}))
                with self.assertRaises(RuntimeError):
                    codex_desktop.require_full_access()

    def test_catalog_failure_preserves_other_engine(self):
        import desktop_agents
        with patch('agent_client.discover', side_effect=RuntimeError('offline')), patch.object(desktop_agents.codex_client.CLIENT, 'discover', return_value={'id': 'codex', 'available': True, 'models': [{'id': 'test'}]}):
            engines = desktop_agents.catalog()['engines']
        self.assertFalse(engines[0]['available'])
        self.assertEqual(engines[0]['models'], [])
        self.assertTrue(engines[1]['available'])

    def test_unavailable_model_never_changes_to_another_model(self):
        from agent_workspace import selected_model
        catalog = {'available': True, 'models': [{'id': 'selected', 'disabled': True}, {'id': 'other', 'efforts': [{'id': 'high'}]}]}
        with self.assertRaises(ValueError):
            selected_model(catalog, 'selected', 'high')


class SerialWritingTests(unittest.TestCase):
    def setUp(self):
        import sys
        streams = sys.stdout, sys.stderr, sys.excepthook
        try:
            import local_bridge
        finally:
            sys.stdout, sys.stderr, sys.excepthook = streams
        self.bridge = local_bridge
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name) / '作品' / '01_正文' / '第一卷_人界篇'
        self.directory.mkdir(parents=True)
        (self.directory / '第001章_起点.md').write_text('第一章前情与伏笔', encoding='utf-8')
        self.root_patch = patch.object(self.bridge, 'BOOKS_ROOT', self.temporary.name)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.temporary.cleanup)
        self.prompts = []

    def write_chapter(self, prompt, **kwargs):
        import re
        from writing_fixtures import response
        if '【阶段 生成草稿】' in prompt:
            self.prompts.append(prompt)
        output = re.search(r'只将本阶段要求的 JSON 或正文写入 `([^`]+)`', prompt).group(1)
        atomic_write(output, response(prompt))
        return True, '完成'

    def run_serial(self, count):
        return self.bridge.execute_serial_chapters('作品', count, '保持伏笔一致', 'codex', 'test-model', 'high', lambda text: None)

    def test_sequential_prompts_use_new_previous_chapter(self):
        with patch.object(self.bridge, 'execute_agentapi', side_effect=self.write_chapter), patch.object(self.bridge, 'sync_all_books_to_cloud', return_value=(True, 0, 0)) as sync:
            ok, result = self.run_serial(2)
        self.assertTrue(ok, result)
        self.assertEqual(sync.call_count, 2)
        self.assertIn('第002章_延续.md', self.prompts[1])
        self.assertIn('正文承接前情', self.prompts[1])
        self.assertIn('人物位置与认知', self.prompts[1])
        self.assertEqual((self.directory / '第001章_起点.md').read_text(encoding='utf-8'), '第一章前情与伏笔')

    def test_ten_then_three_starts_twentieth_then_thirtieth(self):
        original = self.directory / '第001章_起点.md'
        original.rename(self.directory / '第019章_前情.md')
        with patch.object(self.bridge, 'execute_agentapi', side_effect=self.write_chapter), patch.object(self.bridge, 'sync_all_books_to_cloud', return_value=(True, 0, 0)):
            self.assertTrue(self.run_serial(10)[0])
            self.assertTrue(self.run_serial(3)[0])
        self.assertIn('第020章', self.prompts[0])
        self.assertIn('第029章', self.prompts[9])
        self.assertIn('第030章', self.prompts[10])
        self.assertIn('第032章', self.prompts[12])
        self.assertEqual(len(list(self.directory.glob('*.md'))), 14)

    def test_failure_stops_remaining_chapters(self):
        def execute(prompt, **kwargs):
            return (False, '额度不足') if len(self.prompts) >= 1 and '【阶段 生成草稿】' in prompt else self.write_chapter(prompt, **kwargs)
        with patch.object(self.bridge, 'execute_agentapi', side_effect=execute) as execute_mock, patch.object(self.bridge, 'sync_all_books_to_cloud', return_value=(True, 0, 0)):
            ok, result = self.run_serial(3)
        self.assertFalse(ok)
        self.assertIn('1/3', result)
        self.assertGreater(execute_mock.call_count, 2)
        self.assertEqual(len(list(self.directory.glob('*.md'))), 2)

    def test_missing_chapter_cannot_complete_batch(self):
        with patch.object(self.bridge, 'execute_agentapi', return_value=(True, '完成')):
            ok, result = self.run_serial(2)
        self.assertFalse(ok)
        self.assertIn('0/2', result)

    def test_sync_failure_stops_after_local_completion(self):
        with patch.object(self.bridge, 'execute_agentapi', side_effect=self.write_chapter) as execute, patch.object(self.bridge, 'sync_all_books_to_cloud', return_value=(False, 0, 0)):
            ok, result = self.run_serial(3)
        self.assertFalse(ok)
        self.assertIn('1/3', result)
        self.assertGreater(execute.call_count, 1)

    def test_history_change_stops_batch(self):
        def execute(prompt, **kwargs):
            (self.directory / '第001章_起点.md').write_text('异常改写', encoding='utf-8')
            return self.write_chapter(prompt, **kwargs)
        with patch.object(self.bridge, 'execute_agentapi', side_effect=execute):
            ok, result = self.run_serial(2)
        self.assertFalse(ok)
        self.assertIn('历史正文发生变化', result)



class SyncBridgeTests(unittest.TestCase):
    def setUp(self):
        import local_bridge
        self.bridge = local_bridge
        self.temporary = tempfile.TemporaryDirectory(dir='.test-data')
        self.root = Path(self.temporary.name)
        self.file = self.root / '作品/01_正文/第一卷/第001章_开头.md'
        atomic_write(self.file, '旧正文')
        self.patch = patch.object(self.bridge, 'BOOKS_ROOT', str(self.root))
        self.patch.start()
        self.get_patch = patch.object(self.bridge, "api_get", return_value={"books": {}, "deleted": {}})
        self.get_patch.start()
        self.bridge.SYNC_RECEIPTS.clear()
        self.bridge.SYNC_REPORT_PENDING = False
        self.sent = []

    def tearDown(self):
        self.patch.stop()
        self.get_patch.stop()
        self.temporary.cleanup()

    def post(self, endpoint, payload):
        from sync_protocol import content_fingerprint
        if endpoint == 'sync_data':
            self.sent.append(payload)
            return {'ok': True, 'fingerprint': content_fingerprint(payload['chapters'], payload['settings'])}
        return {'ok': True}

    def test_content_change_detected_with_restored_timestamp_and_same_size(self):
        with patch.object(self.bridge, 'api_post', side_effect=self.post):
            self.assertTrue(self.bridge.sync_all_books_to_cloud(False)[0])
            stat = self.file.stat()
            atomic_write(self.file, '新正文')
            os.utime(self.file, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertTrue(self.bridge.sync_all_books_to_cloud(False)[0])
            self.assertEqual(len(self.sent), 2)
            self.assertEqual(self.sent[-1]['chapters'][0]['content'], '新正文')
            self.assertTrue(self.bridge.sync_all_books_to_cloud(False)[0])
            self.assertEqual(len(self.sent), 2)

    def test_failed_upload_retries_identical_content(self):
        with patch.object(self.bridge, 'api_post', return_value=None):
            self.assertFalse(self.bridge.sync_all_books_to_cloud(False)[0])
        with patch.object(self.bridge, 'api_post', side_effect=self.post):
            self.assertTrue(self.bridge.sync_all_books_to_cloud(False)[0])
        self.assertEqual(len(self.sent), 1)

    def test_bad_receipt_retries_identical_content(self):
        with patch.object(self.bridge, 'api_post', return_value={'ok': True, 'fingerprint':'bad'}):
            self.assertFalse(self.bridge.sync_all_books_to_cloud(False)[0])
        self.assertEqual(self.bridge.SYNC_RECEIPTS, {})
        with patch.object(self.bridge, 'api_post', side_effect=self.post):
            self.assertTrue(self.bridge.sync_all_books_to_cloud(False)[0])

    def test_forced_reconcile_uploads_unchanged_files(self):
        with patch.object(self.bridge, 'api_post', side_effect=self.post):
            self.bridge.sync_all_books_to_cloud(False)
            self.bridge.sync_all_books_to_cloud(True)
        self.assertEqual(len(self.sent), 2)

    def test_report_failure_is_retried_without_reupload(self):
        def fail_report(endpoint, payload):
            return self.post(endpoint, payload) if endpoint == 'sync_data' else None
        with patch.object(self.bridge, 'api_post', side_effect=fail_report):
            self.assertFalse(self.bridge.sync_all_books_to_cloud(False)[0])
        with patch.object(self.bridge, 'api_post', side_effect=self.post) as request:
            self.assertTrue(self.bridge.sync_all_books_to_cloud(False)[0])
            self.assertEqual(request.call_args.args[0], 'sync_report')
        self.assertEqual(len(self.sent), 1)

    def test_missing_source_root_is_failure(self):
        with patch.object(self.bridge, 'BOOKS_ROOT', str(self.root / 'missing')), patch.object(self.bridge, 'api_post', side_effect=self.post):
            self.assertFalse(self.bridge.sync_all_books_to_cloud()[0])

    def test_snapshot_read_race_does_not_acknowledge_unuploaded_edits(self):
        original = self.bridge.collect_book_data
        def changing_read(book):
            result = original(book)
            atomic_write(self.file, '读取期间保存的新稿')
            return result
        with patch.object(self.bridge, 'collect_book_data', side_effect=changing_read), patch.object(self.bridge, 'api_post', side_effect=self.post):
            self.assertFalse(self.bridge.sync_all_books_to_cloud(False)[0])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.bridge.SYNC_RECEIPTS, {})

if __name__ == '__main__':
    unittest.main()
