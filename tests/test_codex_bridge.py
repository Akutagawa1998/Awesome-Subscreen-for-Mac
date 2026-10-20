import errno
import io
import json
import os
from pathlib import Path
import queue
import threading
import unittest
from unittest.mock import Mock, patch

from console import codex_bridge


class FakeTransport:
    def __init__(self, initialize=True, requests=True):
        self.initialize = initialize
        self.auto_requests = requests
        self.incoming = queue.Queue()
        self.sent = []
        self.condition = threading.Condition()
        self.closed = threading.Event()
        self.on_send = None

    def send(self, message):
        with self.condition:
            self.sent.append(message)
            self.condition.notify_all()
        if self.on_send:
            self.on_send(message)
        if 'id' in message:
            if message.get('method') == 'initialize':
                if self.initialize:
                    self.reply(message, {})
            elif self.auto_requests:
                self.reply(message, {'method': message.get('method')})

    def receive(self):
        message = self.incoming.get()
        if isinstance(message, Exception):
            raise message
        return message

    def reply(self, request, result):
        self.incoming.put({'id': request['id'], 'result': result})

    def wait_for(self, method):
        with self.condition:
            found = self.condition.wait_for(
                lambda: any(m.get('method') == method for m in self.sent), timeout=2)
            if not found:
                raise AssertionError('Synthetic request was not sent: ' + method)
            return next(m for m in self.sent if m.get('method') == method)

    def close(self):
        if not self.closed.is_set():
            self.closed.set()
            self.incoming.put(ConnectionError('Synthetic EOF'))


class BridgeConnectionTests(unittest.TestCase):
    def setUp(self):
        self.bridge = codex_bridge.CodexBridge()
        self.transports = []
        self.workers = []
        self.results = {}
        self.errors = {}

    def tearDown(self):
        self.bridge.close()
        for transport in self.transports:
            transport.close()
        for worker in self.workers:
            worker.join(2)
            self.assertFalse(worker.is_alive(), 'Synthetic bridge worker did not finish')

    def transport(self, **options):
        result = FakeTransport(**options)
        self.transports.append(result)
        return result

    def worker(self, name, operation):
        def run():
            try:
                self.results[name] = operation()
            except Exception as error:
                self.errors[name] = error
        result = threading.Thread(target=run, name=name, daemon=True)
        self.workers.append(result)
        return result

    def finish(self, worker):
        worker.join(2)
        self.assertFalse(worker.is_alive(), 'Synthetic bridge operation exceeded its deadline')

    def test_eof_before_initialize_fails_without_recursive_connect_and_can_retry(self):
        failed = self.transport()
        failed.incoming.put(ConnectionError('Synthetic EOF before initialize'))
        recovered = self.transport()
        request = self.bridge._request

        def after_reader_disconnect(ws, method, *args, **kwargs):
            if ws is failed and method == 'initialize':
                self.assertTrue(failed.closed.wait(2), 'Reader did not process immediate EOF')
            return request(ws, method, *args, **kwargs)

        with patch.object(self.bridge, '_open_transport', side_effect=[(failed, 'control'), (recovered, 'control')]), \
                patch.object(self.bridge, '_request', side_effect=after_reader_disconnect):
            worker = self.worker('failed-initialize', self.bridge.connect)
            worker.start()
            self.finish(worker)
            self.assertIn('failed-initialize', self.errors)
            self.assertIsInstance(self.errors['failed-initialize'], ConnectionError)
            self.assertFalse(self.bridge.connected)
            self.assertTrue(failed.closed.is_set())
            self.assertEqual(self.bridge.requests, {})
            self.assertEqual(self.bridge.connection_generation, 1, 'Initialization failure still consumes a transport generation')
            self.assertEqual(self.bridge.call('thread/list'), {'method': 'thread/list'})
        self.assertTrue(self.bridge.connected)
        self.assertEqual(self.bridge.connection_generation, 2)
        self.assertEqual(self.bridge.connection_mode, 'control')
        self.assertIsNone(self.bridge.error)

    def test_concurrent_business_calls_wait_for_initialize_and_initialized(self):
        transport = self.transport(initialize=False)
        second_checkpoint = threading.Event()
        second = self.worker('second-read', lambda: self.bridge.call('thread/read'))
        connect = self.bridge.connect

        def observed_connect():
            if threading.current_thread() is second:
                second_checkpoint.set()
            return connect()

        def observed_send(message):
            if message.get('method') == 'thread/read':
                second_checkpoint.set()

        transport.on_send = observed_send
        with patch.object(self.bridge, '_open_transport', return_value=(transport, 'control')) as factory, \
                patch.object(self.bridge, 'connect', side_effect=observed_connect):
            first = self.worker('first-list', lambda: self.bridge.call('thread/list'))
            try:
                first.start()
                transport.wait_for('initialize')
                second.start()
                self.assertTrue(second_checkpoint.wait(2), 'Concurrent call did not reach its connection gate')
                self.assertFalse(self.bridge.connected, 'Connection is not ready until initialization completes')
                self.assertEqual([m.get('method') for m in transport.sent], ['initialize'])
            finally:
                if transport.sent:
                    transport.reply(transport.sent[0], {})
            self.finish(first)
            self.finish(second)
            factory.assert_called_once()
        self.assertEqual(self.errors, {})
        self.assertEqual(self.results['first-list'], {'method': 'thread/list'})
        self.assertEqual(self.results['second-read'], {'method': 'thread/read'})
        methods = [m.get('method') for m in transport.sent]
        self.assertLess(methods.index('initialized'), methods.index('thread/list'))
        self.assertLess(methods.index('initialized'), methods.index('thread/read'))
        self.assertEqual(len({m['id'] for m in transport.sent if 'id' in m}), 3)

    def test_initialize_rpc_error_cleans_up_and_next_call_reconnects(self):
        failed = self.transport(initialize=False)
        recovered = self.transport()
        failed.on_send = lambda m: failed.incoming.put({'id': m['id'], 'error': {'message': 'Synthetic initialization rejection'}}) if m.get('method') == 'initialize' else None
        with patch.object(self.bridge, '_open_transport', side_effect=[(failed, 'control'), (recovered, 'control')]):
            with self.assertRaisesRegex(RuntimeError, 'Synthetic initialization rejection'):
                self.bridge.connect()
            self.assertFalse(self.bridge.connected)
            self.assertEqual(self.bridge.requests, {})
            self.assertTrue(failed.closed.is_set())
            self.assertEqual(self.bridge.call('thread/read'), {'method': 'thread/read'})
        self.assertIs(self.bridge.ws, recovered)
        self.assertIsNone(self.bridge.error)

    def test_stale_reader_cannot_deliver_responses_or_modify_new_connection_state(self):
        current = self.transport(requests=False)
        stale = self.transport()
        with patch.object(self.bridge, '_open_transport', return_value=(current, 'control')):
            self.bridge.connect()
            self.bridge.pending['current-request'] = {'id': 'current-request', 'params': {'threadId': 'synthetic-thread'}}
            self.bridge.attached.add('synthetic-thread')
            worker = self.worker('current-read', lambda: self.bridge.call('thread/read'))
            worker.start()
            request = current.wait_for('thread/read')
            stale.incoming.put({'id': request['id'], 'result': {'source': 'stale'}})
            stale.incoming.put({'id': 'stale-request', 'method': 'item/commandExecution/requestApproval', 'params': {}})
            stale.incoming.put({'method': 'serverRequest/resolved', 'params': {'requestId': 'current-request'}})
            stale.incoming.put(ConnectionError('Synthetic old transport EOF'))
            self.bridge._read(stale)
            self.assertTrue(self.bridge.connected)
            self.assertIs(self.bridge.ws, current)
            self.assertIsNone(self.bridge.error)
            self.assertEqual(set(self.bridge.pending), {'current-request'})
            self.assertEqual(self.bridge.attached, {'synthetic-thread'})
            current.reply(request, {'source': 'current'})
            self.finish(worker)
        self.assertEqual(self.errors, {})
        self.assertEqual(self.results['current-read'], {'source': 'current'})

    def test_request_timeout_disconnects_and_a_later_call_can_reconnect(self):
        stalled = self.transport(requests=False)
        recovered = self.transport()
        self.assertEqual(self.bridge.connection_generation, 0)
        with self.assertRaises(AttributeError):
            self.bridge.connection_generation = 99
        with patch.object(self.bridge, '_open_transport', side_effect=[(stalled, 'control'), (recovered, 'control')]):
            self.bridge.connect()
            self.assertEqual(self.bridge.connection_generation, 1)
            with self.assertRaises(TimeoutError):
                self.bridge.call('thread/read', timeout=0.02)
            self.assertFalse(self.bridge.connected)
            self.assertEqual(self.bridge.connection_generation, 1, 'Disconnect must not reset the source generation')
            self.assertEqual(self.bridge.requests, {})
            self.assertTrue(stalled.closed.is_set())
            self.assertEqual(self.bridge.call('thread/list'), {'method': 'thread/list'})
        self.assertTrue(self.bridge.connected)
        self.assertEqual(self.bridge.connection_generation, 2, 'Reconnecting in the same mode has a new source identity')
        self.assertIs(self.bridge.ws, recovered)
        self.assertIsNone(self.bridge.error)

    def test_reader_eof_wakes_a_pending_call_with_connection_error(self):
        transport = self.transport(requests=False)
        with patch.object(self.bridge, '_open_transport', return_value=(transport, 'control')):
            self.bridge.connect()
            worker = self.worker('disconnected-read', lambda: self.bridge.call('thread/read'))
            worker.start()
            transport.wait_for('thread/read')
            transport.incoming.put(ConnectionError('Synthetic EOF during request'))
            self.finish(worker)
        self.assertIsInstance(self.errors.get('disconnected-read'), ConnectionError)
        self.assertFalse(self.bridge.connected)
        self.assertEqual(self.bridge.requests, {})
        self.assertTrue(transport.closed.is_set())

    def test_remote_jsonrpc_error_keeps_its_code_without_disconnecting_transport(self):
        transport = self.transport(requests=False)
        def reject_unknown_method(message):
            if message.get('method') == 'thread/items/list':
                transport.incoming.put({'id': message['id'], 'error': {'code': -32601, 'message': 'Synthetic unsupported method'}})
        transport.on_send = reject_unknown_method
        with patch.object(self.bridge, '_open_transport', return_value=(transport, 'control')):
            self.bridge.connect()
            with self.assertRaises(codex_bridge.CodexRpcError) as error:
                self.bridge.call('thread/items/list')
        self.assertEqual(error.exception.code, -32601)
        self.assertTrue(self.bridge.connected)
        self.assertIs(self.bridge.ws, transport)
        self.assertEqual(self.bridge.requests, {})

    def test_close_cancels_pending_reads_is_idempotent_and_prevents_reopening(self):
        transport = self.transport(requests=False)
        with patch.object(self.bridge, '_open_transport', return_value=(transport, 'control')) as factory:
            self.bridge.connect()
            worker = self.worker('pending-read', lambda: self.bridge.call('thread/read'))
            worker.start()
            transport.wait_for('thread/read')
            self.bridge.close()
            self.bridge.close()
            self.finish(worker)
            self.assertIn('pending-read', self.errors)
            self.assertIsInstance(self.errors['pending-read'], ConnectionError)
            self.assertFalse(self.bridge.connected)
            self.assertEqual(self.bridge.requests, {})
            with self.assertRaises((RuntimeError, ConnectionError)):
                self.bridge.call('thread/list')
            factory.assert_called_once()

    def test_close_waits_for_an_inflight_factory_to_dispose_its_unregistered_transport(self):
        transport = self.transport()
        factory_entered, release_factory, close_checkpoint, close_finished = [threading.Event() for _ in range(4)]

        def open_transport():
            factory_entered.set()
            self.assertTrue(release_factory.wait(2), 'Synthetic transport factory was not released')
            return transport, 'control'

        def close_bridge():
            try:
                self.bridge.close()
            finally:
                close_finished.set()
                close_checkpoint.set()

        opener = self.worker('opening-factory', self.bridge.connect)
        closer = self.worker('closing-factory', close_bridge)
        lock = self.bridge.connect_lock

        class ObservedConnectLock:
            def __enter__(self):
                if threading.current_thread() is closer:
                    close_checkpoint.set()
                return lock.__enter__()

            def __exit__(self, *args):
                return lock.__exit__(*args)

        self.bridge.connect_lock = ObservedConnectLock()
        with patch.object(self.bridge, '_open_transport', side_effect=open_transport) as factory:
            try:
                opener.start()
                self.assertTrue(factory_entered.wait(2), 'Connect did not reach its transport factory')
                closer.start()
                self.assertTrue(close_checkpoint.wait(2), 'Close did not reach the factory shutdown barrier')
                self.assertTrue(self.bridge._closed)
                self.assertFalse(close_finished.is_set(), 'Close returned before the in-flight factory was cleaned up')
                self.assertFalse(transport.closed.is_set(), 'The factory has not returned its transport yet')
            finally:
                release_factory.set()
                self.finish(opener)
                if closer.ident is not None:
                    self.finish(closer)
            self.assertTrue(close_finished.is_set())
            self.assertTrue(transport.closed.is_set())
            self.assertIsInstance(self.errors.get('opening-factory'), ConnectionError)
            self.assertNotIn('closing-factory', self.errors)
            self.assertFalse(self.bridge.connected)
            self.assertIsNone(self.bridge.ws)
            with self.assertRaises(ConnectionError):
                self.bridge.call('thread/list')
            factory.assert_called_once()

    def test_local_readonly_bridge_rejects_mutations_attach_and_approval_replies(self):
        transport = self.transport()
        self.bridge.pending['old-control-request'] = {'id': 'old-control-request', 'params': {'threadId': 'synthetic-old-thread'}}
        self.bridge.attached.add('synthetic-old-thread')
        with patch.object(self.bridge, '_open_transport', return_value=(transport, 'local_readonly')):
            self.assertEqual(self.bridge.call('thread/read'), {'method': 'thread/read'})
            self.assertEqual(self.bridge.pending, {})
            self.assertEqual(self.bridge.attached, set())
            before = list(transport.sent)
            for method in ['turn/start', 'thread/resume', 'thread/archive']:
                with self.subTest(method=method), self.assertRaises(ValueError):
                    self.bridge.call(method, {'threadId': 'synthetic-thread'})
            with self.assertRaises(ValueError):
                self.bridge.attach('synthetic-thread')
            self.bridge.pending['synthetic-request'] = {'id': 'synthetic-request', 'method': 'item/commandExecution/requestApproval',
                                                        'params': {'threadId': 'synthetic-thread', 'command': 'synthetic-command'}, 'sending': False}
            with self.assertRaises(ValueError):
                self.bridge.respond('synthetic-request', 'synthetic-thread', 'accept')
            self.assertEqual(transport.sent, before)
            self.assertEqual(self.bridge.attached, set())

    def test_attach_ack_from_a_disconnected_control_transport_cannot_pollute_readonly_state(self):
        control = self.transport()
        readonly = self.transport()
        with patch.object(self.bridge, '_open_transport', side_effect=[(control, 'control'), (readonly, 'local_readonly')]):
            self.bridge.connect()

            def acknowledge_after_disconnect(ws, method, params=None, timeout=15):
                self.assertIs(ws, control)
                self.assertEqual(method, 'thread/resume')
                self.bridge._disconnect(control)
                return {'thread': {'id': 'synthetic-thread'}}

            with patch.object(self.bridge, '_request', side_effect=acknowledge_after_disconnect):
                with self.assertRaises(ConnectionError):
                    self.bridge.attach('synthetic-thread')
            self.assertEqual(self.bridge.attached, set())
            self.assertEqual(self.bridge.call('thread/read'), {'method': 'thread/read'})
            self.assertEqual(self.bridge.connection_mode, 'local_readonly')
            self.assertEqual(self.bridge.attached, set())


class TransportFactoryTests(unittest.TestCase):
    def test_missing_or_refused_default_socket_uses_readonly_stdio(self):
        for failure in [FileNotFoundError(errno.ENOENT, 'Synthetic missing socket'),
                        ConnectionRefusedError(errno.ECONNREFUSED, 'Synthetic refused socket')]:
            with self.subTest(error=type(failure).__name__), \
                    patch.dict(os.environ, {}, clear=True), \
                    patch.object(codex_bridge.UnixWebSocket, '__init__', side_effect=failure), \
                    patch.object(codex_bridge, 'LocalStdioTransport', return_value=Mock()) as stdio:
                transport, mode = codex_bridge.CodexBridge()._open_transport()
                self.assertIs(transport, stdio.return_value)
                self.assertEqual(mode, 'local_readonly')
                stdio.assert_called_once_with()

    def test_explicit_socket_failure_and_other_default_errors_do_not_fall_back(self):
        cases = [('/synthetic/explicit.sock', FileNotFoundError(errno.ENOENT, 'Synthetic missing socket')),
                 ('', FileNotFoundError(errno.ENOENT, 'Synthetic explicitly empty socket')),
                 (None, PermissionError(errno.EACCES, 'Synthetic inaccessible socket')),
                 (None, ConnectionError('Synthetic invalid WebSocket handshake'))]
        for configured, failure in cases:
            environment = {} if configured is None else {'WOKYIS_CODEX_SOCKET': configured}
            with self.subTest(configured=configured is not None, error=type(failure).__name__), \
                    patch.dict(os.environ, environment, clear=True), \
                    patch.object(codex_bridge.UnixWebSocket, '__init__', side_effect=failure), \
                    patch.object(codex_bridge, 'LocalStdioTransport') as stdio:
                with self.assertRaises(type(failure)):
                    codex_bridge.CodexBridge()._open_transport()
                stdio.assert_not_called()


class StdioTransportTests(unittest.TestCase):
    def make_transport(self, output=''):
        process = Mock()
        process.stdin = io.BytesIO()
        process.stdout = io.BytesIO(output.encode())
        process.poll.return_value = None
        with patch.object(codex_bridge.shutil, 'which', return_value='/synthetic/codex'), \
                patch.object(codex_bridge.subprocess, 'Popen', return_value=process) as popen:
            transport = codex_bridge.LocalStdioTransport()
        self.addCleanup(transport.close)
        return transport, process, popen

    def test_stdio_uses_only_a_private_child_with_stderr_discarded_and_json_lines(self):
        transport, process, popen = self.make_transport('{"id":7,"result":{"synthetic":true}}\n')
        self.assertEqual(popen.call_args.args[0], ['/synthetic/codex', 'app-server', '--stdio'])
        self.assertEqual(popen.call_args.kwargs['stdin'], codex_bridge.subprocess.PIPE)
        self.assertEqual(popen.call_args.kwargs['stdout'], codex_bridge.subprocess.PIPE)
        self.assertEqual(popen.call_args.kwargs['stderr'], codex_bridge.subprocess.DEVNULL)
        transport.send({'id': 7, 'method': 'thread/read', 'params': {'threadId': 'synthetic-thread'}})
        self.assertEqual(json.loads(process.stdin.getvalue()), {'id': 7, 'method': 'thread/read', 'params': {'threadId': 'synthetic-thread'}})
        self.assertTrue(process.stdin.getvalue().endswith(b'\n'))
        self.assertEqual(transport.receive(), {'id': 7, 'result': {'synthetic': True}})
        with self.assertRaises(ConnectionError):
            transport.receive()

    def test_stdio_enforces_the_exact_readonly_allowlist_before_writing(self):
        transport, process, _ = self.make_transport()
        allowed = {'initialize', 'initialized', 'thread/list', 'thread/read', 'thread/items/list',
                   'thread/turns/list', 'account/rateLimits/read', 'account/usage/read'}
        self.assertEqual(set(codex_bridge.READ_ONLY_METHODS), allowed)
        for method in sorted(allowed):
            transport.send({'method': method, 'params': {}})
        written = process.stdin.getvalue()
        self.assertEqual(len(written.splitlines()), len(allowed))
        for message in [{'method': 'turn/start'}, {'method': 'thread/resume'}, {'method': 'thread/read/extra'},
                        {'method': 'unknown'}, {'id': 'synthetic-request', 'result': {}}]:
            with self.subTest(method=message.get('method')), self.assertRaises(ValueError):
                transport.send(message)
        self.assertEqual(process.stdin.getvalue(), written)

    def test_stdio_close_is_idempotent_and_terminates_only_its_own_child(self):
        transport, process, _ = self.make_transport()
        self.assertIs(transport.process, process)
        transport.close()
        transport.close()
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once()
        process.kill.assert_not_called()

    def test_stdio_close_kills_its_child_only_after_a_bounded_termination_timeout(self):
        transport, process, _ = self.make_transport()
        process.wait.side_effect = [codex_bridge.subprocess.TimeoutExpired('synthetic-child', 2), 0]
        transport.close()
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)
        self.assertTrue(all(call.kwargs.get('timeout') is not None for call in process.wait.call_args_list))

    def test_stdio_finds_an_executable_local_bin_candidate_without_path_lookup_success(self):
        candidate = Path('/synthetic/home/.local/bin/codex')
        process = Mock(stdin=io.BytesIO(), stdout=io.BytesIO())
        process.poll.return_value = None
        with patch.object(codex_bridge.shutil, 'which', return_value=None), \
                patch.object(codex_bridge.Path, 'home', return_value=Path('/synthetic/home')), \
                patch.object(codex_bridge.Path, 'is_file', return_value=True), \
                patch.object(codex_bridge.os, 'access', return_value=True) as access, \
                patch.object(codex_bridge.subprocess, 'Popen', return_value=process) as popen:
            transport = codex_bridge.LocalStdioTransport()
        self.addCleanup(transport.close)
        access.assert_called_once_with(candidate, os.X_OK)
        self.assertEqual(popen.call_args.args[0], [str(candidate), 'app-server', '--stdio'])

    def test_missing_or_nonexecutable_cli_never_starts_a_process(self):
        for exists, executable in [(False, False), (True, False)]:
            with self.subTest(candidate_exists=exists), \
                    patch.object(codex_bridge.shutil, 'which', return_value=None), \
                    patch.object(codex_bridge.Path, 'home', return_value=Path('/synthetic/home')), \
                    patch.object(codex_bridge.Path, 'is_file', return_value=exists), \
                    patch.object(codex_bridge.os, 'access', return_value=executable), \
                    patch.object(codex_bridge.subprocess, 'Popen') as popen:
                with self.assertRaises(FileNotFoundError):
                    codex_bridge.LocalStdioTransport()
                popen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
