import json
import os
from pathlib import Path
import sqlite3
import threading
from datetime import datetime
from zoneinfo import ZoneInfo


class Storage:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            if not self.path.exists():
                return {}
            return json.loads(self.path.read_text())

    def update(self, fn):
        with self.lock:
            data = self.read()
            fn(data)
            temporary = self.path.with_suffix('.tmp')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(temporary, self.path)


def local_thread_metadata():
    """Read metadata only. Runtime status always comes from the owning server."""
    db = Path.home() / '.codex/state_5.sqlite'
    if not db.exists():
        return {}
    try:
        with sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=1) as c:
            c.row_factory = sqlite3.Row
            return {r['id']: dict(r) for r in c.execute(
                'SELECT id,tokens_used,model,reasoning_effort FROM threads WHERE archived=0')}
    except sqlite3.Error:
        return {}


def usage_counts(usage):
    keys = ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')
    top = {k: usage.get(k, 0) or 0 for k in keys}
    iterations = usage.get('iterations') or []
    # Current Claude versions sometimes store totals only in iterations.
    if not any(top.values()) and iterations:
        return {k: sum((i.get(k, 0) or 0) for i in iterations) for k in keys}
    return top


class ClaudeUsage:
    def __init__(self, root=None):
        self.root = root or Path.home() / '.claude/projects'
        self.cache = {}
        self.lock = threading.Lock()

    def read(self, zone='America/New_York'):
        with self.lock:
            return self._read(zone)

    def _read(self, zone):
        tz = ZoneInfo(zone)
        today = datetime.now(tz).date().isoformat()
        totals = dict.fromkeys(('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'), 0)
        sessions, missing = [], 0
        files = sorted(self.root.rglob('*.jsonl'))
        counted = set()
        for path in files:
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
            entry = self.cache.get(str(path))
            if not entry or entry['signature'] != signature:
                messages = {}
                last = None
                project = None
                try:
                    with path.open(errors='replace') as f:
                        for line in f:
                            try:
                                x = json.loads(line)
                            except ValueError:
                                continue
                            project = x.get('cwd', project)
                            timestamp = x.get('timestamp')
                            if timestamp:
                                last = timestamp
                            message = x.get('message', {})
                            if x.get('type') != 'assistant' or not isinstance(message, dict) or not message.get('usage'):
                                continue
                            ident = (x.get('requestId'), message.get('id') or x.get('uuid'))
                            if not ident[1] or not timestamp:
                                continue
                            try:
                                date = datetime.fromisoformat(timestamp.replace('Z', '+00:00')).astimezone(tz).date().isoformat()
                            except ValueError:
                                continue
                            counts = usage_counts(message['usage'])
                            old = messages.get(ident, {}).get('counts', {})
                            messages[ident] = {'date': date, 'counts': {k: max(v, old.get(k, 0)) for k, v in counts.items()}}
                    entry = {'signature': signature, 'messages': messages, 'last': last, 'project': project}
                    self.cache[str(path)] = entry
                except OSError:
                    missing += 1
                    continue
            session_total = 0
            for identity, m in entry['messages'].items():
                if m['date'] == today and identity not in counted:
                    counted.add(identity)
                    for k, v in m['counts'].items():
                        totals[k] += v
                        session_total += v
            if session_total:
                sessions.append({'id': path.stem, 'project': Path(entry['project'] or path.parent.name).name,
                                 'tokens': session_total, 'updatedAt': entry['last']})
        self.cache = {k: v for k, v in self.cache.items() if Path(k).exists()}
        return {'available': bool(files), 'date': today, 'timeZone': zone, 'totals': totals,
                'totalTokens': sum(totals.values()), 'sessions': sorted(sessions, key=lambda s: s['updatedAt'] or '', reverse=True),
                'fileCount': len(files), 'unreadableFiles': missing, 'scope': 'local-claude-code',
                'quota': None}
