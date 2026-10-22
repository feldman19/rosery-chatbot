"""Pure operational progress projection; no collection, model or execution loop.

Consumes trusted host reports, never treats intent, downloaded bytes, dimensions
or manual completion flags as creative readiness or campaign authorization.
"""
from datetime import datetime,timezone
import re
import copy


def _time(value):
    try:
        parsed=datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else None
    except (ValueError,TypeError):return None


def _request(value,text_key):
    if not isinstance(value,dict) or not isinstance(value.get('id'),str) or type(value.get('revision')) is not int or value['revision']<1:return None
    text=value.get(text_key)
    return value if isinstance(text,str) and text.strip() else None


def _normalize(text):return ' '.join(text.split()).casefold()


def work_status(launch_report,strategy_report,materials_status=None,now=None):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError('Aware host time required')
    launch=launch_report if isinstance(launch_report,dict) else {};strategy=strategy_report if isinstance(strategy_report,dict) else {}
    lr=_request(launch.get('request'),'brief');sr=_request(strategy.get('request'),'goal')
    completed=[];blocked=[];stages=[]
    result={'goal':None,'goal_binding':None,'category':'unclear','request':None,'state':'needs_goal','current_state':'טרם נשמרה מטרת עבודה',
            'completed':completed,'blocked':blocked,'next_output':'בקשת עבודה אחת עם תוצאה עסקית ברורה','owner_action':None,'stages':stages,
            'ai_connected':False,'background_execution':False,'live_action_allowed':False,'execution_approval':False,
            'checked_at':now.isoformat(),'note':'זהו מצב העבודה המתועדת. אין כאן סוכן AI מחובר או מנהל קמפיינים שפועל ברקע'}
    fresh=lambda value:(t:=_time(value)) is not None and 0<=(now-t).total_seconds()<=720
    def add(identity,title,state,detail,observed_at=None,owner='system'):
        item={'id':identity,'title':title,'state':state,'detail':detail}
        if observed_at:item['observed_at']=observed_at
        stages.append(item)
        if state=='completed':completed.append(dict(item))
        else:blocked.append({**item,'owner':owner})
    if not lr and not sr:
        add('goal','מטרת העבודה','missing','יש לשמור בקשה עסקית לפני הכנת תוכנית','',owner='owner')
        result['owner_action']={'id':'set_goal','question':'מה העסק רוצה להשיג עכשיו?','options':['חשיפה','קהל חדש','הרשמות','מכירות']}
        return result
    requests=[('launch',lr,'brief'),('strategy',sr,'goal')]
    candidates=[r for r in requests if r[1] is not None]
    chosen=max(candidates,key=lambda r:(_time(r[1].get('saved_at')) or datetime.min.replace(tzinfo=timezone.utc),r[0]=='strategy'))
    kind,request,text_key=chosen
    result.update(goal=request[text_key],request=copy.deepcopy(request),category=sr.get('category','unclear') if sr else lr.get('goal',launch.get('goal','unclear')),goal_binding={'source':kind,'request_id':request['id'],'revision':request['revision']})
    add('saved_goal','המטרה נשמרה','completed','גרסה '+str(request['revision'])+' נשמרה ככוונה עסקית; היא אינה אישור לביצוע',request.get('saved_at'))
    conflict=lr is not None and sr is not None and _normalize(lr['brief'])!=_normalize(sr['goal'])
    if conflict:
        add('goal_conflict','שתי בקשות עבודה שונות','blocked','בקשת ההשקה ובקשת האסטרטגיה אינן אותו טקסט. אין מנוע שיכול לקבוע שהן תואמות, ולכן הן לא אוחדו',owner='owner')
        result.update(state='needs_consolidation',current_state='צריך לאחד את מטרת העבודה',next_output='בריף אחד מוסכם, השומר את היסטוריית שתי הבקשות',
            owner_action={'id':'consolidate_goals','question':'איזו בקשה היא המטרה הפעילה, או כיצד לאחד ביניהן?','options':[{'source':'strategy','goal':sr['goal']},{'source':'launch','goal':lr['brief']}]})
    else:
        result.update(state='preparation_required',current_state='המטרה נשמרה; הכנת התוכנית עדיין לא הושלמה',next_output='תוכנית בדיקה מתועדת שמתאימה למטרה ולנתונים שנאספו')
    if not conflict and lr and (lr.get('goal') or launch.get('goal'))=='sales' and (not sr or sr.get('category')=='unclear'):
        result['category']='sales';result['category_basis']='saved_sales_launch_context_not_execution_authority'
    if sr and result['category']=='unclear' and sr.get('clarifications'):
        add('goal_clarity','הגדרת מדד ההצלחה','missing','לא הוגדר יעד ראשי חד־משמעי; סיווג לפי מילות מפתח אינו הבנה של הבקשה',owner='owner')
        if not conflict:result['owner_action']={'id':'clarify_goal','question':str(sr['clarifications'][0]),'options':[]}
    # A generic strategy request does not require new files when existing ads can
    # be inspected. Materials are relevant only to the separately saved launch.
    summary=launch.get('materials') or {}
    if lr:
        material=materials_status if isinstance(materials_status,dict) else {}
        bound=(material.get('verified') is True and material.get('binding_verified') is True and not material.get('error') and material.get('request_id')==lr['id'] and type(material.get('request_revision')) is int and material['request_revision']==lr['revision'] and material.get('drive_urls')==lr.get('drive_urls') and fresh(material.get('checked_at')))
        trusted_summary=isinstance(summary,dict) and summary.get('metadata_verified') is True and fresh(summary.get('observed_at')) and (materials_status is None or bound)
        if lr.get('drive_urls'):add('source_links','קישורי המקור נשמרו','completed',str(len(lr['drive_urls']))+' קישורי Drive נשמרו; שמירת קישור אינה קריאת קובץ',lr.get('saved_at'))
        else:add('source_links','חומרי ההשקה','missing','טרם נשמרו קישורי חומרי ההשקה',owner='owner')
        if trusted_summary:
            count=summary.get('content_read_count');assets=summary.get('assets')
            valid_count=type(count) is int and count>=0 and isinstance(assets,list) and count==len(assets) and all(isinstance(a,dict) and a.get('content_read') is True and isinstance(a.get('id'),str) and isinstance(a.get('sha256'),str) and re.fullmatch(r'(?:sha256:)?[0-9a-f]{64}',a['sha256']) for a in assets) and len({a['id'] for a in assets if isinstance(a,dict) and isinstance(a.get('id'),str)})==len(assets)
            if valid_count and count>0:
                complete=summary.get('assets_complete') is True
                add('direct_content','קריאת קובצי המקור הישירים','completed' if complete else 'partial',str(count)+' קבצים ישירים נקראו ותועדו בחתימת תוכן. '+('הקריאה הישירה הושלמה' if complete else 'הקריאה הישירה עדיין חלקית'),summary['observed_at'])
            else:add('direct_content','קריאת קובצי המקור','missing','נתוני הקבצים זמינים, אך אין קריאת תוכן מאומתת עבור כל הקבצים',summary.get('observed_at'))
        else:
            detail='נדרשת קריאה מאומתת ועדכנית של המקורות עבור גרסת הבקשה הזאת'
            if material.get('error'):detail='קריאת המקורות האחרונה נכשלה; תיעוד קודם אינו הוכחת השלמה'
            add('direct_content','קריאת קובצי המקור','blocked',detail)
        # A direct-only 54/54 count says nothing about children of child folders.
        metadata=material.get('metadata') if bound else None
        if isinstance(metadata,dict) and metadata.get('verified') is True and metadata.get('complete') is True and metadata.get('email')=='inforosery@gmail.com':
            sources=metadata.get('sources') or [];nested=set();not_recursive=False
            for source in sources:
                if not isinstance(source,dict):continue
                if source.get('recursive') is not True:not_recursive=True
                for file in source.get('files') or []:
                    if isinstance(file,dict) and file.get('mimeType')=='application/vnd.google-apps.folder' and file.get('id')!=source.get('source_id'):nested.add(file.get('id'))
            if nested and not_recursive:add('nested_folders','תיקיות משנה טרם נסרקו','blocked',str(len(nested))+' תיקיות משנה נמצאו בקריאה ישירה. השלמת קריאת הקבצים הישירים אינה בדיקה של תוכן תיקיות המשנה',material.get('checked_at'))
            elif not sources:add('nested_folders','כיסוי התיקיות','missing','לא התקבל תיעוד של היקף הסריקה')
        else:add('nested_folders','כיסוי תיקיות המשנה','missing','אין תיעוד קשור ועדכני שמוכיח מה נסרק בתוך התיקיות')
        add('creative_review','בחירת קריאייטיבים והתאמתם למטרה','missing','הורדה, מידות וחתימת תוכן אינן בדיקת מסר, מוצר, מבצע או התאמה לקהל')
        if material.get('configured') is False and lr.get('drive_urls') and not trusted_summary and not conflict:
            result['owner_action']={'id':'connect_drive','question':'נדרש אישור Google כדי לקרוא את החומרים מהדרייב','options':[]}
    analysis=strategy.get('analysis') or {}
    if isinstance(analysis,dict) and analysis.get('verified') is True and fresh(analysis.get('source_observed_at')) and type(analysis.get('active_ads_count')) is int and analysis['active_ads_count']>=0:
        add('active_ads','מודעות פעילות נקראו','completed',str(analysis['active_ads_count'])+' מודעות פעילות נכללו בסקירה תיאורית. אין בכך הכרזה על מנצחות',analysis['source_observed_at'])
    else:add('active_ads','סקירת הפרסום הפעיל','blocked','נדרשת קריאה מאומתת ועדכנית של המודעות הפעילות; אין להסיק אפס מודעות')
    research=strategy.get('research_plan') or {}
    add('research','מחקר מקורות ותוכנית בדיקה','missing','טרם תועד מחקר עדכני מול מקורות רשמיים וראיות ראשוניות מתאימות. רשימת דרישות מחקר אינה מחקר שבוצע')
    add('decision_output','תוכנית פעולה לאישור','missing','טרם הוצגה תוכנית מנומקת למטרה הפעילה. אין כאן המלצת תקציב או אישור לפרסום')
    if not conflict and blocked:
        result['state']='blocked';result['current_state']='חלק מהמידע נאסף; התוכנית המנומקת עדיין חסרה'
    result['next_output']='איחוד הבקשות לבריף פעיל אחד לפני המלצה' if conflict else 'תוכנית בדיקה עם מקורות, הבחנה בין נתונים להשערות והצעה נפרדת לאישור'
    if not conflict and result['category'] in ('awareness','new_audience','registration'):
        add('workflow_capability','הכנת תוכנית ליעד הזה עדיין אינה נתמכת','blocked','אפשר לשמור את המטרה ולסקור מודעות, אך תהליך ההכנה הנוכחי מיועד להשקת מכירות. הבקשה לא הוסבה למכירות')
        result.update(state='unsupported_goal',current_state='המטרה נשמרה; עדיין חסר תהליך הכנה ייעודי ליעד הזה',
            next_output='הגדרת תהליך מחקר ובדיקה ליעד שנבחר, בלי המלצת מכירות אוטומטית',owner_action=None)
    return result
