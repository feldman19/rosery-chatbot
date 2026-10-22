import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from decision_history import save_observation, ACCOUNT

class HistoryTests(unittest.TestCase):
 def test_hourly_source_time_projection_and_no_overwrite(self):
  source={'last_success_at':'2026-10-20T16:12:00+00:00','data':{
   'verified':True,'complete':True,'account_id':ACCOUNT,'currency':'ILS','today':'2026-10-20',
   'private_data':'DO_NOT_COPY','rows':[
    {'ad_id':'a','ad_name':'DO_NOT_COPY','date_start':'2026-10-19','date_stop':'2026-10-19',
     'actions':[{'action_type':'purchase','value':'1','extra':'DO_NOT_COPY'}]},
    {'ad_id':'a','date_start':'2026-10-20','date_stop':'2026-10-20'}]}}
  with TemporaryDirectory(dir=Path(__file__).parent) as folder:
   self.assertEqual(save_observation(folder,source),'recorded')
   file=Path(folder)/'decision-observations/20261020T16.json'
   text=file.read_text(encoding='utf-8');x=json.loads(text)
   self.assertNotIn('DO_NOT_COPY',text);self.assertEqual(len(x['rows']),1)
   source['data']['rows'][0]['actions'][0]['value']='2'
   self.assertEqual(save_observation(folder,source),'hour_already_recorded')
   self.assertEqual(file.read_text(encoding='utf-8'),text)
   source['last_success_at']='2026-10-20T17:01:00+00:00'
   self.assertEqual(save_observation(folder,source),'recorded')
   self.assertEqual(len(list(file.parent.glob('*.json'))),2)
   file.write_text('{broken',encoding='utf-8')
   source['last_success_at']='2026-10-20T16:59:00+00:00'
   self.assertEqual(save_observation(folder,source),'corrupt_existing_hour')
   self.assertEqual(file.read_text(encoding='utf-8'),'{broken')
 def test_failed_or_partial_or_wrong_account_never_archived(self):
  with TemporaryDirectory(dir=Path(__file__).parent) as folder:
   for source in ({},{'error':'failed'},{'data':{'verified':True,'complete':False}},
                  {'data':{'verified':True,'complete':True,'account_id':'other','currency':'ILS'}}):
    self.assertEqual(save_observation(folder,source),'ineligible_source')
   self.assertFalse((Path(folder)/'decision-observations').exists())
 def test_malformed_source_does_not_break_refresh(self):
  with TemporaryDirectory(dir=Path(__file__).parent) as folder:
   for source in ([],1,{'data':[1]}):
    self.assertEqual(save_observation(folder,source),'invalid_source')

if __name__=='__main__':unittest.main()
