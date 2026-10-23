"""Pure experiment planning and attributed purchase-rate evidence; no I/O."""
from dataclasses import dataclass, replace
from datetime import date, timedelta
import math
import random


LIMITS = 'ייחוס מטא אינו מוכיח מכירות תוספתיות או סיבתיות; חפיפת קהלים ושינויים חיצוניים עלולים להשפיע.'
TRANSITIONS = {'planned': {'prepared'}, 'prepared': {'running'},
               'running': {'decided', 'inconclusive', 'interrupted', 'stopped_for_harm'}}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


@dataclass(frozen=True)
class Arm:
    label: str
    creative_kind: str
    asset_ids: tuple

    def __post_init__(self):
        object.__setattr__(self, 'asset_ids', tuple(self.asset_ids))
        if not self.label or not self.creative_kind or not 5 <= len(self.asset_ids) <= 8 or len(set(self.asset_ids)) != len(self.asset_ids) or not all(self.asset_ids):
            raise ValueError('נדרשים תיאור ו־5–8 נכסים שונים בכל זרוע')


@dataclass(frozen=True)
class Plan:
    question: str
    variable: str
    a: Arm
    b: Arm
    daily_budget_minor: int
    start_date: str
    min_days: int = 7
    max_days: int = 21
    what_it_will_not_prove: str = LIMITS
    state: str = 'planned'

    def __post_init__(self):
        date.fromisoformat(self.start_date)
        if not self.question.strip() or not self.variable.strip() or not self.what_it_will_not_prove.strip():
            raise ValueError('חסרים שאלת הניסוי, המשתנה או מגבלות ההסקה')
        if type(self.daily_budget_minor) is not int or self.daily_budget_minor <= 0:
            raise ValueError('התקציב חייב להיות מספר שלם חיובי באגורות')
        if type(self.min_days) is not int or type(self.max_days) is not int or not 1 <= self.min_days <= self.max_days:
            raise ValueError('טווח הימים אינו תקין')
        if self.state not in set(TRANSITIONS) | TRANSITIONS['running']:
            raise ValueError('מצב ניסוי אינו תקין')
        if set(self.a.asset_ids) & set(self.b.asset_ids):
            raise ValueError('נכסים חייבים להיות שונים בין הזרועות')

    @property
    def total_cost_minor(self):
        return 2 * self.daily_budget_minor * self.max_days

    def transition(self, state):
        if state not in TRANSITIONS.get(self.state, set()):
            raise ValueError('מעבר מצב אסור')
        return replace(self, state=state)

    def edit_arm(self, arm, value):
        if arm not in ('a', 'b') or self.state not in ('planned', 'prepared', 'running'):
            raise ValueError('לא ניתן לערוך זרוע במצב זה')
        return replace(self, **{arm: value}, state='interrupted' if self.state == 'running' else 'planned')


def evaluate(plan, daily, as_of):
    """daily maps A/B to rows: date, spend_minor, purchases, checkouts.

    as_of is the last completed reporting day (inclusive). Every expected day
    must be explicitly covered in both arms, including explicit zero days.
    Gamma shape=.5, rate=1e-6 per ILS; local seeded sampling is reproducible.
    """
    result = {'state': plan.state, 'winner': None, 'probability_b_gt_a': None,
              'lift_interval_90': None, 'reason': '', 'live_action_allowed': False}
    if plan.state != 'running':
        result['reason'] = 'הניסוי אינו פועל'
        return result
    days = (date.fromisoformat(as_of) - date.fromisoformat(plan.start_date)).days + 1
    expected = {(date.fromisoformat(plan.start_date) + timedelta(days=i)).isoformat()
                for i in range(max(0, min(days, plan.max_days)))}
    totals = []
    for arm in ('A', 'B'):
        rows = daily.get(arm, [])
        if not expected or len(rows) != len(expected) or {r.get('date') for r in rows} != expected or any(
                not _number(r.get(k)) or int(r[k]) != r[k]
                for r in rows for k in ('spend_minor', 'purchases', 'checkouts')):
            result['reason'] = 'נתונים יומיים חסרים או לא תקינים; התוצאה אינה ידועה'
            return result
        totals.append(tuple(sum(r[k] for r in rows) for k in ('spend_minor', 'purchases', 'checkouts')))
    for i, (spend, purchases, checkouts) in enumerate(totals):
        if spend >= 40000 and checkouts == 0 and totals[1-i][1] >= 3:
            result.update(state='stopped_for_harm', reason='עצירה בשל נזק: הוצאה ללא התחלות תשלום מול רכישות בזרוע האחרת')
            return result
    rng = random.Random(1729)
    lifts = []
    for _ in range(30000):
        a, b = [rng.gammavariate(p + .5, 1 / (s / 100 + 1e-6)) for s, p, c in totals]
        lifts.append(b / a - 1)
    probability = sum(x > 0 for x in lifts) / len(lifts)
    lifts.sort()
    result.update(probability_b_gt_a=probability, lift_interval_90=[lifts[1500], lifts[28499]])
    eligible = days >= plan.min_days and all(s >= 60000 and p >= 5 for s, p, c in totals)
    if eligible and (probability >= .95 or probability <= .05):
        result.update(state='decided', winner='B' if probability >= .95 else 'A', reason='סף הראיות הושג')
    elif days >= plan.max_days:
        result.update(state='inconclusive', reason='הזמן המרבי הסתיים ללא הכרעה; יש לקרוא את טווח השינוי')
    else:
        result['reason'] = 'טרם הושג סף הראיות'
    return result


def rank_candidates(hypotheses, account_history, owner_ideas, creatives, account_cpa=None):
    """Creatives map creative-kind to asset IDs; CPA is ILS, costs are agorot.

    Explicit ideas/hypotheses use question, variable, a_kind, b_kind and
    optional expected_value. History uses AccountHistory.report windows.
    Unknown CPA stays unknown; it is never replaced with a profitability CPA.
    """
    candidates = [dict(x) for x in hypotheses if x.get('status', 'testing') in ('open', 'testing', 'inconclusive')]
    candidates.extend(dict(x) if isinstance(x, dict) else {'question': x} for x in owner_ideas)
    window = account_history.get('windows', {}).get('all', {})
    for dimension in window.get('patterns', {}).get('dimensions', []):
        values = dimension.get('values', [])
        if len(values) >= 2 and any(not v.get('enough_data') for v in values):
            candidates.append({'question': f"האם {values[1]['value']} משיג יותר רכישות לשקל לעומת {values[0]['value']}?",
                               'variable': dimension['dimension'], 'a_kind': str(values[0]['value']),
                               'b_kind': str(values[1]['value'])})
    proposals = []
    for candidate in candidates:
        assets = [tuple(dict.fromkeys(creatives.get(candidate.get(k), []))) for k in ('a_kind', 'b_kind')]
        refusal = None
        if any(len(a) < 5 for a in assets) or set(assets[0]) & set(assets[1]):
            refusal = 'אין חמישה נכסי קריאייטיב נפרדים לכל זרוע; נדרש להשלים קריאייטיב לפני הכנה'
        cost = math.ceil(2 * max(600, 5 * account_cpa) * 100) if _number(account_cpa) and account_cpa > 0 else None
        proposals.append({**candidate, 'why': 'בדיקה מבוקרת של השערה פתוחה; ההיסטוריה מספקת כיוון ואינה מוכיחה סיבתיות',
                          'estimated_cost_minor': cost, 'cost_note': 'אומדן לפי מחיר רכישה בחשבון, ללא הבטחה להכרעה' if cost else 'מחיר הרכישה בחשבון אינו ידוע',
                          'refusal_reason': refusal, 'asset_ids': {'A': list(assets[0][:8]), 'B': list(assets[1][:8])}})
    proposals.sort(key=lambda x: (x['refusal_reason'] is not None, -x.get('expected_value', 0), x['question']))
    return proposals[:3]
