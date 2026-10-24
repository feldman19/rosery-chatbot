"""A controlled two-version test in Meta (step 3 of the experiment engine); pure, no I/O.

Spec → exact Meta payload → plain-Hebrew approval preview → result reading. Nothing here talks to Meta;
meta_experiment_actions.py does the preview/confirm/readback writes through an injected transport, and
meta_experiment_check.py does the GET-only permission check.

What Meta's docs and official SDK say (read 2026-10-24, see .agents/design/experiments.md, "step 3"):
- A split test is an ad study created with POST /{business_id}/ad_studies, type SPLIT_TEST, and cells
  [{name, treatment_percentage, adsets: [...]}]. The SDK (facebook-python-business-sdk v26.0) has
  create_ad_study on Business and User only; the ad account and the ad set have GET ad_studies only.
- A creative test inside one ad set is type SPLIT_TEST_V2 with creative_test_config (daily_budget or
  lifetime_budget_percentage). The SDK's type enum does not list SPLIT_TEST_V2 and the cell shape for it is
  not confirmed, so it is NOT built here (one ad per cell also breaks the owner's 5–8 ads rule).
Every Meta field name lives in META below so a wrong name is a one-line fix.
"""
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
import math

from business import ACTIVE as BUSINESS
from experiments import LIMITS, Plan, evaluate

# ---- Meta wire names (one place; see the design doc for which are verified) ----
META = {
    'study_edge': 'ad_studies',            # POST /{business_id}/ad_studies (docs + SDK Business.create_ad_study)
    'study_type': 'SPLIT_TEST',            # SDK AdStudy.Type.split_test
    'cell_adsets': 'adsets',               # docs example only; the SDK types cells as list<Object> (unverified key)
    'cell_share': 'treatment_percentage',  # SDK AdStudyCell field
    'readback_fields': 'id,name,type,start_time,end_time,canceled_time,cells{name,treatment_percentage,ad_entities_count}',
    'result_fields': 'id,type,end_time,canceled_time,confidence_level,objectives{id,name,type,is_primary,results,last_updated_results}',
    'purchase_actions': ('offsite_conversion.fb_pixel_purchase', 'omni_purchase'),
    'checkout_actions': ('offsite_conversion.fb_pixel_initiate_checkout', 'omni_initiated_checkout'),
}
META_MAX_DAYS = 30          # Ads Manager limit as reported by third-party guides; not confirmed in Meta's docs
META_MIN_SHARE = 10         # docs: treatment_percentage ≥ 10 per cell, sum ≤ 100
MIN_META_CONFIDENCE = 0.95  # the same bar as the engine's own gate; Meta's own "winner" label may use less
MIN_ARM_SPEND_MINOR = 60000  # ₪600 per version (experiments.evaluate)
MIN_ARM_PURCHASES = 5
RULES_USED = ('ads_per_adset', 'ad_min_full_days', 'breakeven', 'single_purchase_not_verdict',
              'approval_required', 'readback_required', 'no_asc_cbo')
HEBREW_ARM = {'A': 'א', 'B': 'ב'}


class SpecRefused(ValueError):
    """The test cannot be built as asked; the message is for the owner, in Hebrew."""


def ils(minor):
    return '₪' + format(int(minor) / 100, ',.2f').rstrip('0').rstrip('.')


def _day(value):
    return value.strftime('%d/%m/%Y')


def _ids(value):
    return isinstance(value, str) and value.isdigit() and 5 <= len(value) <= 30


def _zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        raise SpecRefused('אזור הזמן של החשבון לא זמין במחשב הזה; אי אפשר לקבוע מתי הניסוי מתחיל') from None


@dataclass(frozen=True)
class Spec:
    """One approved-to-be test: the plan, where its two versions live in Meta, and the success measure."""
    experiment_id: str
    plan: Plan
    business_id: str
    campaign_id: str
    adset_ids: tuple          # (A, B): two existing PAUSED ad sets, one per version
    timezone_name: str
    start_time: int           # Unix seconds, 00:00 of start_date in the account's time zone
    end_time: int             # Unix seconds, 00:00 the day after the last day
    success: dict
    warnings: tuple
    rules: tuple


def build_spec(plan, *, experiment_id, rulebook, business_id, campaign_id, adset_ids, timezone_name=BUSINESS.timezone):
    """Check the plan against the owner's rules and Meta's limits; refuse rather than guess."""
    if not isinstance(experiment_id, str) or not experiment_id.isalnum() or len(experiment_id) > 64:
        raise SpecRefused('מזהה ניסוי לא תקין')
    if plan.state != 'planned':
        raise SpecRefused('אפשר להכין לניסוי רק תכנית שעוד לא הוכנה')
    if (rulebook or {}).get('status') != 'available':
        raise SpecRefused('ספר החוקים לא זמין או השתנה; לא מכינים ניסוי בלי החוקים שלך')
    rules = rulebook['rules']
    try:
        low, high = rules['ads_per_adset']['params']['min'], rules['ads_per_adset']['params']['max']
        adset_days = rules['ad_min_full_days']['params']['adset_days']
        breakeven = rules['breakeven']['params']['breakeven_cpa']
        cited = tuple({'rule_id': r, 'source': rules[r]['source'], 'quote': rules[r]['source_excerpt']} for r in RULES_USED)
    except (KeyError, TypeError):
        raise SpecRefused('חסר בספר החוקים חוק שהניסוי נשען עליו; לא מכינים') from None
    if not all(_ids(x) for x in (business_id, campaign_id)):
        raise SpecRefused('חסר מזהה עסק או קמפיין תקין')
    adset_ids = tuple(adset_ids)
    if len(adset_ids) != 2 or not all(_ids(x) for x in adset_ids) or adset_ids[0] == adset_ids[1]:
        raise SpecRefused('נדרשות שתי קבוצות מודעות שונות, אחת לכל גרסה')
    for arm in (plan.a, plan.b):
        if not low <= len(arm.asset_ids) <= high:
            raise SpecRefused(f'בכל גרסה צריך {low}–{high} מודעות לפי החוקים שלך')
    if plan.min_days < adset_days:
        raise SpecRefused(f'ניסוי קצר מ־{adset_days} ימים לא נשפט לפי החוקים שלך')
    if plan.max_days > META_MAX_DAYS:
        raise SpecRefused(f'מטא מאפשרת ניסוי של עד {META_MAX_DAYS} יום')
    if plan.max_days % 7:
        raise SpecRefused('אורך הניסוי חייב להיות שבועות שלמים (7, 14, 21 או 28 ימים), כדי שכל גרסה תקבל אותם ימי שבוע')
    if plan.daily_budget_minor * plan.max_days < MIN_ARM_SPEND_MINOR:
        raise SpecRefused(f'בתקציב הזה גרסה לא תגיע ל־{ils(MIN_ARM_SPEND_MINOR)} עד הסוף, ואז אי אפשר להכריע. כדאי להגדיל תקציב או להאריך')
    zone = _zone(timezone_name)
    first = date.fromisoformat(plan.start_date)
    start = datetime(first.year, first.month, first.day, tzinfo=zone)
    last = first + timedelta(days=plan.max_days)
    end = datetime(last.year, last.month, last.day, tzinfo=zone)
    warnings = []
    if plan.daily_budget_minor * plan.max_days < MIN_ARM_PURCHASES * breakeven * 100:
        warnings.append(f'כדי להכריע צריך לפחות {MIN_ARM_PURCHASES} רכישות בכל גרסה. במחיר רכישה של ₪{breakeven} (נקודת האיזון שלך) '
                        f'זה בערך {ils(MIN_ARM_PURCHASES * breakeven * 100)} לגרסה, יותר ממה שהתקציב נותן, אז ייתכן שהניסוי ייגמר בלי הכרעה.')
    success = {'primary': 'purchases_per_ils', 'label': 'רכישות לכל שקל שהוצא (לפי הדיווח של מטא)',
               'secondary': 'התחלות תשלום לכל שקל: רק לכיוון מוקדם, לא להכרעה',
               'min_spend_minor_per_arm': MIN_ARM_SPEND_MINOR, 'min_purchases_per_arm': MIN_ARM_PURCHASES,
               'min_probability': MIN_META_CONFIDENCE, 'breakeven_cpa_ils': breakeven}
    return Spec(experiment_id, plan, business_id, campaign_id, adset_ids, timezone_name,
                int(start.timestamp()), int(end.timestamp()), success, tuple(warnings), cited)


def study_payload(spec):
    """The exact body of POST /{business_id}/ad_studies; two cells, half the audience each."""
    plan = spec.plan
    return {'name': ('ROSERY ניסוי ' + spec.experiment_id[:12] + ' | ' + plan.variable)[:200],
            'description': plan.question[:500],
            'type': META['study_type'],
            'start_time': spec.start_time,
            'end_time': spec.end_time,
            'cells': [{'name': (key + ' ' + arm.label)[:100], META['cell_share']: 50, META['cell_adsets']: [adset]}
                      for key, arm, adset in (('A', plan.a, spec.adset_ids[0]), ('B', plan.b, spec.adset_ids[1]))]}


def study_path(spec):
    return spec.business_id + '/' + META['study_edge']


def _local(stamp, spec):
    return datetime.fromtimestamp(stamp, _zone(spec.timezone_name))


def approval_preview(spec, step='create'):
    """Plain Hebrew for the owner: what is now, what will be after, how much it costs, how long."""
    plan = spec.plan
    total = plan.total_cost_minor
    first, last = _local(spec.start_time, spec), _local(spec.end_time, spec) - timedelta(days=1)
    versions = [f"גרסה {HEBREW_ARM[k]}: {arm.label} ({len(arm.asset_ids)} מודעות)" for k, arm in (('A', plan.a), ('B', plan.b))]
    if step == 'create':
        title = 'להכין ניסוי: ' + plan.question
        changes = [{'field': 'הניסוי', 'from': 'שתי קבוצות מודעות מושהות, בלי חלוקת קהל',
                    'to': 'ניסוי של מטא: הקהל מתחלק לשני חצאים בלי חפיפה, כל חצי רואה גרסה אחת'},
                   {'field': 'מצב המודעות', 'from': 'מושהות', 'to': 'עדיין מושהות. שום דבר לא מתחיל לרוץ עד שתאשר הפעלה בנפרד'}]
        spend_now = 'בשלב הזה לא יוצא כסף.'
    elif step == 'activate':
        title = 'להפעיל את הניסוי: ' + plan.question
        changes = [{'field': f'גרסה {HEBREW_ARM[k]}', 'from': 'מושהית', 'to': 'פעילה'} for k in ('A', 'B')]
        spend_now = 'מרגע ההפעלה מתחילה הוצאה.'
    else:
        raise ValueError('step')
    return {'title': title, 'question': plan.question, 'versions': versions, 'changes': changes,
            'cost': f'{ils(plan.daily_budget_minor)} ליום לכל גרסה, כלומר {ils(2 * plan.daily_budget_minor)} ליום. '
                    f'עד {ils(total)} בסך הכול אם הניסוי רץ עד הסוף. {spend_now}',
            'duration': f'{plan.max_days} ימים, מ־{_day(first)} עד {_day(last)}. לא מכריעים לפני {plan.min_days} ימים.',
            'measure': spec.success['label'],
            'decides_when': f'מכריעים רק כשלכל גרסה יש לפחות {ils(MIN_ARM_SPEND_MINOR)} הוצאה ו־{MIN_ARM_PURCHASES} רכישות, '
                            f'והסיכוי שאחת טובה מהשנייה הוא לפחות {MIN_META_CONFIDENCE:.0%}. אחרת התשובה היא "לא הוכרע".',
            'will_not_prove': plan.what_it_will_not_prove or LIMITS,
            'rules': [r['rule_id'] for r in spec.rules],
            'warnings': list(spec.warnings) + ['אסור לשנות אף גרסה בזמן הניסוי. שינוי עוצר את הניסוי ואז אין תשובה.'],
            'live_action_allowed': False}


# ---- monitoring ----
def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _count(actions, kinds):
    """First matching kind (Meta sends several aliases of one event; never sum them)."""
    for kind in kinds:
        for action in actions:
            if action.get('action_type') == kind:
                value = _number(action.get('value'))
                return None if value is None or value != int(value) else int(value)
    return 0


def daily_rows(insights, start_date, as_of):
    """Meta daily insights rows → the rows experiments.evaluate reads. Unknown stays unknown:
    a missing day, missing spend or a missing actions list is not turned into zero."""
    if not isinstance(insights, list):
        return None
    rows = []
    for row in insights:
        if not isinstance(row, dict) or row.get('date_start') != row.get('date_stop'):
            return None
        day = row.get('date_start')
        try:
            if not start_date <= date.fromisoformat(day).isoformat() <= as_of:
                continue
        except (TypeError, ValueError):
            return None
        spend = _number(row.get('spend'))
        actions = row.get('actions')
        if spend is None or not isinstance(actions, list) or not all(isinstance(a, dict) for a in actions):
            return None
        purchases, checkouts = _count(actions, META['purchase_actions']), _count(actions, META['checkout_actions'])
        if purchases is None or checkouts is None:
            return None
        rows.append({'date': day, 'spend_minor': int(round(spend * 100)), 'purchases': purchases, 'checkouts': checkouts})
    return rows


def meta_verdict(study):
    """Meta's own winner and confidence, only from a shape we have verified. The exact result shape of a
    split test (objectives[].results) is not documented in what we could read, so until a live readback
    confirms it this returns None and the decision rests on the engine's own gate."""
    return None


def interpret(spec, daily, as_of, meta=None):
    """The answer for the owner. Never a winner without the engine's gate or Meta's confidence ≥ 95%
    on enough data; a disagreement between the two is "לא הוכרע"."""
    # The stored state follows the Meta writes; the reading itself treats a not-yet-ended plan as running.
    plan = replace(spec.plan, state='running') if spec.plan.state in ('planned', 'prepared') else spec.plan
    engine = evaluate(plan, daily, as_of)
    out = {'state': engine['state'], 'winner': None, 'decided_by': None, 'probability_b_gt_a': engine['probability_b_gt_a'],
           'lift_interval_90': engine['lift_interval_90'], 'meta': meta, 'live_action_allowed': False}
    if engine['probability_b_gt_a'] is None:
        if engine['state'] == 'stopped_for_harm':
            return {**out, 'text': 'הניסוי נעצר כדי לא להפסיד כסף: גרסה אחת הוציאה הרבה בלי אף התחלת תשלום, והשנייה מכרה. זו לא הכרעה מלאה.',
                    'next_step': 'להשהות את הגרסה החלשה ולהחליט אם לנסות שוב עם מודעות אחרות'}
        return {**out, 'state': 'unknown' if plan.state == 'running' else plan.state,
                'text': 'עוד אין תשובה: ' + engine['reason'] + '.', 'next_step': 'לחכות לנתונים מלאים; לא מחליטים כלום בינתיים'}
    totals = {k: (sum(r['spend_minor'] for r in daily[k]), sum(r['purchases'] for r in daily[k])) for k in ('A', 'B')}
    enough = all(s >= MIN_ARM_SPEND_MINOR and p >= MIN_ARM_PURCHASES for s, p in totals.values())
    meta_ok = (isinstance(meta, dict) and meta.get('winner') in ('A', 'B') and _number(meta.get('confidence')) is not None
               and meta['confidence'] <= 1)
    meta_sure = meta_ok and meta['confidence'] >= MIN_META_CONFIDENCE
    p = engine['probability_b_gt_a']
    lo, hi = engine['lift_interval_90']
    spread = f'גרסה ב משיגה בין {lo:+.0%} ל־{hi:+.0%} רכישות לשקל לעומת גרסה א (טווח סביר).'
    numbers = '; '.join(f"גרסה {HEBREW_ARM[k]}: {ils(s)} הוצאה, {n} רכישות" for k, (s, n) in totals.items())
    if engine['state'] == 'decided':
        winner = engine['winner']
        if meta_sure and meta['winner'] != winner:
            return {**out, 'state': 'inconclusive', 'text': f'לא הוכרע: החישוב שלנו ומטא לא מסכימים מי טובה יותר. {numbers}. {spread}',
                    'next_step': 'לא להעביר תקציב על סמך הניסוי הזה'}
        out.update(winner=winner, decided_by='engine+meta' if meta_sure else 'engine')
    elif meta_sure and enough and plan.min_days <= (date.fromisoformat(as_of) - date.fromisoformat(plan.start_date)).days + 1 \
            and ((p >= 0.5) == (meta['winner'] == 'B')):
        out.update(state='decided', winner=meta['winner'], decided_by='meta')
    if out['winner']:
        name = HEBREW_ARM[out['winner']]
        label = (plan.a if out['winner'] == 'A' else plan.b).label
        chance = max(p, 1 - p) if out['decided_by'] != 'meta' else meta['confidence']
        source = 'לפי החישוב של מטא' if out['decided_by'] == 'meta' else 'לפי החישוב שלנו' + (' ומטא מסכימה' if out['decided_by'] == 'engine+meta' else '')
        return {**out, 'state': 'decided',
                'text': f'גרסה {name} ({label}) מכרה יותר לכל שקל, {source}; הסיכוי שזה לא מקרי הוא {'יותר מ־99%' if chance > .99 else 'כ־' + format(chance, '.0%')}. {numbers}. {spread} {LIMITS}',
                'next_step': f'להציע להעביר בהדרגה תקציב לסוג "{label}" (כל שינוי באישור שלך)'}
    if meta_ok and not meta_sure:
        note = f' מטא מציגה כ־{meta["confidence"]:.0%} ביטחון, פחות מ־{MIN_META_CONFIDENCE:.0%}, אז זה לא מספיק.'
    else:
        note = ''
    if engine['state'] == 'inconclusive':
        return {**out, 'text': f'לא הוכרע: הזמן נגמר בלי מספיק ראיות. {numbers}. {spread}{note}',
                'next_step': 'לא להעביר תקציב על סמך הניסוי הזה; אפשר לחזור עליו עם תקציב גדול יותר'}
    return {**out, 'state': 'running', 'text': f'עוד מוקדם להכריע. הסיכוי שגרסה ב טובה יותר כרגע: כ־{p:.0%}. {numbers}.{note}',
            'next_step': 'לא לשנות כלום בגרסאות; לבדוק שוב מחר'}
