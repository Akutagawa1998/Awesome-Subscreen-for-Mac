"""Codex JSON-RPC bridge with a read-only CLI fallback.

The existing control socket supports explicit attachment and approval actions.
When its default location is unavailable, an owned CLI subprocess only reads
local thread metadata and history. Neither transport resumes threads automatically.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import struct
import subprocess
import threading
import time


READ_ONLY_METHODS = frozenset({
    'initialize', 'initialized', 'thread/list', 'thread/read',
    'thread/items/list', 'thread/turns/list',
    'account/rateLimits/read', 'account/usage/read',
})


class CodexRpcError(RuntimeError):
    def __init__(self, error):
        super().__init__(error.get('message', 'Codex request failed'))
        self.code = error.get('code')


class UnixWebSocket:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._handshake(path)
        except Exception:
            self.close()
            raise

    def _handshake(self, path):
        self.sock.settimeout(5)
        self.sock.connect(str(path))
        self.lock = threading.Lock()
        self.buffer = b''
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f'GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                           f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                           'Sec-WebSocket-Version: 13\r\n\r\n').encode())
        header = b''
        while b'\r\n\r\n' not in header:
            chunk = self.sock.recv(4096)
            if not chunk or len(header) > 16384:
                raise ConnectionError('Codex WebSocket handshake failed')
            header += chunk
        header, self.buffer = header.split(b'\r\n\r\n', 1)
        lines = header.decode().split('\r\n')
        headers = {k.lower(): v.strip() for k, v in (line.split(':', 1) for line in lines[1:] if ':' in line)}
        expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        if ' 101 ' not in lines[0] or headers.get('sec-websocket-accept', '') != expected:
            raise ConnectionError('Invalid Codex WebSocket handshake')
        self.sock.settimeout(None)

    def exact(self, size):
        while len(self.buffer) < size:
            chunk = self.sock.recv(max(4096, min(size, 65536)))
            if not chunk:
                raise ConnectionError('Codex disconnected')
            self.buffer += chunk
        result, self.buffer = self.buffer[:size], self.buffer[size:]
        return result

    def send_frame(self, data, opcode=1):
        mask = os.urandom(4)
        n = len(data)
        header = bytes([0x80 | opcode])
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack('!H', n)
        else:
            header += bytes([0x80 | 127]) + struct.pack('!Q', n)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self.lock:
            self.sock.sendall(header + mask + payload)

    def send(self, message):
        self.send_frame(json.dumps(message, ensure_ascii=False).encode())

    def receive(self):
        parts = []
        total = 0
        while True:
            a, b = self.exact(2)
            op, final, masked, n = a & 15, a & 128, b & 128, b & 127
            if n == 126:
                n = struct.unpack('!H', self.exact(2))[0]
            elif n == 127:
                n = struct.unpack('!Q', self.exact(8))[0]
            if n > 32 * 1024 * 1024 or total + n > 32 * 1024 * 1024:
                raise ConnectionError('Codex message too large')
            mask = self.exact(4) if masked else None
            data = self.exact(n)
            if mask:
                data = bytes(v ^ mask[i % 4] for i, v in enumerate(data))
            if op == 8:
                raise ConnectionError('Codex disconnected')
            if op == 9:
                self.send_frame(data, 10)
                continue
            if op == 10:
                continue
            if op not in (0, 1):
                raise ConnectionError('Unsupported Codex message')
            parts.append(data)
            total += n
            if final:
                return json.loads(b''.join(parts))

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class LocalStdioTransport:
    """An owned app-server process, restricted before anything reaches stdin."""
    def __init__(self):
        executable = shutil.which('codex')
        if not executable:
            candidate = Path.home() / '.local/bin/codex'
            if candidate.is_file() and os.access(candidate, os.X_OK):
                executable = str(candidate)
        if not executable:
            raise FileNotFoundError('未找到 Codex CLI，请安装或配置 PATH')
        self.lock = threading.Lock()
        self.close_lock = threading.Lock()
        self.closed = False
        self.process = subprocess.Popen(
            [executable, 'app-server', '--stdio'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def send(self, message):
        if message.get('method') not in READ_ONLY_METHODS:
            raise ValueError('本地只读连接不支持此操作，请在 Codex 中处理')
        payload = json.dumps(message, ensure_ascii=False).encode() + b'\n'
        with self.lock:
            if self.closed or self.process.poll() is not None:
                raise ConnectionError('Codex 本地只读连接已关闭')
            self.process.stdin.write(payload)
            self.process.stdin.flush()

    def receive(self):
        line = self.process.stdout.readline(32 * 1024 * 1024 + 1)
        if not line:
            raise ConnectionError('Codex 本地只读连接已关闭')
        if len(line) > 32 * 1024 * 1024:
            raise ConnectionError('Codex message too large')
        return json.loads(line)

    def close(self):
        with self.close_lock:
            if self.closed:
                return
            self.closed = True
            # Only this Popen child is owned here; never find or signal a daemon.
            try:
                if self.process.poll() is None:
                    try:
                        self.process.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        try:
                            self.process.kill()
                        except ProcessLookupError:
                            pass
                        self.process.wait(timeout=2)
                else:
                    self.process.wait(timeout=2)
            finally:
                for stream in (self.process.stdin, self.process.stdout):
                    try:
                        stream.close()
                    except OSError:
                        pass


class CodexBridge:
    def __init__(self):
        self.ws = None
        self.connected = False
        self.connection_mode = None
        self._connection_generation = 0
        self.error = None
        self.pending = {}
        self.requests = {}
        self.attached = set()
        self.counter = 0
        self.lock = threading.RLock()
        self.connect_lock = threading.Lock()
        self._closed = False

    @property
    def connection_generation(self):
        with self.lock:
            return self._connection_generation

    def _open_transport(self):
        configured = os.environ.get('WOKYIS_CODEX_SOCKET')
        if configured is not None:
            return UnixWebSocket(configured), 'control'
        path = Path.home() / '.codex/app-server-control/app-server-control.sock'
        try:
            return UnixWebSocket(path), 'control'
        except (FileNotFoundError, ConnectionRefusedError):
            return LocalStdioTransport(), 'local_readonly'

    def connect(self):
        with self.connect_lock:
            with self.lock:
                if self._closed:
                    raise ConnectionError('Codex bridge 已关闭')
                if self.connected:
                    return
            ws, mode = self._open_transport()
            try:
                with self.lock:
                    if self._closed:
                        raise ConnectionError('Codex bridge 已关闭')
                    self.ws = ws
                    self.connection_mode = mode
                    self._connection_generation += 1
                    self.pending.clear()
                    self.attached.clear()
                threading.Thread(target=self._read, args=(ws,), daemon=True).start()
                # Initialization must not recursively enter connect(), and no
                # other caller can use this transport before it is ready.
                self._request(ws, 'initialize', {
                    'clientInfo': {'name': 'wokyis_console', 'title': 'Wokyis Console', 'version': '1.0.0'},
                    'capabilities': {'experimentalApi': True,
                                     'optOutNotificationMethods': ['item/agentMessage/delta', 'item/reasoning/textDelta', 'item/commandExecution/outputDelta']}})
                ws.send({'method': 'initialized', 'params': {}})
                with self.lock:
                    if self.ws is not ws or self._closed:
                        raise ConnectionError('Codex disconnected')
                    self.connected = True
                    self.error = None
            except Exception:
                self._disconnect(ws)
                raise

    def _disconnect(self, ws):
        with self.lock:
            if self.ws is ws:
                self.ws = None
                self.connected = False
                self.connection_mode = None
                self.error = 'Codex 连接中断，正在重连'
                self.pending.clear()
                self.attached.clear()
            for owner, q in self.requests.values():
                if owner is ws:
                    q.put(ConnectionError('Codex disconnected'))
        ws.close()

    def close(self):
        """Permanently close this bridge and its own transport; safe to repeat."""
        with self.lock:
            self._closed = True
            ws = self.ws
            self.connected = False
            self.connection_mode = None
            self.pending.clear()
            self.attached.clear()
        if ws is not None:
            self._disconnect(ws)
        # A factory may still own a newly spawned process before publishing it
        # as self.ws. Keep the caller alive until connect() closes that process.
        with self.connect_lock:
            pass
        with self.lock:
            self.error = 'Codex 连接已关闭'

    def _read(self, ws):
        try:
            while True:
                m = ws.receive()
                if 'id' in m and 'method' not in m:
                    with self.lock:
                        entry = self.requests.get(m['id'])
                        if entry and entry[0] is ws:
                            entry[1].put(m)
                elif 'id' in m and 'method' in m:
                    # Keep exact request IDs, including their original JSON type.
                    with self.lock:
                        if self.ws is ws and self.connection_mode == 'control':
                            self.pending[str(m['id'])] = {**m, 'receivedAt': time.time(), 'sending': False}
                elif m.get('method') == 'serverRequest/resolved':
                    with self.lock:
                        if self.ws is ws:
                            self.pending.pop(str(m.get('params', {}).get('requestId')), None)
        except Exception:
            self._disconnect(ws)

    def call(self, method, params=None, timeout=15):
        if not self.connected:
            self.connect()
        with self.lock:
            ws = self.ws
            if self.connection_mode == 'local_readonly' and method not in READ_ONLY_METHODS:
                raise ValueError('本地只读连接不支持此操作，请在 Codex 中处理')
        return self._request(ws, method, params, timeout)

    def _request(self, ws, method, params=None, timeout=15):
        with self.lock:
            if self._closed or ws is None or self.ws is not ws:
                raise ConnectionError('Codex disconnected')
            self.counter += 1
            ident = self.counter
            q = queue.Queue()
            self.requests[ident] = (ws, q)
        try:
            try:
                ws.send({'id': ident, 'method': method, 'params': params or {}})
            except Exception:
                self._disconnect(ws)
                raise
            m = q.get(timeout=timeout)
            if isinstance(m, Exception):
                raise m
            if 'error' in m:
                raise CodexRpcError(m['error'])
            return m.get('result', {})
        except queue.Empty:
            self._disconnect(ws)
            raise TimeoutError('Codex 响应超时') from None
        finally:
            with self.lock:
                self.requests.pop(ident, None)

    def attach(self, thread_id):
        # No prompt, policy override, model override, or turn/start here.
        if not self.connected:
            self.connect()
        with self.lock:
            if self.connection_mode == 'local_readonly':
                raise ValueError('本地只读连接不支持附加，请在 Codex 中处理')
            ws = self.ws
        result = self._request(ws, 'thread/resume', {'threadId': thread_id})
        with self.lock:
            if self.ws is not ws or not self.connected:
                raise ConnectionError('Codex disconnected')
            self.attached.add(thread_id)
        return result

    def approvals(self, thread_id=None):
        with self.lock:
            return [dict(x) for x in self.pending.values()
                    if thread_id is None or x.get('params', {}).get('threadId') == thread_id]

    def respond(self, request_id, thread_id, decision=None, answers=None, expected_generation=None):
        with self.lock:
            if expected_generation is not None and expected_generation != self._connection_generation:
                raise ValueError('连接已变化，请重新查看审批请求')
            if self.connection_mode == 'local_readonly':
                raise ValueError('本地只读连接不支持审批，请在 Codex 中处理')
            p = self.pending.get(str(request_id))
            if not self.connected or not p or p.get('sending'):
                raise ValueError('请求已处理或已失效，请刷新')
            if p.get('params', {}).get('threadId') != thread_id:
                raise ValueError('会话与审批请求不匹配')
            method, params = p['method'], p.get('params', {})
            if method == 'item/fileChange/requestApproval':
                raise ValueError('请在 Codex 中查看完整文件差异后处理')
            if method == 'item/commandExecution/requestApproval':
                if decision not in ('accept', 'decline'):
                    raise ValueError('只支持本次允许或拒绝')
                if decision == 'accept' and not (params.get('command') or params.get('networkApprovalContext')):
                    raise ValueError('请求缺少完整操作内容，请在 Codex 中处理')
                allowed = params.get('availableDecisions')
                if allowed is not None and decision not in allowed:
                    raise ValueError('该请求不允许此决定，请在 Codex 处理')
                result = {'decision': decision}
            elif method == 'item/permissions/requestApproval':
                if decision not in ('accept', 'decline'):
                    raise ValueError('无效决定')
                result = {'permissions': params.get('permissions', {}) if decision == 'accept' else {}, 'scope': 'turn'}
            elif method == 'item/tool/requestUserInput':
                questions = params.get('questions', [])
                if not isinstance(answers, dict) or set(answers) != {q['id'] for q in questions}:
                    raise ValueError('请回答每个问题')
                result = {'answers': {k: {'answers': [str(v)[:16000]]} for k, v in answers.items()}}
            else:
                raise ValueError('此类请求请在 Codex 中处理')
            self.ws.send({'id': p['id'], 'result': result})
            p['sending'] = True
            return {'submitted': True, 'confirmed': False}
