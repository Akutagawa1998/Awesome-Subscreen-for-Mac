#!/usr/bin/env python3
"""Wokyis Console: loopback-only UI + explicit, narrowly scoped local adapters."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
import mimetypes
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlparse, parse_qs

from console.calendar_service import CalendarService
from console.codex_bridge import CodexBridge
from console.local_data import Storage, ClaudeUsage, local_thread_metadata
from console.terminal_service import TerminalService
from console.todo_service import TodoService
from console.wellness_service import WellnessService

ROOT = Path(__file__).resolve().parent
UUID = re.compile(r'^[a-fA-F0-9-]{36}$')


class App:
    def __init__(self, port):
        self.origin = f'http://127.0.0.1:{port}'
        self.store = Storage(ROOT / '.local/settings.json')
        self.calendar = CalendarService(Storage(ROOT / '.local/google.json'), self.origin)
        self.todo = TodoService(Storage(ROOT / '.local/microsoft.json'), self.origin)
        self.wellness = WellnessService(Storage(ROOT / '.local/wellness.json'))
        self.codex = CodexBridge()
        self.claude = ClaudeUsage()
        self.terminals = TerminalService(ROOT / '.local')
        self.lock = threading.RLock()
        self.snapshot = {'connected': False, 'threads': [], 'limits': None, 'usage': None,
                         'fetchedAt': None, 'error': '正在连接 Codex…'}
        self.usage_time = 0

    def poll(self):
        while True:
            try:
                self.refresh()
            except Exception:
                with self.lock:
                    self.snapshot.update(connected=False, error='Codex 本地服务暂不可用', stale=True)
            time.sleep(8)

    def refresh(self):
        threads, cursor = [], None
        for _ in range(20):
            page = self.codex.call('thread/list', {'limit': 100, 'sortKey': 'updated_at', 'useStateDbOnly': True, 'cursor': cursor})
            threads += page.get('data', [])
            cursor = page.get('nextCursor')
            if not cursor:
                break
        metadata = local_thread_metadata()
        rows = []
        for t in threads:
            if t.get('parentThreadId'):
                continue
            meta = metadata.get(t['id'], {})
            rows.append({k: t.get(k) for k in ['id', 'name', 'status', 'updatedAt', 'cwd', 'model', 'reasoningEffort']} | {
                'preview': (t.get('preview') or '')[:240],
                'tokens': meta.get('tokens_used'), 'model': t.get('model') or meta.get('model'),
                'reasoningEffort': t.get('reasoningEffort') or meta.get('reasoning_effort')})
        with self.lock:
            self.snapshot.update(connected=True, threads=rows, fetchedAt=time.time(), error=None, stale=False, truncated=bool(cursor))
        if time.time() - self.usage_time > 60:
            limits, usage, errors = None, None, []
            for method, key in [('account/rateLimits/read', 'limits'), ('account/usage/read', 'usage')]:
                try:
                    result = self.codex.call(method)
                    if key == 'limits':
                        # Do not expose account IDs, reset-credit IDs or billing balances.
                        limits = {'rateLimits': result.get('rateLimits'), 'rateLimitsByLimitId': result.get('rateLimitsByLimitId')}
                    else:
                        usage = result
                except Exception:
                    errors.append(key)
            with self.lock:
                self.snapshot.update(limits=limits, usage=usage, usageErrors=errors, usageFetchedAt=time.time())
            self.usage_time = time.time()

    def status(self):
        with self.lock:
            result = dict(self.snapshot)
        result['hidden'] = self.store.read().get('hidden', [])
        result['approvals'] = self.codex.approvals()
        result['attached'] = list(self.codex.attached)
        return result

    def thread(self, ident):
        if not UUID.fullmatch(ident):
            raise ValueError('无效会话 ID')
        r = self.codex.call('thread/read', {'threadId': ident})
        thread = r['thread']
        items, error = [], None
        try:
            cursor = None
            for _ in range(4):
                r = self.codex.call('thread/items/list', {'threadId': ident, 'limit': 30, 'sortDirection': 'desc', 'cursor': cursor})
                for row in r.get('data', []):
                    item = row.get('item', row)
                    if item.get('type') == 'agentMessage':
                        items.append({'type': 'agent', 'text': item.get('text', '')})
                    elif item.get('type') == 'userMessage':
                        text = '\n'.join(c.get('text', '') for c in item.get('content', []) if c.get('type') in ('text', 'inputText'))
                        items.append({'type': 'user', 'text': text})
                    if len(items) >= 4:
                        break
                cursor = r.get('nextCursor')
                if any(i['type'] == 'agent' for i in items) or not cursor:
                    break
        except Exception:
            error = '此会话暂不提供历史摘要'
        return {'thread': {k: thread.get(k) for k in ['id', 'name', 'preview', 'status', 'cwd', 'model', 'reasoningEffort']},
                'messages': items, 'error': error, 'approvals': self.codex.approvals(ident),
                'attached': ident in self.codex.attached}

    def owning_thread(self, ident):
        if not UUID.fullmatch(ident):
            raise ValueError('无效会话 ID')
        r = self.codex.call('thread/read', {'threadId': ident})['thread']
        if r.get('status', {}).get('type') not in ('active', 'idle'):
            raise ValueError('该会话不由当前本地服务运行，请在 Codex 中打开后处理')
        return r

    def post(self, path, data):
        if path == '/api/microsoft/configure':
            self.todo.configure(data); return {'ok': True}
        if path == '/api/microsoft/connect':
            return {'url': self.todo.begin()}
        if path == '/api/microsoft/disconnect':
            self.todo.disconnect(); return {'ok': True}
        if path == '/api/todo/complete':
            return self.todo.complete(data)
        if path == '/api/wellness/import':
            return self.wellness.import_data(data)
        if path == '/api/wellness/configure':
            return self.wellness.configure(data)
        if path == '/api/wellness/disconnect':
            self.wellness.disconnect(); return {'ok': True}
        if path.startswith('/api/terminal/'):
            return self.terminals.post(path.rsplit('/', 1)[-1], data)
        if path == '/api/hide':
            ident = str(data.get('id', ''))
            if not UUID.fullmatch(ident):
                raise ValueError('无效会话 ID')
            def update(d):
                hidden = set(d.get('hidden', []))
                hidden.add(ident) if data.get('hidden') else hidden.discard(ident)
                d['hidden'] = sorted(hidden)
            self.store.update(update)
            return {'ok': True}
        if path == '/api/restore':
            self.store.update(lambda d: d.update(hidden=[]))
            return {'ok': True}
        if path == '/api/attach':
            ident = str(data.get('id', ''))
            self.owning_thread(ident)
            self.codex.attach(ident)
            return {'ok': True}
        if path == '/api/reply':
            ident, text = str(data.get('id', '')), str(data.get('text', '')).strip()
            if not text or len(text) > 16000:
                raise ValueError('请填写 1–16000 字的回复')
            thread = self.owning_thread(ident)
            if ident not in self.codex.attached:
                self.codex.attach(ident)
            params = {'threadId': ident, 'input': [{'type': 'text', 'text': text, 'text_elements': []}]}
            if thread['status']['type'] == 'active':
                turns = self.codex.call('thread/turns/list', {'threadId': ident, 'limit': 1, 'itemsView': 'notLoaded', 'sortDirection': 'desc'})
                turn = next(iter(turns.get('data', [])), {})
                if turn.get('status') != 'inProgress' or not turn.get('id'):
                    raise ValueError('会话状态已变化，请刷新后重试')
                params['expectedTurnId'] = turn['id']
                self.codex.call('turn/steer', params)
            else:
                self.codex.call('turn/start', params)
            return {'ok': True}
        if path == '/api/approval':
            return self.codex.respond(data.get('requestId'), data.get('threadId'), data.get('decision'), data.get('answers'))
        if path == '/api/google/configure':
            self.calendar.configure(data)
            return {'ok': True}
        if path == '/api/google/connect':
            return {'url': self.calendar.begin()}
        if path == '/api/google/disconnect':
            self.calendar.disconnect(str(data.get('id', '')))
            return {'ok': True}
        raise ValueError('未知操作')


class Handler(BaseHTTPRequestHandler):
    server_version = 'Wokyis/1.0'

    def log_message(self, fmt, *args):
        # OAuth callback URLs and conversation payloads must never reach request logs.
        pass

    def validate(self, mutation=False):
        origin = self.server.app.origin
        if self.headers.get('Host') != urlparse(origin).netloc:
            self.reply(403, {'error': 'Invalid host'})
            return False
        source = self.headers.get('Origin')
        if source and source != origin:
            self.reply(403, {'error': 'Cross-origin access denied'})
            return False
        # OAuth navigation is allowed only at /oauth/callback; state+PKCE validates it.
        if self.path.startswith('/api/') and self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
            self.reply(403, {'error': 'Cross-site access denied'})
            return False
        if mutation and (self.headers.get('X-Wokyis') != '1' or self.headers.get('Content-Type', '').split(';')[0] != 'application/json'):
            self.reply(403, {'error': 'Invalid request'})
            return False
        return True

    def reply(self, status, value, mime='application/json; charset=utf-8'):
        body = json.dumps(value, ensure_ascii=False).encode() if not isinstance(value, bytes) else value
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        self.reply(403, {'error': 'Cross-origin access denied'})

    def do_GET(self):
        if not self.validate():
            return
        path, query = urlparse(self.path).path, parse_qs(urlparse(self.path).query)
        app = self.server.app
        try:
            if path == '/api/health':
                return self.reply(200, {'ok': True, 'app': 'wokyis-console'})
            if path == '/api/microsoft':
                return self.reply(200, app.todo.public_config())
            if path == '/api/todo':
                return self.reply(200, app.todo.read(force=query.get('refresh') == ['1']))
            if path == '/api/wellness':
                return self.reply(200, app.wellness.read())
            if path == '/oauth/microsoft/callback':
                app.todo.callback(query.get('state', [''])[0], query.get('code', [''])[0])
                return self.reply(200, '<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/retro.css"><body style="padding:50px;color:#eee"><h1>Microsoft To Do 已连接</h1><p>可以关闭此窗口，回到操作台。</p></body>'.encode(), 'text/html; charset=utf-8')
            if path == '/api/terminals':
                return self.reply(200, app.terminals.list())
            if path == '/api/terminal/output':
                return self.reply(200, app.terminals.output(query.get('id', [''])[0], query.get('cursor', ['0'])[0], query.get('stream', [''])[0], query.get('viewer', [''])[0]))
            if path == '/api/status':
                return self.reply(200, app.status())
            if path == '/api/thread':
                return self.reply(200, app.thread(query.get('id', [''])[0]))
            if path == '/api/claude':
                return self.reply(200, app.claude.read())
            if path == '/api/google':
                return self.reply(200, app.calendar.public_config())
            if path == '/api/events':
                start, end = query.get('start', [''])[0], query.get('end', [''])[0]
                a, b = datetime.fromisoformat(start.replace('Z', '+00:00')), datetime.fromisoformat(end.replace('Z', '+00:00'))
                if not a.tzinfo or not b.tzinfo or not timedelta(0) < b-a <= timedelta(days=50):
                    raise ValueError('无效日期范围')
                return self.reply(200, app.calendar.events(start, end))
            if path == '/oauth/callback':
                app.calendar.callback(query.get('state', [''])[0], query.get('code', [''])[0])
                return self.reply(200, '<!doctype html><meta charset="utf-8"><title>Google 已连接</title><body style="font:28px system-ui;background:#171918;color:#f0eee6;padding:60px"><h1>日历账号已连接 ✓</h1><p>可以关闭此窗口，回到 Wokyis 操作台。多个账号可以逐个添加。</p></body>'.encode(), 'text/html; charset=utf-8')
            static = {'/': 'index.html', '/app.js': 'app.js', '/time-utils.js': 'time-utils.js', '/styles.css': 'styles.css', '/manifest.webmanifest': 'manifest.webmanifest', '/icon.svg': 'icon.svg'}
            for asset in ['terminal.js', 'retro.css', 'gestures.js', 'wellness.js', 'wellness.css', 'vendor/sarasa-mono-sc-regular.woff2', 'vendor/sarasa-mono-sc-bold.woff2', 'vendor/xterm.js', 'vendor/xterm.css', 'vendor/addon-fit.js', 'vendor/hack-regular.woff2', 'vendor/hack-bold.woff2']:
                static['/' + asset] = asset
            if path in static:
                target = ROOT / 'web' / static[path]
                return self.reply(200, target.read_bytes(), mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
            return self.reply(404, {'error': 'Not found'})
        except (ValueError, RuntimeError, TimeoutError) as e:
            self.reply(400, {'error': str(e)[:500]})
        except Exception:
            self.reply(503, {'error': '数据暂不可用，请稍后刷新'})

    def do_POST(self):
        if not self.validate(mutation=True):
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            limit = WellnessService.MAX_BYTES if self.path == '/api/wellness/import' else 100000
            if size < 0 or size > limit:
                return self.reply(413, {'error': '请求过大'})
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('Invalid JSON object')
            result = self.server.app.post(urlparse(self.path).path, data)
            return self.reply(200, result)
        except (ValueError, RuntimeError, TimeoutError) as e:
            return self.reply(400, {'error': str(e)[:500]})
        except Exception:
            return self.reply(503, {'error': '操作未完成，请刷新确认状态后重试'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    app = App(args.port)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    server.app = app
    threading.Thread(target=app.poll, daemon=True).start()
    print(f'Wokyis Console: {app.origin}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.terminals.detach_all()
        server.server_close()


if __name__ == '__main__':
    main()
