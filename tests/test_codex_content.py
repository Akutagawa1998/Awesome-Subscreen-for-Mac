import threading
import time
import unittest
from unittest.mock import Mock, patch

from console.codex_bridge import CodexRpcError
from console.codex_bridge import CodexBridge
from server import App


THREAD = '00000000-0000-0000-0000-000000000001'
ACTIVE = {'id': THREAD, 'name': 'Synthetic session', 'preview': 'Synthetic preview',
          'status': {'type': 'active', 'activeFlags': ['waitingOnApproval']}}


def fixture(mode='local_readonly'):
    app = App.__new__(App)
    app.codex = Mock(connection_mode=mode, connection_generation=1, connected=True, attached={THREAD})
    app.codex.approvals.return_value = [{'id': 1, 'params': {'threadId': THREAD}}]
    app.store = Mock()
    app.store.read.return_value = {'hidden': []}
    app.lock = threading.RLock()
    app.snapshot = {'connected': True, 'connectionMode': mode, 'connectionGeneration': 1, 'threads': [ACTIVE],
                    'limits': {'old': 'quota'}, 'usage': {'old': 'usage'}, 'fetchedAt': 100}
    app.usage_time = time.time()
    return app


class CodexContentTests(unittest.TestCase):
    def test_old_approval_generation_cannot_match_a_reused_request_id(self):
        bridge = CodexBridge()
        bridge.connected = True
        bridge.connection_mode = 'control'
        bridge.ws = Mock()
        bridge.pending['1'] = {'id': 1, 'method': 'item/commandExecution/requestApproval',
                               'params': {'threadId': THREAD, 'command': 'echo synthetic'}}
        with self.assertRaisesRegex(ValueError, '连接已变化'):
            bridge.respond(1, THREAD, 'accept', expected_generation=-1)
        bridge.ws.send.assert_not_called()

    def test_readonly_status_cannot_claim_runtime_or_expose_cached_approvals(self):
        app = fixture()
        status = app.status()
        self.assertEqual(status['threads'][0]['status'], {'type': 'notLoaded'})
        self.assertEqual(status['approvals'], [])
        self.assertEqual(status['attached'], [])
        self.assertEqual(ACTIVE['status']['type'], 'active')
        app.codex.approvals.assert_not_called()

    def test_offline_control_keeps_history_without_live_state_or_quota(self):
        app = fixture('control')
        app.codex.connected = False
        status = app.status()
        self.assertFalse(status['connected'])
        self.assertTrue(status['stale'])
        self.assertEqual(status['threads'][0]['name'], 'Synthetic session')
        self.assertEqual(status['threads'][0]['status'], {'type': 'notLoaded'})
        self.assertIsNone(status['limits'])
        self.assertIsNone(status['usage'])
        self.assertEqual(status['approvals'], [])

    @patch('server.local_thread_metadata', return_value={})
    def test_source_change_discards_quota_cache_before_requery(self, _metadata):
        app = fixture('local_readonly')
        app.snapshot['connectionMode'] = 'control'
        observed = []

        def rpc(method, params=None):
            if method == 'thread/list':
                self.assertEqual(params['sourceKinds'], ['cli', 'vscode', 'appServer'])
                self.assertTrue(params['useStateDbOnly'])
                return {'data': [ACTIVE], 'nextCursor': None}
            observed.append((method, app.snapshot['limits'], app.snapshot['usage']))
            if method == 'account/rateLimits/read':
                return {'rateLimits': {'planType': 'test'}, 'accountId': 'never exposed'}
            raise CodexRpcError({'code': -32601, 'message': 'Unsupported'})

        app.codex.call.side_effect = rpc
        app.refresh()
        self.assertEqual(len(observed), 2)
        self.assertTrue(all(limits is None and usage is None for _, limits, usage in observed))
        self.assertEqual(app.snapshot['connectionMode'], 'local_readonly')
        self.assertEqual(app.snapshot['threads'][0]['status'], {'type': 'notLoaded'})
        self.assertNotIn('accountId', app.snapshot['limits'])
        self.assertIsNone(app.snapshot['usage'])
        self.assertEqual(app.snapshot['usageErrors'], ['usage'])

    @patch('server.local_thread_metadata', return_value={})
    def test_mid_usage_source_change_never_publishes_mixed_account_quota(self, _metadata):
        app = fixture('control')
        app.usage_time = 0

        def rpc(method, params=None):
            if method == 'thread/list':
                return {'data': [ACTIVE]}
            app.codex.connection_mode = 'local_readonly'
            return {'rateLimits': {'planType': 'wrong-source'}}

        app.codex.call.side_effect = rpc
        with self.assertRaises(ConnectionError):
            app.refresh()
        self.assertIsNone(app.snapshot['limits'])
        self.assertIsNone(app.snapshot['usage'])
        self.assertFalse(app.status()['connected'])

    @patch('server.local_thread_metadata', return_value={})
    def test_same_mode_reconnection_invalidates_quota_and_runtime_cache(self, _metadata):
        app = fixture('control')
        app.codex.connection_generation = 2
        self.assertFalse(app.status()['connected'])
        self.assertIsNone(app.status()['limits'])
        app.codex.call.side_effect = [
            {'data': [ACTIVE]}, {'rateLimits': {'planType': 'new-account'}}, {'dailyUsageBuckets': []}]
        app.refresh()
        self.assertEqual(app.snapshot['connectionGeneration'], 2)
        self.assertEqual(app.snapshot['limits']['rateLimits']['planType'], 'new-account')

    def test_history_cannot_claim_runtime_after_same_mode_reconnection(self):
        app = fixture('control')

        def rpc(method, params=None):
            if method == 'thread/read':
                return {'thread': ACTIVE}
            app.codex.connection_generation += 1
            return {'data': []}

        app.codex.call.side_effect = rpc
        detail = app.thread(THREAD)
        self.assertTrue(detail['stale'])
        self.assertEqual(detail['thread']['status'], {'type': 'notLoaded'})
        self.assertFalse(detail['attached'])
        self.assertEqual(detail['approvals'], [])

    def test_readonly_content_normalizes_untrusted_active_flags(self):
        app = fixture()
        app.codex.call.side_effect = [{'thread': ACTIVE}, {'data': [
            {'item': {'type': 'agentMessage', 'text': 'Synthetic summary'}}]}]
        detail = app.thread(THREAD)
        self.assertEqual(detail['messages'], [{'type': 'agent', 'text': 'Synthetic summary'}])
        self.assertEqual(detail['thread']['status'], {'type': 'notLoaded'})
        self.assertFalse(detail['attached'])
        self.assertEqual(detail['approvals'], [])

    def test_readonly_reply_and_attach_never_reach_rpc(self):
        app = fixture()
        for path, data in [('/api/attach', {'id': THREAD}),
                           ('/api/reply', {'id': THREAD, 'text': 'Synthetic reply'})]:
            with self.assertRaisesRegex(ValueError, '只读'):
                app.post(path, data)
        app.codex.call.assert_not_called()
        app.codex.attach.assert_not_called()

    def test_reply_blocks_fallback_that_happens_during_ownership_check(self):
        app = fixture(None)

        def connect_readonly(*_args):
            app.codex.connection_mode = 'local_readonly'
            return {'thread': ACTIVE}

        app.codex.call.side_effect = connect_readonly
        with self.assertRaisesRegex(ValueError, '只读'):
            app.post('/api/reply', {'id': THREAD, 'text': 'Synthetic reply'})
        self.assertEqual(app.codex.call.call_count, 1)
        app.codex.attach.assert_not_called()

    def test_unsupported_history_and_connection_loss_are_distinct(self):
        app = fixture()
        for failure, expected in [
            (CodexRpcError({'code': -32601, 'message': 'Unsupported'}), '不支持历史摘要'),
            (ConnectionError('synthetic disconnect'), '连接中断'),
            (CodexRpcError({'code': -32000, 'message': 'synthetic error'}), '读取失败')]:
            app.codex.call.side_effect = [{'thread': ACTIVE}, failure]
            self.assertIn(expected, app.thread(THREAD)['error'])
        app.codex.call.side_effect = FileNotFoundError('private local path')
        with self.assertRaisesRegex(RuntimeError, '本地连接暂不可用') as raised:
            app.thread(THREAD)
        self.assertNotIn('private local path', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
