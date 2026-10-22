"""Operator-facing next action from collected evidence, not a universal scale gate."""
from datetime import datetime,timezone
from creative_audit import audit
from live_workplan import build_workplan
from meta_direct import LAUNCH
from experiment_planner import plan_experiment

EXPECTATIONS={'campaigns':{LAUNCH:{'verified':True,
    'source_ref':'workspace:user-request-website-completed-registration-launch',
    'goal':'registration','expected_event':'COMPLETE_REGISTRATION','expected_pixel_id':'723893636602228'}}}

def report(snapshot,now=None):
    now=now or datetime.now(timezone.utc)
    plan=build_workplan(snapshot,EXPECTATIONS,now)
    tracking=audit(snapshot,now)
    cases=[]
    for finding in tracking['findings']:
        cases.append({**finding,'priority':1,'state':'prepared_for_review','type':'tracking_fix',
                      'action_label':'הצג את התיקון המוכן','requires_approval':True,
                      'execution_available':False})
    for task in plan['tasks']:
        cases.append({'id':task['id'],'type':'investigation','priority':task['priority'],
                      'state':'prepared_for_review','title':task['title'],'task':task,
                      'action_label':'הצג את הבדיקה הבאה','requires_approval':False,
                      'execution_available':False})
    cases.sort(key=lambda x:(x['priority'],x['id']))
    experiment=plan_experiment(snapshot,now)
    return {'checked_at':now.isoformat(),'source_observed_at':((snapshot.get('sources') or {}).get('meta') or {}).get('last_success_at'),'mode':'prepare_and_review','account_id':plan['account_id'],
            'scope':'קמפיין ההרשמה להשקה בלבד','headline':(('תיק טיפול אחד מוכן לבחינה' if len(cases)==1 else 'הכנתי '+str(len(cases))+' תיקי טיפול לבחינה') if cases else 'הבדיקה הושלמה — אין תיקון הגדרות מאומת כרגע' if tracking['verified'] and not tracking['unknown_ads'] else 'חסרים נתונים להשלמת בדיקת הקישורים'),
            'summary':('בדקתי את ההגדרות ואת סימוני הקישורים של '+str(tracking['active_ads_checked'])+' מודעות פעילות. תיקון מוצע אינו שינוי שבוצע.' if tracking['verified'] else 'הנתונים הקיימים אינם מספיקים לאימות הגדרות וקישורים.'),
            'cases':cases,'next':cases[0]['id'] if cases else None,
            'analysis_cases':plan['analysis_cases'],'completed':plan['completed'],
            'tracking':{'verified':tracking['verified'],'active_ads_checked':tracking['active_ads_checked'],'unknown_ads':tracking['unknown_ads']},
            'experiment':{'brief_id':experiment['brief_id'],'version':experiment['version'],'question':experiment['question'],
                'proposal':experiment['proposal'],'state':experiment['state'],'inventory_count':len(experiment['inventory']),
                'blocked_by_tracking':experiment.get('blocked_by_tracking',True),'tracking_dependencies':experiment.get('tracking_dependencies',[]),
                'launch_blocked':True,'published':False,'budget_change':None},
            'live_action_allowed':False,'provider_writes':0,
            'note':'המלצת סקייל כלכלית דורשת מדידת ערך עסקי. איתור תקלות הגדרה וקישור אינו תלוי בהשלמתה.'}
