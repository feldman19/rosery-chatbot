"""Read the existing tenant profile and dated evidence, never infer approval.

Only the manager host loads owner financial evidence. This does not add a model,
refresh schedule, cross-tenant access, recommendation or execution authority.
"""
import json
from pathlib import Path
from economics_evidence import summarize_economics, current_recorded_economics
from imported_business_method import load_imported_business_method
from business import ACTIVE as BUSINESS

def load_rosery_dossier(root=None, *, include_owner_financials=False):
    root=Path(root) if root else Path(__file__).resolve().parent
    p=root/'businesses'/BUSINESS.id/'profile.json'
    try:
        profile=json.loads(p.read_text(encoding='utf-8'))
        if profile.get('business_id')!=BUSINESS.id or profile.get('shop')!=BUSINESS.shop or profile.get('meta_account')!=BUSINESS.meta_account:raise ValueError('Tenant mismatch')
        known=list(profile['known']);missing=list(profile['missing'])
        result={'business_id':BUSINESS.id,'known':known,'missing':missing,'profile_source':'businesses/'+BUSINESS.id+'/profile.json','review_depth':profile['review_depth'],'live_action_allowed':False}
    except (OSError,ValueError,KeyError,TypeError):
        return {'known':[],'missing':['תיק העסק המרכזי חסר או אינו תואם לרוסרי'],'live_action_allowed':False}
    result['business_method']=load_imported_business_method(root,business_id=BUSINESS.id,account_id=profile['meta_account'])
    if not include_owner_financials:return result
    evidence={}
    for key,name in profile['evidence_files'].items():
        if Path(name).name!=name:raise ValueError('Evidence file must be a basename')
        try:evidence[key]=json.loads((root.parent/'outputs'/name).read_text(encoding='utf-8'))
        except (OSError,ValueError):missing.append('חסרה ראיה: '+key)
    result['owner_evidence']=evidence
    result['economics_evidence']=summarize_economics(evidence.get('economics'), profile['evidence_files'].get('economics'))
    result['economics_evidence']['current_recorded']=current_recorded_economics(evidence.get('current_economics'),profile['evidence_files'].get('current_economics'))
    if 'economics' in evidence:
        known.append({'fact':'נתוני יולי–ספטמבר 2026: עלות מעורבת ללקוח חדש ₪105.54; נתון היסטורי, לא יעד הוצאה','source_ref':profile['evidence_files']['economics'],'confidence':'observed_snapshot','kind':'historical_metric','observed_at':'2026-10-19'})
    if 'fixed_horizon_cohorts' in evidence:
        e=evidence['fixed_horizon_cohorts'];group=e.get('matched_cohort_90_day') or {};r180=next((x for x in e.get('rows',[]) if x['horizon_days']==180 and x['acquisition_month']=='ALL'),None)
        if group and r180 and group['eligible_customers']==r180['eligible_customers']:
            known.append({'fact':f"אותם {group['eligible_customers']:,} לקוחות: הכנסות מוצר נטו ומשלוח ללקוח ₪{group['observed_sales_plus_shipping_per_customer']:.2f} ב־90 יום ו־₪{r180['observed_sales_plus_shipping_per_customer']:.2f} ב־180 יום; לא רווח ולא יחס LTV:CAC",'source_ref':profile['evidence_files']['fixed_horizon_cohorts'],'confidence':'observed_calendar_day_cohort','kind':'historical_metric','observed_at':'2026-10-19'})
    result['automatic_spend_limits']=None
    return result
