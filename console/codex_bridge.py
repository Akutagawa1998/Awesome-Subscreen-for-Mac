"""Small JSON-RPC client for Codex's official local WebSocket transport.

Uses the existing daemon. Never starts an agent, resumes threads automatically,
or changes Codex configuration. Thread attachment and replies are explicit UI actions.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import socket
import struct
import threading
import time


class UnixWebSocket:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
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


class CodexBridge:
    def __init__(self):
        self.ws = None
        self.connected = False
        self.error = None
        self.pending = {}
        self.requests = {}
        self.attached = set()
        self.counter = 0
        self.lock = threading.RLock()
        self.connect_lock = threading.Lock()

    def connect(self):
        with self.connect_lock:
            if self.connected:
                return
            path = os.environ.get('WOKYIS_CODEX_SOCKET') or Path.home() / '.codex/app-server-control/app-server-control.sock'
            ws = UnixWebSocket(path)
            self.ws = ws
            self.connected = True
            self.pending.clear()
            self.attached.clear()
            threading.Thread(target=self._read, args=(ws,), daemon=True).start()
            try:
                self.call('initialize', {
                    'clientInfo': {'name': 'wokyis_console', 'title': 'Wokyis Console', 'version': '1.0.0'},
                    'capabilities': {'experimentalApi': True,
                                     'optOutNotificationMethods': ['item/agentMessage/delta', 'item/reasoning/textDelta', 'item/commandExecution/outputDelta']}})
                ws.send({'method': 'initialized', 'params': {}})
                self.error = None
            except Exception:
                self.connected = False
                ws.close()
                raise

    def _read(self, ws):
        try:
            while True:
                m = ws.receive()
                if 'id' in m and 'method' not in m:
                    with self.lock:
                        q = self.requests.get(m['id'])
                    if q:
                        q.put(m)
                elif 'id' in m and 'method' in m:
                    # Keep exact request IDs, including their original JSON type.
                    with self.lock:
                        self.pending[str(m['id'])] = {**m, 'receivedAt': time.time(), 'sending': False}
                elif m.get('method') == 'serverRequest/resolved':
                    with self.lock:
                        self.pending.pop(str(m.get('params', {}).get('requestId')), None)
        except Exception:
            if self.ws is ws:
                self.connected = False
                self.error = 'Codex 连接中断，正在重连'
                with self.lock:
                    self.pending.clear()
                    self.attached.clear()
                    for q in self.requests.values():
                        q.put({'error': {'message': 'Codex disconnected'}})
            ws.close()

    def call(self, method, params=None, timeout=15):
        if not self.connected:
            self.connect()
        with self.lock:
            self.counter += 1
            ident = self.counter
            q = queue.Queue()
            self.requests[ident] = q
        try:
            self.ws.send({'id': ident, 'method': method, 'params': params or {}})
            m = q.get(timeout=timeout)
            if 'error' in m:
                raise RuntimeError(m['error'].get('message', 'Codex request failed'))
            return m.get('result', {})
        except queue.Empty:
            raise TimeoutError('Codex 响应超时') from None
        finally:
            with self.lock:
                self.requests.pop(ident, None)

    def attach(self, thread_id):
        # No prompt, policy override, model override, or turn/start here.
        result = self.call('thread/resume', {'threadId': thread_id})
        self.attached.add(thread_id)
        return result

    def approvals(self, thread_id=None):
        with self.lock:
            return [dict(x) for x in self.pending.values()
                    if thread_id is None or x.get('params', {}).get('threadId') == thread_id]

    def respond(self, request_id, thread_id, decision=None, answers=None):
        with self.lock:
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
