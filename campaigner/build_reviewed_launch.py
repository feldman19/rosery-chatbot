"""Save this agent's reviewed ROSERY launch draft. Not an automatic AI reviewer.

Only local artifacts are written. Requires the actual collected source hashes and
owner's current canonical request; no Meta or Drive write calls are made.
"""
from pathlib import Path
import json,hashlib
from work_intake import WorkIntake
from launch_proposal import LaunchProposal


REVIEWED_ASSETS={'Copy of ROSERY_C11_self_style_v10_FEED.png': {'id': '10EhudDguGJfqHRi9oDt-tMRwM7RFtzgT', 'sha256': '4eab73eaa537e1e303536eaade3a095f2c8a81a3579d367e5de64631591494f2'}, 'Copy of ROSERY_C11_self_style_v10_STORY.png': {'id': '1aqN_s0JFrEmyMK2evuF6tbv2MGzLr7i-', 'sha256': '7890b146394ae32a2514390030324af61465c95be680e5812e866e57a7d13ce4'}, 'Copy of ROSERY_C12_self_style_v10_FEED.png': {'id': '1Pb8qLtKQinf8Wi7tn-i2mpIGXH5FNrpN', 'sha256': '9a3bcdddda6259c386db76ba78ce2a926d8404976644fe213da833c3c01c29e1'}, 'Copy of ROSERY_C12_self_style_v10_STORY.png': {'id': '1UXHeDjl6SZDqoMSdUP0PzMtJ_LlSykny', 'sha256': '17e534a12b9b94c86fe2a92385485777e6857761db5b4d58113a7af947d90bd1'}, 'Copy of ROSERY_C14_self_style_v10_FEED.png': {'id': '1QWmL1C3F4g29kjmumr_LfugCvTulMgqZ', 'sha256': '79142f207b7ad3c222c5569f46fac853ef067ac904ae520075fc04ac6e8187bc'}, 'Copy of ROSERY_C14_self_style_v10_STORY.png': {'id': '181MWQB81P6EpPtOFtnUQzWEnqfdEi5F4', 'sha256': '8d2c68807cfe1109121b0382daa947cb82221fdbd03803fef83f57363bc8b0c3'}, 'Copy of ROSERY_C15_self_style_v10_FEED.png': {'id': '1WFO6TDHDR3rrs-hUr2Qsnbu269_1CY_J', 'sha256': '69ef93509634df56d3eb8f1cd14e4e909afb3d799252b6bb8369233ad30a4144'}, 'Copy of ROSERY_C15_self_style_v10_STORY.png': {'id': '1Kmycr2K_5oogRuWiWHoOe_z1pPHyULtp', 'sha256': 'e9fb011b5d715dfdd62f4a91c6a8cf1115c285151f53072654360222d30ac441'}}
COPY_OPTIONS={'C11': {'primary': ['לא צריך להחליף את כל הלוק. התחילו בפרט קטן על היד ובחרו שילוב צמידים שמתאים לכם.', 'צמיד אחד למראה נקי, או כמה שכבות לביטוי אישי. גלו את צמידי ROSERY ובנו שילוב משלכם.'], 'headline': 'הפרט הקטן שעל היד'}, 'C12': {'primary': ['מעל החולצה או כחלק משילוב שכבות — בחרו את השרשרת שתיתן ללוק שלכם נוכחות. גלו את השרשראות של ROSERY.', 'מחפשים את האיזון בין פשוט לבולט? התחילו בשרשרת, נסו שילוב משלכם ומצאו את המראה שמתאים לכם.'], 'headline': 'השרשרת שמתאימה לך'}, 'C14': {'primary': ['מהחולצה ועד התכשיט האחרון: בחרו את הפריטים שלכם ובנו לוק שנוח לכם להיות בו. גלו את ROSERY.', 'יש ימים שבהם מתחשק לצאת מהשגרה. התחילו בחולצה שאתם אוהבים והוסיפו את הפרטים שעושים לכם את הלוק.'], 'headline': 'הלוק מתחיל בפרטים'}, 'C15': {'primary': ['אותו מפגש, שני סגנונות. ביגוד ותכשיטים שאפשר לשלב בדרך שלכם — גלו את הקולקציה של ROSERY.', 'מה הופך לוק לשלכם? הבחירה בפריטים והשילוב ביניהם. בחרו מתוך קולקציית ROSERY את הדרך שלכם להתלבש.'], 'headline': 'הקולקציה, בדרך שלך'}}

def build(directory):
    directory=Path(directory);out=Path(__file__).parent.parent/'outputs'
    request=WorkIntake(directory).latest()
    context=json.loads((out/'rosery-sales-launch-owner-context-2026-10-18.json').read_text(encoding='utf-8'))
    config=json.loads((out/'rosery-sales-restart-configuration-2026-10-18.json').read_text(encoding='utf-8'))
    evidence=json.loads(json.loads((directory/'launch-materials.json').read_text(encoding='utf-8'))['payload'])
    assets=(evidence.get('assets') or {}).get('assets',[])
    definitions=[
      ('C11','השילוב שעושה את ההבדל','לוק פשוט. שילוב שעושה את ההבדל. צמידי ROSERY מוסיפים את הפרט שהופך את הלוק לשלך. מצא את השילוב שלך.','צמידים בשילוב אישי; צילום תקריב של היד'),
      ('C12','נוכחות בלי להגזים','לפעמים שרשרת אחת משנה את כל הלוק. גלה את השרשראות של ROSERY ובחר איך ללבוש אותן — לבד או בשילוב משלך.','שרשראות בגוון זהוב, שכבות ותקריב'),
      ('C14','לא צריך סיבה להתלבש טוב','החולצה, השרשרת והפרטים שביניהם. גלה את הקולקציה של ROSERY ובנה את הלוק שמתאים לך.','צילום לבוש מלא יחסית; שילוב ביגוד ותכשיטים'),
      ('C15','כל אחד והסטייל שלו','אותה קולקציה, הדרך שלך ללבוש אותה. גלה את הביגוד והתכשיטים של ROSERY ובחר את השילוב שלך.','שני דוגמנים ולבוש שונה; זווית קולקציה וסגנון אישי')]
    ads=[]
    for concept,headline,copy,note in definitions:
        chosen={}
        for format_name in ('FEED','STORY'):
            matches=[row for row in assets if row.get('name')=='Copy of ROSERY_'+concept+'_self_style_v10_'+format_name+'.png' and row.get('content_read') is True]
            if len(matches)!=1:raise ValueError('Exact reviewed source missing')
            source=matches[0]
            if REVIEWED_ASSETS.get(source['name'])!={'id':source['id'],'sha256':source['sha256']}:raise ValueError('Historical visual review does not cover changed source')
            chosen[format_name]={'asset_id':source['id'],'source_name':source['name'],'sha256':source['sha256'],'review':{'visual_checked':True,'method':'Historical agent visual review on 2026-10-18; exact source ID and SHA256 pinned in generator','notes':note+'; לא נצפתה הנחה או הבטחת מבצע בתמונה. תצוגת מיקומים בתוך Meta עוד לא נבדקה'}}
        ads.append({**chosen['FEED'],'story':chosen['STORY'],'headline':headline,'primary_text':copy,'description':'גלה את הקולקציה של ROSERY','cta':'SHOP_NOW','destination_url':'https://roseryjewelry.com/',
          'primary_text_options':[copy]+COPY_OPTIONS[concept]['primary'],
          'headline_options':[headline,COPY_OPTIONS[concept]['headline']]})
    campaign=config['campaign'];old_active=[{'id':row['id'],'name':row['name'],'before':row['status'],'proposed_after':'PAUSED'} for row in campaign['adsets']['data'] if row['status']=='ACTIVE']
    proposal={'id':'rosery-sales-launch-20261019-r1','launch_at':context['launch_at_local'],'ends_at':'2026-10-21T20:00:00+03:00','timezone':context['timezone'],'promotion':False,
      'summary':'טיוטת ניסוי מכירות להשקה: ארבע זוויות נבחרות מהחומרים החדשים, לכל אחת פיד וסטורי ושלוש אפשרויות קופי. מוצעת קבוצת בדיקה חדשה ומבודדת בתוך קמפיין המכירות הקודם. אין הפעלה אוטומטית של המודעות הישנות.',
      'campaign':{'id':campaign['id'],'name':campaign['name'],'status':campaign['status'],'source_checked_at':config['checked_at'],'status_is_snapshot':True},
      'budget':{'type':'lifetime','proposed_total_minor':30000,'proposed_daily_minor':None,'currency':'ILS','approved':False,'rationale':'סכום בדיקה מוצע ל־48 שעות, בגודל שני ימים מתקציב הסטטי הקיים של 150 ₪ ביום. זו תקרת ניסוי מוצעת לאישור, לא יעד CPA מחושב או הוכחת רווחיות.'},
      'adset_plan':{'create_new':True,'name':'ROSERY | AFTER HOUR | ניסוי מכירות C11 C12 C14 C15 | 19.10','objective':'OUTCOME_SALES','optimization_event':'PURCHASE','pixel_id':'723893636602228','location':'Israel','age_min':18,'gender':'men','exclude_purchasers_days':30,'status_to_prepare':'PAUSED','placements_note':'להתאים את קובצי הפיד והסטורי ולבדוק תצוגה בכל מיקום לפני פרסום'},
      'required_changes_before_any_activation':old_active,
      'ads':ads,
      'gaps':['האתר עדיין מפנה לדף password. יש לאמת גישה למכירה, דפי מוצר ומלאי לפני מועד ההפעלה.','אירוע Purchase, ערך, מטבע וכפילויות טרם אומתו במסגרת ההשקה הזאת.','יש לבדוק תצוגות Meta בפיד/סטורי ורילס; בדיקת קובץ מקור אינה תצוגה מקדימה של מודעה.','התקציב הכולל 300 ₪ וכל שינויי מצב הקמפיין/האדסטים הם הצעה בלבד ודורשים אישור מדויק.','הנתונים ההיסטוריים אינם הוכחה שהחומרים החדשים ימירו. לא כל 92 הקבצים נבדקו חזותית; נבדקו עשרת קובצי C11–C15, ומתוכם נבחרו שמונה.','לפני הפעלת ההורה צריך להשהות במפורש את ארבע קבוצות המודעות הישנות המסומנות ACTIVE, כולל ניסוי המתנות שפג; אחרת הקמפיין אינו ניסוי מבודד.'],
      'research_sources':[{'url':'https://developers.meta.com/vr/resources/launch-ad-campaign/','use':'התאמת יעד ותלות במדידה; אינו מחקר ביצועי אופנה'},{'url':'https://www.facebook.com/business/ads/facebook-instagram-reels-ads','use':'התאמת פורמט אנכי; אינו אישור שקריאייטיב מסוים ינצח'},{'url':'https://roseryjewelry.com/password','use':'מצב אתר שנקרא בעת ההכנה'}],
      'creative_selection_note':'C13 נשאר כחלופה: דומה לזווית הצמיד של C11. הבחירה נועדה להציע שילובים, שרשראות, לבוש וקולקציה — לא להכריז על מנצחות מראש.'}
    decision_path=out/'rosery-launch-owner-decision-2026-10-18.json'
    revised=False
    if decision_path.exists():
        decision=json.loads(decision_path.read_text(encoding='utf-8'))
        if decision.get('existing_campaign_id')!=campaign['id'] or decision.get('new_adsets_preparation_only') is not True:raise ValueError('Owner decision mismatch')
        revised=True
        proposal.update(id='rosery-sales-launch-20261019-r2',summary='הכנת אדסט נוסף עם ארבע מודעות חדשות בלבד, לצד ההרכב הישן שנשמר ללא שינוי. אין תקציב נוסף מאושר ואין תזמון או הפעלה.',required_changes_before_any_activation=[])
        proposal['owner_decision']=decision
        proposal['budget'].update(proposed_total_minor=None,type='unapproved',rationale='המשתמש בחר הכנה בלבד; 300 ₪ לא אושרו ואין להעביר תקציב קיים לתוספת.')
        proposal['adset_plan']['name']='ROSERY | AFTER HOUR | C11 C12 C14 C15 | הכנה בלבד — ללא תקציב מאושר'
        proposal['restart_plan']={'existing_campaign_id':campaign['id'],'preserve_existing_adsets_and_ads':True,'budget_change_authorized':False,'new_adsets_activation_authorized':False,'automatic_activation_scheduled':False,'requested_restart_at':'2026-10-19T19:00:00+03:00','store_opens_at':'2026-10-19T20:00:00+03:00','early_spend_risk_unresolved':True}
        proposal['gaps']=[g for g in proposal['gaps'] if not g.startswith(('לפני הפעלת ההורה','התקציב הכולל 300'))]
        proposal['gaps']+=['התוספת להכנה בלבד: תקציב חדש והפעלתה לא אושרו. אין שינוי באדסטים הישנים.','המשתמש ביקש הדלקה ב־19:00 אך אישר שהאתר נפתח ב־20:00. לא הוגדר תזמון: בדיקת מודעות אינה עיכוב מובטח ואין אישור להוצאת כסף לפני פתיחת האתר.']
    value=LaunchProposal(directory,(directory/'signing.key').read_bytes()).save(proposal,request)
    (out/('rosery-launch-proposal-2026-10-19-r2.json' if revised else 'rosery-launch-proposal-2026-10-19.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    return value

if __name__=='__main__':
    value=build(Path(__file__).parent/'.local');print(json.dumps({'saved':True,'ads':len(value['ads']),'activation_available':False}))
