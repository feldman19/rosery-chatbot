"""Read back only the five objects recorded in the new paused test receipt."""
import json
from pathlib import Path
from campaign_operations_transport import CampaignTransport
from meta_write_connection import load
from create_gift_test_20261019 import ROOT,HOST,RECEIPT

def run():
 s=json.loads(RECEIPT.read_text(encoding='utf-8'));t=CampaignTransport();token=load(ROOT/'.local');out={'adset':t.read(token,HOST,'id,status,daily_budget,targeting,promoted_object'),'ads':[],'visual_preview_checked':False}
 for e in s['events']:
  if e['step']!='ad_created':continue
  code=e['code'];want=next(x['request'] for x in s['events'] if x['step']=='creative_inflight' and x['code']==code)
  cr=next(x['creative_id'] for x in s['events'] if x['step']=='creative_created' and x['code']==code)
  actual=t.read(token,cr,'id,asset_feed_spec,object_story_spec,url_tags,degrees_of_freedom_spec')
  spec=actual.get('asset_feed_spec',{});w=want['asset_feed_spec'];fields=['bodies','titles','descriptions','link_urls','call_to_action_types']
  core=all(spec.get(k)==w[k] for k in fields)
  images={x['hash'] for x in spec.get('images',[])}=={x['hash'] for x in w['images']}
  rules=all(any(r.get('image_label',{}).get('name')==q['image_label']['name'] and r.get('priority')==q['priority'] for r in spec.get('asset_customization_rules',[])) for q in w['asset_customization_rules'])
  ad=t.read(token,e['ad_id'],'id,status,adset_id,creative{id}')
  out['ads'].append({'code':code,'ad_id':e['ad_id'],'creative_id':cr,'ad':ad,'creative':actual,'copy_matches':core,'image_hashes_match':images,'rule_labels_match':rules,'utm_matches':actual.get('url_tags')==want['url_tags'],'editor_url':'https://adsmanager.facebook.com/adsmanager/manage/ads/edit?act=393059510164294&selected_ad_ids='+e['ad_id']})
 out['verified_five_paused']=len(out['ads'])==5 and out['adset']['status']=='PAUSED' and str(out['adset']['daily_budget'])=='10000' and all(x['ad']['status']=='PAUSED' and all(x[k] for k in ['copy_matches','image_hashes_match','rule_labels_match','utm_matches']) for x in out['ads'])
 (ROOT.parent/'outputs/gift-test-readback-2026-10-19.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'verified_five_paused':out['verified_five_paused'],'ads':[{'code':x['code'],'id':x['ad_id']} for x in out['ads']]}))
if __name__=='__main__':run()
