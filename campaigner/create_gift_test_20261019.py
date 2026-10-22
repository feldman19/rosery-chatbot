"""One-shot owner-approved five-ad paused test. Never activates or retries."""
import json, hashlib
from pathlib import Path
from canary_plan import build_candidate
from campaign_operations_transport import CampaignTransport, ProviderRejected
from meta_write_connection import load

HOST='120253354923980730'
ROOT=Path(__file__).resolve().parent
RECEIPT=ROOT.parent/'outputs/gift-test-execution-2026-10-19.json'
SPECS=[
 ('W01','ROSERY_W01_V3_POST.jpg','ROSERY_W01_V3_STORY.jpg','מחפשת מתנה שהוא באמת ירצה לענוד? גלי את הצמידים של ROSERY ובחרי את הפרט שמתאים לסטייל שלו.','מתנה שמתאימה לסטייל שלו','gift_desire'),
 ('W03','ROSERY_W03_V3_POST.jpg','ROSERY_W03_V3_STORY.jpg','מה קונים לגבר שאוהב להתלבש טוב? התחילי מהפרטים: צמיד של ROSERY שישתלב בלוק שלו גם ביום רגיל.','הפרט הבא בלוק שלו','gift_choice'),
 ('W05','ROSERY_W05_V3_POST.jpg','ROSERY_W05_V3_STORY.jpg','מתנה לא חייבת לחכות לאירוע מיוחד. בחרי לו צמיד של ROSERY — משהו אישי שיוכל לשלב בלוק היומיומי.','מתנה גם ליום רגיל','gift_everyday'),
 ('W08','ROSERY_W08_V3_POST.jpg','ROSERY_W08_V3_STORY.jpg','את מכירה את הסטייל שלו. עכשיו נשאר לבחור את הפרט שישלים אותו. גלי את הצמידים של ROSERY ובחרי מתנה עם אופי.','את מכירה את הסטייל שלו','gift_familiarity'),
 ('AL04','Copy of ROSERY_AL_04_v11_FEED.png','Copy of ROSERY_AL_04_v11_STORY.png','אותו לוק, עוד קצת אופי. אם את מחפשת מתנה לגבר שאוהב את הפרטים הקטנים, הכירי את הצמידים של ROSERY.','תוספת קטנה. מתנה אישית.','gift_style_detail'),
]

def run():
 if RECEIPT.exists(): raise ValueError('Existing receipt: reconcile instead of retry')
 assets=json.loads((ROOT/'.local/drive-assets/manifest.json').read_text())['assets']
 state={'approval':'owner approved five new ads, paused, 100 ILS/day','adset_id':HOST,'daily_budget_ils':100,'activation_approved':False,'events':[],'state':'preflight'}
 def save(): RECEIPT.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
 def event(x): state['events'].append(x);save()
 t=CampaignTransport(); token=load(ROOT/'.local')
 before=t.read(token,HOST,'id,account_id,status,daily_budget,campaign_id')
 if before.get('status')!='PAUSED' or str(before.get('daily_budget'))!='10000' or before.get('account_id')!='393059510164294':raise ValueError('Paused host/budget mismatch')
 state['before']=before
 with RECEIPT.open('x',encoding='utf-8') as f:json.dump(state,f,ensure_ascii=False)
 try:
  for code,feed,story,body,title,angle in SPECS:
   hashes=[]
   for name in (feed,story):
    a=next(x for x in assets if x['name']==name); raw=(ROOT/'.local/drive-assets'/(a['id']+'-'+a['sha256']+'.bin')).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=a['sha256']:raise ValueError('Source checksum mismatch')
    event({'step':'upload_inflight','source_id':a['id'],'sha256':a['sha256'],'name':name})
    r=t.upload(token,raw,a['mime_type'],name.replace('Copy of ','')); image=next(iter(r['images'].values()));hashes.append(image['hash'])
    event({'step':'uploaded','source_id':a['id'],'name':name,'hash':image['hash']})
   ad={'primary_text':body,'headline':title,'description':'גלי את הקולקציה של ROSERY','destination_url':'https://roseryjewelry.com/','url_tags':'utm_source={{site_source_name}}&utm_medium=paid_social&utm_campaign=gift_test_20261019&utm_content='+code.lower()+'&campaign_id={{campaign.id}}&adset_id={{adset.id}}&ad_id={{ad.id}}'}
   c=build_candidate(ad,*hashes);c['name']='ROSERY | GIFT TEST | '+code+' | PAUSED'
   event({'step':'creative_inflight','code':code,'classification':angle,'request':c})
   cr=t.create(token,'adcreatives',c);event({'step':'creative_created','code':code,'creative_id':cr['id']})
   request={'name':c['name'],'adset_id':HOST,'status':'PAUSED','creative':{'creative_id':cr['id']}}
   event({'step':'ad_inflight','code':code,'request':request});r=t.create(token,'ads',request);event({'step':'ad_created','code':code,'ad_id':r['id']})
   read=t.read(token,r['id'],'id,account_id,adset_id,status,creative{id}')
   if read.get('status')!='PAUSED' or read.get('adset_id')!=HOST:raise ValueError('Paused ad readback mismatch')
   event({'step':'ad_verified','code':code,'readback':read})
  state['state']='five_paused_ads_created_preview_qa_pending';save()
 except Exception as e:
  state.update(state='stopped_reconcile_required',error_type=type(e).__name__)
  if isinstance(e,ProviderRejected):state['provider_error']=e.details
  save();raise
 return {'state':state['state'],'verified_ads':sum(x['step']=='ad_verified' for x in state['events'])}
if __name__=='__main__':print(json.dumps(run()))
