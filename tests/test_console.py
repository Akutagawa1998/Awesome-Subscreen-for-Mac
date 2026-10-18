import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from datetime import datetime, timezone

from console.calendar_service import CalendarService
from console.codex_bridge import CodexBridge
from console.local_data import Storage, ClaudeUsage, usage_counts
from server import App


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.bridge = CodexBridge()
        self.bridge.connected = True
        self.bridge.ws = Mock()
        self.bridge.pending['7'] = {'id': 7, 'method': 'item/commandExecution/requestApproval',
                                    'params': {'threadId': 'task-a', 'command': 'echo test'}, 'sending': False}

    def test_decision_only_for_matching_live_request(self):
        with self.assertRaises(ValueError):
            self.bridge.respond(7, 'task-b', 'accept')
        with self.assertRaises(ValueError):
            self.bridge.respond(99, 'task-a', 'accept')
        self.bridge.ws.send.assert_not_called()
        self.bridge.respond(7, 'task-a', 'accept')
        self.bridge.ws.send.assert_called_once_with({'id': 7, 'result': {'decision': 'accept'}})
        with self.assertRaises(ValueError):
            self.bridge.respond(7, 'task-a', 'accept')

    def test_no_permanent_or_session_approval(self):
        with self.assertRaises(ValueError):
            self.bridge.respond(7, 'task-a', 'acceptForSession')
        self.bridge.ws.send.assert_not_called()

    def test_exact_permission_subset_and_turn_scope(self):
        permissions = {'network': {'enabled': True}}
        self.bridge.pending['7'].update(method='item/permissions/requestApproval', params={'threadId':'task-a', 'permissions':permissions})
        self.bridge.respond(7, 'task-a', 'accept')
        self.bridge.ws.send.assert_called_with({'id': 7, 'result': {'permissions':permissions,'scope':'turn'}})

    def test_disconnected_request_cannot_be_approved(self):
        self.bridge.connected = False
        with self.assertRaises(ValueError):
            self.bridge.respond(7, 'task-a', 'accept')

    def test_incomplete_action_and_file_diff_require_original_app(self):
        self.bridge.pending['7']['params'].pop('command')
        with self.assertRaises(ValueError):self.bridge.respond(7,'task-a','accept')
        self.bridge.pending['7']['method']='item/fileChange/requestApproval'
        with self.assertRaises(ValueError):self.bridge.respond(7,'task-a','accept')
        self.bridge.ws.send.assert_not_called()


class LocalStateTests(unittest.TestCase):
    def test_hiding_is_local_only(self):
        with tempfile.TemporaryDirectory() as d:
            app = App.__new__(App)
            app.store = Storage(Path(d)/'settings.json')
            app.codex = Mock()
            ident = '00000000-0000-0000-0000-000000000001'
            app.post('/api/hide', {'id':ident,'hidden':True})
            self.assertEqual(app.store.read()['hidden'], [ident])
            self.assertEqual(app.store.path.stat().st_mode & 0o777, 0o600)
            app.post('/api/restore', {})
            self.assertEqual(app.store.read()['hidden'], [])
            self.assertEqual(app.codex.mock_calls, [])

    def test_external_runtime_never_resumed_for_reply(self):
        app = App.__new__(App)
        app.codex = Mock()
        app.codex.call.return_value = {'thread':{'status':{'type':'notLoaded'}}}
        with self.assertRaises(ValueError):
            app.post('/api/reply', {'id':'00000000-0000-0000-0000-000000000001','text':'test'})
        app.codex.attach.assert_not_called()
        self.assertEqual(app.codex.call.call_count, 1)

    def test_claude_deduplicates_streams_and_forked_logs(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'project').mkdir()
            now=datetime.now(timezone.utc).isoformat()
            def record(output):
                return {'type':'assistant','timestamp':now,'requestId':'req-1','uuid':'uuid-1','cwd':'/work/project',
                        'message':{'id':'msg-1','usage':{'input_tokens':3,'output_tokens':output,'cache_read_input_tokens':5}}}
            (root/'project/a.jsonl').write_text('\n'.join(json.dumps(record(v)) for v in [2,4,4])+'\n{partial')
            (root/'project/b.jsonl').write_text(json.dumps(record(4)))
            result=ClaudeUsage(root).read()
            self.assertEqual(result['totalTokens'],12)
            self.assertEqual(len(result['sessions']),1)

    def test_claude_iteration_usage(self):
        counts=usage_counts({'input_tokens':0,'iterations':[{'input_tokens':2,'output_tokens':4,'cache_read_input_tokens':50}]})
        self.assertEqual(sum(counts.values()),56)


class CalendarTests(unittest.TestCase):
    def test_oauth_state_replay_and_scope_validation(self):
        with tempfile.TemporaryDirectory() as d:
            service=CalendarService(Storage(Path(d)/'google.json'),'http://127.0.0.1:8765')
            service.configure({'installed':{'client_id':'test.apps.googleusercontent.com','client_secret':'test'}})
            url=service.begin()
            self.assertIn('code_challenge_method=S256',url)
            self.assertIn('select_account',url)
            with self.assertRaises(ValueError):service.callback('bad-state','code')
            state=next(iter(service.pending))
            service.fetch=Mock(return_value={'scope':'openid email','access_token':'fake'})
            with self.assertRaises(ValueError):service.callback(state,'code')
            self.assertNotIn(state,service.pending)
            self.assertEqual(service.public_config()['accounts'],[])

    def test_pagination_multiple_accounts_and_error_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            store=Storage(Path(d)/'google.json')
            store.update(lambda x:x.update(accounts=[{'id':'a','email':'a@test'},{'id':'b','email':'b@test'}]))
            service=CalendarService(store,'http://127.0.0.1:8765')
            def token(account):
                if account['id']=='b':raise ValueError('需要重新授权')
                return 'token'
            service.token=token
            service.fetch=Mock(side_effect=[
                {'items':[{'id':'cal/1','summary':'One'}],'nextPageToken':'next'},
                {'items':[{'id':'cal/2','summary':'Two'}]},
                {'items':[{'id':'e1','summary':'Meeting','start':{'date':'2026-10-18'},'end':{'date':'2026-10-19'}}]},
                {'items':[]}
            ])
            result=service.events('2026-10-01T00:00:00Z','2026-11-01T00:00:00Z')
            self.assertEqual(len(result['events']),1)
            self.assertEqual(len(result['calendars']),2)
            self.assertEqual(result['errors'][0]['account'],'b@test')
            self.assertIn('pageToken=next',service.fetch.call_args_list[1].args[0])
            self.assertIn('cal%2F1',service.fetch.call_args_list[2].args[0])


if __name__=='__main__':unittest.main()
