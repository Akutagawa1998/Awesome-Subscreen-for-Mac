from datetime import datetime, timezone, timedelta
import json
import os
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

    def test_personal_authority_used_for_authorization_token_and_refresh(self):
        self.service.configure({'clientId':self.service.client_id(),'authority':'consumers'})
        url=self.service.begin();q=parse_qs(urlparse(url).query)
        self.assertIn('/consumers/',url)
        self.service.fetch.side_effect=[{'scope':'Tasks.ReadWrite','access_token':'test','refresh_token':'refresh','expires_in':0}, {'displayName':'Example'}, {'access_token':'refreshed'}]
        self.service.callback(q['state'][0],'code')
        self.assertIn('/consumers/',self.service.fetch.call_args_list[0].args[0])
        self.assertEqual(self.service.token(),'refreshed')
        self.assertIn('/consumers/',self.service.fetch.call_args.args[0])
        q=parse_qs(urlparse(self.service.begin()).query)
        self.service.configure({'clientId':self.service.client_id(),'authority':'common'})
        with self.assertRaises(ValueError):self.service.callback(q['state'][0],'code')
        with self.assertRaises(ValueError):self.service.configure({'clientId':self.service.client_id(),'authority':'example.org'})

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

    def test_folder_combines_metrics_and_skips_invalid_matching_files(self):
        folder=self.root/'sync';folder.mkdir()
        (folder/'wokyis-health.json').write_text(json.dumps(self.sample))
        (folder/'wokyis-health-hrv.json').write_text(json.dumps({'schemaVersion':2,'samples':[
            {'kind':'hrv','value':'0.045','unit':'s','at':'2025-01-01T10:00:00Z','source':'Example Watch'}]}))
        (folder/'wokyis-health-old.json').write_text('{')
        (folder/'wokyis-health-empty.json').write_text(json.dumps({'schemaVersion':2,'samples':[]}))
        # Neither unrelated JSON, nested files nor symlinks may supply metrics.
        unrelated={'restingHeartRate':{'value':61,'at':'2025-01-01T10:00:00Z'}}
        (folder/'other.json').write_text(json.dumps(unrelated))
        nested=folder/'nested';nested.mkdir()
        (nested/'wokyis-health-resting.json').write_text(json.dumps(unrelated))
        external=self.root/'external.json';external.write_text(json.dumps(unrelated))
        (folder/'wokyis-health-link.json').symlink_to(external)
        result=self.service.configure({'path':str(folder)})
        self.assertEqual(result['mode'],'folder')
        self.assertEqual(result['metrics']['heartRate']['value'],72)
        self.assertEqual(result['metrics']['hrv']['value'],45)
        self.assertNotIn('restingHeartRate',result['metrics'])
        self.assertTrue(result['connected'])
        self.assertIn('3 个健康同步文件',result['warnings'][0])
        (folder/'wokyis-health-resting.json').write_text(json.dumps(unrelated))
        self.assertEqual(self.service.read()['metrics']['restingHeartRate']['value'],61)

    def test_folder_uses_sample_time_instead_of_file_mtime(self):
        folder=self.root/'sync';folder.mkdir()
        older=folder/'wokyis-health.json';newer=folder/'wokyis-health-2.json'
        older.write_text(json.dumps(self.sample))
        newer.write_text(json.dumps({'schemaVersion':2,'samples':[
            {'kind':'heartRate','value':'83','unit':'bpm','at':'2025-01-01T07:00:00-04:00','source':'Example Watch'}]}))
        os.utime(older,(2000000000,2000000000));os.utime(newer,(1,1))
        result=self.service.configure({'path':str(folder)})
        self.assertEqual(result['metrics']['heartRate']['value'],83)
        self.assertEqual(result['metrics']['heartRate']['at'],'2025-01-01T11:00:00+00:00')

    def test_folder_merges_sleep_intervals_across_files_without_device_double_count(self):
        folder=self.root/'sync';folder.mkdir()
        def sleep(start,end,source):
            return {'kind':'sleep','value':'Asleep','at':f'2025-01-01T{start}:00:00Z',
                    'end':f'2025-01-01T{end}:00:00Z','source':source}
        first={'schemaVersion':2,'samples':[sleep('00','04','Example Watch')]}
        second={'schemaVersion':2,'samples':[sleep('00','04','Example Watch'),
            sleep('04','08','Example Watch'),sleep('00','08','Example Band')]}
        (folder/'wokyis-health-sleep.json').write_text(json.dumps(first))
        (folder/'wokyis-health-sleep-2.json').write_text(json.dumps(second))
        result=self.service.configure({'path':str(folder)})
        self.assertEqual(result['metrics']['sleepHours']['value'],8)
        self.assertEqual(result['metrics']['sleepHours']['at'],'2025-01-01T08:00:00+00:00')
        self.assertEqual(result['warnings'],[])

    def test_empty_or_invalid_folder_cannot_replace_working_source(self):
        self.service.import_data(self.sample)
        folder=self.root/'sync';folder.mkdir()
        with self.assertRaisesRegex(ValueError,'没有可用健康数据'):
            self.service.configure({'path':str(folder)})
        (folder/'wokyis-health.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'没有可用健康数据'):
            self.service.configure({'path':str(folder)})
        self.assertEqual(self.service.read()['metrics']['heartRate']['value'],72)
        self.assertEqual(self.service.read()['mode'],'import')

    def test_folder_limits_file_count_total_bytes_and_sample_count(self):
        folder=self.root/'sync';folder.mkdir()
        for index in range(self.service.MAX_FOLDER_FILES+1):
            (folder/f'wokyis-health-{index}.json').write_text(json.dumps(self.sample))
        with self.assertRaisesRegex(ValueError,'文件过多'):
            self.service.configure({'path':str(folder)})
        for file in folder.iterdir():file.unlink()
        for index in range(5):
            (folder/f'wokyis-health-{index}.json').write_bytes(b' '*900000)
        with self.assertRaisesRegex(ValueError,'总大小超过'):
            self.service.configure({'path':str(folder)})
        for file in folder.iterdir():file.unlink()
        row={'kind':'heartRate','value':72,'unit':'bpm','at':'2025-01-01T10:00:00Z'}
        for index in range(2):
            (folder/f'wokyis-health-{index}.json').write_text(json.dumps({'schemaVersion':2,'samples':[row]*3000}))
        with self.assertRaisesRegex(ValueError,'样本过多'):
            self.service.configure({'path':str(folder)})

    def test_oversized_or_non_json_file_cannot_replace_source(self):
        self.service.import_data(self.sample)
        f=self.root/'large.json';f.write_text(' '*(self.service.MAX_BYTES+1))
        with self.assertRaises(ValueError):self.service.configure({'path':str(f)})
        self.assertEqual(self.service.read()['metrics']['heartRate']['value'],72)

    def test_shortcut_samples_keep_source_time_and_convert_known_units(self):
        rows=[{'kind':'heartRate','value':'72','unit':'次/分','at':'2025-01-01T10:00:00Z','source':'Apple Watch'},
              {'kind':'heartRate','value':'75','unit':'bpm','at':'2025-01-01T11:00:00Z','source':'Google Health'},
              {'kind':'hrv','value':'0.045','unit':'s','at':'2025-01-01T10:00:00Z','source':'Apple Watch'}]
        r=self.service.import_data({'schemaVersion':2,'samples':rows})
        self.assertEqual(r['metrics']['heartRate']['value'],75)
        self.assertEqual(r['metrics']['heartRate']['source'],'Google Health')
        self.assertEqual(r['metrics']['hrv']['value'],45)
        self.assertEqual(r['sources'],['Apple Watch','Google Health'])
        self.assertEqual(r['warnings'],[])

    def test_sleep_overlap_and_duplicate_device_records_are_not_added(self):
        def sleep(value,start,end,source='Apple Watch'):
            return {'kind':'sleep','value':value,'at':f'2025-01-01T{start}:00:00Z','end':f'2025-01-01T{end}:00:00Z','source':source}
        rows=[sleep('Asleep','00','08'),sleep('Core','00','04'),sleep('Deep','03','05'),
              sleep('In Bed','00','09'),sleep('Awake','08','09'),sleep('Asleep','00','08','Google Health')]
        r=self.service.import_data({'schemaVersion':2,'samples':rows})
        self.assertEqual(r['metrics']['sleepHours']['value'],8)
        self.assertIn(r['metrics']['sleepHours']['source'],['Apple Watch','Google Health'])
        rows.append(sleep('Core','16','17'))
        r=self.service.import_data({'schemaVersion':2,'samples':rows})
        self.assertEqual(r['metrics']['sleepHours']['value'],1) # latest bout, not all-day sum

    def test_unknown_units_or_categories_do_not_become_valid_metrics(self):
        rows=[{'kind':'heartRate','value':'72','unit':'mystery','at':'2025-01-01T10:00:00Z'},
              {'kind':'sleep','value':'unknown','at':'2025-01-01T00:00:00Z','end':'2025-01-01T08:00:00Z','source':'Watch'}]
        with self.assertRaises(ValueError):self.service.import_data({'schemaVersion':2,'samples':rows})
        rows.append({'kind':'heartRate','value':'76','unit':'count/min','at':'2025-01-01T10:00:00Z'})
        r=self.service.import_data({'schemaVersion':2,'samples':rows})
        self.assertEqual(r['metrics']['heartRate']['value'],76)
        self.assertNotIn('sleepHours',r['metrics'])
        self.assertTrue(r['warnings'])
        with self.assertRaises(ValueError):self.service.import_data({'schemaVersion':2,'samples':[]})

    def test_chinese_shortcut_sleep_labels_keep_rem_and_ignore_time_in_bed(self):
        rows=[{'kind':'sleep','value':label,'at':f'2025-01-01T{start}:00:00Z',
               'end':f'2025-01-01T{end}:00:00Z','source':'Example Watch'}
              for label,start,end in [('睡眠时间','00','02'),('核心睡眠','00','01'),
                                      ('深度睡眠','01','02'),('快速动眼睡眠','02','03'),
                                      ('卧床时间','00','04')]]
        r=self.service.import_data({'schemaVersion':2,'samples':rows})
        self.assertEqual(r['metrics']['sleepHours']['value'],3)
        self.assertEqual(r['warnings'],[])
