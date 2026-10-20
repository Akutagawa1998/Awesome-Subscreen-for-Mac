"""Google Calendar, independent OAuth grants per account, read-only scopes."""
import base64
import hashlib
import json
import secrets
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen


class CalendarService:
    SCOPES = 'openid email https://www.googleapis.com/auth/calendar.readonly'

    def __init__(self, storage, origin):
        self.storage = storage
        self.origin = origin
        self.pending = {}
        self.lock = threading.RLock()

    @staticmethod
    def fetch(url, data=None, token=None):
        headers = {'Accept': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        payload = urlencode(data).encode() if data is not None else None
        if payload is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        try:
            with urlopen(Request(url, data=payload, headers=headers), timeout=20) as response:
                return json.load(response)
        except HTTPError as e:
            # Never surface response bodies containing credentials or authorization codes.
            if e.code in (400, 401):
                raise ValueError('Google 授权失效或配置不正确，请重新连接账号') from None
            if e.code == 403:
                raise ValueError('Google 拒绝访问，请确认 Calendar API 已启用并已授权只读日历') from None
            raise ValueError(f'Google 暂时不可用（HTTP {e.code}）') from None

    def public_config(self):
        data = self.storage.read()
        return {'configured': bool(data.get('googleClient')),
                'ownerOnly': 'allowedAccounts' in data,
                'accounts': [{'id': a['id'], 'email': a['email']} for a in data.get('accounts', [])
                             if self.account_allowed(a, data)]}

    def account_allowed(self, account, data=None):
        # This restricts this local console, not Google's external OAuth audience.
        data = self.storage.read() if data is None else data
        if 'allowedAccounts' not in data:
            return True
        allowed = data['allowedAccounts']
        email = str(account.get('email') or '').strip().casefold()
        ident = account.get('id')
        if not isinstance(allowed, list) or not ident or not email:
            return False
        return any(isinstance(a, dict) and a.get('id') == ident
                   and str(a.get('email') or '').strip().casefold() == email for a in allowed)

    def configure(self, value):
        # Accept downloaded Desktop OAuth JSON, not browser cookies or account passwords.
        client = value.get('installed')
        if not isinstance(client, dict) or not str(client.get('client_id', '')).endswith('.apps.googleusercontent.com'):
            raise ValueError('请导入 Google Cloud 的「桌面应用」OAuth JSON')
        self.storage.update(lambda d: d.update(googleClient={k: client.get(k, '') for k in ['client_id', 'client_secret']}))

    def begin(self):
        client = self.storage.read().get('googleClient')
        if not client:
            raise ValueError('请先导入 Google OAuth 桌面应用配置')
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        with self.lock:
            self.pending = {s: p for s, p in self.pending.items() if time.time() - p['created'] < 600}
            self.pending[state] = {'verifier': verifier, 'created': time.time()}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
            'client_id': client['client_id'], 'redirect_uri': self.origin + '/oauth/callback',
            'response_type': 'code', 'scope': self.SCOPES, 'access_type': 'offline',
            'prompt': 'consent select_account', 'state': state,
            'code_challenge': challenge, 'code_challenge_method': 'S256'})

    def callback(self, state, code):
        with self.lock:
            pending = self.pending.pop(state, None)
        if not pending or time.time() - pending['created'] > 600:
            raise ValueError('授权请求已过期，请从操作台重新连接')
        config = self.storage.read()
        client = config['googleClient']
        tokens = self.fetch('https://oauth2.googleapis.com/token', {
            **client, 'grant_type': 'authorization_code', 'code': code,
            'redirect_uri': self.origin + '/oauth/callback', 'code_verifier': pending['verifier']})
        granted = set(tokens.get('scope', '').split())
        if 'https://www.googleapis.com/auth/calendar.readonly' not in granted:
            raise ValueError('未授予日历读取权限，请重新连接并选择授权日历')
        if config.get('requireNewRefreshToken') and not tokens.get('refresh_token'):
            raise ValueError('Google 未返回新的自动续期凭据，原连接已保留，请重新授权')
        info = self.fetch('https://www.googleapis.com/oauth2/v3/userinfo', token=tokens['access_token'])
        account = {'id': info['sub'], 'email': info.get('email', 'Google account'),
                   'access_token': tokens['access_token'], 'refresh_token': tokens.get('refresh_token'),
                   'expires_at': time.time() + tokens.get('expires_in', 3600),
                   'authorized_at': time.time()}
        if 'allowedAccounts' in config and info.get('email_verified') is not True:
            raise ValueError('请使用经过 Google 验证的本人账号')
        if not self.account_allowed(account, config):
            raise ValueError('此操作台只允许已绑定的本人 Google 账号')
        def save(data):
            accounts = data.setdefault('accounts', [])
            old = next((a for a in accounts if a['id'] == account['id']), {})
            account['refresh_token'] = account['refresh_token'] or old.get('refresh_token')
            if tokens.get('refresh_token'):
                account['refresh_token_received_at'] = time.time()
            elif old.get('refresh_token_received_at'):
                account['refresh_token_received_at'] = old['refresh_token_received_at']
            data['accounts'] = [a for a in accounts if a['id'] != account['id']] + [account]
        with self.lock:
            self.storage.update(save)

    def token(self, account, force_refresh=False):
        with self.lock:
            data = self.storage.read()
            account = next((a for a in data.get('accounts', []) if a['id'] == account['id']), account)
            if not self.account_allowed(account, data):
                raise ValueError('此操作台只允许已绑定的本人 Google 账号')
            if not force_refresh and account['expires_at'] > time.time() + 60:
                return account['access_token']
            if not account.get('refresh_token'):
                raise ValueError('账号需要重新授权')
            result = self.fetch('https://oauth2.googleapis.com/token', {
                **data['googleClient'], 'grant_type': 'refresh_token', 'refresh_token': account['refresh_token']})
            account.update(access_token=result['access_token'], expires_at=time.time() + result.get('expires_in', 3600))
            if result.get('refresh_token'):
                account.update(refresh_token=result['refresh_token'], refresh_token_received_at=time.time())
            def update(d):
                for a in d.get('accounts', []):
                    if a['id'] == account['id']:
                        a.update(account)
            self.storage.update(update)
            return account['access_token']

    def paged(self, endpoint, token, params):
        result = []
        for _ in range(100):
            page = self.fetch(endpoint + '?' + urlencode(params), token=token)
            result.extend(page.get('items', []))
            if not page.get('nextPageToken'):
                return result
            params = {**params, 'pageToken': page['nextPageToken']}
        raise ValueError('日历记录过多，请缩小日期范围')

    def events(self, start, end):
        data = self.storage.read()
        accounts = [a for a in data.get('accounts', []) if self.account_allowed(a, data)]
        events, calendars, errors = [], [], []
        colors = ['#b8d7be', '#dfb991', '#aebddf', '#d2b3d9', '#e0bd7a']
        for index, account in enumerate(accounts):
            try:
                token = self.token(account)
                cals = self.paged('https://www.googleapis.com/calendar/v3/users/me/calendarList', token, {'maxResults': 250})
                for cal in cals:
                    if cal.get('deleted') or cal.get('hidden'):
                        continue
                    color = colors[index % len(colors)]
                    key = account['id'] + ':' + cal['id']
                    calendars.append({'key': key, 'name': cal.get('summary', '日历'), 'account': account['email'], 'color': color})
                    try:
                        rows = self.paged('https://www.googleapis.com/calendar/v3/calendars/' + quote(cal['id'], safe='') + '/events', token, {
                            'timeMin': start, 'timeMax': end, 'singleEvents': 'true',
                            'orderBy': 'startTime', 'maxResults': 2500})
                        for e in rows:
                            if e.get('status') == 'cancelled' or not e.get('start'):
                                continue
                            events.append({'id': key + ':' + e['id'], 'uid': e.get('iCalUID'), 'calendar': key,
                                           'title': e.get('summary', '忙碌'), 'start': e['start'], 'end': e.get('end', e['start']),
                                           'location': e.get('location', ''), 'account': account['email'],
                                           'color': color, 'url': e.get('htmlLink')})
                    except Exception as e:
                        errors.append({'account': account['email'], 'calendar': cal.get('summary'), 'message': str(e)})
            except Exception as e:
                errors.append({'account': account['email'], 'message': str(e)})
        return {'events': events, 'calendars': calendars, 'errors': errors, 'fetchedAt': time.time(), 'accounts': self.public_config()['accounts']}

    def disconnect(self, ident):
        # Disconnect only this console; no calendar or event is deleted.
        self.storage.update(lambda d: d.update(accounts=[a for a in d.get('accounts', []) if a['id'] != ident]))
