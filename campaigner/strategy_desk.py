"""Business-goal intake, explicit research plan and observed active-ad analysis.

No model, web research runner, recommendation authority or provider mutation.
Host must supply authenticated inventory; heuristics are labelled keyword hints.
"""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
import json
from pathlib import Path
import re
import secrets
import sqlite3
import unicodedata
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from meta_direct import ACCOUNT
from business import ACTIVE as BUSINESS


def stamp(value):
    try:
        t=datetime.fromisoformat(value)
        return t if t.tzinfo is not None else None
    except (ValueError,TypeError):return None


def category(goal):
    text=goal.lower()
    matches={key for key,words in {'awareness':('חשיפה','awareness','brand reach'),
        'sales':('מכירות','מכירה','למכור','רכישות','sales','purchases'),
        'registration':('הרשמה','הרשמות','registration','signups','sign ups'),
        'new_audience':('קהל חדש','קהלים חדשים','לקוחות חדשים','new audience','new customers')}.items() if any(word in text for word in words)}
    primary=matches-{'new_audience'}
    selected=next(iter(primary)) if len(primary)==1 else 'new_audience' if not primary and 'new_audience' in matches else 'unclear'
    questions=[]
    if selected=='unclear':questions.append('מה התוצאה הראשית שרוצים להשיג: חשיפה, קהל חדש, הרשמות או רכישות?')
    if selected=='new_audience':questions.append('מה הפעולה הרצויה מהקהל החדש: להכיר את המותג, לבקר באתר, להירשם או לרכוש?')
    return selected,sorted(matches),questions


class StrategyDesk:
    def __init__(self,directory,clock=None,dossier=None,dossier_loader=None):
        self.path=Path(directory)/'strategy-desk.sqlite3';self.path.parent.mkdir(parents=True,exist_ok=True)
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.dossier_loader=dossier_loader
        self.dossier=dossier or {'known':[{'fact':'ROSERY operates roseryjewelry.com','source_ref':'workspace:rosery-owner-business-context','confidence':'owner_declared'},
            {'fact':'Meta account '+ACCOUNT,'source_ref':'fixed-host-account-binding','confidence':'host_binding'}],
            'missing':['קהל היעד והמניע העדכניים לכל קמפיין','כלכלת רכישה מאומתת ומדד הצלחה לפי יעד העסק','הצעה ומלאי בתוקף, ומועד שינוי אחרון בכל מודעה']}
        with self.db() as db:db.execute('CREATE TABLE IF NOT EXISTS requests(id TEXT,revision INTEGER,payload TEXT NOT NULL,PRIMARY KEY(id,revision))')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except Exception:db.rollback();raise
        finally:db.close()
    def now(self):
        now=self.clock()
        if not isinstance(now,datetime) or now.tzinfo is None:raise ValueError('Aware host time required')
        return now
    def latest(self):
        with self.db() as db:row=db.execute('SELECT payload FROM requests ORDER BY revision DESC LIMIT 1').fetchone()
        return json.loads(row['payload']) if row else None
    def create(self,body):
        if not isinstance(body,dict) or set(body)!={'goal'} or not isinstance(body['goal'],str):raise ValueError('Only freeform business goal is accepted')
        goal=''.join(c for c in body['goal'] if c in '\n\t' or not unicodedata.category(c).startswith('C')).strip()
        if not 3<=len(goal)<=6000:raise ValueError('Describe a business goal in 3–6000 characters')
        if re.search(r'(?:access_token|api[_ -]?key|client_secret|password)\s*[:=]',goal,re.I):raise ValueError('Credentials do not belong in a business goal')
        selected,tags,questions=category(goal);now=self.now().isoformat()
        with self.db() as db:
            row=db.execute('SELECT payload FROM requests ORDER BY revision DESC LIMIT 1').fetchone();prior=json.loads(row['payload']) if row else None
            if prior and prior['goal']==goal:return {'saved':True,'unchanged':True,'request':prior,'live_action_allowed':False}
            request={'id':prior['id'] if prior else secrets.token_hex(16),'revision':prior['revision']+1 if prior else 1,'goal':goal,'category':selected,'intent_tags':tags,
                     'classification':'keyword_hint_not_a_model_answer','clarifications':questions,'created_at':prior['created_at'] if prior else now,'saved_at':now,'execution_approval':False}
            db.execute('INSERT INTO requests VALUES(?,?,?)',(request['id'],request['revision'],json.dumps(request,ensure_ascii=False)))
        return {'saved':True,'request':request,'live_action_allowed':False}
    def report(self,snapshot):
        if self.dossier_loader is not None:self.dossier=self.dossier_loader()
        request=self.latest();analysis=self.analyze(snapshot)
        questions=list(request.get('clarifications',[])) if request else ['מה העסק רוצה להשיג עכשיו?']
        questions+=['איזה שינוי אווירה, מניע קנייה או חסם הקריאייטיב אמור לבדוק?','איזה מדד תוצאה יתאים ליעד, ומאילו מקורות ניתן לאמת אותו?']
        research={'state':'not_executed','model_connected':False,'requirements':[
            {'id':'official','requirement':'Verify current official platform documentation, publication/update date and applicability to objective/account before proposing a change'},
            {'id':'independent','requirement':'For causal performance claims seek independent primary experiments with disclosed sample, market, measurement and limitations; repeated marketing claims are not consensus'},
            {'id':'contradictions','requirement':'Record contradictory findings and confidence; no consensus claim without genuinely independent supporting sources'},
            {'id':'separation','requirement':'Keep observed metrics separate from hook/CTA hypotheses and controlled test proposals'},
            {'id':'approval','requirement':'Business intent and research do not authorize budgets, pauses or publication'}],
            'questions':questions,'sources':[],'confidence':'research not performed'}
        return {'request':request,'business_dossier':json.loads(json.dumps(self.dossier,ensure_ascii=False)),
                'research_plan':research,'analysis':analysis,'model_connected':False,'live_action_allowed':False,'provider_writes':0,
                'note':'This is a saved planning request and observed-data analysis, not an AI research answer or execution approval'}
    def analyze(self,snapshot):
        now=self.now();source=(snapshot.get('sources') or {}).get('meta') or {};data=source.get('data') or {};inventory=data.get('active_ads') or {}
        result={'verified':False,'account_id':ACCOUNT,'currency':None,'scope':'all currently active Meta ads in fixed ROSERY account','active_ads_count':None,'ads':[],
                'unknowns':[],'checked_at':now.isoformat(),'live_action_allowed':False,'provider_writes':0,'numerical_recommendations':[],
                'attribution_note':'Attributed events are not unique people, reconciled orders or incremental business effects'}
        fresh=lambda value:(t:=stamp(value)) is not None and 0<=(now-t).total_seconds()<=720
        if source.get('error') or not fresh(source.get('last_success_at')) or not fresh(inventory.get('checked_at')) or data.get('verified') is not True or data.get('account_id')!=ACCOUNT or inventory.get('verified') is not True or inventory.get('complete') is not True or inventory.get('account_id')!=ACCOUNT:
            result['unknowns'].append('Fresh authenticated complete account-wide active-ad inventory is unavailable');return result
        ads=inventory.get('ads')
        if not isinstance(ads,list) or len(ads)>1000 or any(not isinstance(a,dict) or not isinstance(a.get('id'),str) for a in ads) or len({a['id'] for a in ads})!=len(ads):
            result['unknowns'].append('Active-ad identity coverage is invalid');return result
        for ad in ads:
            if ad.get('account_id')!=ACCOUNT or ad.get('effective_status')!='ACTIVE' or (ad.get('campaign') or {}).get('effective_status')!='ACTIVE' or (ad.get('adset') or {}).get('effective_status')!='ACTIVE':
                result['unknowns'].append('Parent/child identity or active status is inconsistent');return result
        result.update(verified=True,active_ads_count=len(ads),source_observed_at=inventory['checked_at'],currency=data.get('currency') if isinstance(data.get('currency'),str) and re.fullmatch('[A-Z]{3}',data['currency']) else None)
        if result['currency']!=BUSINESS.currency:result['unknowns'].append('Fixed ROSERY ILS monetary normalization is unavailable; no spend totals are inferred')
        try:today=now.astimezone(ZoneInfo(data['timezone'])).date()
        except (KeyError,TypeError,ValueError,ZoneInfoNotFoundError):
            result['unknowns'].append('Account timezone is unknown; full-day metrics cannot be calculated');today=None
        rows=data.get('rows') if isinstance(data.get('rows'),list) else []
        for ad in ads:
            campaign=ad.get('campaign') or {};objective=campaign.get('objective');goal=self._objective(objective)
            event=((ad.get('adset') or {}).get('promoted_object') or {}).get('custom_event_type')
            if goal=='leads' and event=='COMPLETE_REGISTRATION':goal='registration'
            windows=[self._window(data,rows,ad['id'],today,d) for d in (1,3,7)]
            observed=[];hypotheses=[];unknown=[]
            if goal=='awareness':unknown.append('Reach, frequency and brand-lift evidence were not collected; purchases are not required to judge awareness delivery')
            if not any(w['spend_minor'] is not None for w in windows):unknown.append('Full-day reporting coverage is unavailable; missing metrics are not zero')
            creative=ad.get('creative') or {};body=creative.get('body')
            if not body:
                story=creative.get('object_story_spec') or {}
                body=next(((story.get(k) or {}).get('message') for k in ('link_data','video_data') if (story.get(k) or {}).get('message')),None)
            if isinstance(body,str):observed.append({'kind':'copy_text','value':body[:2000],'source_ref':'meta:creative:'+str(creative.get('id','unknown')),'is_performance_explanation':False})
            else:unknown.append('Copy/creative content was not fully read; no hook quality conclusion is supported')
            if goal in ('sales','leads','registration') and any(w['spend_minor'] is not None and w['spend_minor']>0 and w['purchases_attributed' if goal=='sales' else 'registrations_attributed' if goal=='registration' else 'leads_attributed'] is None for w in windows):
                unknown.append('The objective result event is absent or not collected; verify measurement before interpreting efficiency')
            if isinstance(body,str):hypotheses.append({'claim':'A different opening or CTA may affect response','state':'untested','causal_evidence':False,'source':'copy inspection, not a controlled experiment'})
            tests=[{'kind':'controlled_creative_test','goal':goal,'proposal':'Compare one hook or CTA change while keeping product, offer, landing page and audience comparable',
                    'metric_to_define':'A measured outcome appropriate to the business goal','budget_proposed':None,'requires_exact_approval':True,'execution_available':False}]
            result['ads'].append({'id':ad['id'],'name':ad.get('name'),'campaign_id':campaign.get('id'),'objective':objective,'goal':goal,'windows':windows,
                'priority':'investigate' if unknown else 'observe','observed':observed,'hypotheses':hypotheses,'controlled_tests':tests,'unknowns':unknown,
                'last_change_verified':False,'warning':'Windows can include prior creative/settings; no last-change history was collected'})
        return result
    def _objective(self,value):
        if value in ('OUTCOME_AWARENESS','BRAND_AWARENESS','REACH'):return 'awareness'
        if value in ('OUTCOME_SALES','SALES','PRODUCT_CATALOG_SALES'):return 'sales'
        if value in ('OUTCOME_LEADS','LEAD_GENERATION'):return 'leads'
        if value in ('OUTCOME_TRAFFIC','LINK_CLICKS','TRAFFIC'):return 'traffic'
        return 'unknown'
    def _window(self,data,rows,identity,today,days):
        keys=('spend_minor','impressions','landing_views','registrations_attributed','leads_attributed','purchases_attributed')
        result={'days':days,'since':None,'until':None,'full_days':True,**{k:None for k in keys}}
        if today is None:return result
        start=(today-timedelta(days=days)).isoformat();end=(today-timedelta(days=1)).isoformat();result.update(since=start,until=end)
        if data.get('complete') is not True or not isinstance(data.get('since'),str) or not isinstance(data.get('until'),str) or data['since']>start or data['until']<end:return result
        selected=[r for r in rows if isinstance(r,dict) and r.get('ad_id')==identity and isinstance(r.get('date_start'),str) and start<=r['date_start']<=end]
        if not selected:return result
        if any(r.get('date_stop')!=r.get('date_start') for r in selected):return result
        grain=[(r.get('ad_id'),r.get('date_start'),r.get('date_stop')) for r in selected]
        if len(set(grain))!=len(grain):return result
        totals={k:Decimal(0) for k in keys};seen={k:True for k in keys}
        for row in selected:
            actions=row.get('actions')
            for key,field in [('spend_minor','spend'),('impressions','impressions')]:
                value=self._number(row.get(field));seen[key]&=value is not None
                if value is not None:totals[key]+=value*(100 if key=='spend_minor' else 1)
            for key,types in [('landing_views',('landing_page_view',)),('registrations_attributed',('omni_complete_registration','complete_registration')),('leads_attributed',('lead','offsite_conversion.fb_pixel_lead')),('purchases_attributed',('omni_purchase','purchase','offsite_conversion.fb_pixel_purchase'))]:
                found=next((a.get('value') for t in types for a in actions if isinstance(a,dict) and a.get('action_type')==t),None) if isinstance(actions,list) else None
                value=self._number(found);seen[key]&=value is not None
                if value is not None:totals[key]+=value
        for key in keys:
            if seen[key] and (key not in ('spend_minor','impressions') or totals[key]==totals[key].to_integral_value()):result[key]=int(totals[key]) if key in ('spend_minor','impressions') else float(totals[key])
        if data.get('currency')!=BUSINESS.currency:result['spend_minor']=None
        return result
    def _number(self,value):
        if value is None or isinstance(value,bool):return None
        try:
            number=Decimal(str(value));return number if number.is_finite() and number>=0 else None
        except (InvalidOperation,ValueError,TypeError):return None
