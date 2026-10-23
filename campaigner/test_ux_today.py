import json
import subprocess
import time
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch
from insights_split import read_split_insights
from management_report import _daily, ACCOUNT
from outside_check import _run, _clean

class TodayReadTests(unittest.TestCase):
    def data(self):
        day='2026-10-23'
        row={'ad_id':'123','adset_id':'456','campaign_id':'789','date_start':day,'date_stop':day,'spend':'129','impressions':'100','actions':[],'action_values':[]}
        orphan={**row,'ad_id':'124'}
        def graph(token,path,params):
            fields=params['fields'].split(',')
            return {'data':[{k:v for k,v in r.items() if k in fields} for r in ([row] if 'spend' in fields else [row,orphan])]}
        result=read_split_insights(graph,'synthetic',ACCOUNT,day,day,'ad',time.monotonic()+30,{})
        return {**result,'account_id':ACCOUNT,'since':day,'until':day,'query_settings':{},'checked_at':datetime.now(timezone.utc).isoformat()}

    def test_complete_delivery_today_ignores_action_only_rows_for_account_spend(self):
        result=_daily(self.data(),{},date(2026,10,23),True,'ILS')[-1]
        self.assertTrue(result['partial'])
        self.assertEqual(result['metrics']['spend'],129)
        self.assertEqual(result['metrics']['roas'],0)
        self.assertIsNone(result['metrics']['store_orders'])

    def test_incomplete_delivery_stays_unknown_with_explicit_lower_bound(self):
        data=self.data();data.pop('metric_collection')
        result=_daily(data,{},date(2026,10,23),True,'ILS')[-1]['metrics']
        self.assertIsNone(result['spend']);self.assertEqual(result['spend_lower_bound'],129)
        self.assertIsNone(result['roas'])

    def test_incomplete_account_read_exposes_only_identity_bound_lower_bound(self):
        data=self.data();data.pop('metric_collection');data.update(verified=True,currency='ILS',today='2026-10-23',complete=False)
        result=_daily(data,{},date(2026,10,23),False,'ILS')[-1]['metrics']
        self.assertIsNone(result['spend']);self.assertEqual(result['spend_lower_bound'],129)
        self.assertIsNone(result['roas'])
        data['account_id']='other'
        self.assertNotIn('spend_lower_bound',_daily(data,{},date(2026,10,23),False,'ILS')[-1]['metrics'])

    def test_invalid_provenance_does_not_adopt_delivery_total(self):
        data=self.data();data['metric_streams']['delivery']['complete']=False
        result=_daily(data,{},date(2026,10,23),True,'ILS')[-1]['metrics']
        self.assertIsNone(result['spend']);self.assertNotIn('spend_lower_bound',result)

    def test_research_failure_diagnosis_does_not_expose_cli_output(self):
        done=subprocess.CompletedProcess([],1,'','rate limit private-marker')
        with patch('outside_check.shutil.which',return_value='claude'),patch('outside_check.subscription_environment',return_value={}),patch('outside_check.subscription_auth',return_value=True),patch('outside_check.subprocess.run',return_value=done):
            with self.assertRaisesRegex(ValueError,'מגבלת שימוש') as error:_run('synthetic')
        self.assertNotIn('private-marker',str(error.exception))

    def test_source_optional_fields_do_not_crash_cleaning(self):
        result=_clean({'findings':[{'url':'https://example.com','relation':'context','source':'Example'}]})
        self.assertEqual(result['findings'][0]['source'],'Example')

class VersionTests(unittest.TestCase):
    def test_update_requires_valid_distinct_descendant(self):
        from server import screen_update_newer
        with patch('subprocess.run',return_value=subprocess.CompletedProcess([],0)) as run:
            self.assertFalse(screen_update_newer('__VERSION__','גרסה abcdef012'))
            self.assertFalse(screen_update_newer('abcdef0','גרסה abcdef012 · המסך עודכן 23.10'))
            self.assertFalse(run.called)
            self.assertTrue(screen_update_newer('1234567','גרסה abcdef012 · המסך עודכן 23.10'))
        with patch('subprocess.run',return_value=subprocess.CompletedProcess([],1)):
            self.assertFalse(screen_update_newer('1234567','גרסה abcdef012'))
