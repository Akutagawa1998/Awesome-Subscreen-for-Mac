"""Microsoft To Do via delegated Graph OAuth. No bundled/shared client secret."""
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote, urlparse
from urllib.request import Request, urlopen


class TodoService:
    GRAPH = 'https://graph.microsoft.com/v1.0'
    SCOPES = 'offline_access https://graph.microsoft.com/Tasks.ReadWrite https://graph.microsoft.com/User.Read'

    def __init__(self, storage, origin):
        self.storage, self.origin = storage, origin
        self.lock = threading.RLock()
        self.pending = {}
        self.cache = None

    @staticmethod
    def fetch(url, *, form=None, body=None, token=None, method=None):
        headers = {'Accept': 'application/json'}
        payload = None
        if form is not None:
            payload = urlencode(form).encode(); headers['Content-Type'] = 'application/x-www-form-urlencoded'
        if body is not None:
            payload = json.dumps(body).encode(); headers['Content-Type'] = 'application/json'
        if token:
            headers['Authorization'] = 'Bearer ' + token
        try:
            with urlopen(Request(url, data=payload, headers=headers, method=method), timeout=15) as response:
                return json.load(response)
        except HTTPError as e:
            if e.code in (400, 401, 403):
                raise ValueError('Microsoft 授权或配置失效，请重新连接并允许任务访问') from None
            if e.code == 429:
                raise ValueError('Microsoft 暂时限流，请稍后刷新') from None
            raise ValueError(f'Microsoft 暂时不可用（HTTP {e.code}）') from None
        except (URLError, TimeoutError):
            raise ValueError('Microsoft 连接超时，请稍后重试') from None

    def client_id(self):
        return self.storage.read().get('clientId') or os.environ.get('WOKYIS_MICROSOFT_CLIENT_ID', '')

    def authority(self):
        # Personal-only registrations must use the consumers sign-in endpoint.
        return self.storage.read().get('authority', 'common')

    def auth_url(self, authority=None):
        return 'https://login.microsoftonline.com/' + (authority or self.authority()) + '/oauth2/v2.0'

    def public_config(self):
        account = self.storage.read().get('account')
        return {'configured': bool(self.client_id()), 'connected': bool(account),
                'account': account.get('name', 'Microsoft') if account else None,
                'redirectUri': self.origin + '/oauth/microsoft/callback', 'authority': self.authority()}

    def configure(self, value):
        ident = str(value.get('clientId', '')).strip()
        if not re.fullmatch(r'[a-fA-F0-9]{8}-(?:[a-fA-F0-9]{4}-){3}[a-fA-F0-9]{12}', ident):
            raise ValueError('请输入 Microsoft 应用的 Application (client) ID')
        authority = value.get('authority', self.authority())
        if authority not in ('common', 'consumers'):
            raise ValueError('无效的 Microsoft 账号类型')
        with self.lock:
            if ident != self.client_id():
                self.storage.update(lambda d: (d.clear(), d.update(clientId=ident, authority=authority)))
                self.pending.clear(); self.cache = None
            elif authority != self.authority():
                self.storage.update(lambda d: d.update(authority=authority))
                self.pending.clear()

    def begin(self):
        if not self.client_id():
            raise ValueError('请先设置 Microsoft 应用 ID')
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        with self.lock:
            self.pending = {s:p for s,p in self.pending.items() if time.time()-p['created'] < 600}
            self.pending[state] = {'verifier': verifier, 'created': time.time(), 'clientId': self.client_id(), 'authority': self.authority()}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        return self.auth_url() + '/authorize?' + urlencode({
            'client_id': self.client_id(), 'redirect_uri': self.origin+'/oauth/microsoft/callback',
            'response_type': 'code', 'response_mode': 'query', 'scope': self.SCOPES,
            'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256', 'prompt': 'select_account'})

    def callback(self, state, code):
        with self.lock:
            pending = self.pending.pop(state, None)
            if not pending or time.time()-pending['created'] > 600 or pending['clientId'] != self.client_id():
                raise ValueError('授权请求已过期，请从操作台重新连接')
            if not code:
                raise ValueError('Microsoft 授权未完成')
            tokens = self.fetch(self.auth_url(pending['authority'])+'/token', form={
                'client_id': self.client_id(), 'grant_type': 'authorization_code', 'code': code,
                'redirect_uri': self.origin+'/oauth/microsoft/callback', 'code_verifier': pending['verifier']})
            granted = {s.rsplit('/', 1)[-1].lower() for s in tokens.get('scope', '').split()}
            if 'tasks.readwrite' not in granted:
                raise ValueError('未授予 To Do 任务权限，请重新连接')
            profile = self.fetch(self.GRAPH+'/me?$select=displayName', token=tokens['access_token'])
            account = {'name': profile.get('displayName', 'Microsoft'), 'accessToken': tokens['access_token'],
                       'refreshToken': tokens.get('refresh_token'), 'expiresAt': time.time()+tokens.get('expires_in', 3600),
                       'authority': pending['authority']}
            self.storage.update(lambda d: d.update(account=account)); self.cache = None

    def token(self):
        with self.lock:
            account = self.storage.read().get('account')
            if not account:
                raise ValueError('请先连接 Microsoft To Do')
            if account['expiresAt'] > time.time()+60:
                return account['accessToken']
            if not account.get('refreshToken'):
                raise ValueError('Microsoft 账号需要重新授权')
            tokens = self.fetch(self.auth_url(account.get('authority', self.authority()))+'/token', form={'client_id': self.client_id(),
                'grant_type':'refresh_token','refresh_token':account['refreshToken'],'scope':self.SCOPES})
            account.update(accessToken=tokens['access_token'], refreshToken=tokens.get('refresh_token', account['refreshToken']),
                           expiresAt=time.time()+tokens.get('expires_in', 3600))
            self.storage.update(lambda d:d.update(account=account))
            return account['accessToken']

    def paged(self, url, token):
        rows = []
        for _ in range(100):
            # Never send a bearer token to an arbitrary @odata.nextLink host.
            p = urlparse(url)
            if p.scheme != 'https' or p.netloc != 'graph.microsoft.com' or not p.path.startswith('/v1.0/me/todo/'):
                raise ValueError('Microsoft 返回了无效分页地址')
            result = self.fetch(url, token=token)
            rows.extend(result.get('value', []))
            url = result.get('@odata.nextLink')
            if not url:
                return rows
        raise ValueError('任务记录过多，请减少列表中的历史任务')

    def read(self, force=False):
        with self.lock:
            config = self.public_config()
            if not config['connected']:
                return {**config, 'lists':[], 'tasks':[], 'fetchedAt':None}
            if self.cache and not force and time.time()-self.cache['fetchedAt'] < 60:
                return self.cache
            try:
                token = self.token()
                lists = self.paged(self.GRAPH+'/me/todo/lists?$top=100', token)
                tasks, errors = [], []
                for row in lists:
                    try:
                        data = self.paged(self.GRAPH+'/me/todo/lists/'+quote(row['id'],safe='')+'/tasks?$top=100', token)
                        for t in data:
                            tasks.append({'id':t['id'],'listId':row['id'],'listName':row['displayName'],
                                'title':t.get('title','未命名任务'),'status':t.get('status','notStarted'),
                                'importance':t.get('importance','normal'),'due':t.get('dueDateTime')})
                    except ValueError as e:
                        errors.append(str(e))
                self.cache = {**config,'lists':[{'id':r['id'],'name':r['displayName']} for r in lists],
                              'tasks':tasks,'errors':errors,'fetchedAt':time.time(),'stale':False}
            except ValueError as e:
                if self.cache:
                    return {**self.cache,'stale':True,'errors':[str(e)]}
                raise
            return self.cache

    def complete(self, value):
        list_id, task_id = value.get('listId'), value.get('id')
        if not all(isinstance(x,str) and 0 < len(x) <= 1024 for x in (list_id, task_id)) or type(value.get('completed')) is not bool:
            raise ValueError('无效任务操作')
        with self.lock:
            self.fetch(self.GRAPH+'/me/todo/lists/'+quote(list_id,safe='')+'/tasks/'+quote(task_id,safe=''),
                       token=self.token(), method='PATCH', body={'status':'completed' if value['completed'] else 'notStarted'})
            self.cache = None
        return {'ok':True}

    def disconnect(self):
        with self.lock:
            self.storage.update(lambda d:d.pop('account',None))
            self.pending.clear(); self.cache = None
