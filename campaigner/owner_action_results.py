"""Execute only bounded local analysis; never touches an advertising provider."""
import hashlib
import json
from datetime import datetime, timezone


def run_analysis(report, card):
    action = card.get('next_action') or {}
    if action.get('type') == 'local_experiment_plan':
        return {'id':card['id'], 'state':'completed', 'title':'תוכנית הניסוי מוכנה לעיון',
            'source_binding':card['source_binding'], 'completed_at':datetime.now(timezone.utc).isoformat(),
            'provider_writes':0, 'campaign_changes':False, 'periods':[],
            'conclusion':card['why'],
            'brief':{'reference_ads':card['before']['ad_ids'], 'adset_id':card['before']['adset_id'],
                'hypothesis':'לבדוק אם כיוון התוכן של המודעה בעלת העלות הנמוכה משפר רכישות כאשר משנים רכיב תוכן אחד בלבד.',
                'design':'לבחור מהחומרים הקיימים חלופה אחת לאחר בדיקת התמונות והקופי. לשמור יעד, הצעה ודף נחיתה זהים. התוכן טרם נותח חזותית.',
                'measurement':'רכישות ועלות לרכישה. לפני השקה יש לקבוע חלוקת קהל, תקציב, משך וכלל החלטה בהתאם לנפח הצפוי.',
                'budget':None, 'launch_authorized':False,
                'open_items':['visual_and_copy_review','randomized_test_feasibility','budget_duration_and_decision_rule'],
                'evidence':card['evidence'], 'risk':card['risk']}}
    if action.get('type') not in ('local_analysis', 'local_reconciliation'):
        raise ValueError('This endpoint cannot execute advertising changes')
    ids = action.get('entity_ids')
    if not isinstance(ids, list) or not ids:
        raise ValueError('Exact adsets required')
    periods = []
    for days in ('1', '3', '7'):
        window = report.get('windows', {}).get(days, {})
        if window.get('partial') is not False:
            continue
        rows = []
        for row in window.get('ads', []):
            if row.get('adset_id') not in ids:
                continue
            spend, purchases = row.get('spend'), row.get('purchases')
            known = type(spend) in (int, float) and type(purchases) in (int, float)
            rows.append({'id': row['id'], 'name': row.get('name') or row['id'],
                'adset_id': row['adset_id'], 'spend': spend, 'purchases': purchases,
                'cpa': spend/purchases if known and purchases > 0 else None,
                'configured_active': row.get('configured_active'),
                'finding': 'דיווח רכישות חסר' if purchases is None else
                           'אין רכישות מיוחסות בתקופה' if purchases == 0 else
                           'יש רכישות מיוחסות — עלות מוצגת',
                'meta_url': 'https://adsmanager.facebook.com/adsmanager/manage/ads?act=393059510164294&selected_ad_ids='+row['id']})
        rows.sort(key=lambda x: -(x['spend'] or 0))
        periods.append({'days': int(days), 'since': window.get('since'), 'until': window.get('until'), 'ads': rows})
    last = next((w['ads'] for w in periods if w['days'] == 1), [])
    missing = [r for r in last if r['purchases'] is None]
    if not last:
        conclusion = 'אין פירוט מודעות מספק לישות הזאת בדוח הקיים. הניתוח לא הושלם ולא בוצע שינוי.'
        state = 'blocked'
    else:
        state = 'completed'
        conclusion = f"נותחו {len(last)} מודעות ביום המלא האחרון; ב־{len(missing)} מהן חסר דיווח רכישות."
        if action['type'] == 'local_analysis':
            active = [r for r in last if r['configured_active'] is True and (r['spend'] or 0) > 0]
            if active:
                largest = max(active, key=lambda r: r['spend'])
                conclusion += f" ההוצאה הגדולה ביותר בין המודעות הפעילות הייתה במודעה {largest['name']}: {largest['spend']:.2f} ₪."
        conclusion += ' לא הוצעה עצירה אוטומטית: פירוט ההוצאה אינו הוכחת הפסד או סיבת השינוי בביצועים.'
    return {'id': card['id'], 'state': state, 'title': card['title'],
        'source_binding': card['source_binding'], 'periods': periods, 'conclusion': conclusion,
        'completed_at': datetime.now(timezone.utc).isoformat(),
        'provider_writes': 0, 'campaign_changes': False,
        'scope': 'saved_report_analysis_not_live_measurement_repair'}


def execute_local(directory, report, cards, body):
    if set(body) != {'id', 'source_binding'}:
        raise ValueError('Exact displayed proposal required')
    card = next((c for c in cards if c['id'] == body['id'] and
                 c['source_binding'] == body['source_binding']), None)
    if card is None:
        raise ValueError('הנתונים השתנו. רענן את ההצעה לפני המשך.')
    result = run_analysis(report, card)
    directory.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(card['id'].encode()).hexdigest()+'.json'
    path = directory/name
    # The source binding identifies immutable report content; repeated clicks reuse it.
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    pending.replace(path)
    return result
