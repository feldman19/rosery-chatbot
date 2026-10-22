import copy,json,time,unittest
from pathlib import Path
from datetime import datetime,timezone
from insights_split import read_split_insights,split_evidence_valid
from meta_direct import ACCOUNT,QUERY_SETTINGS


class SplitInsightsTests(unittest.TestCase):
 def row(self,identity='123'):
  return {'ad_id':identity,'ad_name':'Ad','adset_id':'456','campaign_id':'789','date_start':'2026-10-20','date_stop':'2026-10-20','spend':'699.30','impressions':'1000','actions':[{'action_type':'offsite_conversion.fb_pixel_purchase','value':'4'}],'action_values':[{'action_type':'offsite_conversion.fb_pixel_purchase','value':'1085'}],'inline_link_clicks':'20'}
 def run_read(self,graph):return read_split_insights(graph,'synthetic',ACCOUNT,'2026-10-14','2026-10-21','ad',time.monotonic()+30,QUERY_SETTINGS)
 def evidence(self,value):return {**value,'checked_at':datetime.now(timezone.utc).isoformat(),'account_id':ACCOUNT,'since':'2026-10-14','until':'2026-10-21','query_settings':QUERY_SETTINGS}
 def test_mixed_metric_corruption_avoided_by_disjoint_queries(self):
  calls=[]
  def graph(token,path,params):
   calls.append(params);row=self.row();fields=set(params['fields'].split(','))
   if 'spend' in fields and fields&{'actions','action_values','inline_link_clicks'}:row['spend']='0'
   return {'data':[{k:v for k,v in row.items() if k in fields}]}
  result=self.run_read(graph)
  self.assertEqual(result['rows'][0]['spend'],'699.30');self.assertEqual(result['rows'][0]['actions'][0]['value'],'4')
  self.assertEqual(len(calls),2);self.assertTrue(split_evidence_valid(self.evidence(result),'ad'))
 def test_missing_delivery_row_never_becomes_zero(self):
  def graph(token,path,params):return {'data':[] if 'spend' in params['fields'].split(',') else [self.row()]}
  row=self.run_read(graph)['rows'][0]
  self.assertNotIn('spend',row);self.assertFalse(row['metric_sources']['delivery_present']);self.assertEqual(row['actions'][0]['value'],'4')
 def test_missing_action_row_never_becomes_empty_actions(self):
  def graph(token,path,params):return {'data':[self.row()] if 'spend' in params['fields'].split(',') else []}
  row=self.run_read(graph)['rows'][0]
  self.assertEqual(row['spend'],'699.30');self.assertNotIn('actions',row)
 def test_wrong_scope_duplicate_or_ranged_grain_block(self):
  r=self.row()
  for rows in ([r,r],[{**r,'account_id':'other'}],[{**r,'date_stop':'2026-10-21'}],[{**r,'date_start':'2026-10-13','date_stop':'2026-10-13'}]):
   with self.subTest(rows=rows),self.assertRaises(ValueError):self.run_read(lambda *a:{'data':rows})
 def test_paging_never_follows_url_and_incomplete_fails(self):
  calls=[]
  def graph(token,path,params):
   calls.append((path,dict(params)))
   if 'after' in params:return {'data':[]}
   return {'data':[self.row()],'paging':{'next':'https://untrusted.invalid','cursors':{'after':'safe'}}}
  result=self.run_read(graph);self.assertTrue(result['complete']);self.assertEqual(len(calls),4)
  self.assertTrue(all(p=='act_'+ACCOUNT+'/insights' for p,_ in calls))
  with self.assertRaises(ValueError):self.run_read(lambda *a:{'data':[],'paging':{'next':'x'}})
 def test_conflicting_parent_blocks_join(self):
  def graph(token,path,params):return {'data':[{**self.row(),'campaign_id':'789' if 'spend' in params['fields'].split(',') else '999'}]}
  with self.assertRaises(ValueError):self.run_read(graph)
 def test_provenance_tampering_or_time_mismatch_invalid(self):
  result=self.evidence(self.run_read(lambda *a:{'data':[self.row()]}))
  for field,value in [('account_id','other'),('level','campaign'),('complete',False),('since','2026-10-13'),('checked_at','2026-10-01T00:00:00+00:00')]:
   changed=copy.deepcopy(result);changed['metric_streams']['actions'][field]=value
   self.assertFalse(split_evidence_valid(changed,'ad'))
  changed=copy.deepcopy(result);changed['metric_streams']['delivery']['rows'][0]['spend']='0'
  self.assertFalse(split_evidence_valid(changed,'ad'))

 def test_rehashed_merged_mutations_are_rejected(self):
  import hashlib
  original=self.evidence(self.run_read(lambda *a:{'data':[self.row()]}))
  def rehash(data):
   data['metric_rows_sha256']=hashlib.sha256(json.dumps(data['rows'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
   for stream in data['metric_streams'].values():stream['rows_sha256']=hashlib.sha256(json.dumps(stream['rows'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
  for mutation in ('value','delete','add','membership','duplicate','parent','grain','old','reverse'):
   data=copy.deepcopy(original)
   if mutation=='value':data['rows'][0]['spend']='999'
   if mutation=='delete':data['rows']=[]
   if mutation=='add':data['rows'].append({**data['rows'][0],'ad_id':'999'})
   if mutation=='membership':data['rows'][0]['metric_sources']['delivery_present']=False
   if mutation=='duplicate':data['metric_streams']['delivery']['rows']*=2
   if mutation=='parent':data['metric_streams']['actions']['rows'][0]['adset_id']='999'
   if mutation=='grain':data['metric_streams']['actions']['rows'][0]['date_stop']='2026-10-21'
   if mutation=='old':data['checked_at']='2026-11-21T00:00:00+00:00'
   if mutation=='reverse':data['metric_streams']['actions']['started_at']='2099-01-01T00:00:00+00:00'
   rehash(data)
   with self.subTest(mutation=mutation):
    self.assertFalse(split_evidence_valid(data,'ad'))
    from management_report import _meta_rows
    self.assertIsNone(_meta_rows(data,None,None)[0])

 def test_only_exact_collection_and_request_contract_accepted(self):
  original=self.evidence(self.run_read(lambda *a:{'data':[self.row()]}))
  self.assertTrue(split_evidence_valid({},'ad'))
  for marker in ('unknown','',False):
   changed=copy.deepcopy(original);changed['metric_collection']=marker
   self.assertFalse(split_evidence_valid(changed,'ad'))
  for key,value in (('action_report_time','conversion'),('action_attribution_windows','["1d_click"]'),('filtering','[]'),('breakdowns','age'),('level','campaign'),('limit','100'),('time_increment','all_days'),('use_unified_attribution_setting','false'),('fields',original['metric_streams']['actions']['request']['fields']+',spend')):
   changed=copy.deepcopy(original);changed['metric_streams']['actions']['request'][key]=value
   with self.subTest(key=key):self.assertFalse(split_evidence_valid(changed,'ad'))

 def test_saved_real_query_isolation_restores_only_covered_campaign(self):
  root=Path(__file__).resolve().parents[1]
  paths=[root/'outputs'/name for name in ('rosery-collector-shape-live-diagnostic-2026-10-21.json','rosery-collector-shape-live-diagnostic-2026-10-21-spend-only.json','rosery-collector-shape-live-diagnostic-2026-10-21-spend-only-delivery-only.json')]
  if not all(p.exists() for p in paths):self.skipTest('Saved live diagnostic not present')
  mixed,spend,delivery=[json.loads(p.read_text(encoding='utf-8')) for p in paths]
  def graph(token,path,params):
   level=params['level'];source=(delivery if level=='ad' else spend) if 'spend' in params['fields'].split(',') else mixed
   return {'data':next(r['rows'] for r in source['requests'] if r['params']['level']==level)}
  snapshot=json.loads((root/'campaigner/.local/live-reports.json').read_text(encoding='utf-8'))
  data=snapshot['sources']['meta']['data'];stamp=datetime.now(timezone.utc).isoformat()
  # This replays the historical split fixture, not newer independently fetched
  # period totals. Mixing generations correctly creates a spend conflict.
  data.pop('period_delivery',None)
  for level in ('ad','campaign'):
   split=read_split_insights(graph,'fixture',ACCOUNT,'2026-10-14','2026-10-21',level,time.monotonic()+30,QUERY_SETTINGS)
   if level=='ad':data.update(split,checked_at=datetime.now(timezone.utc).isoformat())
   else:data['campaign_insights'].update(split,checked_at=datetime.now(timezone.utc).isoformat())
  from management_report import build_report
  report=build_report(snapshot,now=datetime.now(timezone.utc))
  yesterday=report['windows']['1'];sales=next(c for c in yesterday['campaigns'] if c['id']=='120246379886090730')
  self.assertEqual(sales['spend'],699.30)
  self.assertIsNone(sales['purchases']) # Four source rows have no action coverage; never invent zeros.
  self.assertEqual(sales['observed_results']['purchases']['value'],4)
  self.assertFalse(sales['observed_results']['purchases']['complete'])
  from action_recommendations import build_recommendations
  decisions=build_recommendations(report,now=datetime.now(timezone.utc))['recommendations']
  sales_decision=next(item for item in decisions if item['id']=='campaigns:'+sales['id'])
  self.assertFalse(sales_decision['evidence'][0]['complete'])
  self.assertIsNone(sales_decision['evidence'][0]['results'])
  self.assertIn('complete_entity_conversion_windows',sales_decision['missing_data'])
  self.assertFalse(sales_decision['live_change'])
  self.assertEqual(sales_decision['action'],'INSUFFICIENT_EVIDENCE')
  ads=[a for a in yesterday['ads'] if a['campaign_id']==sales['id']]
  self.assertTrue(all(a['spend'] is not None for a in ads))
  weekly=report['windows']['7']
  own=next(a for a in weekly['ads'] if a['id']=='120253138095700730')
  from decimal import Decimal
  source_rows=[r for r in data['rows'] if r.get('ad_id')==own['id'] and '2026-10-14'<=r['date_start']<='2026-10-20']
  self.assertTrue(all('spend' in r for r in source_rows))
  self.assertEqual(own['spend'],float(sum(Decimal(r['spend']) for r in source_rows)))
  self.assertEqual(own['efficiency_integrity']['status'],'comparison_unavailable')
  self.assertFalse(own['efficiency_integrity']['cross_level_reconciled'])
  missing_own=next(a for a in weekly['ads'] if a['id']=='120253099109680730')
  self.assertIsNone(missing_own['spend'])
  registration=next(c for c in yesterday['campaigns'] if c['id']=='120253309360290730')
  self.assertIsNone(registration['spend']) # Action-only rows must not become zero.
  self.assertEqual(registration['efficiency_integrity']['reason'],'split_source_coverage_missing')

if __name__=='__main__':unittest.main()
