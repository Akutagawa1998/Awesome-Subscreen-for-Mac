from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.parse import urlparse, parse_qs

from console.local_data import Storage
from console.todo_service import TodoService
from console.wellness_service import WellnessService


class ToDoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.service=TodoService(Storage(Path(self.temp.name)/'ms.json'),'http://127.0.0.1:8765')
        self.service.configure({'clientId':'00000000-0000-0000-0000-000000000001'})
        self.service.fetch=Mock()

    def test_pkce_state_is_single_use_and_cannot_switch_client(self):
        q=parse_qs(urlparse(self.service.begin()).query)
        self.assertEqual(q['code_challenge_method'],['S256'])
        self.service.fetch.side_effect=[{'scope':'Tasks.ReadWrite User.Read','access_token':'test','refresh_token':'refresh'}, {'displayName':'Example'}]
        self.service.callback(q['state'][0],'code')
        self.assertEqual(self.service.public_config()['account'],'Example')
        with self.assertRaises(ValueError):self.service.callback(q['state'][0],'code')
        self.assertEqual(self.service.fetch.call_count,2)
        self.assertNotIn('accessToken',json.dumps(self.service.public_config()))
        self.assertEqual(self.service.storage.path.stat().st_mode & 0o777,0o600)
        q=parse_qs(urlparse(self.service.begin()).query)
        self.service.configure({'clientId':'00000000-0000-0000-0000-000000000002'})
        with self.assertRaises(ValueError):self.service.callback(q['state'][0],'code')
        self.assertFalse(self.service.public_config()['connected'])

    def test_missing_scope_rejected_before_storing_credentials(self):
        q=parse_qs(urlparse(self.service.begin()).query)
        self.service.fetch.return_value={'scope':'User.Read','access_token':'test'}
        with self.assertRaises(ValueError):self.service.callback(q['state'][0],'code')
        self.assertFalse(self.service.public_config()['connected'])

    def test_pagination_rejects_external_host_before_sending_token(self):
        self.service.fetch.return_value={'value':[], '@odata.nextLink':'https://example.org/collect'}
        with self.assertRaises(ValueError):self.service.paged(self.service.GRAPH+'/me/todo/lists','test')
        self.assertEqual(self.service.fetch.call_count,1)

    def test_list_failure_is_visible_and_completion_is_explicit(self):
        self.service.storage.update(lambda d:d.update(account={'name':'Example','accessToken':'test','expiresAt':9999999999}))
        self.service.fetch.side_effect=[{'value':[{'id':'a','displayName':'Inbox'},{'id':'b','displayName':'Other'}]},
            {'value':[{'id':'t','title':'Example task'}]},ValueError('offline')]
        result=self.service.read()
        self.assertEqual(len(result['tasks']),1); self.assertEqual(result['errors'],['offline'])
        self.service.fetch.reset_mock(side_effect=True);self.service.fetch.return_value={}
        with self.assertRaises(ValueError):self.service.complete({'listId':'a','id':'t','completed':'true'})
        self.service.fetch.assert_not_called()
        self.service.complete({'listId':'a','id':'t','completed':True})
        self.assertEqual(self.service.fetch.call_args.kwargs['body'],{'status':'completed'})
        self.service.disconnect();self.assertIsNone(self.service.cache)
        self.assertFalse(self.service.public_config()['connected'])


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.service=WellnessService(Storage(self.root/'state.json'))
        self.sample={'heartRate':{'value':72,'at':'2025-01-01T10:00:00Z'}}

    def test_no_data_is_not_zero_or_a_recovery_score(self):
        self.assertEqual(self.service.read()['metrics'],{})
        self.assertFalse(self.service.read()['connected'])
        result=self.service.import_data(self.sample)
        self.assertEqual(result['mode'],'import')
        self.assertEqual(result['metrics']['heartRate']['value'],72)
        self.assertNotIn('recovery',result)

    def test_invalid_numbers_missing_dates_and_future_dates_rejected(self):
        for row in [{'value':float('nan'),'at':'2025-01-01T10:00:00Z'},
                    {'value':72,'at':'2025-01-01T10:00:00'}, {'value':72},
                    {'value':72,'at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}]:
            with self.assertRaises(ValueError):self.service.import_data({'heartRate':row})
        self.assertFalse(self.service.read()['connected'])

    def test_live_file_rereads_actual_sample_and_copes_with_partial_sync(self):
        f=self.root/'health.json';f.write_text(json.dumps(self.sample))
        self.service.configure({'path':str(f)})
        self.sample['heartRate']['value']=83;f.write_text(json.dumps(self.sample))
        self.assertEqual(self.service.read()['metrics']['heartRate']['value'],83)
        f.write_text('{')
        result=self.service.read();self.assertTrue(result['error']);self.assertEqual(result['metrics'],{})
        f.write_text(json.dumps(self.sample))
        self.assertEqual(self.service.read()['metrics']['heartRate']['value'],83)
        self.service.disconnect();self.assertFalse(self.service.read()['connected'])

    def test_oversized_or_non_json_file_cannot_replace_source(self):
        self.service.import_data(self.sample)
        f=self.root/'large.json';f.write_text(' '*100001)
        with self.assertRaises(ValueError):self.service.configure({'path':str(f)})
        self.assertEqual(self.service.read()['metrics']['heartRate']['value'],72)
