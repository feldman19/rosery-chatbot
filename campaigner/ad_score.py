"""Weighted Ad Score (owner's spec, 2026-10-23): the whole funnel, weighted by how much an ad has spent.

Advisory only, and in shadow: it never changes the decision the owner sees. The current rulebook decides;
the score is recorded beside it so disagreements can be judged later on what really happened.

Backtest 2026-10-23 (120 days, 64 launches, read-only): the score ranked days 4–14 better than raw 3-day
ROAS (Spearman 0.25 vs 0.00; 0.33 vs 0.14 for ads ≥₪500), but weakly, and weights tuned on one period
did not hold on the next (0.27 → 0.15). So the spec's weights are kept as they are.

Baselines are the account's medians over the last 7 full days (ads with ≥₪100 spend), not the spec's 30
days: the live report reads 1, 3 and 7 days. Missing events score 20, not 0 (little data ≠ failure).
"""
import math
from statistics import median

WEIGHTS = {'roas': 45, 'ic': 20, 'atc': 15, 'lpv': 10, 'ctr': 10}
ROAS_TARGET = 3.0
FAIL_BELOW = 2.6          # Meta default attribution includes view-through (owner rule)
LABELS = {'untested': 'לא נבדקה', 'continue': 'ממשיכה עד ₪300', 'kill': 'לכבות', 'hold': 'להחזיק',
          'success': 'מצליחה', 'winner': 'מנצחת: +20% תקציב'}
# The same verdict in the rule engine's words, to tell agreement from disagreement.
RULE_EQUIVALENT = {'untested': {'UNTESTED'}, 'continue': {'KEEP_TESTING'}, 'kill': {'PAUSE'}, 'hold': {'HOLD', 'KEEP_TESTING'},
                   'success': {'SUCCESS'}, 'winner': {'SCALE'}}


def _num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def metrics(row):
    """Window metrics of one report ad row; None when spend or purchases are unknown."""
    funnel = row.get('funnel') or {}
    m = {'spend': _num(row.get('spend')), 'purchases': _num(row.get('purchases')), 'revenue': _num(row.get('attributed_revenue')),
         'atc': _num(funnel.get('add_to_cart')), 'ic': _num(funnel.get('initiate_checkout')),
         'clicks': _num(funnel.get('link_clicks')), 'impressions': _num(funnel.get('impressions')),
         'lpv': _num(row.get('landing_page_views'))}
    if m['spend'] is None or m['purchases'] is None or (m['purchases'] and m['revenue'] is None):
        return None
    return m


def baseline(ad_rows):
    """Account medians and ratios from a window's ad rows (ads with ≥₪100 spend)."""
    rows = [m for m in (metrics(r) for r in ad_rows) if m and m['spend'] >= 100]
    med = lambda xs: median(xs) if xs else None
    total = lambda k: sum(m[k] or 0 for m in rows)
    return {'cpa': med([m['spend'] / m['purchases'] for m in rows if m['purchases']]),
            'cost_per_atc': med([m['spend'] / m['atc'] for m in rows if m['atc']]),
            'cost_per_ic': med([m['spend'] / m['ic'] for m in rows if m['ic']]),
            'lpv_rate': med([m['lpv'] / m['clicks'] for m in rows if m['lpv'] and m['clicks']]),
            'ctr': med([m['clicks'] / m['impressions'] for m in rows if m['clicks'] and m['impressions']]),
            'atc_to_purchase': total('purchases') / total('atc') if total('atc') else None,
            'ic_to_purchase': total('purchases') / total('ic') if total('ic') else None,
            'aov': total('revenue') / total('purchases') if total('purchases') else None, 'ads': len(rows)}


def _ratio(x, m):
    return 20.0 if not x or not m else max(0.0, min(100.0, 50 + 25 * math.log2(x / m)))


def _cost(c, m):
    return 20.0 if not c or not m else max(0.0, min(100.0, 50 + 25 * math.log2(m / c)))


def score(m, b, weights=WEIGHTS):
    """Score, effective ROAS, confidence, verdict and the two components that moved the score most."""
    funnel = 0.6 * (m['ic'] or 0) * (b['ic_to_purchase'] or 0) + 0.4 * (m['atc'] or 0) * (b['atc_to_purchase'] or 0)
    trust = min(1.0, m['spend'] / (3 * b['cpa'])) if b['cpa'] else 1.0
    effective = trust * m['purchases'] + (1 - trust) * funnel
    aov = m['revenue'] / m['purchases'] if m['purchases'] else b['aov']
    eroas = effective * aov / m['spend'] if m['spend'] and aov else 0.0
    per = lambda value, total: value / total if value and total else None
    parts = {'roas': _ratio(eroas, ROAS_TARGET),
             'ic': _cost(per(m['spend'], m['ic']), b['cost_per_ic']),
             'atc': _cost(per(m['spend'], m['atc']), b['cost_per_atc']),
             'lpv': _ratio(per(m['lpv'], m['clicks']), b['lpv_rate']),
             'ctr': _ratio(per(m['clicks'], m['impressions']), b['ctr'])}
    value = sum(weights[k] * parts[k] for k in weights) / sum(weights.values())
    confidence = 'high' if m['spend'] > 400 or m['purchases'] >= 3 else \
        'low' if m['spend'] < 150 or (m['atc'] or 0) < 3 else 'medium'
    ctr = per(m['clicks'], m['impressions']) or 0
    if m['spend'] < 150:
        verdict = 'untested'
    elif m['spend'] < 300 and m['purchases'] == 0 and (((m['atc'] or 0) >= 2 and (m['ic'] or 0) >= 1) or ctr >= 0.015):
        verdict = 'continue'
    elif m['spend'] >= 200 and eroas < FAIL_BELOW and value < 40:
        verdict = 'kill'
    elif eroas > 3.7 and value >= 70 and confidence == 'high':
        verdict = 'winner'
    elif eroas > ROAS_TARGET and value >= 60:
        verdict = 'success'
    else:
        verdict = 'hold'
    names = {'roas': f'ROAS אפקטיבי {eroas:.2f}',
             'ic': f'עלות לקופה ₪{per(m["spend"], m["ic"]):.0f} מול ₪{b["cost_per_ic"]:.0f} חציון' if m['ic'] and b['cost_per_ic'] else 'אין התחלות תשלום',
             'atc': f'עלות לסל ₪{per(m["spend"], m["atc"]):.0f} מול ₪{b["cost_per_atc"]:.0f} חציון' if m['atc'] and b['cost_per_atc'] else 'אין הוספות לסל',
             'lpv': 'אחוז צפייה בדף', 'ctr': f'CTR {ctr * 100:.1f}%'}
    reasons = [names[k] for k in sorted(parts, key=lambda k: -abs(weights[k] * (parts[k] - 50)))[:2]]
    return {'score': round(value, 1), 'effective_roas': round(eroas, 3), 'trust_in_purchases': round(trust, 3),
            'confidence': confidence, 'verdict': verdict, 'label': LABELS[verdict], 'reasons': reasons}


def agrees(verdict, rule_action):
    return rule_action in RULE_EQUIVALENT.get(verdict, set())
