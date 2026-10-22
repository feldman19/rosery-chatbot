"""Optional documented Ad field read; omitted is unknown, never missing media.

Source: facebook/facebook-python-business-sdk/facebook_business/adobjects/ad.py
"""
import json,time
from datetime import datetime,timezone

def enrich_placement_readback(graph,token,account,ads,deadline):
 selected=[a for a in ads if isinstance(a,dict) and a.get('account_id')==account and isinstance(a.get('id'),str) and a['id'].isdigit()][:100]
 if not selected:return
 ids=[a['id'] for a in selected]
 for ad in selected:ad['placement_readback']={'status':'unavailable','rendered_preview_verified':False}
 try:
  if time.monotonic()>deadline:return
  params={'fields':'id,account_id,creative{id},creative_asset_groups_spec','filtering':json.dumps([{'field':'id','operator':'IN','value':ids}]),'limit':'100'}
  result=graph(token,'act_'+account+'/ads',params);rows=result.get('data')
  if not isinstance(rows,list) or (result.get('paging') or {}).get('next'):return
  index={r['id']:r for r in rows if isinstance(r,dict) and r.get('account_id')==account and r.get('id') in ids}
  if len(index)!=len(rows) or set(index)!=set(ids):return
  stamp=datetime.now(timezone.utc).isoformat()
  for ad in selected:
   row=index[ad['id']];creative=(row.get('creative') or {}).get('id')
   if not creative or creative!=(ad.get('creative') or {}).get('id'):continue
   present='creative_asset_groups_spec' in row and isinstance(row['creative_asset_groups_spec'],dict)
   ad['placement_readback']={'status':'field_returned_unverified' if present else 'unknown_field_not_returned','account_id':account,'ad_id':ad['id'],'creative_id':creative,'checked_at':stamp,'requested_field':'creative_asset_groups_spec','rendered_preview_verified':False,'placement_pair_verified':False}
   if present:ad['placement_readback']['creative_asset_groups_spec']=row['creative_asset_groups_spec']
 except Exception:
  # Optional diagnostic must never invalidate otherwise available reports.
  return
