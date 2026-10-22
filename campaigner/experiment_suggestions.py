"""Evidence-linked experiment suggestions, never financial or causal conclusions."""
import hashlib
import json
from math import isfinite


def _descriptive_candidates(report):
    windows=report.get('windows',{})
    if report.get('freshness',{}).get('state')!='fresh': return []
    if any(windows.get(d,{}).get('partial') is not False for d in ('3','7')): return []
    def usable(r):
        return (r.get('configured_active') is True and r.get('result_type')=='purchase'
            and all(type(r.get(k)) in (float,int) and isfinite(r[k]) and r[k]>0 for k in ('spend','purchases')))
    recent={r['id']:r for r in windows['3'].get('ads',[]) if usable(r)}
    groups={}
    for r in windows['7'].get('ads',[]):
        if usable(r) and r.get('id') in recent and r.get('adset_id') and r.get('campaign_id'):
            groups.setdefault((r['campaign_id'],r['adset_id']),[]).append(r)
    candidates=[]
    for (campaign,adset),rows in groups.items():
        if len(rows)<2: continue
        ordered=sorted(rows,key=lambda r:r['spend']/r['purchases'])
        low,high=ordered[0],ordered[-1]
        if low['spend']/low['purchases']>=high['spend']/high['purchases']:continue
        a,b=recent[low['id']],recent[high['id']]
        if a.get('adset_id')!=adset or b.get('adset_id')!=adset:continue
        if a['spend']/a['purchases']>=b['spend']/b['purchases']:continue
        evidence=[]
        for days in ('3','7'):
            for r in (low,high) if days=='7' else (a,b):
                evidence.append({'days':int(days),'since':windows[days].get('since'),'until':windows[days].get('until'),
                    'ad_id':r['id'],'name':r.get('name') or r['id'],'spend':r['spend'],'purchases':r['purchases'],
                    'cpa':round(r['spend']/r['purchases'],2)})
        digest=hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest()
        binding={'account_id':report['account_id'],'snapshot_sha256':digest,
                 'meta_last_success_at':report.get('freshness',{}).get('meta_last_success_at')}
        candidates.append({'id':'experiment:'+adset+':'+digest[:16],
            'title':'להכין ניסוי המשך ל־'+str(low.get('name') or low['id']),
            'why':f"באותה קבוצת מודעות, העלות לרכישה של {low.get('name') or low['id']} נמוכה מזו של {high.get('name') or high['id']} גם ב־3 וגם ב־7 ימים. זו סיבה לבחון את הכיוון בניסוי, לא הוכחה שהוא המנצח.",
            'risk':'החלונות חופפים ואינם שתי בדיקות עצמאיות. מספר הרכישות עשוי להיות קטן והקהל שקיבל כל מודעה עשוי להיות שונה. אין עדיין הסבר מוכח להבדל.',
            'before':{'campaign_id':campaign,'adset_id':adset,'ad_ids':[low['id'],high['id']]},
            'after':{'artifact':'experiment_brief','campaign_changes':False},
            'source_binding':binding,'evidence':evidence,'approval_required':False,
            'next_action':{'type':'local_experiment_plan','entity_ids':[adset],'source_binding':binding},
            'budget_impact':'ההכנה ללא שינוי בהוצאה. תקציב ניסוי והפעלה לא נקבעו ולא אושרו.',
            'is_performance_recommendation':True,'recommendation_scope':'prepare_experiment_only'})
    return candidates[:2]


def assess_experiment_evidence(report):
    """No promotion of observational rankings to an action recommendation.

    There is no fixed purchases threshold: the missing piece is an appropriate
    decision model, uncertainty, economic effect and reliable comparison design.
    Counts and overlapping windows alone cannot provide these.
    """
    return {'recommendation_allowed': False,
            'status': 'descriptive_only',
            'reasons': ['No validated uncertainty estimate for the proposed decision',
                        'No meaningful effect and economic loss criterion specified',
                        'Adaptive ad delivery is not randomized allocation',
                        'Three-day and seven-day windows overlap; not independent replication',
                        'Creative contents and relevant change history not evaluated'],
            'withdrawn_rule': 'lower observed CPA in overlapping 3/7 day windows',
            'message': 'הצעות הניסוי האוטומטיות נמשכו: פער בעלות לרכישה במדגם קטן אינו מוכיח שכדאי לשנות או ליצור ניסוי נוסף. לא הוכח ששתי המודעות אינן תקינות.'}


def propose_experiments(report):
    # Intentionally disabled until a decision method is validated. Do not turn a
    # model opinion, fixed purchase count or source citation into statistical proof.
    return []
