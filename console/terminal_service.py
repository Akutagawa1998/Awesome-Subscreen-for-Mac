"""Private tmux sessions with disposable PTY clients; shells survive UI/server restarts."""
import base64
import fcntl
import os
from pathlib import Path
import re
import select
import shutil
import struct
import subprocess
import sys
import termios
import threading
import time
import uuid

IDENT = re.compile(r'^w-[a-f0-9]{32}$')
VIEWER = re.compile(r'^[a-f0-9]{32}$')
MAX_BUFFER = 2 * 1024 * 1024


class TerminalClient:
    def __init__(self, command, cwd, env):
        self.lock = threading.RLock()
        self.buffer = bytearray()
        self.start = 0
        self.stream = uuid.uuid4().hex
        self.closed = False
        self.touched = time.monotonic()
        self.master, slave = os.openpty()
        self.tty = os.ttyname(slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
        try:
            self.process = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name('terminal_child.py')), *command],
                stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env,
                start_new_session=True, close_fds=True)
        except Exception:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            while not self.closed:
                if not select.select([self.master], [], [], .5)[0]:
                    continue
                chunk = os.read(self.master, 65536)
                if not chunk:
                    break
                with self.lock:
                    self.buffer.extend(chunk)
                    if len(self.buffer) > MAX_BUFFER:
                        drop = len(self.buffer) - MAX_BUFFER
                        del self.buffer[:drop]
                        self.start += drop
        except (OSError, ValueError):
            pass

    def read(self, cursor, stream):
        with self.lock:
            end = self.start + len(self.buffer)
            if stream != self.stream:
                cursor = 0
            if cursor < self.start or cursor > end:
                return {'data': '', 'cursor': end, 'stream': self.stream, 'reset': True,
                        'redraw': True, 'alive': self.process.poll() is None}
            reset = stream != self.stream or cursor < self.start or cursor > end
            cursor = self.start if reset else cursor
            chunk = bytes(self.buffer[cursor-self.start:cursor-self.start+262144])
            return {'data': base64.b64encode(chunk).decode(), 'cursor': cursor + len(chunk),
                    'stream': self.stream, 'reset': reset, 'alive': self.process.poll() is None}

    def write(self, data):
        with self.lock:
            if self.closed or self.process.poll() is not None:
                raise ValueError('终端已断开，请重新连接')
            view = memoryview(data)
            while view:
                _, ready, _ = select.select([], [self.master], [], 2)
                if not ready:
                    raise RuntimeError('终端输入超时，请检查终端状态')
                count = os.write(self.master, view)
                view = view[count:]

    def resize(self, cols, rows):
        with self.lock:
            if not self.closed:
                fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def detach(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            os.close(self.master)
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


class TerminalService:
    def __init__(self, directory, cwd=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.socket = str(self.directory / 'terminal.sock')
        self.cwd = str(cwd or Path.home())
        self.tmux = shutil.which('tmux') or next((p for p in ['/opt/homebrew/bin/tmux', '/usr/local/bin/tmux'] if Path(p).is_file()), None)
        self.lock = threading.RLock()
        self.clients = {}
        self.env = os.environ.copy()
        self.env.update(TERM='xterm-256color', LANG='en_US.UTF-8', WOKYIS_TERMINAL='1')

    def _run(self, *args, check=True):
        if not self.tmux:
            raise ValueError('需要安装 tmux 才能使用常驻终端')
        result = subprocess.run([self.tmux, '-S', self.socket, '-f', '/dev/null', *args],
                                capture_output=True, text=True, timeout=5, env=self.env)
        if check and result.returncode:
            raise RuntimeError('终端会话已退出或暂不可用，请刷新')
        return result

    def list(self):
        if not self.tmux:
            return {'sessions': [], 'available': False, 'error': '需要安装 tmux'}
        result = self._run('list-sessions', '-F', '#{session_name}\t#{session_created}\t#{pane_current_command}\t#{session_id}', check=False)
        rows = []
        for line in result.stdout.splitlines():
            ident, created, command, number = (line.split('\t') + ['', '', '', ''])[:4]
            if IDENT.fullmatch(ident):
                order = int(number.removeprefix('$'))
                rows.append({'id': ident, 'created': int(created), 'command': command, 'order': order, 'name': f'TTY {order+1:02}'})
        rows.sort(key=lambda row: row['order'])
        return {'sessions': rows, 'available': True}

    def _validate(self, ident):
        if not isinstance(ident, str) or not IDENT.fullmatch(ident):
            raise ValueError('无效终端 ID')
        if self._run('has-session', '-t', ident, check=False).returncode:
            raise ValueError('终端已关闭')

    def create(self):
        with self.lock:
            if len(self.list()['sessions']) >= 12:
                raise ValueError('最多同时保留 12 个终端')
            ident = 'w-' + uuid.uuid4().hex
            self._run('new-session', '-d', '-s', ident, '-c', self.cwd, '-x', '80', '-y', '24', '/bin/zsh', '-l')
            try:
                for option, value in [('status', 'off'), ('mouse', 'on'), ('history-limit', '10000'), ('window-size', 'latest')]:
                    self._run('set-option', '-t', ident, option, value)
            except Exception:
                self._run('kill-session', '-t', ident, check=False)
                raise
            return {'id': ident, **self.list()}

    def _viewer(self, viewer):
        if not isinstance(viewer, str) or not VIEWER.fullmatch(viewer):
            raise ValueError('无效终端窗口 ID')

    def open(self, ident, viewer):
        self._viewer(viewer)
        with self.lock:
            self._validate(ident)
            for key, old in list(self.clients.items()):
                if time.monotonic() - old.touched > 90:
                    old.detach()
                    del self.clients[key]
            key = (ident, viewer)
            client = self.clients.get(key)
            if client and client.process.poll() is None:
                client.touched = time.monotonic()
                return {'ok': True}
            if client:
                client.detach()
            if key not in self.clients and len(self.clients) >= 48:
                raise ValueError('终端窗口过多，请关闭不用的操作台窗口')
            self.clients[key] = TerminalClient([self.tmux, '-S', self.socket, 'attach-session', '-t', ident], self.cwd, self.env)
            return {'ok': True}

    def client(self, ident, viewer):
        self._viewer(viewer)
        if not isinstance(ident, str) or not IDENT.fullmatch(ident):
            raise ValueError('无效终端 ID')
        with self.lock:
            client = self.clients.get((ident, viewer))
            if not client:
                raise ValueError('请重新连接终端')
            client.touched = time.monotonic()
            return client

    def output(self, ident, cursor, stream, viewer):
        client = self.client(ident, viewer)
        result = client.read(max(0, int(cursor)), stream)
        if result.pop('redraw', False):
            self._run('refresh-client', '-t', client.tty, check=False)
        return result

    def post(self, action, data):
        ident = data.get('id')
        viewer = data.get('viewer')
        if action == 'create':
            return self.create()
        if action == 'open':
            return self.open(ident, viewer)
        if action == 'detach':
            self._viewer(viewer)
            with self.lock:
                for key, client in list(self.clients.items()):
                    if key[1] == viewer:
                        client.detach()
                        del self.clients[key]
            return {'ok': True}
        if action == 'input':
            value = data.get('data')
            if not isinstance(value, str) or not 0 < len(value.encode()) <= 32768:
                raise ValueError('终端输入为空或过长')
            self.client(ident, viewer).write(value.encode())
            return {'ok': True}
        if action == 'resize':
            cols, rows = int(data.get('cols', 80)), int(data.get('rows', 24))
            if not 10 <= cols <= 500 or not 2 <= rows <= 200:
                raise ValueError('终端尺寸无效')
            self.client(ident, viewer).resize(cols, rows)
            return {'ok': True}
        if action == 'close':
            with self.lock:
                self._validate(ident)
                self._run('kill-session', '-t', ident)
                for key, client in list(self.clients.items()):
                    if key[0] == ident:
                        client.detach()
                        del self.clients[key]
                return {'ok': True, **self.list()}
        raise ValueError('未知终端操作')

    def detach_all(self):
        with self.lock:
            for client in self.clients.values():
                client.detach()
            self.clients.clear()
