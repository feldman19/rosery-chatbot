"""Onboarding a new business (owner, 2026-10-24: "the system can later be sold to other shop owners").

A plain-Hebrew flow, one step at a time; nothing here calls Meta or Shopify and nothing reads a credential:
  1. business      פרטי העסק: name, what you sell, currency, time zone, language
  2. meta_connect  חיבור מטא: done on the business's own computer, in the existing connection screen
  3. meta_account  בחירת חשבון מודעות: from the accounts the connection can see; currency must match step 1
  4. meta_campaign בחירת קמפיין מכירות (or "none yet")
  5. shopify       חיבור שופיפיי (or "no Shopify store")
  6. questionnaire שאלון קצר: order value, gross margin, copy voice, store facts, what never to write
  7. review        בדיקת ברירות המחדל: proposed thresholds with how each was derived; the owner may change any
  8. approve       writes business.json, the business's own rules folder (sources + hash-bound rulebook in the
                   format rulebook.py loads) and profile.json, all inside the new business's data folder.
The connections are reached through a `connectors` object (see LocalOnlyConnectors); tests pass fakes.
The thresholds start from ROSERY's proportions to its own economics and are shown as proposals, never as facts.
"""
import hashlib
import json
import os
import re
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import business as business_config

STEPS = ('business', 'meta_connect', 'meta_account', 'meta_campaign', 'shopify', 'questionnaire', 'review', 'done')
TITLES = {'business': 'פרטי העסק', 'meta_connect': 'חיבור לפייסבוק ואינסטגרם (מטא)', 'meta_account': 'בחירת חשבון המודעות',
          'meta_campaign': 'בחירת קמפיין המכירות', 'shopify': 'חיבור החנות (שופיפיי)', 'questionnaire': 'כמה שאלות על העסק',
          'review': 'בדיקה אחרונה לפני שמתחילים', 'done': 'העסק מוכן'}
HELP = {'business': 'שם העסק ומה הוא מוכר, ובאיזה מטבע ושעון הוא עובד.',
        'meta_connect': 'החיבור נעשה במחשב של העסק, במסך החיבורים. המערכת רק קוראת, ושום שינוי לא קורה בלי אישור שלך.',
        'meta_account': 'בוחרים את חשבון המודעות של העסק. המטבע שלו צריך להיות אותו מטבע של העסק.',
        'meta_campaign': 'הקמפיין שבו המערכת תעזור לנהל מכירות. אם עוד אין, אפשר לדלג.',
        'shopify': 'אם יש חנות שופיפיי, מחברים אותה כדי לראות מכירות אמיתיות. אם אין, אפשר לדלג.',
        'questionnaire': 'התשובות קובעות מתי מודעה נחשבת מצליחה ומתי כדאי לעצור אותה, ואיך כותבים את הטקסטים.',
        'review': 'כאן רואים את כל הכללים שהמערכת תעבוד לפיהם, ואפשר לשנות כל מספר לפני האישור.',
        'done': 'הכל נשמר. מעכשיו המערכת עובדת לפי הכללים שאישרת.'}
SYMBOL = {'ILS': '₪', 'USD': '$', 'EUR': '€', 'GBP': '£'}
FORMS = {'masculine': 'בלשון זכר', 'feminine': 'בלשון נקבה', 'plural': 'בלשון רבים'}
# How ROSERY's own thresholds sit against its economics (margin 80% → break-even return 1.25 on ₪1; a ₪315 order).
# Spend amounts follow the order value, not ROSERY's ₪190 break-even cost per sale, which already includes costs
# other than the product. New businesses start from the same proportions; the owner reviews every number.
ROSERY_PROPORTIONS = {'fail_x_breakeven_roas': 2.3 / 1.25, 'hold_x_fail': 3.0 / 2.3, 'winner_x_fail': 3.7 / 2.3,
                      'default_window_x_fail': 2.6 / 2.3, 'min_spend_x_aov': 200 / 315,
                      'daily_gate_x_aov': 35 / 315, 'watch_x_min_spend': 150 / 200, 'extended_x_min_spend': 300 / 200}
SOURCES = ('01-חוקי-חשבון.md', '02-חוקי-קריאייטיב.md', '03-חוקי-ניתוח-ביצועים.md', '04-חוקי-טיקטוק.md')


class NotConnected(ValueError):
    """A step that needs a live connection made on the business's own computer."""


class LocalOnlyConnectors:
    """The default in the cloud and in tests that do not inject fakes: nothing is connected, nothing is called.
    The real connectors are written on the business's computer against the existing read paths (meta_direct,
    shopify_direct, connections) and only read: accounts, campaigns, the shop's domain and currency."""

    def meta_status(self):
        return {'connected': False}

    def meta_accounts(self):
        raise NotConnected('צריך קודם לחבר את מטא במחשב של העסק')

    def meta_campaigns(self, account_id):
        raise NotConnected('צריך קודם לחבר את מטא במחשב של העסק')

    def shopify_identity(self, domain):
        raise NotConnected('צריך קודם לחבר את שופיפיי במחשב של העסק')


def _number(value, low, high, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ValueError('מספר לא תקין: ' + field)
    return float(value)


def proposed_thresholds(aov, gross_margin):
    """Proposed numbers with the arithmetic behind each, for the review step."""
    breakeven_roas = 1 / gross_margin
    breakeven_cpa = aov * gross_margin
    p = ROSERY_PROPORTIONS
    fail = round(breakeven_roas * p['fail_x_breakeven_roas'], 1)
    min_spend = int(round(aov * p['min_spend_x_aov'] / 10) * 10) or 10
    return {'breakeven_roas': round(breakeven_roas, 2), 'breakeven_cpa': int(round(breakeven_cpa)), 'fail_below': fail,
            'hold_up_to': round(fail * p['hold_x_fail'], 1), 'winner_above': round(fail * p['winner_x_fail'], 1),
            'default_window_fail_below': round(fail * p['default_window_x_fail'], 1), 'min_spend': min_spend,
            'min_daily_spend': max(5, int(round(aov * p['daily_gate_x_aov']))),
            'watch_from': int(round(min_spend * p['watch_x_min_spend'])), 'extended_limit': int(round(min_spend * p['extended_x_min_spend']))}


def rule_lines(b, answers, t):
    """Every rule ROSERY's rulebook has, in plain Hebrew for this business: (source, id, use, category, sentence, params).
    The same ids keep every module that reads a rule working; the sentence is what the owner approves."""
    s = SYMBOL.get(b.currency, b.currency + ' ')
    form = FORMS[answers['copy_form']]
    facts = answers.get('store_facts') or []
    link = 'https://www.facebook.com/adsmanager/manage/ads/edit?act=' + b.meta_account + '&selected_ad_ids='
    a, c, p, k = SOURCES
    return [
        (a, 'approval_required', 'engine', 'account', 'אין שינוי בקמפיינים (השהיה, הפעלה, תקציב, עריכה) בלי אישור מפורש של הבעלים.', None),
        (a, 'readback_required', 'engine', 'account', 'לעולם לא לדווח "בוצע" בלי קריאה חוזרת מהמערכת.', None),
        (a, 'scale_step', 'engine', 'account', 'הגדלת תקציב: +20% בכל פעם, בלי קפיצות.', {'step': 0.2}),
        (a, 'scale_in_place', 'engine', 'account', 'מודעות מנצחות נשארות באד-סט שלהן ומוגדלות במקום.', None),
        (a, 'never_darken_working_adset', 'engine', 'account', 'לעולם לא לכבות אד-סט שעובד.', None),
        (a, 'ads_manager_link', 'engine', 'account', link[len('https://'):], {'url': link}),
        (a, 'ads_per_adset', 'engine', 'account', '5–8 מודעות בכל אד-סט.', {'min': 5, 'max': 8}),
        (a, 'catalog_single_ad_risk', 'engine', 'account', 'סיכון: מודעה אחת שלוקחת 90% או יותר מההוצאה.', {'share': 0.9}),
        (a, 'no_asc_cbo', 'engine', 'account', 'ברירת מחדל: תקציב לכל אד-סט בנפרד, בלי קמפיין אוטומטי מלא של מטא.', None),
        (a, 'dpa_retargeting_only', 'review', 'account', 'מודעות קטלוג רק למי שכבר ביקר באתר.', None),
        (a, 'new_creatives_new_dated_adset', 'review', 'account', 'תכנים חדשים נכנסים לאד-סט חדש עם תאריך, בתוך הקמפיין שרץ.', None),
        (a, 'existing_adset_reuse', 'engine', 'account', 'אפשר להוסיף לאד-סט קיים רק אם יש בו פחות מ-5 מודעות פעילות, ורק לאותו מוצר ואותה זווית.',
         {'max_active_ads': 5, 'same_product': True, 'same_angle': True}),
        (a, 'full_copy_set', 'creative_check', 'creative', 'כל מודעה: 5 טקסטים ו-5 כותרות מלאים.', {'texts': 5, 'headlines': 5}),
        (c, 'masculine_copy', 'review', 'creative', 'כל הקופי ' + form + '.', {'form': answers['copy_form']}),
        (c, 'headline_no_emoji', 'creative_check', 'creative', "אין אימוג'ים בכותרות.", None),
        (c, 'no_superlatives', 'creative_check', 'creative', 'אין סופרלטיבים ואין טענות מומצאות.', None),
        (c, 'no_material_emphasis', 'creative_check', 'creative', 'לא להבליט חומרים שנשמעים זולים.', None),
        (c, 'cta_shop_now', 'creative_check', 'creative', 'כפתור קבוע: "קנה עכשיו".', {'api_value': 'SHOP_NOW'}),
        (c, 'offer_in_headline', 'creative_check', 'creative', 'מבצעים ומשלוח כותבים בכותרת, לא בטקסט הראשי.', None),
        (c, 'no_carousel', 'creative_check', 'creative', 'בלי קרוסלות (כמה תמונות באותה מודעה).', None),
        (c, 'no_variation_label', 'creative_check', 'creative', 'בלי תווית "וריאציה" בשם המודעה.', None),
        (c, 'known_asset_typo', 'creative_check', 'creative', 'לבדוק שאין בתכנים שגיאות כתיב שכבר ידועות.', None),
        (c, 'store_facts', 'creative_check', 'creative', ' · '.join(facts) if facts else 'אין עובדות חנות מאושרות לקופי.',
         {'free_shipping_from': answers.get('free_shipping_from'), 'returns_days': answers.get('returns_days')}),
        (c, 'disable_text_improvements', 'review', 'creative', 'לכבות את השיפורים האוטומטיים של מטא לטקסט ולתמונה (Advantage+) לפני פרסום.', None),
        (p, 'roas_ladder', 'engine', 'performance',
         f'חוק החזר: לפי מודעה, חלון 3 ימים, מ-{s}{t["min_spend"]} הוצאה. מתחת ל-{t["fail_below"]} לעצור, עד {t["hold_up_to"]} להחזיק, מעל {t["winner_above"]} מנצחת.',
         {'window_days': 3, 'min_spend': t['min_spend'], 'fail_below': t['fail_below'], 'hold_up_to': t['hold_up_to'], 'winner_above': t['winner_above']}),
        (p, 'complete_days_only', 'engine', 'performance', 'לא לפעול על היום שעוד לא נגמר; רק ימים מלאים.', None),
        (p, 'ad_min_full_days', 'engine', 'performance', 'מודעה: לפחות 3 ימים מלאים. אד-סט: 7 ימים. לעולם לא יום בודד.', {'ad_days': 3, 'adset_days': 7}),
        (p, 'pause_by_ad_only', 'engine', 'performance', 'החלטות עצירה לפי מודעה, לא לפי אד-סט.', None),
        (p, 'spend_gate', 'engine', 'performance', f'מודעה שמוציאה פחות מ-{s}{t["min_daily_spend"]} ביום עוד לא נבדקה, וזה לא כישלון.', {'min_daily_spend': t['min_daily_spend']}),
        (p, 'kill_requires_spend', 'engine', 'performance', f'עוצרים רק מודעה שהוציאה לפחות {s}{t["min_spend"]} ולא עמדה ביעד.', {'min_spend': t['min_spend']}),
        (p, 'rescue_signals', 'engine', 'performance',
         f'מודעה מתחת ל-{s}{t["watch_from"]} בלי מכירה ממשיכה עד {s}{t["extended_limit"]} רק אם יש 2 הוספות לסל ועוד התחלת תשלום, או אחוז הקלקה 1.5% ומעלה.',
         {'watch_from': t['watch_from'], 'extended_limit': t['extended_limit'], 'min_add_to_cart': 2, 'min_checkout': 1, 'min_link_ctr': 0.015}),
        (p, 'single_purchase_not_verdict', 'engine', 'performance', 'מכירה אחת היא עוד לא פסק דין.', {'min_purchases_for_scale': 2}),
        (p, 'breakeven', 'engine', 'economics',
         f'רווח גולמי {round(answers["gross_margin"] * 100)}%. נקודת איזון: עלות למכירה {s}{t["breakeven_cpa"]} (הזמנה ממוצעת {s}{round(answers["aov"])}).',
         {'breakeven_cpa': t['breakeven_cpa'], 'aov': answers['aov'], 'gross_margin': answers['gross_margin']}),
        (p, 'retargeting_double_threshold', 'engine', 'performance', 'למודעות שפונות למי שכבר ביקר באתר: סף כפול.', {'multiplier': 2}),
        (p, 'default_attribution_threshold', 'engine', 'performance', f'בחלון הייחוס הרגיל של מטא הסף הוא {t["default_window_fail_below"]}.',
         {'fail_below': t['default_window_fail_below']}),
        (p, 'scale_stabilization', 'engine', 'performance', 'אחרי הגדלה: 3 ימים בלי שינויים נוספים.', {'days': 3}),
        (p, 'secondary_metrics_protect_only', 'engine', 'performance', 'מדדים משניים (עלות לסל, אחוז הקלקה) רק מגינים על מודעה, לא מכריעים לבד.', None),
        (p, 'state_what_data_supports', 'engine', 'performance', 'כל המלצה אומרת במפורש מה הנתונים תומכים ומה לא.', None),
        (k, 'tiktok_click_breakeven', 'context', 'tiktok', f'בטיקטוק נקודת האיזון היא בערך {t["breakeven_roas"]} על כל {s}1.', {'breakeven_roas': t['breakeven_roas']}),
        (k, 'tiktok_no_smart_plus', 'context', 'tiktok', 'בטיקטוק בלי קמפיין אוטומטי מלא, עד שיש מספיק נתונים.', None),
    ] + ([(c, 'owner_forbidden', 'review', 'creative', 'אסור לכתוב: ' + ' · '.join(answers['never_write']), None)]
         if answers.get('never_write') else [])


def build_rules(b, answers, thresholds):
    """{file name: source text} and the rulebook dict, hash-bound exactly as rulebook.py checks it."""
    lines = rule_lines(b, answers, thresholds)
    titles = {SOURCES[0]: 'חוקי חשבון', SOURCES[1]: 'חוקי קריאייטיב וקופי', SOURCES[2]: 'חוקי ניתוח ביצועים', SOURCES[3]: 'חוקי טיקטוק'}
    texts = {}
    for source in SOURCES:
        body = [f'- {sentence}' for src, _, _, _, sentence, _ in lines if src == source]
        texts[source] = f'# {b.name} — {titles[source]}\n\n' + '\n'.join(body) + '\n'
    book = {'business_id': b.id, 'account_id': b.meta_account, 'version': 1, 'owner': answers.get('owner_name') or b.name,
            'currency': b.currency,
            'note': 'כל חוק מעוגן בציטוט מדויק מקובץ מקור. שינוי בקובץ מקור משבית את ספר החוקים עד לעדכון ה-hash. החוקים מייצרים הצעות בלבד; כל שינוי חי דורש אישור מפורש.',
            'sources': {name: {'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()} for name, text in texts.items()},
            'rules': [{'id': rid, 'category': category, 'use': use, 'source': source, 'source_excerpt': sentence,
                       **({'params': params} if params is not None else {})}
                      for source, rid, use, category, sentence, params in lines]}
    return texts, book


class Onboarding:
    def __init__(self, directory, connectors=None, clock=None):
        self.directory = Path(directory)
        self.path = self.directory / 'onboarding.json'
        self.connectors = connectors or LocalOnlyConnectors()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.Lock()
        if self.directory.exists() and not self.path.exists() and any(self.directory.iterdir()):
            # Never onboard over an existing business's data (ROSERY's folder has no onboarding.json).
            raise ValueError('התיקייה כבר מכילה נתונים של עסק; לעסק חדש צריך תיקייה חדשה וריקה')
        self.directory.mkdir(parents=True, exist_ok=True)

    def _read(self):
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {'answers': {}, 'step': 'business'}

    def _save(self, state):
        pending = self.path.with_suffix('.pending')
        pending.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(pending, self.path)

    def status(self):
        with self.lock:
            state = self._read()
        at = STEPS.index(state['step'])
        view = {'step': state['step'], 'title': TITLES[state['step']], 'help': HELP[state['step']],
                'steps': [{'id': s, 'title': TITLES[s], 'state': 'done' if i < at else 'now' if i == at else 'later'} for i, s in enumerate(STEPS)],
                'answers': state['answers'], 'live_calls': 0}
        if state['step'] == 'meta_connect':
            view['meta'] = self.connectors.meta_status()
        if state['step'] == 'meta_account':
            view['accounts'] = self._try(self.connectors.meta_accounts)
        if state['step'] == 'meta_campaign':
            view['campaigns'] = self._try(lambda: self.connectors.meta_campaigns(state['answers']['meta_account']))
        if state['step'] == 'review':
            view['review'] = self.review()
        return view

    @staticmethod
    def _try(call):
        try:
            return call()
        except NotConnected as error:
            return {'error': str(error)}

    def _advance(self, step, update):
        with self.lock:
            state = self._read()
            if state['step'] != step:
                raise ValueError('השלב הזה כבר עבר או עוד לא הגיע (עכשיו: ' + TITLES[state['step']] + ')')
            state['answers'].update(update)
            state['step'] = STEPS[STEPS.index(step) + 1]
            state['updated_at'] = self.clock().isoformat()
            self._save(state)
        return self.status()

    def submit(self, step, body):
        """One step's answers; each step checks exactly its own fields."""
        if not isinstance(body, dict):
            raise ValueError('תשובה לא תקינה')
        if step == 'business':
            if set(body) != {'name', 'what_you_sell', 'currency', 'timezone', 'language', 'country'}:
                raise ValueError('נדרשים: שם, מה מוכרים, מטבע, שעון, שפה ומדינה')
            if not isinstance(body['what_you_sell'], str) or not 2 <= len(body['what_you_sell'].strip()) <= 200:
                raise ValueError('כתוב בקצרה מה העסק מוכר')
            business_config.validate({'id': 'check', 'name': body['name'], 'meta_account': '00000', 'currency': body['currency'],
                                      'timezone': body['timezone'], 'language': body['language'], 'country': body['country']})
            return self._advance('business', {k: (v.strip() if isinstance(v, str) else v) for k, v in body.items()})
        if step == 'meta_connect':
            if body != {} or not self.connectors.meta_status().get('connected'):
                raise NotConnected('מטא עוד לא מחובר. מחברים במחשב של העסק, ואז ממשיכים')
            return self._advance('meta_connect', {})
        if step == 'meta_account':
            accounts = self.connectors.meta_accounts()
            chosen = next((a for a in accounts if a.get('id') == body.get('account_id')), None) if set(body) == {'account_id'} else None
            if not chosen:
                raise ValueError('בחר אחד מחשבונות המודעות ברשימה')
            answers = self._read()['answers']
            if chosen.get('currency') != answers['currency']:
                raise ValueError(f'המטבע של החשבון ({chosen.get("currency")}) שונה ממטבע העסק ({answers["currency"]}); המערכת לא ממירה מטבעות')
            return self._advance('meta_account', {'meta_account': chosen['id'], 'meta_account_name': chosen.get('name'),
                                                  'meta_timezone': chosen.get('timezone')})
        if step == 'meta_campaign':
            if set(body) != {'campaign_id'}:
                raise ValueError('בחר קמפיין, או "אין עדיין"')
            if body['campaign_id'] is not None:
                campaigns = self.connectors.meta_campaigns(self._read()['answers']['meta_account'])
                if not any(c.get('id') == body['campaign_id'] for c in campaigns):
                    raise ValueError('בחר קמפיין מהרשימה')
            return self._advance('meta_campaign', {'meta_sales_campaign': body['campaign_id']})
        if step == 'shopify':
            if set(body) != {'domain'}:
                raise ValueError('כתוב את כתובת החנות, או "אין חנות שופיפיי"')
            if body['domain'] is None:
                return self._advance('shopify', {'shop': None})
            domain = str(body['domain']).strip().lower().removeprefix('https://').rstrip('/')
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]*\.myshopify\.com', domain):
                raise ValueError('כתובת החנות צריכה להיות בצורה name.myshopify.com')
            identity = self.connectors.shopify_identity(domain)
            answers = self._read()['answers']
            if identity.get('domain') != domain or identity.get('currency') != answers['currency']:
                raise ValueError('החנות שחוברה לא תואמת את הכתובת או את מטבע העסק')
            return self._advance('shopify', {'shop': domain})
        if step == 'questionnaire':
            allowed = {'owner_name', 'aov', 'gross_margin', 'copy_form', 'store_facts', 'free_shipping_from', 'returns_days', 'never_write'}
            if not {'aov', 'gross_margin', 'copy_form'} <= set(body) <= allowed:
                raise ValueError('נדרשים: הזמנה ממוצעת, רווח גולמי ולשון הפנייה בקופי')
            clean = {'aov': _number(body['aov'], 1, 1_000_000, 'הזמנה ממוצעת'),
                     'gross_margin': _number(body['gross_margin'], 0.05, 0.95, 'רווח גולמי (בין 5% ל-95%)')}
            if body['copy_form'] not in FORMS:
                raise ValueError('בחר לשון פנייה: זכר, נקבה או רבים')
            clean['copy_form'] = body['copy_form']
            for key in ('store_facts', 'never_write'):
                values = body.get(key) or []
                if not isinstance(values, list) or len(values) > 10 or any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 80 or '·' in v for v in values):
                    raise ValueError('עד 10 משפטים קצרים בכל רשימה')
                clean[key] = [v.strip() for v in values]
            for key, high in (('free_shipping_from', 100_000), ('returns_days', 365)):
                value = body.get(key)
                clean[key] = None if value is None else int(_number(value, 0, high, key))
            if body.get('owner_name') is not None:
                if not isinstance(body['owner_name'], str) or not 1 <= len(body['owner_name'].strip()) <= 60:
                    raise ValueError('שם הבעלים לא תקין')
                clean['owner_name'] = body['owner_name'].strip()
            return self._advance('questionnaire', clean)
        raise ValueError('שלב לא מוכר')

    def _business(self, answers):
        account = answers['meta_account']
        return business_config.validate({
            'id': 'b' + account, 'name': answers['name'], 'description': 'shop selling: ' + answers['what_you_sell'],
            'meta_account': account, 'meta_sales_campaign': answers.get('meta_sales_campaign'), 'shop': answers.get('shop'),
            'currency': answers['currency'], 'timezone': answers['timezone'], 'language': answers['language'],
            'country': answers['country'], 'rules_dir': 'rules', 'rulebook_file': 'rulebook.json'})

    def review(self, overrides=None):
        """What approval will write: the business, every rule in plain Hebrew, and how each number was derived."""
        answers = self._read()['answers']
        b = self._business(answers)
        t = proposed_thresholds(answers['aov'], answers['gross_margin'])
        for key, value in (overrides or {}).items():
            if key not in t or key in ('breakeven_roas', 'breakeven_cpa'):
                raise ValueError('אפשר לשנות רק את הספים המוצעים')
            t[key] = _number(value, 0, 1_000_000, key) if not isinstance(t[key], int) else int(_number(value, 0, 1_000_000, key))
        if not t['fail_below'] < t['hold_up_to'] < t['winner_above']:
            raise ValueError('הספים צריכים לעלות: עצירה < החזקה < מנצחת')
        s = SYMBOL.get(b.currency, b.currency + ' ')
        derived = [f'כל {s}1 בפרסום צריך להחזיר לפחות {t["breakeven_roas"]} כדי לא להפסיד (כי הרווח הגולמי הוא {round(answers["gross_margin"] * 100)}%).',
                   f'מכירה אחת יכולה לעלות עד {s}{t["breakeven_cpa"]} בפרסום בלי הפסד (הזמנה ממוצעת {s}{round(answers["aov"])}).',
                   'הספים למטה מוצעים לפי אותם יחסים שעובדים אצל ROSERY. הם הצעה, לא עובדה, ואפשר לשנות כל אחד.']
        texts, book = build_rules(b, answers, t)
        return {'business': b.public(), 'thresholds': t, 'derived': derived,
                'rules': [{'id': r['id'], 'text': r['source_excerpt']} for r in book['rules']], 'live_calls': 0}

    def approve(self, overrides=None):
        """Write the new business's files into its own data folder. Nothing outside it is touched."""
        with self.lock:
            state = self._read()
            if state['step'] != 'review':
                raise ValueError('אפשר לאשר רק בשלב הבדיקה האחרונה')
        preview = self.review(overrides)
        answers = state['answers']
        b = self._business(answers)
        texts, book = build_rules(b, answers, preview['thresholds'])
        rules = self.directory / 'rules'
        rules.mkdir(exist_ok=True)
        for name, text in texts.items():
            (rules / name).write_bytes(text.encode('utf-8'))
        (rules / b.rulebook_file).write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding='utf-8')
        profile = {'business_id': b.id, 'name': b.name, 'what_you_sell': answers['what_you_sell'], 'meta_account': b.meta_account,
                   'shop': b.shop, 'currency': b.currency, 'aov': answers['aov'], 'gross_margin': answers['gross_margin'],
                   'known': ['הזמנה ממוצעת', 'רווח גולמי'], 'missing': ['עלויות משלוח ואריזה', 'היסטוריית מודעות'],
                   'review_depth': 'onboarding', 'created_at': self.clock().isoformat()}
        (self.directory / 'profile.json').write_text(json.dumps(profile, ensure_ascii=False, indent=1), encoding='utf-8')
        business_config.write(self.directory, b)
        with self.lock:
            state = self._read()
            state.update(step='done', approved_at=self.clock().isoformat(), thresholds=preview['thresholds'])
            self._save(state)
        return {**self.status(), 'business': asdict(b)}
