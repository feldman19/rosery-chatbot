"""Deterministic, read-only operator guidance. No budget recommendations or causal claims."""

LABELS = {'prepare_launch':'הכנת מודעות', 'set_budget':'שינוי תקציב', 'pause':'השהיה', 'activate':'הפעלה'}
STATES = {'cancelled':'בוטלה לפני ביצוע','prepared':'ממתין לאישור — לא בוצע', 'approved':'אושר — טרם אומת ביצוע',
          'queued':'ממתין לביצוע', 'running':'בביצוע', 'scheduled':'מתוזמן',
          'verified':'השינוי הטכני אומת', 'unknown':'תוצאת הביצוע אינה ידועה',
          'partial':'בוצע חלקית', 'blocked':'נחסם', 'stale':'נדרשת בדיקה מחדש',
          'not_applied':'לא הוחל', 'expired':'פג תוקף ההצעה'}


def build_guidance(report, operations=None):
    operations = operations if isinstance(operations, dict) else {}
    cards = []
    def card(identity, title, meaning, next_step, target, evidence, priority):
        cards.append(dict(id=identity, title=title, meaning=meaning, next_step=next_step,
                          target=target, evidence=evidence, priority=priority))
    fresh = report.get('freshness') or {}
    window = (report.get('windows') or {}).get('7') or {}
    metrics = window.get('metrics') or {}
    if fresh.get('state') != 'fresh':
        card('refresh','צריך לעדכן את הנתונים','המידע אינו מלא או אינו עדכני מספיק להחלטת תקציב.',
             'רענן נתונים ובדוק שהחיבור חזר.', 'refresh', fresh.get('notes') or [], 0)
    if metrics.get('purchases') is None or metrics.get('registrations') is None:
        card('measurement','חסר אימות של התוצאות','לא ניתן לקבוע עלות מלאה לרכישה או להרשמה מהדוח הנוכחי.',
             'פתח את פירוט המדידה. עד הבירור לא נסיק שכדאי להגדיל תקציב.', 'measurement',
             window.get('notes') or [], 1)
    jobs = [x for x in operations.get('jobs', []) if isinstance(x, dict)]
    attention = [x for x in jobs if x.get('state') in ('unknown','partial','blocked','stale')]
    if attention:
        card('execution','יש פעולה שדורשת בדיקה','אחת הפעולות לא הסתיימה באימות מלא. ניסיון חוזר עלול ליצור כפילות.',
             'פתח מעקב פעולות ובדוק מה נשמר לפני ניסיון נוסף.', 'track',
             [str(len(attention))+' פעולות דורשות בדיקה'], 0)
    card('economics','עוד אין יעד רווחיות מאומת','מכירות החנות והכנסות מיוחסות אינן רווח. טרם חובר כאן חישוב מלא של עלויות והחזרות.',
         'השתמש כרגע בדוח התוצאות; יעד עלות וסקייל ידרשו חישוב עסקי מאומת.', 'business',
         ['עלות מוצר, משלוח, עמלה, אריזה ומע״מ נדרשים לחישוב; לא הושלמו בהנחות.'], 5)
    pending = report.get('action_summary', {}).get('pending_approval_count', 0)
    if pending:
        card('approval','יש הצעות שממתינות לבדיקה','תצוגה מקדימה אינה פעולה שבוצעה.',
             'פתח את מרכז הפעולות. בדוק חומרים, תקציב ומה יקרה לפני אישור.', 'actions',
             [str(pending)+' הצעות ממתינות לפי המצב שנקרא'], 3)
    journal = []
    for job in reversed(jobs[-20:]):
        steps = job.get('steps') or []
        verified = sum(isinstance(x,dict) and x.get('state')=='verified' for x in steps)
        state = job.get('state')
        journal.append({'id':job.get('id') or job.get('job_id'),
                        'title':LABELS.get(job.get('action'),'פעולת קמפיין'),
                        'state':state, 'state_label':STATES.get(state,'מצב לא מזוהה — דורש בדיקה'),
                        'verified_steps':verified, 'total_steps':len(steps),
                        'technical_verified':state=='verified',
                        'business_result':'טרם נמדד; אימות טכני אינו הוכחת שיפור במכירות.'})
    cards.sort(key=lambda x:x['priority'])
    return {'cards':cards, 'next_action':cards[0] if cards else None, 'journal':journal,
            'business_result':{'status':'not_verified','profit':None,'target_cpa':None},
            'live_action_performed':False}
