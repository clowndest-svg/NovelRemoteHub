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


if __name__ == '__main__':
    unittest.main()
