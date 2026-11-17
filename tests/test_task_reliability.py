"""Regressions for interrupted desktop tasks and acknowledged cloud writes."""
import io
import json
import socket
import ssl
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

streams = sys.stdout, sys.stderr, sys.excepthook
import local_bridge as bridge
sys.stdout, sys.stderr, sys.excepthook = streams

from storage import atomic_write
from writing_fixtures import response


class TaskReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir='.test-data')
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_second_daemon_cannot_bind_single_instance_port(self):
        previous = bridge._LOCK_SOCKET
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        with patch.object(bridge, '_LOCK_SOCKET', None):
            bridge.ensure_single_instance(port)
            owner = bridge._LOCK_SOCKET
            try:
                with self.assertRaises(SystemExit):
                    bridge.ensure_single_instance(port)
                self.assertIs(bridge._LOCK_SOCKET, owner)
            finally:
                owner.close()
        self.assertIs(bridge._LOCK_SOCKET, previous)

    def test_pinned_connection_keeps_hostname_and_certificate_checks(self):
        connection = bridge.CloudHTTPSConnection('scientificexploration.art', context=bridge.SSL_CTX)
        with patch('http.client.HTTPSConnection.connect') as connect, patch.object(socket, 'create_connection') as create:
            connection.connect()
            connection._create_connection(('scientificexploration.art', 443), 4, None)
        connect.assert_called_once()
        create.assert_called_once_with(('124.220.54.64', 443), 4, None)
        self.assertEqual(connection.host, 'scientificexploration.art')
        self.assertTrue(connection._context.check_hostname)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)

    def test_http_conflict_details_reach_sync_caller(self):
        error = urllib.error.HTTPError('https://example.test', 400, 'conflict', {}, io.BytesIO(
            json.dumps({'error': '云端已更新，请重新同步后重试'}).encode()))
        with patch.object(bridge.CLOUD_OPENER, 'open', side_effect=error) as call:
            self.assertIn('云端已更新', bridge.api_post('sync_data', {})['error'])
        self.assertEqual(call.call_count, 1)

    def test_transient_http_failure_retries_identical_request(self):
        failure = urllib.error.HTTPError('https://example.test', 503, 'unavailable', {}, io.BytesIO(b'{}'))
        with patch.object(bridge.CLOUD_OPENER, 'open', side_effect=[failure, io.BytesIO(b'{"ok":true}')]) as call, \
                patch.object(bridge.time, 'sleep'):
            self.assertTrue(bridge.api_post('heartbeat', {})['ok'])
        self.assertIs(call.call_args_list[0].args[0], call.call_args_list[1].args[0])

    def test_terminal_report_survives_lost_ack_and_replays_same_event(self):
        reports = self.root / 'reports.json'
        active = self.root / 'active.json'
        atomic_write(active, '{"id":"task"}')
        with patch.object(bridge, 'TASK_REPORTS_PATH', reports), patch.object(bridge, 'ACTIVE_JOB_PATH', active), \
                patch.object(bridge, 'api_post', return_value=None):
            bridge.report_task({'id': 'task', 'status': 'completed', 'log': '已保存'})
            pending = json.loads(reports.read_text())['task']
            self.assertTrue(active.exists())
        with patch.object(bridge, 'TASK_REPORTS_PATH', reports), patch.object(bridge, 'ACTIVE_JOB_PATH', active), \
                patch.object(bridge, 'api_post', return_value={'ok': True}) as post:
            self.assertTrue(bridge.flush_task_reports())
        self.assertEqual(post.call_args.args[1], pending)
        self.assertEqual(json.loads(reports.read_text()), {})
        self.assertFalse(active.exists())

    def test_resumed_conversation_waits_for_previous_turn_before_sending(self):
        session = {'id': 'existing', 'receipt': 'previous.json'}
        events = []
        def wait(path, *args):
            events.append(('wait', str(path)))
            return {'summary': '完成', 'files': []}
        def follow(*args):
            events.append(('send', 'existing'))
            return 'existing'
        with patch.object(bridge, 'BOOKS_ROOT', str(self.root)), \
                patch.object(bridge, 'ensure_antigravity_running', return_value=(True, 'ready')), \
                patch.object(bridge.desktop_agents, 'wait_for_receipt', side_effect=wait), \
                patch.object(bridge.desktop_agents, 'continue_conversation', side_effect=follow):
            self.assertTrue(bridge.execute_agentapi('继续', session=session)[0])
        self.assertEqual(events[0], ('wait', 'previous.json'))
        self.assertEqual(events[1], ('send', 'existing'))
        self.assertEqual(events[2][0], 'wait')
        self.assertNotEqual(session['receipt'], 'previous.json')

    def test_antigravity_stage_saves_final_text_without_agent_file_commands(self):
        root = self.root / '作品'
        root.mkdir()
        session = {}
        with patch.object(bridge.desktop_agents, 'new_conversation', return_value='text-conversation') as create, \
                patch.object(bridge.agent_client, 'wait_for_response', return_value='完整正文'):
            result = bridge.desktop_stage(root, '作品', '生成正文', 'antigravity', 'selected-model', 'high', lambda m: None, session)
        self.assertEqual(result, '完整正文')
        self.assertEqual(session['id'], 'text-conversation')
        self.assertIn('无需调用工具、执行命令、写文件', create.call_args.args[0])
        self.assertEqual(create.call_args.args[2:], ('selected-model', 'antigravity', 'high'))
        self.assertEqual(next((root / '.story-memory/agent-results').glob('*.txt')).read_text(encoding='utf-8'), result)

    def test_ten_native_text_chapters_reuse_writer_and_reviewer(self):
        root = self.root / '作品'
        atomic_write(root / '01_正文/第一卷/第001章_开篇.md', '前情在码头。')
        atomic_write(root / '00_设定集/剧情账本.md', '截至第一章，主角在码头。')
        pending = {}
        def create(prompt, *args):
            identifier = f'conversation-{len(pending)}'
            pending[identifier] = prompt
            return identifier
        def follow(identifier, prompt, *args):
            pending[identifier] = prompt
            return identifier
        def wait(identifier, *args):
            return response(pending[identifier])
        with patch.object(bridge, 'BOOKS_ROOT', str(self.root)), \
                patch.object(bridge.desktop_agents, 'new_conversation', side_effect=create) as new, \
                patch.object(bridge.desktop_agents, 'continue_conversation', side_effect=follow) as continued, \
                patch.object(bridge.agent_client, 'wait_for_response', side_effect=wait), \
                patch.object(bridge, 'sync_all_books_to_cloud', return_value=(True, 0, 0)):
            ok, message = bridge.execute_serial_chapters('作品', 10, '延续前情', 'antigravity', 'selected', 'high', lambda m: None)
        self.assertTrue(ok, message)
        self.assertEqual(new.call_count, 2)
        self.assertEqual(continued.call_count, 19)
        self.assertEqual(len(bridge.novel_manager.chapter_files(root)), 11)

    def test_restart_counts_saved_chapters_and_finishes_original_ten(self):
        root = self.root / '作品'
        atomic_write(root / '01_正文/第一卷/第001章_起点.md', '前情在码头。')
        atomic_write(root / '00_设定集/剧情账本.md', '截至第001章，主角在码头调查旧约。')
        requests = []
        interrupted = False

        def stage(book_root, book, prompt, *args):
            nonlocal interrupted
            if prompt.startswith('【阶段 生成草稿】'):
                requests.append(prompt)
                if len(requests) == 3 and not interrupted:
                    interrupted = True
                    raise KeyboardInterrupt('模拟守护进程意外退出')
            return response(prompt)

        with patch.object(bridge, 'BOOKS_ROOT', str(self.root)), patch.object(bridge, 'desktop_stage', side_effect=stage), \
                patch.object(bridge, 'sync_all_books_to_cloud', return_value=(True, 0, 0)):
            with self.assertRaises(KeyboardInterrupt):
                bridge.execute_serial_chapters('作品', 10, '延续', 'antigravity', 'model', 'high', lambda m: None, task_id='original')
            self.assertEqual(len(bridge.novel_manager.chapter_files(root)), 3)
            ok, result = bridge.execute_serial_chapters('作品', 10, '延续', 'antigravity', 'model', 'high', lambda m: None, task_id='original')
            self.assertTrue(ok, result)
            self.assertIn('10', result)
            self.assertEqual(len(bridge.novel_manager.chapter_files(root)), 11)
            self.assertTrue(bridge.execute_serial_chapters('作品', 10, '延续', 'antigravity', 'model', 'high', lambda m: None, task_id='original')[0])
            self.assertEqual(len(bridge.novel_manager.chapter_files(root)), 11)
            self.assertTrue(all(json.loads(path.read_text(encoding='utf-8'))['batch_count'] == 10
                for path in (root / '.story-memory/reviews').glob('*.json')))


class WorkerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir='.test-data')
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        for name, value in [('ACTIVE_JOB_PATH', self.root / 'active.json'),
                            ('TASK_REPORTS_PATH', self.root / 'reports.json'), ('ACTIVE_WORKERS', {})]:
            patcher = patch.object(bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.task = {'id': 'first', 'book_id': '作品一', 'status': 'running',
                     'extra_data': {'workflow': 'serial'}}

    def test_parallel_tasks_are_persisted_before_worker_start(self):
        def started():
            self.assertIn('first', bridge.active_jobs())
        with patch.object(bridge.threading, 'Thread') as thread:
            thread.return_value.start.side_effect = started
            self.assertTrue(bridge.start_task_worker(self.task))
            self.assertFalse(bridge.start_task_worker(self.task))
            self.assertEqual(thread.call_count, 1)
        other = {**self.task, 'id': 'second', 'book_id': '作品二'}
        bridge.remember_task(other)
        with patch.object(bridge, 'api_post', return_value={'ok': True}):
            bridge.report_task({'id': 'first', 'status': 'completed'})
        self.assertEqual(bridge.active_jobs(), {'second': other})

    def test_restart_recovers_original_serial_task(self):
        bridge.remember_task(self.task)
        with patch.object(bridge, 'api_get', return_value=self.task), \
                patch.object(bridge, 'start_task_worker', return_value=True) as start:
            self.assertTrue(bridge.recover_active_tasks())
        start.assert_called_once_with(self.task)

    def test_restart_waits_for_cloud_without_discarding_checkpoint(self):
        bridge.remember_task(self.task)
        with patch.object(bridge, 'api_get', return_value=None), patch.object(bridge, 'start_task_worker') as start:
            self.assertFalse(bridge.recover_active_tasks())
        start.assert_not_called()
        self.assertIn('first', bridge.active_jobs())

    def test_restart_does_not_reexecute_terminal_task(self):
        bridge.remember_task(self.task)
        with patch.object(bridge, 'api_get', return_value={**self.task, 'status': 'failed'}), \
                patch.object(bridge, 'start_task_worker') as start:
            self.assertTrue(bridge.recover_active_tasks())
        start.assert_not_called()
        self.assertFalse(bridge.ACTIVE_JOB_PATH.exists())

    def test_cancel_ack_stops_stage_before_saving(self):
        with patch.object(bridge, 'api_post', return_value={'ok': True, 'cancel_requested': True}):
            with self.assertRaises(bridge.TaskCancelled):
                bridge.report_task({'id': 'first', 'status': 'running', 'log': '生成中'})

    def test_transient_location_rejection_retries_original_model_once(self):
        session = {}
        with patch.object(bridge.desktop_agents, 'new_conversation', side_effect=['old', 'retry']) as create, \
                patch.object(bridge.agent_client, 'wait_for_response', side_effect=[
                    RuntimeError('Antigravity 执行失败 User location is not supported for the API use.'), '完整正文']), \
                patch.object(bridge.time, 'sleep'):
            self.assertEqual(bridge.desktop_stage(self.root, '作品', '生成正文', 'antigravity', 'selected', 'high',
                                                  lambda message: None, session), '完整正文')
        self.assertEqual(create.call_count, 2)
        self.assertTrue(all(call.args[2:] == ('selected', 'antigravity', 'high') for call in create.call_args_list))
        self.assertEqual(session['id'], 'retry')

    def test_persistent_rejection_is_bounded(self):
        with patch.object(bridge.desktop_agents, 'new_conversation', return_value='id') as create, \
                patch.object(bridge.agent_client, 'wait_for_response', side_effect=RuntimeError('User location is not supported')), \
                patch.object(bridge.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'location'):
                bridge.desktop_stage(self.root, '作品', '生成正文', 'antigravity', 'selected', 'high', lambda message: None)
        self.assertEqual(create.call_count, 2)

    def test_supervisor_adopts_existing_bridge(self):
        import launch_daemon
        with patch.object(launch_daemon, 'acquire_port', side_effect=[unittest.mock.MagicMock(), None]), \
                patch.object(launch_daemon, 'launch_hidden') as launch, \
                patch.object(launch_daemon.time, 'sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                launch_daemon.supervise()
        launch.assert_not_called()

    def test_recovered_native_session_waits_before_resending(self):
        events = []
        session = {'id': 'existing', 'recovering': True, 'book_root': str(self.root.resolve())}
        with patch.object(bridge.agent_client, 'wait_for_idle', side_effect=lambda *args: events.append('idle')), \
                patch.object(bridge.desktop_agents, 'continue_conversation', side_effect=lambda *args: events.append('send') or 'existing'), \
                patch.object(bridge.agent_client, 'wait_for_response', return_value='完整正文'):
            bridge.desktop_stage(self.root, '作品', '生成正文', 'antigravity', 'selected', 'high', lambda message: None, session)
        self.assertEqual(events, ['idle', 'send'])
        self.assertNotIn('recovering', session)

    def test_reviewer_requires_json_at_both_ends_of_context(self):
        with patch.object(bridge.desktop_agents, 'new_conversation', return_value='reviewer') as create, \
                patch.object(bridge.agent_client, 'wait_for_response', return_value='{"passed":true,"issues":[]}'):
            bridge.desktop_stage(self.root, '作品', '【阶段 一致性审核】\n审核草稿', 'antigravity',
                                 'selected', 'high', lambda message: None, {})
        instruction = create.call_args.args[0]
        self.assertIn('本轮执行独立审核', instruction)
        self.assertIn('passed 布尔值和 issues 数组', instruction)
        self.assertEqual(instruction.count('最终回复输出单个 JSON 对象'), 2)


class AntigravityTextReplyTests(unittest.TestCase):
    def trajectory(self, text='本轮正文', status='CASCADE_RUN_STATUS_IDLE'):
        return {'status': status, 'trajectory': {'steps': [
            {'type': 'CORTEX_STEP_TYPE_USER_INPUT', 'userInput': {'items': [{'text': 'old'}]}},
            {'type': 'CORTEX_STEP_TYPE_PLANNER_RESPONSE', 'plannerResponse': {'response': '旧轮正文'}},
            {'type': 'CORTEX_STEP_TYPE_USER_INPUT', 'userInput': {'items': [{'text': 'current-marker'}]}},
            {'type': 'CORTEX_STEP_TYPE_PLANNER_RESPONSE', 'status': 'CORTEX_STEP_STATUS_DONE',
             'plannerResponse': {'response': text}}]}}

    def test_returns_current_final_reply(self):
        import agent_client
        with patch.object(agent_client, 'rpc', return_value=self.trajectory()):
            self.assertEqual(agent_client.wait_for_response('id', 'current-marker'), '本轮正文')

    def test_partial_reply_cannot_finish_running_turn(self):
        import agent_client
        with patch.object(agent_client, 'rpc', side_effect=[self.trajectory('半段', 'CASCADE_RUN_STATUS_RUNNING'), self.trajectory()]) as rpc, \
                patch.object(agent_client.time, 'sleep'):
            self.assertEqual(agent_client.wait_for_response('id', 'current-marker'), '本轮正文')
        self.assertEqual(rpc.call_count, 2)

    def test_old_turn_is_ignored_while_new_turn_is_loading(self):
        import agent_client
        stale = self.trajectory()
        stale['trajectory']['steps'] = stale['trajectory']['steps'][:2]
        with patch.object(agent_client, 'rpc', side_effect=[stale, self.trajectory()]), patch.object(agent_client.time, 'sleep'):
            self.assertEqual(agent_client.wait_for_response('id', 'current-marker'), '本轮正文')

    def test_empty_final_reply_has_explicit_error(self):
        import agent_client
        with patch.object(agent_client, 'rpc', return_value=self.trajectory('')):
            with self.assertRaisesRegex(RuntimeError, '未返回完整'):
                agent_client.wait_for_response('id', 'current-marker')

    def test_stopped_current_turn_reports_upstream_error(self):
        import agent_client
        result = self.trajectory()
        result['trajectory']['steps'][-1] = {'type': 'CORTEX_STEP_TYPE_ERROR_MESSAGE',
            'errorMessage': {'error': {'shortError': 'quota exceeded'}}}
        with patch.object(agent_client, 'rpc', return_value=result):
            with self.assertRaisesRegex(RuntimeError, 'quota exceeded'):
                agent_client.wait_for_response('id', 'current-marker')

    def test_connection_outage_is_bounded_and_reports_reconnecting(self):
        import agent_client
        elapsed = [0]
        progress = []
        def advance(seconds):
            elapsed[0] += 31
        with patch.object(agent_client, 'rpc', side_effect=OSError('connection lost')) as rpc, \
                patch.object(agent_client.time, 'monotonic', side_effect=lambda: elapsed[0]), \
                patch.object(agent_client.time, 'sleep', side_effect=advance):
            with self.assertRaisesRegex(RuntimeError, '持续无法连接'):
                agent_client.wait_for_response('id', 'current-marker', progress.append)
        self.assertEqual(rpc.call_count, 3)
        self.assertTrue(any('正在重连' in message for message in progress))

    def test_temporary_disconnect_recovers_current_reply(self):
        import agent_client
        with patch.object(agent_client, 'rpc', side_effect=[OSError('connection lost'), self.trajectory()]), \
                patch.object(agent_client.time, 'sleep'):
            self.assertEqual(agent_client.wait_for_response('id', 'current-marker'), '本轮正文')

    def test_previous_running_turn_finishes_before_recovery(self):
        import agent_client
        with patch.object(agent_client, 'rpc', side_effect=[self.trajectory(status='CASCADE_RUN_STATUS_RUNNING'), self.trajectory()]) as rpc, \
                patch.object(agent_client.time, 'sleep'):
            agent_client.wait_for_idle('existing')
        self.assertEqual(rpc.call_count, 2)


if __name__ == '__main__':
    unittest.main()
