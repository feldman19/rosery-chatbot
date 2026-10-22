"""Offline content audit; no providers, credentials or mutations."""
import json,hashlib,datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
read=ROOT/'outputs/gift-test-readback-2026-10-19.json'
execution=ROOT/'outputs/gift-test-execution-2026-10-19.json'
s=json.loads(read.read_text(encoding='utf-8'));events=json.loads(execution.read_text(encoding='utf-8'))['events']
assets=json.loads((ROOT/'campaigner/.local/drive-assets/manifest.json').read_text(encoding='utf-8'))['assets']
observations={
'W01':{'visible':'יד עם שרוול בהיר, כמה צמידים וטבעת על רקע ים ושקיעה. כיתוב: מתנה שהוא ירצה ללבוש; בדיוק בסטייל שלו.','angle':'מתנה המתאימה לסטייל של המקבל','fit':'ישירה','defects':['אותו צילום בסיס כמו W08; אין עצמאות חזותית בין שתי הגרסאות.','בתמונה הבטחות 14 יום להחזרה ומשלוחים מהירים; קיימות בחומר שסופק, אך תחולה ומדד מהירות לא אומתו בביקורת המקומית.']},
'W03':{'visible':'תקריב יד בשרוול חום, צמידים מרובים וטבעות. כיתוב: בלי לחכות ליום מיוחד; יש מתנות שלא צריכות סיבה.','angle':'מתנה ספונטנית, ללא אירוע','fit':'ישירה, עם פער קל','defects':['התמונה מדגישה מתנה ללא אירוע; הקופי מדגיש מה לקנות לגבר. תואם לנושא הכללי אך אינו מבחן נקי של אותו תת-מסר.','אותו צילום בסיס כמו W05.','14 יום להחזרה ומשלוחים מהירים מופיעים במקור ולא אומתו מול מדיניות עדכנית.']},
'W05':{'visible':'אותה יד בשרוול חום וצמידים, עם תיבת כיתוב כהה: מתנה ליום אחד? או לכל יום.','angle':'מתנה לשימוש יומיומי','fit':'ישירה','defects':['הקופי מוסיף מתנה ללא אירוע מיוחד, ולכן חופף גם ל-W03.','אותו צילום בסיס כמו W03; הבדל הוא מסר ועיצוב, לא צילום חדש.','שימוש יומיומי ורצון המקבל הם מסר שיווקי והשערה, לא תוצאת מחקר לקוחות.']},
'W08':{'visible':'יד עם שרוול בהיר וצמידים על רקע שקיעה; את מכירה את הסטייל שלו.','angle':'היכרות אישית עם הטעם של מקבל המתנה','fit':'ישירה','defects':['אותו צילום בסיס כמו W01; השינוי במסר ובפריסה.','אין ראיה שהנמען יקנה או שהקונה מכירה את הטעם שלו; זו פנייה שיווקית.','הבטחות ההחזרה והמשלוח שבמקור דורשות התאמה למדיניות לפני הפעלה.']},
'AL04':{'visible':'יד עם צמיד וטבעת בגוון זהב, ליד מכנסיים מפוספסים וחולצה. כיתוב: אותו לוק יותר אופי; לצפייה בצמידים.','angle':'מתנה שמשלימה את הסטייל','fit':'עקיפה','defects':['בתמונה אין אזכור מתנה או קונה עבור אדם אחר; רק הקופי מייצר את זווית המתנה. החלשה ביותר באחידות הסט.','צילום תכשיט בגוון זהב שונה מהצמידים מרובי השכבות בשאר הסט; אין להסיק חומר, טוהר או SKU מהמראה.']}}
report={'purpose':'Review saved content and local original sources for five paused gift ads','scope':'offline_only_no_external_actions','evidence_file':str(read.relative_to(ROOT)),'evidence_sha256':hashlib.sha256(read.read_bytes()).hexdigest(),'adset_id':s['adset']['id'],'snapshot_status':s['adset']['status'],'snapshot_budget_minor':s['adset']['daily_budget'],'copy_options_per_ad':1,'placement_images_per_ad':2,'five_copy_plus_placement_proven':False,'rendered_feed':'missing','rendered_story':'missing','avatar':'Hypothesis: women buying jewelry as gifts for men; no validated avatar or strict women-only delivery claim','ads':[]}
upload_pairs=[(events[i],events[i+1]) for i in range(len(events)-1) if events[i]['step']=='upload_inflight' and events[i+1]['step']=='uploaded']
for ad in s['ads']:
 spec=ad['creative']['asset_feed_spec'];hashes={x['hash'] for x in spec['images']};sources=[]
 for start,end in upload_pairs:
  if end['hash'] not in hashes:continue
  a=next(x for x in assets if x['id']==start['source_id']);p=ROOT/'campaigner/.local/drive-assets'/(a['id']+'-'+a['sha256']+'.bin')
  sources.append({'drive_id':a['id'],'name':a['name'],'sha256':a['sha256'],'checksum_verified':hashlib.sha256(p.read_bytes()).hexdigest()==a['sha256'],'meta_image_hash':end['hash'],'local_path':str(p.relative_to(ROOT))})
 report['ads'].append({'code':ad['code'],'ad_id':ad['ad_id'],'creative_id':ad['creative_id'],'source_bindings':sources,'body':spec['bodies'][0]['text'],'headline':spec['titles'][0]['text'],'description':spec['descriptions'][0]['text'],'destination':spec['link_urls'][0]['website_url'],'claims_evidence':'Product appearance supported visually. No discount, material, lifetime or guaranteed performance claim added in saved copy. Recipient desire/style fit/everyday use are hypotheses. Source footer shipping/return claims not independently verified here.','content_review':observations[ad['code']],'rendered_feed':'missing','rendered_story':'missing','visual_evidence':'Original paired-source contact sheet viewed; not a Meta render','api_readback':{k:ad[k] for k in ['copy_matches','image_hashes_match','rule_labels_match','utm_matches']},'editor_url':ad['editor_url']})
report['timing']={'per_step_timestamps':'missing: execution events contain no timestamps or durations','preparation_seconds':None,'creation_seconds':None,'verification_seconds':None,'execution_receipt_mtime_utc':datetime.datetime.fromtimestamp(execution.stat().st_mtime,datetime.timezone.utc).isoformat(),'readback_file_mtime_utc':datetime.datetime.fromtimestamp(read.stat().st_mtime,datetime.timezone.utc).isoformat(),'artifact_mtime_gap_seconds':round(read.stat().st_mtime-execution.stat().st_mtime,3),'interpretation':'File metadata interval is not measured work duration; cannot prove a one-to-two-minute end-to-end target.'}
report['acceptance']={'all_five_source_bound':all(len(x['source_bindings'])==2 and all(a['checksum_verified'] for a in x['source_bindings']) for x in report['ads']),'saved_copy_inspected':True,'original_contact_sheet_viewed':True,'rendered_placement_QA_complete':False,'publish_ready':False,'learning_or_profitability_proven':False}
report['next_step']='Observe actual saved Feed/Story renders and validate destination product/availability and source shipping/return claims before activation; no live change authorized by this audit.'
out=ROOT/'outputs/gift-test-content-audit-2026-10-19.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# ביקורת חמש מודעות המתנה — 19.10.2026','','מטרה: התאמת החומרים והקופי לזווית המתנה. תנאי סיום הביקורת המקומית: חמש מודעות קשורות לגרסאות המקור, הקופי בפועל וליקויים; פערי תצוגה וזמן מסומנים כחסרים.','','שלב: ביקורת מקומית הושלמה; תצוגות מטא חסרות. אין פעולות חיצוניות.','', 'מסקנה: 5 גרסאות, 3 צילומי בסיס. 4 פניות ישירות למתנה; AL04 עקיפה. קופי אחד לכל מודעה, לא חמש אפשרויות קופי.','']
for a in report['ads']:
 lines += ['## '+a['code']+' — '+a['ad_id'],'',a['content_review']['visible'],'','קופי שנשמר: '+a['body'],'','כותרת: '+a['headline'],'','תיאור: '+a['description'],'','התאמה: '+a['content_review']['fit']+'; זווית: '+a['content_review']['angle'],'']
 lines += ['- '+x for x in a['content_review']['defects']]
 lines += ['','ראיות לטענות: '+a['claims_evidence'],'','Feed/Story מרונדרים: missing. התאמת hash אינה בדיקה חזותית.','']
 for x in a['source_bindings']:lines += ['מקור: '+x['name']+'; SHA256 '+x['sha256']+'; Meta hash '+x['meta_image_hash'],'']
lines += ['## זמן והשלמה','','אין ביומן זמני התחלה/סיום לכל פעולה; הכנה, יצירה ואימות: missing. הפרש זמני שינוי הקבצים הוא '+str(report['timing']['artifact_mtime_gap_seconds'])+' שניות, ואינו משך העבודה. יעד דקה–שתיים לא הוכח.','','חסם: אין תצוגות מטא שנשמרו ונצפו בביקורת, ואין אימות מקומי לתחולת טענות המשלוח/החזרה ולמלאי.','','צעד הבא: בדיקת תצוגות Feed/Story ויעד המוצר לפני הפעלה. אין שינוי או הפעלה בביקורת הזאת.']
(ROOT/'outputs/gift-test-content-audit-2026-10-19.md').write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps(report['acceptance']))
